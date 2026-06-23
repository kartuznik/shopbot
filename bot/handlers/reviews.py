from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import get_settings
from bot.database import (
    can_user_review_order,
    delete_review,
    get_order_for_review,
    get_product_rating_summary,
    get_product_reviews,
    save_review,
)
from bot.keyboards.inline import review_comment_skip_keyboard, review_rating_keyboard
from bot.states import ReviewStates

router = Router()


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in set(get_settings().ADMIN_IDS)


async def send_review_request(bot, order_id: int) -> bool:
    order = await get_order_for_review(order_id)
    if not order or order['status'] != 'delivered':
        return False
    text = (
        f"Как вам {order['product_name']}? Оцените от 1 до 5 и оставьте отзыв.\n"
        f"Заказ #{order_id}"
    )
    await bot.send_message(
        chat_id=order['user_id'],
        text=text,
        reply_markup=review_rating_keyboard(order_id),
    )
    return True


@router.callback_query(F.data.startswith('review_rate:'))
async def callback_review_rate(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user:
        return
    _, raw_order_id, raw_rating = (callback.data or '').split(':')
    order_id = int(raw_order_id)
    rating = int(raw_rating)
    order = await can_user_review_order(callback.from_user.id, order_id)
    if not order:
        await callback.answer('Оставить отзыв можно только по доставленному заказу', show_alert=True)
        return
    await state.update_data(order_id=order_id, product_id=order['product_id'], rating=rating)
    await state.set_state(ReviewStates.comment)
    await callback.answer('Оценка принята')
    await callback.message.answer(
        'Напишите текстовый отзыв или нажмите "Пропустить комментарий".',
        reply_markup=review_comment_skip_keyboard(),
    )


@router.callback_query(F.data.startswith('review_skip:'))
async def callback_review_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer('Без отзыва')
    await callback.message.answer('Отзыв пропущен.')


@router.callback_query(F.data == 'review_comment_skip', ReviewStates.comment)
async def callback_review_comment_skip(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user:
        return
    data = await state.get_data()
    review_id = await save_review(
        user_id=callback.from_user.id,
        product_id=int(data['product_id']),
        order_id=int(data['order_id']),
        rating=int(data['rating']),
        comment=None,
    )
    await state.clear()
    await callback.answer('Отзыв сохранен')
    await callback.message.answer(f'Спасибо! Отзыв #{review_id} сохранен.')


@router.message(ReviewStates.comment)
async def review_comment(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    data = await state.get_data()
    review_id = await save_review(
        user_id=message.from_user.id,
        product_id=int(data['product_id']),
        order_id=int(data['order_id']),
        rating=int(data['rating']),
        comment=message.text or '',
    )
    await state.clear()
    await message.answer(f'Спасибо! Отзыв #{review_id} сохранен.')


@router.callback_query(F.data.startswith('product_reviews:'))
async def callback_product_reviews(callback: CallbackQuery) -> None:
    product_id = int((callback.data or '').split(':')[1])
    await callback.answer()
    await _send_reviews_text(callback.message, product_id)


@router.message(Command('reviews'))
async def cmd_reviews(message: Message) -> None:
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer('Использование: /reviews <product_id>')
        return
    await _send_reviews_text(message, int(parts[1]))


async def _send_reviews_text(message: Message, product_id: int) -> None:
    summary = await get_product_rating_summary(product_id)
    reviews = await get_product_reviews(product_id, limit=20)
    if summary['reviews_count'] == 0:
        await message.answer('Отзывов по этому товару пока нет.')
        return
    lines = [
        f'⭐ Отзывы по товару #{product_id}',
        f"Средний рейтинг: {summary['avg_rating']:.1f}/5",
        f"Всего отзывов: {summary['reviews_count']}",
        '',
    ]
    for review in reviews:
        lines.append(
            f"#{review['id']} | {'⭐' * int(review['rating'])} | "
            f"@{review['username'] or review['user_id']}\n"
            f"{review['comment'] or '(без комментария)'}"
        )
    await message.answer('\n'.join(lines))


@router.message(Command('product_reviews'))
async def cmd_product_reviews(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer('Использование: /product_reviews <product_id>')
        return
    product_id = int(parts[1])
    summary = await get_product_rating_summary(product_id)
    reviews = await get_product_reviews(product_id, limit=50)
    dist = summary['distribution']
    lines = [
        f'🧾 Админ-отзывы по товару #{product_id}',
        f"Средний рейтинг: {summary['avg_rating']:.2f}/5",
        f"Всего отзывов: {summary['reviews_count']}",
        'Распределение:',
        f"5⭐: {dist.get(5, 0)}",
        f"4⭐: {dist.get(4, 0)}",
        f"3⭐: {dist.get(3, 0)}",
        f"2⭐: {dist.get(2, 0)}",
        f"1⭐: {dist.get(1, 0)}",
        '',
    ]
    for review in reviews:
        lines.append(
            f"ID {review['id']} | order #{review['order_id']} | @"
            f"{review['username'] or review['user_id']} | {review['rating']}⭐\n"
            f"{review['comment'] or '(без комментария)'}"
        )
    await message.answer('\n'.join(lines))


@router.message(Command('delete_review'))
async def cmd_delete_review(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer('Использование: /delete_review <id>')
        return
    ok = await delete_review(int(parts[1]))
    await message.answer('✅ Отзыв удален' if ok else '❌ Отзыв не найден')
