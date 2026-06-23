from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import gspread
from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from oauth2client.service_account import ServiceAccountCredentials

from bot.config import get_settings
from bot.database import (
    add_sheets_config,
    delete_sheets_config,
    get_orders_rows_for_sheets,
    get_products_rows_for_sheets,
    get_sales_rows_for_sheets,
    get_sheets_config,
    get_users_rows_for_sheets,
    list_auto_sync_sheets_configs,
    list_sheets_configs,
    touch_sheets_sync,
)
from bot.states import SheetsStates

logger = logging.getLogger('shopbot.sheets')

router = Router()

SCOPE = [
    'https://spreadsheets.google.com/feeds',
    'https://www.googleapis.com/auth/drive',
]


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in set(get_settings().ADMIN_IDS)


def _credentials_path() -> Path:
    return Path('/opt/bots/shopbot/credentials.json')


def _build_client() -> gspread.Client:
    credentials_file = _credentials_path()
    if not credentials_file.exists():
        raise RuntimeError('credentials.json не найден')
    creds = ServiceAccountCredentials.from_json_keyfile_name(str(credentials_file), SCOPE)
    return gspread.authorize(creds)


async def _report_rows(report_type: str) -> list[list[Any]]:
    if report_type == 'sales':
        return await get_sales_rows_for_sheets()
    if report_type == 'orders':
        return await get_orders_rows_for_sheets()
    if report_type == 'users':
        return await get_users_rows_for_sheets()
    return await get_products_rows_for_sheets()


def _sync_sheet_sync(config: dict[str, Any], rows: list[list[Any]]) -> None:
    client = _build_client()
    spreadsheet = client.open_by_key(str(config['spreadsheet_id']))
    try:
        worksheet = spreadsheet.worksheet(str(config['sheet_name']))
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=str(config['sheet_name']), rows=1000, cols=30)
    worksheet.clear()
    if rows:
        worksheet.update('A1', rows)


async def sync_sheet_config(config_id: int) -> tuple[bool, str]:
    config = await get_sheets_config(config_id)
    if not config:
        return False, 'Конфиг не найден'
    rows = await _report_rows(str(config['report_type']))
    try:
        await asyncio.to_thread(_sync_sheet_sync, config, rows)
    except Exception as error:
        logger.error('Sheets sync failed for %s: %s', config_id, error)
        return False, str(error)
    await touch_sheets_sync(config_id)
    return True, 'Синхронизация завершена'


async def process_auto_sync_configs(bot) -> None:
    configs = await list_auto_sync_sheets_configs()
    for config in configs:
        ok, msg = await sync_sheet_config(int(config['id']))
        if not ok:
            logger.error('Auto sync failed for config %s: %s', config['id'], msg)
            continue
        for admin_id in get_settings().ADMIN_IDS:
            try:
                await bot.send_message(admin_id, f"📊 Google Sheets sync #{config['id']} выполнен")
            except Exception:
                continue


@router.message(Command('sheets_setup'))
async def cmd_sheets_setup(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    await state.clear()
    await state.set_state(SheetsStates.spreadsheet_id)
    await message.answer('Введите spreadsheet_id (из URL Google Sheets):')


@router.message(SheetsStates.spreadsheet_id)
async def sheets_spreadsheet_id(message: Message, state: FSMContext) -> None:
    spreadsheet_id = (message.text or '').strip()
    if not spreadsheet_id:
        await message.answer('spreadsheet_id не может быть пустым.')
        return
    await state.update_data(spreadsheet_id=spreadsheet_id)
    await state.set_state(SheetsStates.sheet_name)
    await message.answer('Введите имя листа (sheet_name):')


@router.message(SheetsStates.sheet_name)
async def sheets_sheet_name(message: Message, state: FSMContext) -> None:
    sheet_name = (message.text or '').strip()
    if not sheet_name:
        await message.answer('sheet_name не может быть пустым.')
        return
    await state.update_data(sheet_name=sheet_name)
    await state.set_state(SheetsStates.report_type)
    await message.answer('Введите тип отчёта: sales / orders / users / products')


@router.message(SheetsStates.report_type)
async def sheets_report_type(message: Message, state: FSMContext) -> None:
    report_type = (message.text or '').strip().lower()
    if report_type not in {'sales', 'orders', 'users', 'products'}:
        await message.answer('Допустимые значения: sales, orders, users, products')
        return
    await state.update_data(report_type=report_type)
    await state.set_state(SheetsStates.auto_sync)
    await message.answer('Включить авто-синхронизацию? (да/нет)')


@router.message(SheetsStates.auto_sync)
async def sheets_auto_sync(message: Message, state: FSMContext) -> None:
    raw = (message.text or '').strip().lower()
    if raw not in {'да', 'нет', 'yes', 'no', 'y', 'n'}:
        await message.answer('Введите "да" или "нет".')
        return
    auto_sync = raw in {'да', 'yes', 'y'}
    data = await state.get_data()
    config_id = await add_sheets_config(
        spreadsheet_id=str(data.get('spreadsheet_id', '')),
        sheet_name=str(data.get('sheet_name', '')),
        report_type=str(data.get('report_type', 'sales')),
        auto_sync=auto_sync,
        created_by=message.from_user.id if message.from_user else 0,
    )
    await state.clear()
    await message.answer(f'✅ Настройка Google Sheets сохранена, ID: {config_id}')


@router.message(Command('sheets_list'))
async def cmd_sheets_list(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    configs = await list_sheets_configs()
    if not configs:
        await message.answer('Настроек Google Sheets пока нет.')
        return
    lines = ['Google Sheets конфиги:']
    for item in configs:
        lines.append(
            f"#{item['id']} | {item['report_type']} | sheet={item['sheet_name']} | "
            f"auto={item['auto_sync']} | last={item['last_sync_at'] or '-'}"
        )
    await message.answer('\n'.join(lines))


@router.message(Command('sheets_sync'))
async def cmd_sheets_sync(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer('Использование: /sheets_sync <id>')
        return
    config_id = int(parts[1])
    ok, msg = await sync_sheet_config(config_id)
    await message.answer(('✅ ' if ok else '❌ ') + msg)


@router.message(Command('sheets_delete'))
async def cmd_sheets_delete(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else None):
        await message.answer('Недостаточно прав.')
        return
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer('Использование: /sheets_delete <id>')
        return
    ok = await delete_sheets_config(int(parts[1]))
    await message.answer('✅ Настройка удалена' if ok else '❌ Настройка не найдена')
