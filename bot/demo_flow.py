from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiogram import Bot

from bot.config import Settings
from bot.database import (
    add_to_cart,
    clear_cart,
    create_order_from_cart,
    get_order_summary,
    get_payment_by_order,
    mark_order_paid,
    save_payment,
)
from bot.demo_catalog import (
    DemoCatalog,
    DemoItem,
    format_order_items,
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
class Reservation:
    order_id: int
    total: float
    items: str
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


def product_id_for(item: DemoItem) -> int | None:
    return _product_ids.get(item.id)


def demo_product_ids() -> list[int]:
    return list(_product_ids.values())


def payments_simulated(settings: Settings) -> bool:
    """Без ключей кассы оплата имитируется, чтобы запись ролика не упиралась в ЮKassa."""
    return not settings.YUKASSA_SHOP_ID.strip() or not settings.YUKASSA_SECRET_KEY.strip()


async def reserve(
    catalog: DemoCatalog,
    item: DemoItem,
    product_id: int,
    user_id: int,
    create_payment: CreatePayment | None,
) -> Reservation:
    # Одна позиция на заказ: create_order_from_cart заводит отдельный заказ на каждую строку корзины.
    await clear_cart(user_id)
    await add_to_cart(user_id, product_id, 1)
    result = await create_order_from_cart(user_id=user_id, address=PICKUP_ADDRESS, shipping_cost=0.0)
    order_id = int(result['order_ids'][0])
    total = float(result['total'])

    payment_url: str | None = None
    if create_payment is None:
        await save_payment(
            order_id=order_id,
            amount=total,
            payment_url='',
            payment_id=f'{SIMULATED_PAYMENT_PREFIX}{order_id}',
        )
    else:
        payment_url, _ = await create_payment(order_id, total, f'{catalog.shop_name}: заказ #{order_id}')

    return Reservation(
        order_id=order_id,
        total=total,
        items=format_order_items(catalog, item.title, 1),
        payment_url=payment_url,
    )


def _order_values(catalog: DemoCatalog, order: dict[str, Any]) -> dict[str, Any]:
    return {
        'order_id': int(order['id']),
        'items': format_order_items(catalog, str(order['product_name']), int(order['quantity'])),
        'total': float(order['total_price']),
        'pickup': pickup_time(parse_db_timestamp(order['created_at'])),
    }


def build_confirmation(catalog: DemoCatalog, order: dict[str, Any]) -> OrderMessage:
    return OrderMessage(
        order_id=int(order['id']),
        user_id=int(order['user_id']),
        text=render_confirmation(catalog, **_order_values(catalog, order)),
    )


def build_reminder(catalog: DemoCatalog, order: dict[str, Any]) -> OrderMessage:
    return OrderMessage(
        order_id=int(order['id']),
        user_id=int(order['user_id']),
        text=render_reminder(catalog, **_order_values(catalog, order)),
    )


async def confirm_payment(
    catalog: DemoCatalog, order_id: int, fetch_status: FetchStatus
) -> OrderMessage | None:
    """Подтверждение заказа или None, если касса ещё не видит оплату."""
    order = await get_order_summary(order_id)
    if order is None:
        return None
    if order['status'] != 'paid':
        payment = await get_payment_by_order(order_id)
        if payment is None:
            return None
        payment_id = str(payment.get('payment_id') or '')
        if not payment_id.startswith(SIMULATED_PAYMENT_PREFIX):
            status = await asyncio.to_thread(fetch_status, payment_id)
            if status != 'succeeded':
                return None
        await mark_order_paid(order_id)
        order = await get_order_summary(order_id) or order
    return build_confirmation(catalog, order)


async def notify_customer_paid(bot: Bot, order_id: int) -> None:
    """Подтверждение покупателю после вебхука ЮKassa; заказы вне витрины не трогаем."""
    if _catalog is None:
        return
    order = await get_order_summary(order_id)
    if order is None or int(order['product_id']) not in set(_product_ids.values()):
        return
    message = build_confirmation(_catalog, order)
    try:
        await bot.send_message(chat_id=message.user_id, text=message.text)
    except Exception as error:
        logger.warning('Не удалось отправить подтверждение по заказу #%s: %s', order_id, error)
