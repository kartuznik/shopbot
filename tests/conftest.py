from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

# Settings требует токен бота ещё на импорте, а .env в репозитории нет.
os.environ.setdefault('TELEGRAM_BOT_TOKEN', 'test-token')

from bot.config import get_settings  # noqa: E402
from bot.webhook import create_app  # noqa: E402

WEBHOOK_TOKEN = 'test-webhook-token'
YOOKASSA_IP = '185.71.76.1'
PAYMENT_ID = '22d6d597-000f-5000-9000-145f6df21d6f'
ORDER_ID = 1


def _create_fixture_db(path: Path) -> None:
    db = sqlite3.connect(path)
    try:
        db.executescript(
            """
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                total_price REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL UNIQUE,
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'RUB',
                payment_url TEXT,
                payment_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                paid_at TEXT
            );
            """
        )
        db.execute(
            "INSERT INTO orders (id, user_id, product_id, quantity, total_price, status)"
            " VALUES (?, 100, 1, 1, 500.0, 'new')",
            (ORDER_ID,),
        )
        db.execute(
            "INSERT INTO payments (order_id, amount, payment_id, status)"
            " VALUES (?, 500.0, ?, 'pending')",
            (ORDER_ID, PAYMENT_ID),
        )
        db.commit()
    finally:
        db.close()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / 'shopbot.db'
    _create_fixture_db(path)
    return path


@pytest.fixture
def client(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    monkeypatch.setenv('DB_PATH', str(db_path))
    monkeypatch.setenv('WEBHOOK_SECRET_TOKEN', WEBHOOK_TOKEN)
    monkeypatch.setenv('YUKASSA_SHOP_ID', '')
    monkeypatch.setenv('YUKASSA_SECRET_KEY', '')
    get_settings.cache_clear()

    app = create_app()
    app.config['TESTING'] = True
    with app.test_client() as test_client:
        yield test_client

    get_settings.cache_clear()


def order_state(db_path: Path) -> tuple[str, str, str | None]:
    """Статус заказа, статус платежа и отметка об оплате."""
    db = sqlite3.connect(db_path)
    try:
        db.row_factory = sqlite3.Row
        order = db.execute('SELECT status FROM orders WHERE id = ?', (ORDER_ID,)).fetchone()
        payment = db.execute(
            'SELECT status, paid_at FROM payments WHERE order_id = ?', (ORDER_ID,)
        ).fetchone()
        return order['status'], payment['status'], payment['paid_at']
    finally:
        db.close()


def notification(status: str = 'succeeded') -> dict[str, object]:
    return {
        'type': 'notification',
        'event': f'payment.{status}',
        'object': {
            'id': PAYMENT_ID,
            'status': status,
            'paid': status == 'succeeded',
            'amount': {'value': '500.00', 'currency': 'RUB'},
            'metadata': {'order_id': str(ORDER_ID)},
        },
    }
