from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher

from bot.config import get_settings
from bot.database import init_db
from bot.health import BotHealthMonitor, set_monitor
from bot.handlers import admin, analytics, broadcast, delivery, payment, reviews, sheets, user
from bot.handlers.broadcast import process_due_broadcasts
from bot.handlers.sheets import process_auto_sync_configs
from bot.webhook import start_webhook_server


async def broadcast_scheduler_loop(bot: Bot) -> None:
    while True:
        await process_due_broadcasts(bot)
        await asyncio.sleep(60)


async def sheets_scheduler_loop(bot: Bot) -> None:
    while True:
        await process_auto_sync_configs(bot)
        await asyncio.sleep(24 * 60 * 60)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    await init_db()

    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(admin.router)
    dp.include_router(analytics.router)
    dp.include_router(broadcast.router)
    dp.include_router(delivery.router)
    dp.include_router(payment.router)
    dp.include_router(reviews.router)
    dp.include_router(sheets.router)
    dp.include_router(user.router)

    monitor = BotHealthMonitor(
        bot=bot,
        db_path=settings.DB_PATH,
        data_dir='data',
        admin_ids=settings.ADMIN_IDS,
    )
    set_monitor(monitor)
    health_task = asyncio.create_task(monitor.run())
    broadcast_task = asyncio.create_task(broadcast_scheduler_loop(bot))
    sheets_task = asyncio.create_task(sheets_scheduler_loop(bot))
    start_webhook_server(bot=bot, loop=asyncio.get_running_loop())
    try:
        await dp.start_polling(bot)
    finally:
        monitor.stop()
        health_task.cancel()
        broadcast_task.cancel()
        sheets_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await health_task
        with contextlib.suppress(asyncio.CancelledError):
            await broadcast_task
        with contextlib.suppress(asyncio.CancelledError):
            await sheets_task


if __name__ == '__main__':
    asyncio.run(main())
