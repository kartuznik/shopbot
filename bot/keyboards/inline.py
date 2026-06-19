from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def catalog_keyboard(products: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{item['name']} - {item['price']}", callback_data=f"product_{item['id']}")]
        for item in products
    ]
    rows.append([InlineKeyboardButton(text='🛒 Корзина', callback_data='open_cart')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def product_keyboard(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Купить', callback_data=f'buy_{product_id}')],
            [InlineKeyboardButton(text='⬅️ Назад к каталогу', callback_data='back_catalog')],
        ]
    )


def cart_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='✅ Оформить заказ', callback_data='checkout_cart')],
            [InlineKeyboardButton(text='🗑 Очистить корзину', callback_data='clear_cart')],
        ]
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='➕ Добавить товар', callback_data='admin_add_product')],
            [InlineKeyboardButton(text='📦 Заказы', callback_data='admin_orders')],
            [InlineKeyboardButton(text='👥 Пользователи', callback_data='admin_users')],
        ]
    )
