from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from aiogram import Bot
from flask import Flask, jsonify, request

from bot.config import get_settings

logger = logging.getLogger('shopbot.webhook')

_bot: Bot | None = None
_loop: asyncio.AbstractEventLoop | None = None


def _resolve_db_path() -> str:
    raw = get_settings().DB_PATH
    path = Path(raw)
    if not path.is_absolute():
        path = Path('/opt/bots/shopbot') / path
    return str(path)


def _update_payment_and_order(payment_id: str, status: str, order_id: int | None = None) -> int | None:
    db = sqlite3.connect(_resolve_db_path(), timeout=5)
    try:
        db.row_factory = sqlite3.Row
        if order_id is None:
            row = db.execute('SELECT order_id FROM payments WHERE payment_id = ?', (payment_id,)).fetchone()
            if row is None:
                return None
            order_id = int(row['order_id'])

        if status == 'succeeded':
            db.execute(
                """
                UPDATE payments
                SET status = 'succeeded', paid_at = CURRENT_TIMESTAMP
                WHERE order_id = ?
                """,
                (order_id,),
            )
            db.execute("UPDATE orders SET status = 'paid' WHERE id = ?", (order_id,))
        elif status == 'cancelled':
            db.execute("UPDATE payments SET status = 'cancelled' WHERE order_id = ?", (order_id,))
            db.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
        else:
            db.execute("UPDATE payments SET status = ? WHERE order_id = ?", (status, order_id))
        db.commit()
        return order_id
    finally:
        db.close()


def _notify_admins_payment(order_id: int, status: str) -> None:
    if _bot is None or _loop is None:
        return
    text = f'💳 Платеж по заказу #{order_id}: {status}'
    for admin_id in get_settings().ADMIN_IDS:
        future = asyncio.run_coroutine_threadsafe(
            _bot.send_message(chat_id=admin_id, text=text),
            _loop,
        )
        try:
            future.result(timeout=5)
        except Exception:
            continue


def create_app() -> Flask:
    app = Flask(__name__)

    @app.post('/webhook/yookassa')
    def webhook_yookassa() -> tuple[Any, int]:
        payload = request.get_json(silent=True) or {}
        obj = payload.get('object') or {}
        status = str(obj.get('status') or '')
        payment_id = str(obj.get('id') or '')
        metadata = obj.get('metadata') or {}
        raw_order_id = metadata.get('order_id')
        order_id = int(raw_order_id) if str(raw_order_id or '').isdigit() else None

        if not payment_id and order_id is None:
            return jsonify({'ok': False, 'error': 'invalid payload'}), 400

        try:
            resolved_order_id = _update_payment_and_order(payment_id, status, order_id)
        except Exception as error:
            logger.exception('Webhook processing error: %s', error)
            return jsonify({'ok': False, 'error': 'processing error'}), 500

        if resolved_order_id is not None and status in {'succeeded', 'cancelled'}:
            _notify_admins_payment(resolved_order_id, status)
        return jsonify({'ok': True}), 200

    return app


def start_webhook_server(bot: Bot, loop: asyncio.AbstractEventLoop) -> threading.Thread:
    global _bot, _loop
    _bot = bot
    _loop = loop

    app = create_app()

    def _run() -> None:
        app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

    thread = threading.Thread(target=_run, name='shopbot-webhook', daemon=True)
    thread.start()
    return thread
