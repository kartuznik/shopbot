from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
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
        self.last_bot_error: str | None = None
        self.last_bot_error_kind: str | None = None
        self.free_disk_bytes = 0
        self.active_users = 0
        self.last_critical_error: str | None = None
        self.last_critical_at: datetime | None = None
        self.last_heal_at: datetime | None = None
        self.last_restart_alert_at: datetime | None = None

        self.consecutive_bot_errors = 0
        self.max_consecutive_bot_errors = 5
        self.retry_delays = [5, 10, 20, 40]
        self.restart_alert_threshold = 10

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
            if self.consecutive_bot_errors:
                self._log(
                    logging.INFO,
                    'BOT',
                    f'Проверка get_me успешна, сбрасываю счетчик ошибок ({self.consecutive_bot_errors} -> 0)',
                )
            self.consecutive_bot_errors = 0
            self.last_bot_error = None
            self.last_bot_error_kind = None
            self._log(logging.INFO, 'BOT', 'Проверка get_me успешна')
            return True
        except Exception as error:
            self.bot_online = False
            self.last_bot_error = str(error)
            self.last_bot_error_kind = self._classify_bot_error(self.last_bot_error)
            self._log(
                logging.WARNING,
                'BOT',
                f'Бот не отвечает (тип={self.last_bot_error_kind}): {self.last_bot_error}',
            )
            return False

    @staticmethod
    def _is_transient_bot_error(error_text: str) -> bool:
        text = error_text.lower()
        transient_markers = (
            'bad gateway',
            'gateway timeout',
            'request timeout',
            'timeout error',
            'timed out',
            'connection reset',
            'temporarily unavailable',
            'server says',
            'networkerror',
        )
        return any(marker in text for marker in transient_markers)

    @staticmethod
    def _is_persistent_bot_error(error_text: str) -> bool:
        text = error_text.lower()
        persistent_markers = (
            'unauthorized',
            'forbidden',
            'token is invalid',
            'invalid token',
            'not found',
            'network is unreachable',
            'no route to host',
            'name or service not known',
            'temporary failure in name resolution',
        )
        return any(marker in text for marker in persistent_markers)

    def _classify_bot_error(self, error_text: str) -> str:
        if self._is_persistent_bot_error(error_text):
            return 'persistent'
        if self._is_transient_bot_error(error_text):
            return 'transient'
        return 'transient'

    async def _retry_bot_recovery(self) -> tuple[bool, str | None, str | None]:
        last_error: str | None = None
        last_error_kind: str | None = None
        for attempt, delay in enumerate(self.retry_delays, start=1):
            self._log(
                logging.INFO,
                'BOT',
                (
                    f'Попытка восстановления {attempt}/{len(self.retry_delays)} '
                    f'через {delay}с перед heal()'
                ),
            )
            await asyncio.sleep(delay)
            try:
                await self.bot.get_me()
                self.bot_online = True
                self.consecutive_bot_errors = 0
                self.last_bot_error = None
                self.last_bot_error_kind = None
                self._log(logging.INFO, 'BOT', 'Восстановление успешно, перезапуск не требуется')
                return True, None, None
            except Exception as error:
                last_error = str(error)
                last_error_kind = self._classify_bot_error(last_error)
                self.bot_online = False
                self.last_bot_error = last_error
                self.last_bot_error_kind = last_error_kind
                self._log(
                    logging.WARNING,
                    'BOT',
                    (
                        f'Попытка {attempt} неуспешна '
                        f'(тип={last_error_kind}): {last_error}'
                    ),
                )
                if last_error_kind == 'persistent':
                    self._log(
                        logging.ERROR,
                        'BOT',
                        'Обнаружена устойчивая ошибка во время ретраев, прекращаю восстановление',
                    )
                    break
        return False, last_error, last_error_kind

    async def _count_restarts_last_hour(self) -> int | None:
        process = await asyncio.create_subprocess_exec(
            'journalctl',
            '-u',
            'shopbot',
            '--since',
            '1 hour ago',
            '--no-pager',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            self._log(
                logging.ERROR,
                'RESTARTS',
                f'Не удалось прочитать journalctl для restart rate: {stderr.decode(errors="ignore").strip()}',
            )
            return None
        text = stdout.decode(errors='ignore')
        return text.count('Scheduled restart job')

    async def _check_restart_frequency(self) -> None:
        restart_count = await self._count_restarts_last_hour()
        if restart_count is None:
            return
        self._log(
            logging.INFO,
            'RESTARTS',
            f'Перезапусков shopbot за последний час: {restart_count}',
        )
        if restart_count <= self.restart_alert_threshold:
            return

        self._log(
            logging.ERROR,
            'RESTARTS',
            (
                'Высокая частота перезапусков: '
                f'{restart_count} за последний час (порог {self.restart_alert_threshold})'
            ),
        )
        now = datetime.now(tz=UTC)
        should_notify = (
            self.last_restart_alert_at is None
            or now - self.last_restart_alert_at >= timedelta(minutes=30)
        )
        if should_notify:
            await self.notify_admins(
                (
                    '⚠️ ShopBot часто перезапускается: '
                    f'{restart_count} раз(а) за последний час. '
                    'Проверьте стабильность Telegram/API и состояние сервиса.'
                )
            )
            self.last_restart_alert_at = now

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
        last_restart_check = 0.0
        while not self._stop_event.is_set():
            now = asyncio.get_running_loop().time()
            is_ok = True
            critical_reason: str | None = None

            if now - last_bot >= 30:
                last_bot = now
                if not await self.check_bot_alive():
                    self.consecutive_bot_errors += 1
                    error_text = self.last_bot_error or 'bot_check_failed'
                    error_kind = self.last_bot_error_kind or self._classify_bot_error(error_text)
                    self._log(
                        logging.WARNING,
                        'BOT',
                        (
                            'Ошибка проверки бота '
                            f'({self.consecutive_bot_errors}/{self.max_consecutive_bot_errors}), '
                            f'тип={error_kind}'
                        ),
                    )

                    if error_kind == 'persistent':
                        is_ok = False
                        critical_reason = f'bot_unreachable_persistent: {error_text}'
                        self._log(
                            logging.ERROR,
                            'BOT',
                            f'Перезапуск по persistent-ошибке: {critical_reason}',
                        )
                    elif self.consecutive_bot_errors >= self.max_consecutive_bot_errors:
                        self._log(
                            logging.ERROR,
                            'BOT',
                            (
                                'Достигнут лимит последовательных ошибок '
                                f'({self.max_consecutive_bot_errors}), запускаю retry/backoff'
                            ),
                        )
                        recovered, retry_error, retry_error_kind = await self._retry_bot_recovery()
                        if not recovered:
                            is_ok = False
                            final_error = retry_error or error_text
                            final_kind = retry_error_kind or 'transient'
                            critical_reason = f'bot_unreachable_{final_kind}: {final_error}'
                            self._log(
                                logging.CRITICAL,
                                'BOT',
                                (
                                    'Все попытки восстановления исчерпаны, '
                                    f'причина перезапуска: {critical_reason}'
                                ),
                            )
                    else:
                        is_ok = False
                        self._log(
                            logging.INFO,
                            'BOT',
                            'Транзиентная ошибка: жду следующие проверки без перезапуска',
                        )
                        critical_reason = None

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

            if now - last_restart_check >= 300:
                last_restart_check = now
                await self._check_restart_frequency()

            if critical_reason is not None:
                self._degraded = True
                self.last_critical_error = critical_reason
                self.last_critical_at = datetime.now(tz=UTC)
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
            'consecutive_bot_errors': self.consecutive_bot_errors,
            'max_consecutive_bot_errors': self.max_consecutive_bot_errors,
        }


_monitor: BotHealthMonitor | None = None


def set_monitor(monitor: BotHealthMonitor) -> None:
    global _monitor
    _monitor = monitor


def get_monitor() -> BotHealthMonitor | None:
    return _monitor
