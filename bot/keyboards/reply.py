from __future__ import annotations

import re

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from bot.demo_catalog import format_price

MENU = '📋 Меню'
CART = '🛒 Корзина'
PAY = '💳 Оплатить'
CLEAR_CART = '🗑 Очистить'
ABOUT = 'ℹ️ О пекарне'
HELP = '❓ Помощь'

# Нажатие нижней кнопки приходит текстом её подписи. Клиент Telegram держит последнюю
# присланную клавиатуру, поэтому шаблоны принимают и подписи прежних версий меню.
_PRICE = r'\d[\d ]*(?:\.\d{2})?'
CART_BUTTON_PATTERN = re.compile(rf'^🛒 Корзина(?: \d+ шт на {_PRICE} ₽)?$')
PAY_BUTTON_PATTERN = re.compile(rf'^💳 Оплатить {_PRICE} ₽$')
CLEAR_CART_TEXTS = frozenset({CLEAR_CART, '🗑 Очистить корзину'})


def cart_button_text(cart_count: int = 0, cart_total: float = 0) -> str:
    if cart_count <= 0:
        return CART
    return f'{PAY} {format_price(cart_total)} ₽'


def get_main_keyboard(cart_count: int = 0, cart_total: float = 0) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MENU), KeyboardButton(text=cart_button_text(cart_count, cart_total))],
            [KeyboardButton(text=CLEAR_CART), KeyboardButton(text=ABOUT), KeyboardButton(text=HELP)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
    )
