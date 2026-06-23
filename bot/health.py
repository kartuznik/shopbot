from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
from aiogram import Bot

from bot.database import db_integrity_check, get_active_users_count


class BotHealthMonitor:
    def __init__(
        self,
        bot: Bot,
        db_path: str,
        data_dir: str,
        admin_ids: list[int],
        log_path: str = 'bot_health.log',
    ) -> None:
        self.bot = bot
        self.db_path = self._resolve_path(db_path)
        self.data_dir = self._resolve_path(data_dir)
        self.admin_ids = admin_ids
        self.started_at = datetime.now(tz=UTC)

        self.bot_online = False
        self.last_bot_check: datetime | None = None
        self.last_db_check: datetime | None = None
        self.last_resources_check: datetime | None = None
        self.last_db_ok = False
        self.last_db_error: str | None = None
        self.free_disk_bytes = 0
        self.active_users = 0
        self.last_critical_error: str | None = None
        self.last_critical_at: datetime | None = None
        self.last_heal_at: datetime | None = None

        self._stop_event = asyncio.Event()
        self._is_healing = False
        self._degraded = False
        self._logger = self._build_logger(log_path)

    @staticmethod
    def _resolve_path(path: str) -> str:
        value = Path(path)
        if not value.is_absolute():
            value = Path('/opt/bots/shopbot') / value
        return str(value)

    def _build_logger(self, log_path: str) -> logging.Logger:
        logger = logging.getLogger('shopbot.health')
        logger.setLevel(logging.INFO)

        absolute_path = Path(log_path)
        if not absolute_path.is_absolute():
            absolute_path = Path('/opt/bots/shopbot') / log_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)

        if not logger.handlers:
            file_handler = logging.FileHandler(absolute_path, encoding='utf-8')
            file_handler.setFormatter(
                logging.Formatter(
                    '[%(asctime)s] [%(levelname)s] %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                )
            )
            logger.addHandler(file_handler)
        return logger

    def _log(self, level: int, component: str, message: str) -> None:
        self._logger.log(level, f'[{component}] {message}')

    async def notify_admins(self, text: str) -> None:
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(chat_id=admin_id, text=text)
            except Exception as error:  # pragma: no cover
                self._log(logging.ERROR, 'NOTIFY', f'Ошибка отправки админу {admin_id}: {error}')

    async def check_bot_alive(self) -> bool:
        self.last_bot_check = datetime.now(tz=UTC)
        try:
            await self.bot.get_me()
            self.bot_online = True
            self._log(logging.INFO, 'BOT', 'Проверка get_me успешна')
            return True
        except Exception as error:
            self.bot_online = False
            self.last_critical_error = f'bot_unreachable: {error}'
            self.last_critical_at = datetime.now(tz=UTC)
            self._log(logging.ERROR, 'BOT', f'Бот не отвечает: {error}')
            return False

    def _check_db_sync(self) -> tuple[bool, str | None]:
        integrity = db_integrity_check(self.db_path)
        if integrity != 'ok':
            return False, f'integrity_check={integrity}'
        return True, None

    async def check_database(self) -> bool:
        self.last_db_check = datetime.now(tz=UTC)
        try:
            ok, error_text = await asyncio.to_thread(self._check_db_sync)
            self.last_db_ok = ok
            self.last_db_error = error_text
            self.active_users = await get_active_users_count()
            if ok:
                self._log(logging.INFO, 'DB', 'SQLite integrity_check = ok')
                return True
            self.last_critical_error = f'database_integrity: {error_text}'
            self.last_critical_at = datetime.now(tz=UTC)
            self._log(logging.ERROR, 'DB', f'Ошибка целостности БД: {error_text}')
            return False
        except Exception as error:
            self.last_db_ok = False
            self.last_db_error = str(error)
            self.last_critical_error = f'database_error: {error}'
            self.last_critical_at = datetime.now(tz=UTC)
            self._log(logging.ERROR, 'DB', f'Ошибка проверки БД: {error}')
            return False

    async def check_resources(self) -> bool:
        self.last_resources_check = datetime.now(tz=UTC)
        usage = shutil.disk_usage(self.data_dir)
        self.free_disk_bytes = usage.free
        min_free_bytes = 100 * 1024 * 1024
        if usage.free < min_free_bytes:
            self.last_critical_error = 'low_disk_space'
            self.last_critical_at = datetime.now(tz=UTC)
            self._log(
                logging.ERROR,
                'RESOURCES',
                f'Свободного места меньше 100MB: {usage.free // (1024 * 1024)}MB',
            )
            return False
        self._log(
            logging.INFO,
            'RESOURCES',
            f'Свободно {usage.free // (1024 * 1024)}MB в {self.data_dir}',
        )
        return True

    async def heal(self, reason: str) -> bool:
        if self._is_healing:
            return False
        self._is_healing = True
        self.last_heal_at = datetime.now(tz=UTC)
        self.last_critical_error = reason
        self.last_critical_at = datetime.now(tz=UTC)
        self._log(logging.CRITICAL, 'HEAL', f'Критическая ошибка, запускаю heal: {reason}')
        await self.notify_admins(f'⚠️ Критическая ошибка ShopBot: {reason}. Запускаю аварийный heal.')

        db: aiosqlite.Connection | None = None
        try:
            db = await aiosqlite.connect(self.db_path)
            await db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            await db.commit()
            await db.close()
            db = None
            self._log(logging.WARNING, 'HEAL', 'WAL checkpoint и commit выполнены')
        except Exception as error:
            self._log(logging.ERROR, 'HEAL', f'Ошибка checkpoint/commit: {error}')
        finally:
            if db is not None:
                try:
                    await db.close()
                except Exception as error:
                    self._log(logging.ERROR, 'HEAL', f'Ошибка закрытия БД: {error}')

        await asyncio.sleep(2)
        backup_path = f"{self.db_path}.pre_crash_{datetime.now(tz=UTC).strftime('%Y%m%d%H%M%S')}"
        try:
            await asyncio.to_thread(shutil.copy2, self.db_path, backup_path)
            self._log(logging.WARNING, 'HEAL', f'Создан backup перед перезапуском: {backup_path}')
        except Exception as error:
            self._log(logging.ERROR, 'HEAL', f'Не удалось создать backup: {error}')

        self._is_healing = False
        sys.exit(1)

    async def run(self) -> None:
        self._log(logging.INFO, 'MONITOR', 'Фоновый монитор самодиагностики запущен')
        last_bot = 0.0
        last_db = 0.0
        last_resources = 0.0
        while not self._stop_event.is_set():
            now = asyncio.get_running_loop().time()
            is_ok = True
            critical_reason: str | None = None

            if now - last_bot >= 30:
                last_bot = now
                if not await self.check_bot_alive():
                    is_ok = False
                    critical_reason = self.last_critical_error or 'bot_check_failed'

            if now - last_db >= 60:
                last_db = now
                if not await self.check_database():
                    is_ok = False
                    critical_reason = self.last_critical_error or 'db_check_failed'

            if now - last_resources >= 300:
                last_resources = now
                if not await self.check_resources():
                    is_ok = False
                    critical_reason = self.last_critical_error or 'resource_check_failed'

            if critical_reason is not None:
                self._degraded = True
                await self.heal(critical_reason)
            elif is_ok and self._degraded:
                self._degraded = False
                self._log(logging.INFO, 'MONITOR', 'Система восстановлена после деградации')
                await self.notify_admins('✅ ShopBot восстановлен и снова работает стабильно.')

            await asyncio.sleep(1)

    def stop(self) -> None:
        self._stop_event.set()

    def get_status(self) -> dict[str, Any]:
        return {
            'bot_online': self.bot_online,
            'last_bot_check': self.last_bot_check,
            'last_db_check': self.last_db_check,
            'last_resources_check': self.last_resources_check,
            'last_db_ok': self.last_db_ok,
            'last_db_error': self.last_db_error,
            'free_disk_bytes': self.free_disk_bytes,
            'active_users': self.active_users,
            'uptime_seconds': int((datetime.now(tz=UTC) - self.started_at).total_seconds()),
            'last_critical_error': self.last_critical_error,
            'last_critical_at': self.last_critical_at,
            'last_heal_at': self.last_heal_at,
        }


_monitor: BotHealthMonitor | None = None


def set_monitor(monitor: BotHealthMonitor) -> None:
    global _monitor
    _monitor = monitor


def get_monitor() -> BotHealthMonitor | None:
    return _monitor
