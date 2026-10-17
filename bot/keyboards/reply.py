from __future__ import annotations

import re

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from bot.demo_catalog import format_price

MENU = '📋 Меню'
CART = '🛒 Корзина'
CLEAR_CART = '🗑 Очистить корзину'
ABOUT = 'ℹ️ О пекарне'
HELP = '❓ Помощь'

# Нажатие нижней кнопки приходит текстом её подписи, а подпись корзины несёт счётчик.
# Шаблон принимает и подпись из старой клавиатуры с устаревшим счётчиком.
CART_BUTTON_PATTERN = re.compile(r'^🛒 Корзина(?: \d+ шт на \d[\d ]*(?:\.\d{2})? ₽)?$')


def cart_button_text(cart_count: int = 0, cart_total: float = 0) -> str:
    if cart_count <= 0:
        return CART
    return f'{CART} {cart_count} шт на {format_price(cart_total)} ₽'


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
