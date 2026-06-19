from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from bot.database import (
    add_to_cart,
    clear_cart,
    create_order_from_cart,
    get_cart_items,
    get_product,
    get_products,
    get_user_orders,
    upsert_user,
)
from bot.keyboards.inline import cart_keyboard, catalog_keyboard, product_keyboard

router = Router()


@router.message(CommandStart())
@router.message(Command('catalog'))
async def cmd_catalog(message: Message) -> None:
    if not message.from_user:
        return
    await upsert_user(message.from_user.id, message.from_user.username)
    products = await get_products()
    if not products:
        await message.answer('Каталог пока пуст. Загляните позже.')
        return
    await message.answer('Каталог товаров:', reply_markup=catalog_keyboard(products))


@router.message(Command('cart'))
async def cmd_cart(message: Message) -> None:
    if not message.from_user:
        return
    items = await get_cart_items(message.from_user.id)
    if not items:
        await message.answer('Корзина пуста.')
        return
    lines = ['Ваша корзина:']
    total = 0.0
    for item in items:
        line_total = float(item['price']) * int(item['quantity'])
        total += line_total
        lines.append(f"- {item['name']} x{item['quantity']} = {line_total:.2f}")
    lines.append(f"Итого: {total:.2f}")
    await message.answer('\n'.join(lines), reply_markup=cart_keyboard())


@router.message(Command('orders'))
async def cmd_orders(message: Message) -> None:
    if not message.from_user:
        return
    orders = await get_user_orders(message.from_user.id)
    if not orders:
        await message.answer('У вас пока нет заказов.')
        return
    lines = ['Ваши заказы:']
    for order in orders[:20]:
        lines.append(
            f"#{order['id']} | {order['name']} x{order['quantity']} | {order['total_price']:.2f} | {order['status']}"
        )
    await message.answer('\n'.join(lines))


@router.callback_query(F.data.startswith('product_'))
async def callback_product(callback: CallbackQuery) -> None:
    product_id = int(callback.data.replace('product_', ''))
    product = await get_product(product_id)
    if not product:
        await callback.answer('Товар не найден', show_alert=True)
        return
    text = (
        f"{product['name']}\n\n"
        f"{product['description'] or '-'}\n"
        f"Цена: {float(product['price']):.2f}"
    )
    await callback.answer()
    await callback.message.answer(text, reply_markup=product_keyboard(product_id))


@router.callback_query(F.data == 'back_catalog')
async def callback_back_catalog(callback: CallbackQuery) -> None:
    products = await get_products()
    await callback.answer()
    await callback.message.answer('Каталог товаров:', reply_markup=catalog_keyboard(products))


@router.callback_query(F.data == 'open_cart')
async def callback_open_cart(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    items = await get_cart_items(callback.from_user.id)
    await callback.answer()
    if not items:
        await callback.message.answer('Корзина пуста.')
        return
    lines = ['Ваша корзина:']
    total = 0.0
    for item in items:
        line_total = float(item['price']) * int(item['quantity'])
        total += line_total
        lines.append(f"- {item['name']} x{item['quantity']} = {line_total:.2f}")
    lines.append(f"Итого: {total:.2f}")
    await callback.message.answer('\n'.join(lines), reply_markup=cart_keyboard())


@router.callback_query(F.data.startswith('buy_'))
async def callback_buy(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    product_id = int(callback.data.replace('buy_', ''))
    await add_to_cart(callback.from_user.id, product_id, 1)
    await callback.answer('Товар добавлен в корзину')


@router.callback_query(F.data == 'clear_cart')
async def callback_clear_cart(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    await clear_cart(callback.from_user.id)
    await callback.answer('Корзина очищена')
    await callback.message.answer('Корзина очищена.')


@router.callback_query(F.data == 'checkout_cart')
async def callback_checkout(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    count = await create_order_from_cart(callback.from_user.id)
    await callback.answer()
    if count == 0:
        await callback.message.answer('Корзина пуста, нечего оформлять.')
    else:
        await callback.message.answer(f'✅ Заказ оформлен. Позиции: {count}')
