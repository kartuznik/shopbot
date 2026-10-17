from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aiogram import Bot

from bot.config import Settings
from bot.database import (
    add_to_cart,
    cancel_pending_groups,
    clear_cart,
    create_order_group_from_cart,
    get_cart_items,
    get_order_group,
    get_payment_by_order,
    mark_group_paid,
    save_payment,
)
from bot.demo_catalog import (
    DemoCatalog,
    format_lines,
    parse_db_timestamp,
    pickup_time,
    render_confirmation,
    render_reminder,
)

logger = logging.getLogger('shopbot.demo')

PICKUP_ADDRESS = 'Самовывоз'
SIMULATED_PAYMENT_PREFIX = 'sim-'

CreatePayment = Callable[[int, float, str], Awaitable[tuple[str, str]]]
FetchStatus = Callable[[str], str | None]


@dataclass(frozen=True)
class CartSummary:
    lines: tuple[dict[str, Any], ...]
    quantity: int
    total: float

    @property
    def empty(self) -> bool:
        return not self.lines


@dataclass(frozen=True)
class Receipt:
    group_id: int
    items: str
    quantity: int
    total: float
    pickup: datetime
    payment_url: str | None

    @property
    def simulated(self) -> bool:
        return self.payment_url is None


@dataclass(frozen=True)
class OrderMessage:
    order_id: int
    user_id: int
    text: str


_catalog: DemoCatalog | None = None
_product_ids: dict[str, int] = {}


def activate(catalog: DemoCatalog, product_ids: dict[str, int]) -> None:
    global _catalog, _product_ids
    _catalog = catalog
    _product_ids = dict(product_ids)


def active_catalog() -> DemoCatalog | None:
    return _catalog


def product_id_for(item: Any) -> int | None:
    return _product_ids.get(item.id)


def demo_product_ids() -> list[int]:
    return list(_product_ids.values())


def payments_simulated(settings: Settings) -> bool:
    """Без ключей кассы оплата имитируется, чтобы запись ролика не упиралась в ЮKassa."""
    return not settings.YUKASSA_SHOP_ID.strip() or not settings.YUKASSA_SECRET_KEY.strip()


async def cart_summary(user_id: int) -> CartSummary:
    lines = tuple(
        {**item, 'line_total': float(item['price']) * int(item['quantity'])}
        for item in await get_cart_items(user_id)
    )
    return CartSummary(
        lines=lines,
        quantity=sum(int(line['quantity']) for line in lines),
        total=sum(line['line_total'] for line in lines),
    )


async def add_item(user_id: int, product_id: int) -> CartSummary:
    await add_to_cart(user_id, product_id, 1)
    return await cart_summary(user_id)


async def clear_user_cart(user_id: int) -> None:
    await cancel_pending_groups(user_id)
    await clear_cart(user_id)


def _group_quantity(group: dict[str, Any]) -> int:
    return sum(int(line['quantity']) for line in group['lines'])


def _group_pickup(group: dict[str, Any]) -> datetime:
    return pickup_time(parse_db_timestamp(group['created_at']))


async def checkout(catalog: DemoCatalog, user_id: int, create_payment: CreatePayment | None) -> Receipt | None:
    """Чек на всю корзину: один номер заказа и один платёж. None — корзина пуста."""
    if (await cart_summary(user_id)).empty:
        return None
    await cancel_pending_groups(user_id)
    group_id = await create_order_group_from_cart(user_id, address=PICKUP_ADDRESS)
    group = await get_order_group(group_id) if group_id else None
    if group is None:
        return None
    total = float(group['total'])

    payment_url: str | None = None
    if create_payment is None:
        await save_payment(
            order_id=group_id,
            amount=total,
            payment_url='',
            payment_id=f'{SIMULATED_PAYMENT_PREFIX}{group_id}',
        )
    else:
        payment_url, _ = await create_payment(group_id, total, f'{catalog.shop_name}: заказ #{group_id}')

    return Receipt(
        group_id=group_id,
        items=format_lines(catalog, group['lines']),
        quantity=_group_quantity(group),
        total=total,
        pickup=_group_pickup(group),
        payment_url=payment_url,
    )


def _template_args(catalog: DemoCatalog, group: dict[str, Any]) -> dict[str, Any]:
    return {
        'order_id': int(group['group_id']),
        'items': format_lines(catalog, group['lines']),
        'total': float(group['total']),
        'pickup': _group_pickup(group),
    }


def build_confirmation(catalog: DemoCatalog, group: dict[str, Any]) -> OrderMessage:
    return OrderMessage(
        order_id=int(group['group_id']),
        user_id=int(group['user_id']),
        text=render_confirmation(catalog, **_template_args(catalog, group)),
    )


def build_reminder(catalog: DemoCatalog, group: dict[str, Any]) -> OrderMessage:
    return OrderMessage(
        order_id=int(group['group_id']),
        user_id=int(group['user_id']),
        text=render_reminder(catalog, **_template_args(catalog, group)),
    )


async def confirm_payment(
    catalog: DemoCatalog, group_id: int, fetch_status: FetchStatus
) -> OrderMessage | None:
    """Подтверждение заказа или None, если чек отменён или касса ещё не видит оплату."""
    group = await get_order_group(group_id)
    if group is None or group['status'] == 'cancelled':
        return None
    if group['status'] != 'paid':
        payment = await get_payment_by_order(group_id)
        if payment is None:
            return None
        payment_id = str(payment.get('payment_id') or '')
        if not payment_id.startswith(SIMULATED_PAYMENT_PREFIX):
            status = await asyncio.to_thread(fetch_status, payment_id)
            if status != 'succeeded':
                return None
        await mark_group_paid(group_id)
        await clear_cart(int(group['user_id']))
        group = await get_order_group(group_id) or group
    return build_confirmation(catalog, group)


async def notify_customer_paid(bot: Bot, order_id: int) -> None:
    """Подтверждение покупателю после вебхука ЮKassa; заказы вне витрины не трогаем."""
    if _catalog is None:
        return
    group = await get_order_group(order_id)
    demo_ids = set(_product_ids.values())
    if group is None or not all(int(line['product_id']) in demo_ids for line in group['lines']):
        return
    await clear_cart(int(group['user_id']))
    message = build_confirmation(_catalog, group)
    try:
        await bot.send_message(chat_id=message.user_id, text=message.text)
    except Exception as error:
        logger.warning('Не удалось отправить подтверждение по заказу #%s: %s', order_id, error)
