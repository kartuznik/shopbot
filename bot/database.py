from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite

from bot.config import get_settings


def _resolve_db_path() -> Path:
    db_path = Path(get_settings().DB_PATH)
    if not db_path.is_absolute():
        db_path = Path('/opt/bots/shopbot') / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(str(_resolve_db_path()))
    db.row_factory = aiosqlite.Row
    await db.execute('PRAGMA journal_mode=WAL;')
    await db.execute('PRAGMA foreign_keys=ON;')
    return db


async def init_db() -> None:
    db = await get_db()
    try:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_activity TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                price REAL NOT NULL,
                photo_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS cart_items (
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (user_id, product_id),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                total_price REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
            );
            """
        )
        await db.commit()
    finally:
        await db.close()


async def upsert_user(user_id: int, username: str | None) -> None:
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO users (user_id, username)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                last_activity=CURRENT_TIMESTAMP
            """,
            (user_id, username),
        )
        await db.commit()
    finally:
        await db.close()


async def get_products() -> list[dict[str, Any]]:
    db = await get_db()
    try:
        rows = await (await db.execute('SELECT * FROM products ORDER BY id DESC')).fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_product(product_id: int) -> dict[str, Any] | None:
    db = await get_db()
    try:
        row = await (await db.execute('SELECT * FROM products WHERE id = ?', (product_id,))).fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def add_product(name: str, description: str, price: float, photo_id: str | None) -> int:
    db = await get_db()
    try:
        await db.execute(
            'INSERT INTO products (name, description, price, photo_id) VALUES (?, ?, ?, ?)',
            (name, description, price, photo_id),
        )
        await db.commit()
        row = await (await db.execute('SELECT last_insert_rowid()')).fetchone()
        return int(row[0])
    finally:
        await db.close()


async def update_product(product_id: int, field: str, value: str) -> bool:
    if field not in {'name', 'description', 'price', 'photo_id'}:
        return False
    db = await get_db()
    try:
        await db.execute(f'UPDATE products SET {field} = ? WHERE id = ?', (value, product_id))
        await db.commit()
        return True
    finally:
        await db.close()


async def delete_product(product_id: int) -> None:
    db = await get_db()
    try:
        await db.execute('DELETE FROM products WHERE id = ?', (product_id,))
        await db.commit()
    finally:
        await db.close()


async def add_to_cart(user_id: int, product_id: int, quantity: int = 1) -> None:
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO cart_items (user_id, product_id, quantity)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, product_id) DO UPDATE SET
                quantity = quantity + excluded.quantity
            """,
            (user_id, product_id, quantity),
        )
        await db.commit()
    finally:
        await db.close()


async def get_cart_items(user_id: int) -> list[dict[str, Any]]:
    db = await get_db()
    try:
        rows = await (
            await db.execute(
                """
                SELECT c.user_id, c.product_id, c.quantity, p.name, p.price
                FROM cart_items c
                JOIN products p ON p.id = c.product_id
                WHERE c.user_id = ?
                ORDER BY p.name ASC
                """,
                (user_id,),
            )
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def clear_cart(user_id: int) -> None:
    db = await get_db()
    try:
        await db.execute('DELETE FROM cart_items WHERE user_id = ?', (user_id,))
        await db.commit()
    finally:
        await db.close()


async def create_order_from_cart(user_id: int) -> int:
    items = await get_cart_items(user_id)
    if not items:
        return 0
    db = await get_db()
    count = 0
    try:
        for item in items:
            total = float(item['price']) * int(item['quantity'])
            await db.execute(
                """
                INSERT INTO orders (user_id, product_id, quantity, total_price, status)
                VALUES (?, ?, ?, ?, 'new')
                """,
                (user_id, item['product_id'], item['quantity'], total),
            )
            count += 1
        await db.execute('DELETE FROM cart_items WHERE user_id = ?', (user_id,))
        await db.commit()
        return count
    finally:
        await db.close()


async def get_user_orders(user_id: int) -> list[dict[str, Any]]:
    db = await get_db()
    try:
        rows = await (
            await db.execute(
                """
                SELECT o.id, o.quantity, o.total_price, o.status, o.created_at, p.name
                FROM orders o
                JOIN products p ON p.id = o.product_id
                WHERE o.user_id = ?
                ORDER BY o.id DESC
                """,
                (user_id,),
            )
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_all_orders() -> list[dict[str, Any]]:
    db = await get_db()
    try:
        rows = await (
            await db.execute(
                """
                SELECT o.id, o.user_id, o.quantity, o.total_price, o.status, o.created_at, p.name
                FROM orders o
                JOIN products p ON p.id = o.product_id
                ORDER BY o.id DESC
                """
            )
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_all_users() -> list[dict[str, Any]]:
    db = await get_db()
    try:
        rows = await (await db.execute('SELECT * FROM users ORDER BY created_at DESC')).fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()
