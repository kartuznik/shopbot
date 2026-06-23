from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import get_settings
from bot.database import (
    ORDER_STATUSES,
    add_category,
    add_product,
    delete_category,
    delete_product,
    get_all_orders,
    get_all_users,
    get_categories,
    get_order_details,
    update_order_status,
    update_product,
)
from bot.health import get_monitor
from bot.keyboards.inline import (
    ORDER_STATUS_LABELS,
    admin_keyboard,
    categories_pick_keyboard,
    order_status_keyboard,
    skip_photo_keyboard,
)
from bot.states import ProductStates

router = Router()


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in set(get_settings().ADMIN_IDS)


def _fmt_dt(value: object) -> str:
    if value is None:
        return 'N/A'
    return str(value)


def _fmt_uptime(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    return f'{days}d {hours:02d}:{minutes:02d}:{secs:02d}'


async def _notify_admins_status_change(bot, order_id: int, status: str) -> None:
    text = f"🔄 Статус заказа #{order_id} изменен: {ORDER_STATUS_LABELS.get(status, status)}"
    for admin_id in get_settings().ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text)
        except Exception:
            continue


@router.message(Command('admin'))
async def cmd_admin(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    await message.answer('Админ панель:', reply_markup=admin_keyboard())


@router.callback_query(F.data == 'admin_add_product')
@router.message(Command('add_product'))
async def cmd_add_product(event: Message | CallbackQuery, state: FSMContext) -> None:
    user_id = event.from_user.id if event.from_user else None
    if not _is_admin(user_id):
        if isinstance(event, CallbackQuery):
            await event.answer('Недостаточно прав', show_alert=True)
        else:
            await event.answer('Недостаточно прав.')
        return
    await state.set_state(ProductStates.name)
    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer('Введите название товара:')
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.message(ProductStates.name)
async def product_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text or '')
    await state.set_state(ProductStates.description)
    await message.answer('Введите описание товара:')


@router.message(ProductStates.description)
async def product_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description=message.text or '')
    categories = await get_categories()
    await state.set_state(ProductStates.category_id)
    await message.answer('Выберите категорию товара:', reply_markup=categories_pick_keyboard(categories))


@router.callback_query(F.data.startswith('admin_pick_category:'), ProductStates.category_id)
async def product_category_pick(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id if callback.from_user else None
    if not _is_admin(user_id):
        await callback.answer('Недостаточно прав', show_alert=True)
        return
    raw_category = (callback.data or '').split(':', maxsplit=1)[1]
    category_id = None if raw_category == 'none' else int(raw_category)
    await state.update_data(category_id=category_id)
    await state.set_state(ProductStates.price)
    await callback.answer()
    await callback.message.answer('Введите цену товара (например 199.99):')


@router.message(ProductStates.price)
async def product_price(message: Message, state: FSMContext) -> None:
    try:
        price = float((message.text or '').replace(',', '.'))
    except ValueError:
        await message.answer('Некорректная цена, попробуйте снова.')
        return
    await state.update_data(price=price)
    await state.set_state(ProductStates.photo)
    await message.answer('Отправьте фото товара или нажмите кнопку ниже:', reply_markup=skip_photo_keyboard())


@router.callback_query(F.data == 'admin_skip_photo', ProductStates.photo)
async def product_photo_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    product_id = await add_product(
        name=str(data.get('name', '')).strip(),
        description=str(data.get('description', '')).strip(),
        price=float(data.get('price', 0)),
        photo_id=None,
        category_id=data.get('category_id'),
    )
    await state.clear()
    await callback.message.answer(f'✅ Товар добавлен, ID: {product_id}')


@router.message(ProductStates.photo)
async def product_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    photo_id = message.photo[-1].file_id if message.photo else None
    if photo_id is None and (message.text or '').strip() not in {'', '-'}:
        await message.answer('Ожидается фото. Либо отправьте изображение, либо нажмите "Пропустить фото".')
        return
    product_id = await add_product(
        name=str(data.get('name', '')).strip(),
        description=str(data.get('description', '')).strip(),
        price=float(data.get('price', 0)),
        photo_id=photo_id,
        category_id=data.get('category_id'),
    )
    await state.clear()
    await message.answer(f'✅ Товар добавлен, ID: {product_id}')


@router.message(Command('edit_product'))
async def cmd_edit_product(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    parts = (message.text or '').split(maxsplit=3)
    if len(parts) < 4:
        await message.answer('Использование: /edit_product <id> <field> <value>')
        return
    product_id = int(parts[1])
    field = parts[2]
    value = parts[3]
    ok = await update_product(product_id, field, value)
    await message.answer('✅ Обновлено' if ok else '❌ Поле не поддерживается')


@router.message(Command('delete_product'))
async def cmd_delete_product(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer('Использование: /delete_product <id>')
        return
    await delete_product(int(parts[1]))
    await message.answer('✅ Товар удален')


@router.message(Command('add_category'))
async def cmd_add_category(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    raw = (message.text or '').replace('/add_category', '', 1).strip()
    if not raw:
        await message.answer('Использование: /add_category <name> | <description>')
        return
    if '|' in raw:
        name, description = [part.strip() for part in raw.split('|', maxsplit=1)]
    else:
        name, description = raw, ''
    if not name:
        await message.answer('Название категории не может быть пустым.')
        return
    try:
        category_id = await add_category(name, description)
    except Exception as error:
        await message.answer(f'❌ Не удалось добавить категорию: {error}')
        return
    await message.answer(f'✅ Категория добавлена, ID: {category_id}')


@router.message(Command('list_categories'))
async def cmd_list_categories(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    categories = await get_categories()
    if not categories:
        await message.answer('Категорий пока нет.')
        return
    lines = ['Категории:']
    for category in categories:
        lines.append(f"#{category['id']} | {category['name']} | {category['description'] or '-'}")
    await message.answer('\n'.join(lines))


@router.message(Command('delete_category'))
async def cmd_delete_category(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer('Использование: /delete_category <id>')
        return
    ok = await delete_category(int(parts[1]))
    await message.answer('✅ Категория удалена' if ok else '❌ Категория не найдена')


@router.callback_query(F.data == 'admin_orders')
@router.message(Command('admin_orders'))
async def cmd_orders(event: Message | CallbackQuery) -> None:
    user_id = event.from_user.id if event.from_user else None
    if not _is_admin(user_id):
        if isinstance(event, CallbackQuery):
            await event.answer('Недостаточно прав', show_alert=True)
        else:
            await event.answer('Недостаточно прав.')
        return
    orders = await get_all_orders()
    lines = ['Все заказы:']
    for order in orders[:30]:
        lines.append(
            f"#{order['id']} | user {order['user_id']} (@{order['username'] or '-'}) | "
            f"{order['name']} x{order['quantity']} | {order['total_price']:.2f} | "
            f"{ORDER_STATUS_LABELS.get(order['status'], order['status'])}"
        )
    if len(lines) == 1:
        lines.append('Заказов пока нет.')
    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer('\n'.join(lines))
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.message(Command('order_status'))
async def cmd_order_status(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    parts = (message.text or '').split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer('Использование: /order_status <id> <status>')
        return
    order_id = int(parts[1])
    if len(parts) == 2:
        await message.answer('Выберите новый статус:', reply_markup=order_status_keyboard(order_id))
        return
    status = parts[2].strip().lower()
    ok = await update_order_status(order_id, status)
    if not ok:
        await message.answer(f'❌ Неверный статус. Доступно: {", ".join(sorted(ORDER_STATUSES))}')
        return
    await message.answer(f'✅ Статус заказа #{order_id} обновлен: {ORDER_STATUS_LABELS.get(status, status)}')
    await _notify_admins_status_change(message.bot, order_id, status)


@router.callback_query(F.data.startswith('order_status_set:'))
async def callback_order_status_set(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id if callback.from_user else None
    if not _is_admin(user_id):
        await callback.answer('Недостаточно прав', show_alert=True)
        return
    _, raw_order_id, status = (callback.data or '').split(':')
    order_id = int(raw_order_id)
    ok = await update_order_status(order_id, status)
    if not ok:
        await callback.answer('Не удалось обновить статус', show_alert=True)
        return
    await callback.answer('Статус обновлен')
    await callback.message.answer(
        f'✅ Статус заказа #{order_id}: {ORDER_STATUS_LABELS.get(status, status)}'
    )
    await _notify_admins_status_change(callback.bot, order_id, status)


@router.message(Command('order_details'))
async def cmd_order_details(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer('Использование: /order_details <id>')
        return
    order = await get_order_details(int(parts[1]))
    if not order:
        await message.answer('Заказ не найден.')
        return
    await message.answer(
        '\n'.join(
            [
                f"Заказ #{order['id']}",
                f"Пользователь: {order['user_id']} (@{order['username'] or '-'})",
                f"Товар: {order['product_name']} x{order['quantity']}",
                f"Сумма: {float(order['total_price']):.2f}",
                f"Статус: {ORDER_STATUS_LABELS.get(order['status'], order['status'])}",
                f"Адрес: {order['address'] or '-'}",
                f"Доставка: {float(order['shipping_cost'] or 0):.2f}",
                f"Трек-номер: {order['tracking_number'] or '-'}",
                f"Создан: {order['created_at']}",
            ]
        )
    )


@router.callback_query(F.data == 'admin_users')
@router.message(Command('users'))
async def cmd_users(event: Message | CallbackQuery) -> None:
    user_id = event.from_user.id if event.from_user else None
    if not _is_admin(user_id):
        if isinstance(event, CallbackQuery):
            await event.answer('Недостаточно прав', show_alert=True)
        else:
            await event.answer('Недостаточно прав.')
        return
    users = await get_all_users()
    lines = ['Пользователи:']
    for user in users[:50]:
        lines.append(f"- {user['user_id']} @{user['username'] or '-'}")
    if len(lines) == 1:
        lines.append('Пользователей пока нет.')
    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer('\n'.join(lines))
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.message(Command('health'))
async def cmd_health(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    monitor = get_monitor()
    if monitor is None:
        await message.answer('Health monitor не инициализирован.')
        return
    state = monitor.get_status()
    text = (
        '🩺 Health monitor\n\n'
        f"Бот: {'online' if state['bot_online'] else 'offline'}\n"
        f"БД: {'ok' if state['last_db_ok'] else (state['last_db_error'] or 'error')}\n"
        f"Диск (data): {state['free_disk_bytes'] // (1024 * 1024)} MB\n"
        f"Uptime: {_fmt_uptime(state['uptime_seconds'])}\n"
        f"Активные пользователи: {state['active_users']}\n"
        f"Последний heal: {_fmt_dt(state['last_heal_at'])}\n"
        f"Последняя критическая ошибка: {state['last_critical_error'] or 'нет'}"
    )
    await message.answer(text)
