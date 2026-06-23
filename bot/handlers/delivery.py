from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import get_settings
from bot.database import (
    add_delivery_zone,
    add_user_address,
    create_order_from_cart,
    delete_delivery_zone,
    delete_user_address,
    get_delivery_stats,
    get_delivery_zones,
    get_user_address,
    get_user_addresses,
    set_default_user_address,
)
from bot.handlers.payment import payment_keyboard
from bot.states import DeliveryAddressStates, DeliveryZoneStates

router = Router()


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in set(get_settings().ADMIN_IDS)


def _zones_keyboard(zones: list[dict], prefix: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{zone['zone_name']} ({float(zone['cost']):.2f})", callback_data=f'{prefix}:{zone["id"]}')]
        for zone in zones
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _addresses_keyboard(addresses: list[dict], for_checkout: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in addresses:
        text = f"{item['address']} [{item['zone_name'] or '-'} | {float(item['cost'] or 0):.2f}]"
        choose_cb = f"checkout_use_addr:{item['id']}" if for_checkout else f"addr_set_default:{item['id']}"
        rows.append([InlineKeyboardButton(text=f'Выбрать: {text}', callback_data=choose_cb)])
        rows.append([InlineKeyboardButton(text='Удалить', callback_data=f'addr_delete:{item["id"]}')])
    rows.append([InlineKeyboardButton(text='Ввести новый', callback_data='checkout_new_address')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _notify_admins_new_order(message: Message, order_data: dict) -> None:
    if not message.from_user:
        return
    settings = get_settings()
    order_no = order_data['order_ids'][0] if order_data['order_ids'] else '-'
    username = f"@{message.from_user.username}" if message.from_user.username else str(message.from_user.id)
    items_text = ', '.join(
        f"{item['name']} x{item['quantity']} ({item['final_total']:.2f})" for item in order_data['items']
    )
    text = (
        f"🆕 Новый заказ #{order_no} от {username}\n"
        f"Товары: {items_text}\n"
        f"Сумма: {order_data['total']:.2f}₽"
    )
    for admin_id in settings.ADMIN_IDS:
        try:
            await message.bot.send_message(chat_id=admin_id, text=text)
        except Exception:
            continue


async def _finalize_checkout(callback: CallbackQuery, address: dict) -> None:
    if not callback.from_user:
        return
    shipping_cost = float(address.get('cost') or 0.0)
    result = await create_order_from_cart(
        user_id=callback.from_user.id,
        address=str(address['address']),
        shipping_cost=shipping_cost,
    )
    await callback.answer()
    if result['count'] == 0:
        await callback.message.answer('Корзина пуста, нечего оформлять.')
        return
    await callback.message.answer(
        f"✅ Заказ оформлен. Позиции: {result['count']}\n"
        f"Адрес: {address['address']}\n"
        f"Доставка: {shipping_cost:.2f}"
    )
    for order_id in result['order_ids']:
        await callback.message.answer(
            f'Заказ #{order_id}: нажмите кнопку для оплаты.',
            reply_markup=payment_keyboard(order_id),
        )
    await _notify_admins_new_order(callback.message, result)


@router.callback_query(F.data == 'checkout_cart')
async def callback_checkout(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    addresses = await get_user_addresses(callback.from_user.id)
    await callback.answer()
    if not addresses:
        await callback.message.answer(
            'Сначала добавьте адрес доставки через /add_address или кнопку ниже.',
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text='Ввести новый', callback_data='checkout_new_address')]]
            ),
        )
        return
    await callback.message.answer(
        'Выберите адрес доставки:',
        reply_markup=_addresses_keyboard(addresses, for_checkout=True),
    )


@router.callback_query(F.data.startswith('checkout_use_addr:'))
async def callback_checkout_use_addr(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    address_id = int((callback.data or '').split(':')[1])
    address = await get_user_address(address_id, callback.from_user.id)
    if not address:
        await callback.answer('Адрес не найден', show_alert=True)
        return
    await _finalize_checkout(callback, address)


@router.callback_query(F.data == 'checkout_new_address')
@router.message(Command('add_address'))
async def cmd_add_address(event: Message | CallbackQuery, state: FSMContext) -> None:
    await state.set_state(DeliveryAddressStates.address)
    await state.update_data(checkout_mode=isinstance(event, CallbackQuery))
    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer('Введите адрес доставки:')
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.message(DeliveryAddressStates.address)
async def delivery_address_text(message: Message, state: FSMContext) -> None:
    zones = await get_delivery_zones()
    if not zones:
        await message.answer('Зоны доставки не настроены. Сообщите администратору.')
        await state.clear()
        return
    await state.update_data(address=(message.text or '').strip())
    await state.set_state(DeliveryAddressStates.zone)
    await message.answer('Выберите зону доставки:', reply_markup=_zones_keyboard(zones, 'address_zone'))


@router.callback_query(F.data.startswith('address_zone:'), DeliveryAddressStates.zone)
async def delivery_address_zone(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user:
        return
    zone_id = int((callback.data or '').split(':')[1])
    data = await state.get_data()
    address_text = str(data.get('address', '')).strip()
    if not address_text:
        await callback.answer('Адрес не заполнен', show_alert=True)
        return
    existing = await get_user_addresses(callback.from_user.id)
    address_id = await add_user_address(
        user_id=callback.from_user.id,
        address=address_text,
        zone_id=zone_id,
        is_default=len(existing) == 0,
    )
    checkout_mode = bool(data.get('checkout_mode'))
    await state.clear()
    await callback.answer()
    await callback.message.answer(f'✅ Адрес сохранен (ID: {address_id})')
    if checkout_mode:
        selected = await get_user_address(address_id, callback.from_user.id)
        if selected is not None:
            await _finalize_checkout(callback, selected)


@router.message(Command('my_addresses'))
async def cmd_my_addresses(message: Message) -> None:
    if not message.from_user:
        return
    addresses = await get_user_addresses(message.from_user.id)
    if not addresses:
        await message.answer('У вас пока нет сохраненных адресов. Используйте /add_address.')
        return
    await message.answer('Ваши адреса:', reply_markup=_addresses_keyboard(addresses))


@router.callback_query(F.data.startswith('addr_delete:'))
async def callback_delete_address(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    address_id = int((callback.data or '').split(':')[1])
    ok = await delete_user_address(address_id, callback.from_user.id)
    await callback.answer('Удалено' if ok else 'Адрес не найден')


@router.callback_query(F.data.startswith('addr_set_default:'))
async def callback_set_default(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    address_id = int((callback.data or '').split(':')[1])
    ok = await set_default_user_address(address_id, callback.from_user.id)
    await callback.answer('Адрес выбран' if ok else 'Адрес не найден')


@router.callback_query(F.data == 'admin_delivery')
async def callback_admin_delivery(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id if callback.from_user else None):
        await callback.answer('Недостаточно прав', show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        'Команды доставки:\n'
        '/add_delivery_zone\n'
        '/list_delivery_zones\n'
        '/delete_delivery_zone <id>\n'
        '/delivery_stats'
    )


@router.message(Command('add_delivery_zone'))
async def cmd_add_delivery_zone(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    await state.set_state(DeliveryZoneStates.zone_name)
    await message.answer('Введите название зоны доставки:')


@router.message(DeliveryZoneStates.zone_name)
async def delivery_zone_name(message: Message, state: FSMContext) -> None:
    await state.update_data(zone_name=(message.text or '').strip())
    await state.set_state(DeliveryZoneStates.zone_cost)
    await message.answer('Введите стоимость доставки для зоны:')


@router.message(DeliveryZoneStates.zone_cost)
async def delivery_zone_cost(message: Message, state: FSMContext) -> None:
    try:
        cost = float((message.text or '').replace(',', '.'))
    except ValueError:
        await message.answer('Некорректная стоимость, попробуйте снова.')
        return
    data = await state.get_data()
    zone_id = await add_delivery_zone(zone_name=str(data.get('zone_name', '')).strip(), cost=cost)
    await state.clear()
    await message.answer(f'✅ Зона доставки добавлена, ID: {zone_id}')


@router.message(Command('list_delivery_zones'))
async def cmd_list_delivery_zones(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    zones = await get_delivery_zones()
    if not zones:
        await message.answer('Зоны доставки не созданы.')
        return
    lines = ['Зоны доставки:']
    for zone in zones:
        lines.append(
            f"#{zone['id']} | {zone['zone_name']} | {float(zone['cost']):.2f} | {zone['description'] or '-'}"
        )
    await message.answer('\n'.join(lines))


@router.message(Command('delete_delivery_zone'))
async def cmd_delete_delivery_zone(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer('Использование: /delete_delivery_zone <id>')
        return
    ok = await delete_delivery_zone(int(parts[1]))
    await message.answer('✅ Зона удалена' if ok else '❌ Зона не найдена')


@router.message(Command('delivery_stats'))
async def cmd_delivery_stats(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    stats = await get_delivery_stats()
    if not stats:
        await message.answer('Нет данных по доставке.')
        return
    lines = ['Статистика доставки по зонам:']
    for item in stats:
        lines.append(
            f"{item['zone_name']}: заказов {int(item['orders_count'])}, "
            f"доставка {float(item['shipping_total']):.2f}, продажи {float(item['sales_total']):.2f}"
        )
    await message.answer('\n'.join(lines))
