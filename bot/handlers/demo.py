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
    ReplyKeyboardMarkup,
)

from bot.config import get_settings
from bot.database import (
    get_last_group_for_products,
    get_order_group,
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
    format_lines,
    format_price,
    image_file,
    plural_positions,
)
from bot.demo_flow import (
    CartSummary,
    Receipt,
    active_catalog,
    add_item,
    build_confirmation,
    build_reminder,
    cart_summary,
    checkout,
    clear_user_cart,
    confirm_payment,
    demo_product_ids,
    payments_simulated,
    product_id_for,
)
from bot.handlers.payment import create_yukassa_payment
from bot.keyboards.reply import (
    ABOUT,
    CART_BUTTON_PATTERN,
    CLEAR_CART,
    CLEAR_CART_TEXTS,
    HELP,
    MENU,
    PAY,
    PAY_BUTTON_PATTERN,
    get_main_keyboard,
)
from bot.webhook_security import fetch_payment_status

logger = logging.getLogger('shopbot.demo')

router = Router()

SIMULATED_PAYMENT_DELAY_SECONDS = 2


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in set(get_settings().ADMIN_IDS)


def _btn(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def _cart_button(cart: CartSummary | None = None) -> InlineKeyboardButton:
    if cart is None or cart.empty:
        return _btn('🛒 Корзина', 'dm:cart')
    return _btn(f'🛒 Корзина · {cart.quantity} шт · {format_price(cart.total)} ₽', 'dm:cart')


BACK_TO_MENU = ('⬅️ Назад в меню', 'dm:menu')


def menu_keyboard(catalog: DemoCatalog, cart: CartSummary | None = None) -> InlineKeyboardMarkup:
    # Корзина — первой строкой: после добавления товаров путь к оформлению виден сразу.
    rows: list[list[InlineKeyboardButton]] = [[_cart_button(cart)]]
    for section in catalog.sections():
        rows.append([_btn(f'— {section} —', 'dm:noop')])
        for item in catalog.items_in(section):
            rows.append([_btn(f'{item.emoji} {item.title} · {format_price(item.price)} ₽', f'dm:item:{item.id}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def card_keyboard(item: DemoItem) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn('➕ В корзину', f'dm:add:{item.id}'), _btn('🛒 Оформить заказ', f'dm:buy:{item.id}')],
            [_cart_button(), _btn(*BACK_TO_MENU)],
        ]
    )


def cart_keyboard(cart: CartSummary) -> InlineKeyboardMarkup:
    if cart.empty:
        return InlineKeyboardMarkup(inline_keyboard=[[_btn(*BACK_TO_MENU)]])
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn('🛒 Оформить заказ', 'dm:checkout')],
            [_btn('🗑 Очистить корзину', 'dm:cart_clear')],
            [_btn(*BACK_TO_MENU)],
        ]
    )


def receipt_keyboard(group_id: int, total: float, payment_url: str | None) -> InlineKeyboardMarkup:
    if payment_url:
        pay_rows = [
            [InlineKeyboardButton(text=f'💳 Оплатить {format_price(total)} ₽', url=payment_url)],
            [_btn('✅ Я оплатил', f'dm:check:{group_id}')],
        ]
    else:
        pay_rows = [[_btn(f'💳 Оплатить {format_price(total)} ₽', f'dm:check:{group_id}')]]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            *pay_rows,
            [_btn('🗑 Очистить корзину', 'dm:cart_clear')],
            [_btn('⬅️ Назад в корзину', 'dm:cart')],
        ]
    )


async def main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    cart = await cart_summary(user_id)
    return get_main_keyboard(cart.quantity, cart.total)


def help_text(catalog: DemoCatalog) -> str:
    return (
        f'❓ Как заказать в «{catalog.shop_name}»\n\n'
        f'1. «{MENU}» — выберите позицию, откроется карточка с фото.\n'
        '2. «➕ В корзину» — добавьте всё, что хочется; количество растёт с каждым нажатием.\n'
        '3. «🛒 Корзина» — проверьте заказ и нажмите «🛒 Оформить заказ».\n'
        '4. «💳 Оплатить» — после оплаты заказ отложат к указанному времени самовывоза.\n\n'
        f'Когда в корзине есть товары, внизу появляется «{PAY} … ₽» — чек сразу, без захода в корзину.\n'
        f'«{CLEAR_CART}» — начать заново, «{ABOUT}» — адрес, часы работы и телефон.'
    )


CLEARED_TEXT = f'🗑 Корзина очищена. Загляните в «{MENU}», чтобы выбрать заново.'


def cart_text(catalog: DemoCatalog, cart: CartSummary) -> str:
    if cart.empty:
        return '🛒 Корзина пуста'
    return (
        '🛒 Ваша корзина:\n'
        f'{format_lines(catalog, list(cart.lines))}\n\n'
        f'Итого: {plural_positions(cart.quantity)} на {format_price(cart.total)} ₽'
    )


def added_text(item: DemoItem, cart: CartSummary) -> str:
    return (
        f'✅ Добавлено: {item.title}. '
        f'В корзине: {plural_positions(cart.quantity)} на {format_price(cart.total)} ₽'
    )


def receipt_text(receipt: Receipt) -> str:
    return (
        f'🧾 Заказ #{receipt.group_id}\n'
        f'{receipt.items}\n\n'
        f'Итого: {plural_positions(receipt.quantity)} на {format_price(receipt.total)} ₽\n'
        f'🕒 Самовывоз к {receipt.pickup.strftime("%H:%M")}\n\n'
        'Оплатите — и мы отложим заказ к вашему приходу.'
    )


async def _current(state: FSMContext) -> PurchaseState:
    raw = (await state.get_data()).get('demo_state')
    try:
        return PurchaseState(raw) if raw else PurchaseState.MENU
    except ValueError:
        return PurchaseState.MENU


_PREREQUISITES: dict[PurchaseEvent, tuple[PurchaseEvent, ...]] = {
    PurchaseEvent.ADD_TO_CART: (PurchaseEvent.OPEN_CARD,),
    PurchaseEvent.CHECKOUT: (PurchaseEvent.OPEN_CART,),
    PurchaseEvent.PAY: (PurchaseEvent.OPEN_CART, PurchaseEvent.CHECKOUT),
    PurchaseEvent.PAID: (PurchaseEvent.OPEN_CART, PurchaseEvent.CHECKOUT, PurchaseEvent.PAY),
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


def _item_from(callback: CallbackQuery, catalog: DemoCatalog | None) -> DemoItem | None:
    return catalog.get((callback.data or '').split(':', 2)[2]) if catalog else None


def _menu_header(catalog: DemoCatalog) -> str:
    return f'☕ {catalog.shop_name} — меню 👇'


async def _send_menu(message: Message, catalog: DemoCatalog, user_id: int) -> None:
    await message.answer(_menu_header(catalog), reply_markup=menu_keyboard(catalog, await cart_summary(user_id)))


async def _send_greeting_and_menu(message: Message, catalog: DemoCatalog, user_id: int) -> None:
    # У сообщения одна клавиатура: нижнюю везёт приветствие, инлайн-меню идёт следом.
    await message.answer(catalog.greeting, reply_markup=await main_keyboard(user_id))
    await _send_menu(message, catalog, user_id)


async def _clear_and_report(message: Message, state: FSMContext, user_id: int) -> None:
    await clear_user_cart(user_id)
    await state.clear()
    await message.answer(CLEARED_TEXT, reply_markup=get_main_keyboard())


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


async def _send_receipt(
    message: Message,
    user_id: int,
    state: FSMContext,
    catalog: DemoCatalog,
    empty_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
) -> None:
    create_payment = None if payments_simulated(get_settings()) else create_yukassa_payment
    try:
        receipt = await checkout(catalog, user_id, create_payment)
    except Exception as error:
        logger.exception('Не удалось оформить заказ: %s', error)
        await message.answer(
            '😔 Не получилось оформить оплату. Попробуйте ещё раз через минуту.',
            reply_markup=cart_keyboard(await cart_summary(user_id)),
        )
        return
    if receipt is None:
        await _move(state, PurchaseEvent.OPEN_CART)
        await message.answer('🛒 Корзина пуста', reply_markup=empty_markup or cart_keyboard(CartSummary((), 0, 0.0)))
        return
    await _move(state, PurchaseEvent.CHECKOUT, demo_order_id=receipt.group_id)
    await message.answer(
        receipt_text(receipt),
        reply_markup=receipt_keyboard(receipt.group_id, receipt.total, receipt.payment_url),
    )


@router.message(CommandStart())
@router.message(Command('menu'))
async def cmd_start(message: Message, state: FSMContext) -> None:
    catalog = _catalog_or_none()
    if catalog is None or not message.from_user:
        return
    await upsert_user(message.from_user.id, message.from_user.username)
    await _move(state, PurchaseEvent.OPEN_MENU)
    await _send_greeting_and_menu(message, catalog, message.from_user.id)


@router.callback_query(F.data == 'dm:noop')
async def callback_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == 'dm:menu')
async def callback_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    catalog = _catalog_or_none()
    if catalog is None or callback.message is None or not callback.from_user:
        return
    await _move(state, PurchaseEvent.OPEN_MENU)
    await _send_menu(callback.message, catalog, callback.from_user.id)


@router.callback_query(F.data.startswith('dm:item:'))
async def callback_item(callback: CallbackQuery, state: FSMContext) -> None:
    item = _item_from(callback, _catalog_or_none())
    if item is None or callback.message is None:
        await callback.answer('Позиция не найдена', show_alert=True)
        return
    await callback.answer()
    await _move(state, PurchaseEvent.OPEN_CARD, demo_item=item.id)
    await send_card(callback.message, item)


@router.callback_query(F.data.startswith('dm:add:'))
async def callback_add(callback: CallbackQuery, state: FSMContext) -> None:
    item = _item_from(callback, _catalog_or_none())
    product_id = product_id_for(item) if item else None
    if item is None or product_id is None or callback.message is None or not callback.from_user:
        await callback.answer('Позиция не найдена', show_alert=True)
        return
    await upsert_user(callback.from_user.id, callback.from_user.username)
    cart = await add_item(callback.from_user.id, product_id)
    await callback.answer(f'Добавлено: {item.title}')
    await _move(state, PurchaseEvent.ADD_TO_CART, demo_item=item.id)
    await callback.message.answer(added_text(item, cart), reply_markup=get_main_keyboard(cart.quantity, cart.total))


@router.callback_query(F.data.startswith('dm:buy:'))
async def callback_buy(callback: CallbackQuery, state: FSMContext) -> None:
    catalog = _catalog_or_none()
    item = _item_from(callback, catalog)
    product_id = product_id_for(item) if item else None
    if catalog is None or item is None or product_id is None or callback.message is None or not callback.from_user:
        await callback.answer('Позиция не найдена', show_alert=True)
        return
    await callback.answer()
    await upsert_user(callback.from_user.id, callback.from_user.username)
    # Быстрый сценарий: с пустой корзиной «Оформить» на карточке берёт эту позицию.
    # Если в корзине уже что-то есть, оформляется корзина как есть.
    if (await cart_summary(callback.from_user.id)).empty:
        await add_item(callback.from_user.id, product_id)
    await _move(state, PurchaseEvent.OPEN_CARD, demo_item=item.id)
    await _send_receipt(callback.message, callback.from_user.id, state, catalog)


@router.callback_query(F.data == 'dm:cart')
async def callback_cart(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    catalog = _catalog_or_none()
    if catalog is None or callback.message is None or not callback.from_user:
        return
    cart = await cart_summary(callback.from_user.id)
    await _move(state, PurchaseEvent.OPEN_CART)
    await callback.message.answer(cart_text(catalog, cart), reply_markup=cart_keyboard(cart))


@router.callback_query(F.data == 'dm:checkout')
async def callback_checkout(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    catalog = _catalog_or_none()
    if catalog is None or callback.message is None or not callback.from_user:
        return
    await upsert_user(callback.from_user.id, callback.from_user.username)
    await _send_receipt(callback.message, callback.from_user.id, state, catalog)


@router.callback_query(F.data == 'dm:cart_clear')
async def callback_cart_clear(callback: CallbackQuery, state: FSMContext) -> None:
    catalog = _catalog_or_none()
    if catalog is None or callback.message is None or not callback.from_user:
        await callback.answer()
        return
    await callback.answer('Корзина очищена')
    await _clear_and_report(callback.message, state, callback.from_user.id)


@router.callback_query(F.data.startswith('dm:check:'))
async def callback_check(callback: CallbackQuery, state: FSMContext) -> None:
    catalog = _catalog_or_none()
    raw_id = (callback.data or '').split(':', 2)[2]
    if catalog is None or not raw_id.isdigit() or callback.message is None or not callback.from_user:
        await callback.answer('Заказ не найден', show_alert=True)
        return
    group_id = int(raw_id)
    group = await get_order_group(group_id)
    if group is None:
        await callback.answer('Заказ не найден', show_alert=True)
        return
    if group['user_id'] != callback.from_user.id and not _is_admin(callback.from_user.id):
        await callback.answer('Нет доступа к заказу', show_alert=True)
        return
    await callback.answer()
    if group['status'] == 'cancelled':
        cart = await cart_summary(callback.from_user.id)
        await callback.message.answer(
            'Этот чек уже неактуален — корзина менялась. Оформите заказ заново 👇',
            reply_markup=cart_keyboard(cart),
        )
        return
    if group['status'] == 'paid':
        await callback.message.answer(
            build_confirmation(catalog, group).text, reply_markup=await main_keyboard(callback.from_user.id)
        )
        return

    payment = await get_payment_by_order(group_id)
    payment_url = (payment or {}).get('payment_url') or None
    await _move(state, PurchaseEvent.PAY, demo_order_id=group_id)
    await callback.message.answer('⏳ Проводим оплату…' if payment_url is None else '🔎 Проверяем оплату…')
    if payment_url is None:
        await asyncio.sleep(SIMULATED_PAYMENT_DELAY_SECONDS)

    confirmation = await confirm_payment(catalog, group_id, fetch_payment_status)
    if confirmation is None:
        await callback.message.answer(
            'Оплата пока не поступила. Завершите её по кнопке «Оплатить» и нажмите «Я оплатил» ещё раз.',
            reply_markup=receipt_keyboard(group_id, float(group['total']), payment_url),
        )
        return
    await _move(state, PurchaseEvent.PAID, demo_order_id=group_id)
    await callback.message.answer(confirmation.text, reply_markup=await main_keyboard(callback.from_user.id))


@router.message(F.text == MENU)
async def button_menu(message: Message, state: FSMContext) -> None:
    await cmd_start(message, state)


@router.message(F.text.regexp(CART_BUTTON_PATTERN))
async def button_cart(message: Message, state: FSMContext) -> None:
    catalog = _catalog_or_none()
    if catalog is None or not message.from_user:
        return
    cart = await cart_summary(message.from_user.id)
    await _move(state, PurchaseEvent.OPEN_CART)
    if cart.empty:
        await message.answer(cart_text(catalog, cart), reply_markup=get_main_keyboard())
        return
    await message.answer(cart_text(catalog, cart), reply_markup=cart_keyboard(cart))


@router.message(F.text.regexp(PAY_BUTTON_PATTERN))
async def button_pay(message: Message, state: FSMContext) -> None:
    catalog = _catalog_or_none()
    if catalog is None or not message.from_user:
        return
    await upsert_user(message.from_user.id, message.from_user.username)
    # Сумма в подписи могла устареть: чек всегда собирается по текущей корзине.
    await _send_receipt(message, message.from_user.id, state, catalog, empty_markup=get_main_keyboard())


@router.message(F.text.in_(CLEAR_CART_TEXTS))
async def button_clear_cart(message: Message, state: FSMContext) -> None:
    if _catalog_or_none() is None or not message.from_user:
        return
    await _clear_and_report(message, state, message.from_user.id)


@router.message(F.text == ABOUT)
async def button_about(message: Message) -> None:
    catalog = _catalog_or_none()
    if catalog is None or not message.from_user:
        return
    await message.answer(catalog.about, reply_markup=await main_keyboard(message.from_user.id))


@router.message(F.text == HELP)
async def button_help(message: Message) -> None:
    catalog = _catalog_or_none()
    if catalog is None or not message.from_user:
        return
    await message.answer(help_text(catalog), reply_markup=await main_keyboard(message.from_user.id))


@router.message(Command('demo_remind'))
async def cmd_demo_remind(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    catalog = _catalog_or_none()
    group_id = await get_last_group_for_products(demo_product_ids()) if catalog else None
    group = await get_order_group(group_id) if group_id else None
    if catalog is None or group is None:
        await message.answer('Заказов витрины пока нет.')
        return
    reminder = build_reminder(catalog, group)
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
