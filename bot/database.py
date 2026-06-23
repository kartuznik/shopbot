from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite

from bot.config import get_settings

ORDER_STATUSES = {'new', 'confirmed', 'paid', 'shipped', 'delivered', 'cancelled'}


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
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

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
                category_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
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
                address TEXT,
                shipping_cost REAL NOT NULL DEFAULT 0,
                tracking_number TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
            );
            """
        )
        await _migrate_products_table_with_category_fk(db)
        await _ensure_column_exists(db, 'orders', 'address', 'TEXT')
        await _ensure_column_exists(db, 'orders', 'shipping_cost', 'REAL NOT NULL DEFAULT 0')
        await _ensure_column_exists(db, 'orders', 'tracking_number', 'TEXT')
        await db.commit()
    finally:
        await db.close()


async def _ensure_column_exists(
    db: aiosqlite.Connection, table_name: str, column_name: str, column_sql: str
) -> None:
    rows = await (await db.execute(f'PRAGMA table_info({table_name})')).fetchall()
    existing = {row[1] for row in rows}
    if column_name not in existing:
        await db.execute(f'ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}')


async def _migrate_products_table_with_category_fk(db: aiosqlite.Connection) -> None:
    columns = await (await db.execute('PRAGMA table_info(products)')).fetchall()
    existing_columns = {row[1] for row in columns}
    has_category_column = 'category_id' in existing_columns
    foreign_keys = await (await db.execute('PRAGMA foreign_key_list(products)')).fetchall()
    has_category_fk = any(row[2] == 'categories' and row[3] == 'category_id' for row in foreign_keys)

    if has_category_fk:
        return

    await db.execute('PRAGMA foreign_keys=OFF;')
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS products_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            price REAL NOT NULL,
            photo_id TEXT,
            category_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
        );
        """
    )
    if has_category_column:
        await db.execute(
            """
            INSERT INTO products_new (id, name, description, price, photo_id, category_id, created_at)
            SELECT id, name, description, price, photo_id, category_id, created_at
            FROM products
            """
        )
    else:
        await db.execute(
            """
            INSERT INTO products_new (id, name, description, price, photo_id, category_id, created_at)
            SELECT id, name, description, price, photo_id, NULL, created_at
            FROM products
            """
        )
    await db.execute('DROP TABLE products')
    await db.execute('ALTER TABLE products_new RENAME TO products')
    await db.execute('PRAGMA foreign_keys=ON;')


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


async def get_products(category_id: int | None = None) -> list[dict[str, Any]]:
    db = await get_db()
    try:
        if category_id is None:
            query = """
                SELECT p.*, c.name AS category_name
                FROM products p
                LEFT JOIN categories c ON c.id = p.category_id
                ORDER BY p.id DESC
            """
            rows = await (await db.execute(query)).fetchall()
        else:
            query = """
                SELECT p.*, c.name AS category_name
                FROM products p
                LEFT JOIN categories c ON c.id = p.category_id
                WHERE p.category_id = ?
                ORDER BY p.id DESC
            """
            rows = await (await db.execute(query, (category_id,))).fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def search_products(query: str) -> list[dict[str, Any]]:
    db = await get_db()
    try:
        like_query = f'%{query}%'
        rows = await (
            await db.execute(
                """
                SELECT p.*, c.name AS category_name
                FROM products p
                LEFT JOIN categories c ON c.id = p.category_id
                WHERE p.name LIKE ? OR COALESCE(p.description, '') LIKE ?
                ORDER BY p.id DESC
                """,
                (like_query, like_query),
            )
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_product(product_id: int) -> dict[str, Any] | None:
    db = await get_db()
    try:
        row = await (
            await db.execute(
                """
                SELECT p.*, c.name AS category_name
                FROM products p
                LEFT JOIN categories c ON c.id = p.category_id
                WHERE p.id = ?
                """,
                (product_id,),
            )
        ).fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def add_product(
    name: str,
    description: str,
    price: float,
    photo_id: str | None,
    category_id: int | None,
) -> int:
    db = await get_db()
    try:
        await db.execute(
            'INSERT INTO products (name, description, price, photo_id, category_id) VALUES (?, ?, ?, ?, ?)',
            (name, description, price, photo_id, category_id),
        )
        await db.commit()
        row = await (await db.execute('SELECT last_insert_rowid()')).fetchone()
        return int(row[0])
    finally:
        await db.close()


async def update_product(product_id: int, field: str, value: str) -> bool:
    if field not in {'name', 'description', 'price', 'photo_id', 'category_id'}:
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


async def create_order_from_cart(user_id: int) -> dict[str, Any]:
    items = await get_cart_items(user_id)
    if not items:
        return {'count': 0, 'order_ids': [], 'items': [], 'total': 0.0}
    db = await get_db()
    count = 0
    order_ids: list[int] = []
    summary_items: list[dict[str, Any]] = []
    total_sum = 0.0
    try:
        for item in items:
            total = float(item['price']) * int(item['quantity'])
            await db.execute(
                """
                INSERT INTO orders (user_id, product_id, quantity, total_price, status, shipping_cost)
                VALUES (?, ?, ?, ?, 'new', 0)
                """,
                (user_id, item['product_id'], item['quantity'], total),
            )
            row = await (await db.execute('SELECT last_insert_rowid()')).fetchone()
            order_ids.append(int(row[0]))
            summary_items.append(
                {
                    'name': item['name'],
                    'quantity': int(item['quantity']),
                    'line_total': total,
                }
            )
            total_sum += total
            count += 1
        await db.execute('DELETE FROM cart_items WHERE user_id = ?', (user_id,))
        await db.commit()
        return {
            'count': count,
            'order_ids': order_ids,
            'items': summary_items,
            'total': total_sum,
        }
    finally:
        await db.close()


async def get_user_orders(user_id: int) -> list[dict[str, Any]]:
    db = await get_db()
    try:
        rows = await (
            await db.execute(
                """
                SELECT o.id, o.quantity, o.total_price, o.status, o.created_at, o.tracking_number, p.name
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
                SELECT
                    o.id,
                    o.user_id,
                    u.username,
                    o.quantity,
                    o.total_price,
                    o.status,
                    o.address,
                    o.shipping_cost,
                    o.tracking_number,
                    o.created_at,
                    p.name
                FROM orders o
                LEFT JOIN users u ON u.user_id = o.user_id
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


async def get_active_users_count() -> int:
    db = await get_db()
    try:
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM users WHERE datetime(last_activity) >= datetime('now', '-1 day')"
            )
        ).fetchone()
        return int(row[0] if row else 0)
    finally:
        await db.close()


async def add_category(name: str, description: str | None) -> int:
    db = await get_db()
    try:
        await db.execute(
            'INSERT INTO categories (name, description) VALUES (?, ?)',
            (name.strip(), (description or '').strip() or None),
        )
        await db.commit()
        row = await (await db.execute('SELECT last_insert_rowid()')).fetchone()
        return int(row[0])
    finally:
        await db.close()


async def get_categories() -> list[dict[str, Any]]:
    db = await get_db()
    try:
        rows = await (await db.execute('SELECT * FROM categories ORDER BY name ASC')).fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def delete_category(category_id: int) -> bool:
    db = await get_db()
    try:
        await db.execute('UPDATE products SET category_id = NULL WHERE category_id = ?', (category_id,))
        cursor = await db.execute('DELETE FROM categories WHERE id = ?', (category_id,))
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def update_order_status(order_id: int, status: str) -> bool:
    if status not in ORDER_STATUSES:
        return False
    db = await get_db()
    try:
        cursor = await db.execute('UPDATE orders SET status = ? WHERE id = ?', (status, order_id))
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def get_order_details(order_id: int) -> dict[str, Any] | None:
    db = await get_db()
    try:
        row = await (
            await db.execute(
                """
                SELECT
                    o.id,
                    o.user_id,
                    u.username,
                    p.name AS product_name,
                    o.quantity,
                    o.total_price,
                    o.status,
                    o.address,
                    o.shipping_cost,
                    o.tracking_number,
                    o.created_at
                FROM orders o
                LEFT JOIN users u ON u.user_id = o.user_id
                JOIN products p ON p.id = o.product_id
                WHERE o.id = ?
                """,
                (order_id,),
            )
        ).fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


def db_integrity_check(db_path: str) -> str:
    connection = sqlite3.connect(db_path, timeout=5)
    try:
        row = connection.execute('PRAGMA integrity_check;').fetchone()
        return str(row[0]) if row else 'unknown'
    finally:
        connection.close()
