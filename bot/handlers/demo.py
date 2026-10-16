from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.config import get_settings
from bot.database import (
    clear_cart,
    get_last_order_for_products,
    get_order_summary,
    get_payment_by_order,
    reset_demo_orders,
    upsert_user,
)
from bot.demo_catalog import (
    DemoCatalog,
    DemoItem,
    PurchaseEvent,
    PurchaseState,
    advance,
    can_advance,
    card_caption,
    card_text_fallback,
    format_price,
    image_file,
)
from bot.demo_flow import (
    Reservation,
    active_catalog,
    build_reminder,
    confirm_payment,
    demo_product_ids,
    payments_simulated,
    product_id_for,
    reserve,
)
from bot.handlers.payment import create_yukassa_payment
from bot.webhook_security import fetch_payment_status

logger = logging.getLogger('shopbot.demo')

router = Router()

SIMULATED_PAYMENT_DELAY_SECONDS = 2


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in set(get_settings().ADMIN_IDS)


def _btn(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def menu_keyboard(catalog: DemoCatalog) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for section in catalog.sections():
        rows.append([_btn(f'— {section} —', 'dm:noop')])
        for item in catalog.items_in(section):
            rows.append([_btn(f'{item.emoji} {item.title} · {format_price(item.price)} ₽', f'dm:item:{item.id}')])
    rows.append([_btn('🧺 Сбросить корзину', 'dm:cart_reset')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def card_keyboard(item: DemoItem) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(f'💳 Забронировать за {format_price(item.price)} ₽', f'dm:reserve:{item.id}')],
            [_btn('⬅️ Назад в меню', 'dm:menu'), _btn('🧺 Сбросить корзину', 'dm:cart_reset')],
        ]
    )


def payment_keyboard(order_id: int, total: float, payment_url: str | None) -> InlineKeyboardMarkup:
    if payment_url:
        pay_rows = [
            [InlineKeyboardButton(text=f'💳 Оплатить {format_price(total)} ₽', url=payment_url)],
            [_btn('✅ Я оплатил', f'dm:check:{order_id}')],
        ]
    else:
        pay_rows = [[_btn(f'💳 Оплатить {format_price(total)} ₽', f'dm:check:{order_id}')]]
    return InlineKeyboardMarkup(
        inline_keyboard=[*pay_rows, [_btn('⬅️ Назад в меню', 'dm:menu'), _btn('🧺 Сбросить корзину', 'dm:cart_reset')]]
    )


def done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn('🥐 Заказать ещё', 'dm:menu')]])


async def _current(state: FSMContext) -> PurchaseState:
    raw = (await state.get_data()).get('demo_state')
    try:
        return PurchaseState(raw) if raw else PurchaseState.MENU
    except ValueError:
        return PurchaseState.MENU


_PREREQUISITES: dict[PurchaseEvent, tuple[PurchaseEvent, ...]] = {
    PurchaseEvent.RESERVE: (PurchaseEvent.OPEN_CARD,),
    PurchaseEvent.PAID: (PurchaseEvent.OPEN_CARD, PurchaseEvent.RESERVE),
}


async def _move(state: FSMContext, event: PurchaseEvent, **extra: object) -> PurchaseState:
    current = await _current(state)
    if not can_advance(current, event):
        # Кнопка из старого сообщения в истории чата: проходим недостающие шаги цепочки,
        # чтобы повторный дубль записи не упирался в состояние прошлого.
        for step in _PREREQUISITES.get(event, ()):
            current = advance(current, step)
    new_state = advance(current, event)
    await state.update_data(demo_state=new_state.value, **extra)
    return new_state


def _catalog_or_none() -> DemoCatalog | None:
    catalog = active_catalog()
    if catalog is None:
        logger.error('Витрина не загружена: DEMO_MODE включён, но каталог не активирован')
    return catalog


async def _send_menu(message: Message, catalog: DemoCatalog, text: str) -> None:
    await message.answer(text, reply_markup=menu_keyboard(catalog))


async def send_card(message: Message, item: DemoItem) -> None:
    keyboard = card_keyboard(item)
    path = image_file(item)
    if path is not None:
        try:
            # Файл читается в момент отправки: новая карточка в demo/images видна без рестарта.
            photo = BufferedInputFile(path.read_bytes(), filename=path.name)
            await message.bot.send_photo(
                chat_id=message.chat.id, photo=photo, caption=card_caption(item), reply_markup=keyboard
            )
            return
        except Exception as error:
            logger.warning('Карточка %s ушла текстом: %s', item.id, error)
    await message.answer(card_text_fallback(item), reply_markup=keyboard)


def _order_text(reservation: Reservation) -> str:
    return (
        f'🧾 Заказ #{reservation.order_id}\n'
        f'{reservation.items}\n'
        f'К оплате: {format_price(reservation.total)} ₽\n\n'
        'Оплатите — и я отложу его для вас к вашему приходу.'
    )


@router.message(CommandStart())
@router.message(Command('menu'))
async def cmd_start(message: Message, state: FSMContext) -> None:
    catalog = _catalog_or_none()
    if catalog is None or not message.from_user:
        return
    await upsert_user(message.from_user.id, message.from_user.username)
    await _move(state, PurchaseEvent.OPEN_MENU)
    await _send_menu(message, catalog, catalog.greeting)


@router.callback_query(F.data == 'dm:noop')
async def callback_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == 'dm:menu')
async def callback_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    catalog = _catalog_or_none()
    if catalog is None or callback.message is None:
        return
    await _move(state, PurchaseEvent.OPEN_MENU)
    await _send_menu(callback.message, catalog, f'☕ {catalog.shop_name} — меню 👇')


@router.callback_query(F.data == 'dm:cart_reset')
async def callback_cart_reset(callback: CallbackQuery, state: FSMContext) -> None:
    catalog = _catalog_or_none()
    if catalog is None or callback.message is None or not callback.from_user:
        await callback.answer()
        return
    await clear_cart(callback.from_user.id)
    await state.clear()
    await callback.answer('Корзина очищена')
    await _send_menu(callback.message, catalog, '🧺 Корзина пуста. Выберите, что вам по душе 👇')


@router.callback_query(F.data.startswith('dm:item:'))
async def callback_item(callback: CallbackQuery, state: FSMContext) -> None:
    catalog = _catalog_or_none()
    item = catalog.get((callback.data or '').split(':', 2)[2]) if catalog else None
    if item is None or callback.message is None:
        await callback.answer('Позиция не найдена', show_alert=True)
        return
    await callback.answer()
    await _move(state, PurchaseEvent.OPEN_CARD, demo_item=item.id)
    await send_card(callback.message, item)


@router.callback_query(F.data.startswith('dm:reserve:'))
async def callback_reserve(callback: CallbackQuery, state: FSMContext) -> None:
    catalog = _catalog_or_none()
    item = catalog.get((callback.data or '').split(':', 2)[2]) if catalog else None
    product_id = product_id_for(item) if item else None
    if catalog is None or item is None or product_id is None or callback.message is None or not callback.from_user:
        await callback.answer('Позиция не найдена', show_alert=True)
        return

    data = await state.get_data()
    pending_id = data.get('demo_order_id')
    if await _current(state) == PurchaseState.AWAITING_PAYMENT and data.get('demo_item') == item.id and pending_id:
        pending = await get_order_summary(int(pending_id))
        if pending is not None and pending['status'] != 'paid':
            payment = await get_payment_by_order(int(pending_id))
            await callback.answer('Этот заказ уже ждёт оплаты')
            await callback.message.answer(
                f'🧾 Заказ #{pending_id} уже ждёт оплаты.',
                reply_markup=payment_keyboard(
                    int(pending_id), float(pending['total_price']), (payment or {}).get('payment_url') or None
                ),
            )
            return

    await callback.answer()
    await upsert_user(callback.from_user.id, callback.from_user.username)
    create_payment = None if payments_simulated(get_settings()) else create_yukassa_payment
    try:
        reservation = await reserve(catalog, item, product_id, callback.from_user.id, create_payment)
    except Exception as error:
        logger.exception('Не удалось оформить бронь %s: %s', item.id, error)
        await callback.message.answer(
            '😔 Не получилось оформить оплату. Попробуйте ещё раз через минуту.',
            reply_markup=card_keyboard(item),
        )
        return

    await _move(state, PurchaseEvent.RESERVE, demo_item=item.id, demo_order_id=reservation.order_id)
    await callback.message.answer(
        _order_text(reservation),
        reply_markup=payment_keyboard(reservation.order_id, reservation.total, reservation.payment_url),
    )


@router.callback_query(F.data.startswith('dm:check:'))
async def callback_check(callback: CallbackQuery, state: FSMContext) -> None:
    catalog = _catalog_or_none()
    raw_id = (callback.data or '').split(':', 2)[2]
    if catalog is None or not raw_id.isdigit() or callback.message is None or not callback.from_user:
        await callback.answer('Заказ не найден', show_alert=True)
        return
    order_id = int(raw_id)
    order = await get_order_summary(order_id)
    if order is None:
        await callback.answer('Заказ не найден', show_alert=True)
        return
    if order['user_id'] != callback.from_user.id and not _is_admin(callback.from_user.id):
        await callback.answer('Нет доступа к заказу', show_alert=True)
        return

    payment = await get_payment_by_order(order_id)
    simulated = not (payment or {}).get('payment_url')
    await callback.answer()
    if order['status'] != 'paid':
        await callback.message.answer('⏳ Проводим оплату…' if simulated else '🔎 Проверяем оплату…')
        if simulated:
            await asyncio.sleep(SIMULATED_PAYMENT_DELAY_SECONDS)

    confirmation = await confirm_payment(catalog, order_id, fetch_payment_status)
    if confirmation is None:
        await callback.message.answer(
            'Оплата пока не поступила. Завершите её по кнопке «Оплатить» и нажмите «Я оплатил» ещё раз.',
            reply_markup=payment_keyboard(order_id, float(order['total_price']), (payment or {}).get('payment_url')),
        )
        return
    await _move(state, PurchaseEvent.PAID, demo_order_id=order_id)
    await callback.message.answer(confirmation.text, reply_markup=done_keyboard())


@router.message(Command('demo_remind'))
async def cmd_demo_remind(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    catalog = _catalog_or_none()
    order = await get_last_order_for_products(demo_product_ids()) if catalog else None
    if catalog is None or order is None:
        await message.answer('Заказов витрины пока нет.')
        return
    reminder = build_reminder(catalog, order)
    try:
        await message.bot.send_message(chat_id=reminder.user_id, text=reminder.text)
    except Exception as error:
        await message.answer(f'❌ Напоминание по заказу #{reminder.order_id} не доставлено: {error}')
        return
    await message.answer(f'📨 Напоминание по заказу #{reminder.order_id} отправлено.')


@router.message(Command('demo_reset'))
async def cmd_demo_reset(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    counts = await reset_demo_orders(demo_product_ids())
    await state.clear()
    await message.answer(
        '🧹 Витрина сброшена: '
        f"заказов {counts['orders']}, платежей {counts['payments']}, "
        f"отзывов {counts['reviews']}, позиций в корзинах {counts['cart_items']}."
    )
