from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import get_settings
from bot.database import (
    create_broadcast,
    delete_broadcast,
    get_all_user_ids,
    get_broadcast,
    get_broadcast_stats,
    get_due_scheduled_broadcasts,
    list_broadcasts,
    update_broadcast_status,
    upsert_broadcast_recipient,
)
from bot.states import BroadcastStates

logger = logging.getLogger('shopbot.broadcast')

router = Router()


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in set(get_settings().ADMIN_IDS)


def _message_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Текст', callback_data='broadcast_type:text')],
            [InlineKeyboardButton(text='Фото', callback_data='broadcast_type:photo')],
            [InlineKeyboardButton(text='Документ', callback_data='broadcast_type:document')],
        ]
    )


def _schedule_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Сейчас', callback_data='broadcast_schedule:now')],
            [InlineKeyboardButton(text='Отложенная', callback_data='broadcast_schedule:later')],
        ]
    )


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='✅ Подтвердить', callback_data='broadcast_confirm:send')],
            [InlineKeyboardButton(text='❌ Отмена', callback_data='broadcast_confirm:cancel')],
        ]
    )


async def send_broadcast_by_id(bot, broadcast_id: int) -> dict[str, int]:
    broadcast = await get_broadcast(broadcast_id)
    if not broadcast:
        return {'total': 0, 'sent': 0, 'failed': 0}

    users = await get_all_user_ids()
    sent = 0
    failed = 0
    for user_id in users:
        try:
            if broadcast['message_type'] == 'photo':
                await bot.send_photo(chat_id=user_id, photo=broadcast['content'], caption=broadcast['title'])
            elif broadcast['message_type'] == 'document':
                await bot.send_document(chat_id=user_id, document=broadcast['content'], caption=broadcast['title'])
            else:
                await bot.send_message(chat_id=user_id, text=f"{broadcast['title']}\n\n{broadcast['content']}")
            await upsert_broadcast_recipient(broadcast_id, user_id, status='sent')
            sent += 1
        except Exception as error:  # pragma: no cover
            await upsert_broadcast_recipient(broadcast_id, user_id, status='failed', error=str(error))
            failed += 1
            logger.error('Broadcast %s failed for %s: %s', broadcast_id, user_id, error)
        await asyncio.sleep(0.03)

    await update_broadcast_status(broadcast_id, status='sent', sent_now=True)
    for admin_id in get_settings().ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"📢 Рассылка #{broadcast_id} завершена.\nУспешно: {sent}\nОшибок: {failed}\nВсего: {len(users)}",
            )
        except Exception:
            continue
    return {'total': len(users), 'sent': sent, 'failed': failed}


async def process_due_broadcasts(bot) -> None:
    due = await get_due_scheduled_broadcasts()
    for item in due:
        await send_broadcast_by_id(bot, int(item['id']))


@router.callback_query(F.data == 'admin_broadcast')
@router.message(Command('broadcast'))
async def cmd_broadcast(event: Message | CallbackQuery, state: FSMContext) -> None:
    user_id = event.from_user.id if event.from_user else None
    if not _is_admin(user_id):
        if isinstance(event, CallbackQuery):
            await event.answer('Недостаточно прав', show_alert=True)
        else:
            await event.answer('Недостаточно прав.')
        return
    await state.clear()
    await state.set_state(BroadcastStates.title)
    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer('Введите название рассылки:')
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.message(BroadcastStates.title)
async def broadcast_title(message: Message, state: FSMContext) -> None:
    title = (message.text or '').strip()
    if not title:
        await message.answer('Название не может быть пустым.')
        return
    await state.update_data(title=title)
    await state.set_state(BroadcastStates.message_type)
    await message.answer('Выберите тип сообщения:', reply_markup=_message_type_keyboard())


@router.callback_query(F.data.startswith('broadcast_type:'), BroadcastStates.message_type)
async def broadcast_message_type(callback: CallbackQuery, state: FSMContext) -> None:
    message_type = (callback.data or '').split(':')[1]
    await state.update_data(message_type=message_type)
    await state.set_state(BroadcastStates.content)
    await callback.answer()
    if message_type == 'text':
        await callback.message.answer('Введите текст рассылки:')
    elif message_type == 'photo':
        await callback.message.answer('Отправьте фото (будет использоваться file_id).')
    else:
        await callback.message.answer('Отправьте документ (будет использоваться file_id).')


@router.message(BroadcastStates.content)
async def broadcast_content(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    message_type = data.get('message_type')
    content = ''
    if message_type == 'photo':
        if not message.photo:
            await message.answer('Нужно отправить фото.')
            return
        content = message.photo[-1].file_id
    elif message_type == 'document':
        if not message.document:
            await message.answer('Нужно отправить документ.')
            return
        content = message.document.file_id
    else:
        content = (message.text or '').strip()
        if not content:
            await message.answer('Текст не может быть пустым.')
            return

    await state.update_data(content=content)
    await state.set_state(BroadcastStates.schedule_type)
    await message.answer('Когда отправить рассылку?', reply_markup=_schedule_keyboard())


@router.callback_query(F.data.startswith('broadcast_schedule:'), BroadcastStates.schedule_type)
async def broadcast_schedule(callback: CallbackQuery, state: FSMContext) -> None:
    schedule_type = (callback.data or '').split(':')[1]
    await state.update_data(schedule_type=schedule_type)
    await callback.answer()
    if schedule_type == 'later':
        await state.set_state(BroadcastStates.scheduled_at)
        await callback.message.answer('Введите дату и время в формате YYYY-MM-DD HH:MM')
        return
    await state.update_data(scheduled_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    await state.set_state(BroadcastStates.confirm)
    await callback.message.answer('Подтвердите отправку:', reply_markup=_confirm_keyboard())


@router.message(BroadcastStates.scheduled_at)
async def broadcast_scheduled_at(message: Message, state: FSMContext) -> None:
    raw = (message.text or '').strip()
    try:
        value = datetime.strptime(raw, '%Y-%m-%d %H:%M')
    except ValueError:
        await message.answer('Неверный формат. Используйте YYYY-MM-DD HH:MM')
        return
    await state.update_data(scheduled_at=value.strftime('%Y-%m-%d %H:%M:%S'))
    await state.set_state(BroadcastStates.confirm)
    await message.answer('Подтвердите создание отложенной рассылки:', reply_markup=_confirm_keyboard())


@router.callback_query(F.data.startswith('broadcast_confirm:'), BroadcastStates.confirm)
async def broadcast_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    action = (callback.data or '').split(':')[1]
    if action == 'cancel':
        await state.clear()
        await callback.answer('Отменено')
        await callback.message.answer('Создание рассылки отменено.')
        return

    data = await state.get_data()
    await state.clear()
    schedule_type = data.get('schedule_type')
    status = 'scheduled'
    broadcast_id = await create_broadcast(
        title=str(data.get('title', '')),
        message_type=str(data.get('message_type', 'text')),
        content=str(data.get('content', '')),
        created_by=callback.from_user.id if callback.from_user else 0,
        status=status,
        scheduled_at=str(data.get('scheduled_at')),
    )
    await callback.answer('Сохранено')
    if schedule_type == 'now':
        await callback.message.answer(f'Рассылка #{broadcast_id} отправляется...')
        await send_broadcast_by_id(callback.bot, broadcast_id)
    else:
        await callback.message.answer(
            f"Отложенная рассылка #{broadcast_id} запланирована на {data.get('scheduled_at')}"
        )


@router.message(Command('broadcasts'))
async def cmd_broadcasts(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    broadcasts = await list_broadcasts()
    if not broadcasts:
        await message.answer('Рассылок пока нет.')
        return
    lines = ['📢 Рассылки:']
    for item in broadcasts[:50]:
        lines.append(
            f"#{item['id']} | {item['title']} | {item['message_type']} | {item['status']} | "
            f"scheduled: {item['scheduled_at'] or '-'} | sent: {item['sent_at'] or '-'}"
        )
    await message.answer('\n'.join(lines))


@router.message(Command('broadcast_details'))
async def cmd_broadcast_details(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer('Использование: /broadcast_details <id>')
        return
    broadcast_id = int(parts[1])
    broadcast = await get_broadcast(broadcast_id)
    if not broadcast:
        await message.answer('Рассылка не найдена.')
        return
    stats = await get_broadcast_stats(broadcast_id)
    await message.answer(
        '\n'.join(
            [
                f"Рассылка #{broadcast['id']}: {broadcast['title']}",
                f"Тип: {broadcast['message_type']}",
                f"Статус: {broadcast['status']}",
                f"Запланировано: {broadcast['scheduled_at'] or '-'}",
                f"Отправлено: {broadcast['sent_at'] or '-'}",
                f"Всего получателей: {stats['total']}",
                f"Успешно: {stats['sent']}",
                f"Ошибок: {stats['failed']}",
                f"Ожидает: {stats['pending']}",
            ]
        )
    )


@router.message(Command('broadcast_cancel'))
async def cmd_broadcast_cancel(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer('Использование: /broadcast_cancel <id>')
        return
    broadcast_id = int(parts[1])
    broadcast = await get_broadcast(broadcast_id)
    if not broadcast:
        await message.answer('Рассылка не найдена.')
        return
    if broadcast['status'] != 'scheduled':
        await message.answer('Отменить можно только запланированную рассылку.')
        return
    ok = await update_broadcast_status(broadcast_id, 'cancelled')
    await message.answer('✅ Рассылка отменена' if ok else '❌ Не удалось отменить')


@router.message(Command('broadcast_delete'))
async def cmd_broadcast_delete(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer('Использование: /broadcast_delete <id>')
        return
    ok = await delete_broadcast(int(parts[1]))
    await message.answer('✅ Рассылка удалена' if ok else '❌ Рассылка не найдена')
