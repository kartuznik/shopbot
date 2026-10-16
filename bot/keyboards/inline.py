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
    return product_keyboard_with_reviews(product_id=product_id, scope=scope, has_reviews=False)


def product_keyboard_with_reviews(product_id: int, scope: str, has_reviews: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text='Купить', callback_data=f'buy:{product_id}:{scope}')]]
    if has_reviews:
        rows.append([InlineKeyboardButton(text='⭐ Отзывы', callback_data=f'product_reviews:{product_id}')])
    rows.append([InlineKeyboardButton(text='⬅️ Назад', callback_data=f'back_scope:{scope}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
            [InlineKeyboardButton(text='📊 Аналитика', callback_data='admin_analytics')],
            [InlineKeyboardButton(text='🚚 Доставка', callback_data='admin_delivery')],
            [InlineKeyboardButton(text='📢 Рассылки', callback_data='admin_broadcast')],
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


def review_rating_keyboard(order_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text='1⭐', callback_data=f'review_rate:{order_id}:1'),
            InlineKeyboardButton(text='2⭐', callback_data=f'review_rate:{order_id}:2'),
            InlineKeyboardButton(text='3⭐', callback_data=f'review_rate:{order_id}:3'),
            InlineKeyboardButton(text='4⭐', callback_data=f'review_rate:{order_id}:4'),
            InlineKeyboardButton(text='5⭐', callback_data=f'review_rate:{order_id}:5'),
        ],
        [InlineKeyboardButton(text='Пропустить', callback_data=f'review_skip:{order_id}')],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def review_comment_skip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text='Пропустить комментарий', callback_data='review_comment_skip')]]
    )
