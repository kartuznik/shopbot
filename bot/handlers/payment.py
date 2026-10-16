from __future__ import annotations

import asyncio
import base64
import json
import uuid
from typing import Any
from urllib import request

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import get_settings
from bot.database import (
    get_order_basic,
    get_payment_by_order,
    list_payments,
    save_payment,
)

router = Router()


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in set(get_settings().ADMIN_IDS)


def payment_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text='💳 Оплатить', callback_data=f'pay_order:{order_id}')]]
    )


def _create_payment_sync(order_id: int, amount: float, description: str) -> tuple[str, str]:
    settings = get_settings()
    shop_id = settings.YUKASSA_SHOP_ID.strip()
    secret_key = settings.YUKASSA_SECRET_KEY.strip()
    if not shop_id or not secret_key:
        raise RuntimeError('YUKASSA_SHOP_ID или YUKASSA_SECRET_KEY не заполнены')

    auth_raw = f'{shop_id}:{secret_key}'.encode('utf-8')
    auth_b64 = base64.b64encode(auth_raw).decode('utf-8')
    payload = {
        'amount': {'value': f'{amount:.2f}', 'currency': 'RUB'},
        'capture': True,
        'confirmation': {'type': 'redirect', 'return_url': 'https://t.me'},
        'description': description,
        'metadata': {'order_id': str(order_id)},
    }
    req = request.Request(
        'https://api.yookassa.ru/v3/payments',
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Basic {auth_b64}',
            'Idempotence-Key': str(uuid.uuid4()),
        },
        method='POST',
    )
    with request.urlopen(req, timeout=20) as response:
        body = json.loads(response.read().decode('utf-8'))
    payment_id = str(body.get('id') or '')
    payment_url = str((body.get('confirmation') or {}).get('confirmation_url') or '')
    if not payment_id or not payment_url:
        raise RuntimeError('YooKassa вернула неполный ответ')
    return payment_url, payment_id


async def create_yukassa_payment(order_id: int, amount: float, description: str) -> tuple[str, str]:
    payment_url, payment_id = await asyncio.to_thread(_create_payment_sync, order_id, amount, description)
    await save_payment(
        order_id=order_id,
        amount=amount,
        payment_url=payment_url,
        payment_id=payment_id,
        status='pending',
    )
    return payment_url, payment_id


@router.callback_query(F.data.startswith('pay_order:'))
async def callback_pay_order(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    order_id = int((callback.data or '').split(':')[1])
    order = await get_order_basic(order_id)
    if not order:
        await callback.answer('Заказ не найден', show_alert=True)
        return
    if order['user_id'] != callback.from_user.id and not _is_admin(callback.from_user.id):
        await callback.answer('Нет доступа к заказу', show_alert=True)
        return
    if order['status'] == 'paid':
        await callback.answer('Заказ уже оплачен')
        return

    existing = await get_payment_by_order(order_id)
    if existing and existing.get('payment_url') and existing.get('status') == 'pending':
        await callback.answer()
        await callback.message.answer(
            f"Оплатите заказ #{order_id}: {existing['payment_url']}"
        )
        return

    try:
        payment_url, _ = await create_yukassa_payment(
            order_id=order_id,
            amount=float(order['total_price']),
            description=f"Оплата заказа #{order_id} ({order['product_name']})",
        )
    except Exception as error:
        await callback.answer('Ошибка создания платежа', show_alert=True)
        await callback.message.answer(f'❌ Не удалось создать платеж: {error}')
        return
    await callback.answer()
    await callback.message.answer(f'Оплатите заказ #{order_id}: {payment_url}')


@router.message(Command('payments'))
async def cmd_payments(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    payments = await list_payments()
    if not payments:
        await message.answer('Платежей пока нет.')
        return
    lines = ['💳 Платежи:']
    for payment in payments[:50]:
        lines.append(
            f"#{payment['id']} | order #{payment['order_id']} | {float(payment['amount']):.2f} "
            f"{payment['currency']} | {payment['status']}"
        )
    await message.answer('\n'.join(lines))


@router.message(Command('payment_details'))
async def cmd_payment_details(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer('Использование: /payment_details <order_id>')
        return
    payment = await get_payment_by_order(int(parts[1]))
    if not payment:
        await message.answer('Платеж не найден.')
        return
    text = (
        f"Платеж #{payment['id']}\n"
        f"Заказ: #{payment['order_id']}\n"
        f"Сумма: {float(payment['amount']):.2f} {payment['currency']}\n"
        f"Статус: {payment['status']}\n"
        f"YooKassa payment_id: {payment['payment_id'] or '-'}\n"
        f"URL: {payment['payment_url'] or '-'}\n"
        f"Создан: {payment['created_at']}\n"
        f"Оплачен: {payment['paid_at'] or '-'}"
    )
    await message.answer(text)
