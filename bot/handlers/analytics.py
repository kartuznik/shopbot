from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.config import get_settings
from bot.database import get_sales_by_period, get_sales_overview, get_users_stats

router = Router()


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in set(get_settings().ADMIN_IDS)


@router.callback_query(F.data == 'admin_analytics')
@router.message(Command('stats'))
async def cmd_stats(event: Message | CallbackQuery) -> None:
    user_id = event.from_user.id if event.from_user else None
    if not _is_admin(user_id):
        if isinstance(event, CallbackQuery):
            await event.answer('Недостаточно прав', show_alert=True)
        else:
            await event.answer('Недостаточно прав.')
        return
    data = await get_sales_overview()
    statuses = data['statuses']
    top_products = data['top_products'] or []
    top_categories = data['top_categories'] or []

    lines = [
        '📊 Общая статистика',
        '',
        f"Пользователи: {data['users_total']}",
        f"Заказы: {data['orders_total']}",
        f"Сумма продаж: {data['sales_total']:.2f}",
        f"Средний чек: {data['avg_check']:.2f}",
        '',
        'Топ-5 товаров:',
    ]
    if top_products:
        for item in top_products:
            lines.append(f"- {item['name']}: {int(item['qty'])} шт")
    else:
        lines.append('- нет данных')
    lines.append('')
    lines.append('Топ-5 категорий:')
    if top_categories:
        for item in top_categories:
            lines.append(f"- {item['name']}: {float(item['total']):.2f}")
    else:
        lines.append('- нет данных')
    lines.extend(
        [
            '',
            'Заказы по статусам:',
            f"- new: {statuses.get('new', 0)}",
            f"- confirmed: {statuses.get('confirmed', 0)}",
            f"- paid: {statuses.get('paid', 0)}",
            f"- shipped: {statuses.get('shipped', 0)}",
            f"- delivered: {statuses.get('delivered', 0)}",
            f"- cancelled: {statuses.get('cancelled', 0)}",
            '',
            'Продажи:',
            f"- 7 дней: {data['last_7']['amount']:.2f} ({data['last_7']['count']} заказов)",
            f"- 30 дней: {data['last_30']['amount']:.2f} ({data['last_30']['count']} заказов)",
        ]
    )
    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer('\n'.join(lines))
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.message(Command('stats_sales'))
async def cmd_stats_sales(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    parts = (message.text or '').split(maxsplit=1)
    period = parts[1].strip().lower() if len(parts) > 1 else ''
    if period not in {'day', 'week', 'month', 'year'}:
        await message.answer('Использование: /stats_sales <day|week|month|year>')
        return
    data = await get_sales_by_period(period)
    lines = [
        f'📈 Продажи за период: {period}',
        f"Сумма продаж: {data['sales_total']:.2f}",
        f"Количество заказов: {data['orders_count']}",
        f"Средний чек: {data['avg_check']:.2f}",
        '',
        'Топ товаров:',
    ]
    if data['top_products']:
        for item in data['top_products']:
            lines.append(f"- {item['name']}: {int(item['qty'])} шт")
    else:
        lines.append('- нет данных')
    await message.answer('\n'.join(lines))


@router.message(Command('stats_users'))
async def cmd_stats_users(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    data = await get_users_stats()
    text = (
        '👥 Статистика пользователей\n\n'
        f"Новые за день: {data['new_day']}\n"
        f"Новые за неделю: {data['new_week']}\n"
        f"Новые за месяц: {data['new_month']}\n\n"
        f"Активные (с заказами) за день: {data['active_day']}\n"
        f"Активные (с заказами) за неделю: {data['active_week']}\n"
        f"Активные (с заказами) за месяц: {data['active_month']}\n\n"
        f"Конверсия: {data['conversion']:.2f}%"
    )
    await message.answer(text)
