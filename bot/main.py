from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from bot.config import get_settings
from bot.database import init_db
from bot.handlers import admin, user


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    await init_db()

    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(admin.router)
    dp.include_router(user.router)

    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
