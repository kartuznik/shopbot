from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


ORDER_STATUS_LABELS = {
    'new': '🆕 new',
    'confirmed': '✅ confirmed',
    'paid': '💰 paid',
    'shipped': '🚚 shipped',
    'delivered': '📦 delivered',
    'cancelled': '❌ cancelled',
}


def catalog_categories_keyboard(categories: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=category['name'], callback_data=f"catalog_category:{category['id']}")]
        for category in categories
    ]
    rows.extend(
        [
            [InlineKeyboardButton(text='📦 Все товары', callback_data='catalog_all')],
            [InlineKeyboardButton(text='🔍 Поиск', callback_data='catalog_search')],
            [InlineKeyboardButton(text='🛒 Корзина', callback_data='open_cart')],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def catalog_products_keyboard(products: list[dict], scope: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{item['name']} - {item['price']}", callback_data=f"product:{item['id']}:{scope}")]
        for item in products
    ]
    rows.extend(
        [
            [InlineKeyboardButton(text='🛒 Корзина', callback_data='open_cart')],
            [InlineKeyboardButton(text='⬅️ Категории', callback_data='back_catalog')],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def product_keyboard(product_id: int, scope: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Купить', callback_data=f'buy:{product_id}:{scope}')],
            [InlineKeyboardButton(text='⬅️ Назад', callback_data=f'back_scope:{scope}')],
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


def skip_photo_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text='Пропустить фото', callback_data='admin_skip_photo')]]
    )


def categories_pick_keyboard(categories: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=category['name'], callback_data=f"admin_pick_category:{category['id']}")]
        for category in categories
    ]
    rows.append([InlineKeyboardButton(text='Без категории', callback_data='admin_pick_category:none')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def order_status_keyboard(order_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f'order_status_set:{order_id}:{status}')]
        for status, label in ORDER_STATUS_LABELS.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
