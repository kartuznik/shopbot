from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import get_settings
from bot.database import add_product, delete_product, get_all_orders, get_all_users, update_product
from bot.keyboards.inline import admin_keyboard
from bot.states import ProductStates

router = Router()


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in set(get_settings().ADMIN_IDS)


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
    await state.set_state(ProductStates.price)
    await message.answer('Введите цену товара (например 199.99):')


@router.message(ProductStates.price)
async def product_price(message: Message, state: FSMContext) -> None:
    try:
        price = float((message.text or '').replace(',', '.'))
    except ValueError:
        await message.answer('Некорректная цена, попробуйте снова.')
        return
    await state.update_data(price=price)
    await state.set_state(ProductStates.photo_id)
    await message.answer('Отправьте photo_id или напишите "-" если фото нет:')


@router.message(ProductStates.photo_id)
async def product_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    photo_id = None if (message.text or '').strip() in {'', '-'} else message.text.strip()
    product_id = await add_product(
        name=data.get('name', '').strip(),
        description=data.get('description', '').strip(),
        price=float(data.get('price', 0)),
        photo_id=photo_id,
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


@router.callback_query(F.data == 'admin_orders')
@router.message(Command('orders'))
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
            f"#{order['id']} | user {order['user_id']} | {order['name']} x{order['quantity']} | {order['total_price']:.2f} | {order['status']}"
        )
    if len(lines) == 1:
        lines.append('Заказов пока нет.')
    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer('\n'.join(lines))
    if isinstance(event, CallbackQuery):
        await event.answer()


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
