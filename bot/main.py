from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher

from bot.config import get_settings
from bot.database import init_db
from bot.health import BotHealthMonitor, set_monitor
from bot.handlers import admin, user


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    await init_db()

    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(admin.router)
    dp.include_router(user.router)

    monitor = BotHealthMonitor(
        bot=bot,
        db_path=settings.DB_PATH,
        data_dir='data',
        admin_ids=settings.ADMIN_IDS,
    )
    set_monitor(monitor)
    health_task = asyncio.create_task(monitor.run())
    try:
        await dp.start_polling(bot)
    finally:
        monitor.stop()
        health_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await health_task


if __name__ == '__main__':
    asyncio.run(main())
