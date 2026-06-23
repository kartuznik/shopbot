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

            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                order_id INTEGER NOT NULL UNIQUE,
                rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
                comment TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
                FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL UNIQUE,
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'RUB',
                payment_url TEXT,
                payment_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                paid_at TEXT,
                FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS delivery_zones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                zone_name TEXT NOT NULL,
                cost REAL NOT NULL,
                description TEXT
            );

            CREATE TABLE IF NOT EXISTS user_addresses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                address TEXT NOT NULL,
                zone_id INTEGER,
                is_default INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                FOREIGN KEY (zone_id) REFERENCES delivery_zones(id) ON DELETE SET NULL
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


async def create_order_from_cart(
    user_id: int, address: str | None = None, shipping_cost: float = 0.0
) -> dict[str, Any]:
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
            item_total = float(item['price']) * int(item['quantity'])
            total = item_total + float(shipping_cost)
            await db.execute(
                """
                INSERT INTO orders (user_id, product_id, quantity, total_price, status, shipping_cost, address)
                VALUES (?, ?, ?, ?, 'new', ?, ?)
                """,
                (user_id, item['product_id'], item['quantity'], total, shipping_cost, address),
            )
            row = await (await db.execute('SELECT last_insert_rowid()')).fetchone()
            order_ids.append(int(row[0]))
            summary_items.append(
                {
                    'name': item['name'],
                    'quantity': int(item['quantity']),
                    'line_total': item_total,
                    'shipping_cost': float(shipping_cost),
                    'final_total': total,
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


async def get_order_basic(order_id: int) -> dict[str, Any] | None:
    db = await get_db()
    try:
        row = await (
            await db.execute(
                """
                SELECT o.id, o.user_id, o.total_price, o.status, p.name AS product_name
                FROM orders o
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


async def get_product_rating_summary(product_id: int) -> dict[str, Any]:
    db = await get_db()
    try:
        avg_row = await (
            await db.execute(
                """
                SELECT AVG(rating) AS avg_rating, COUNT(*) AS reviews_count
                FROM reviews
                WHERE product_id = ?
                """,
                (product_id,),
            )
        ).fetchone()
        distribution_rows = await (
            await db.execute(
                """
                SELECT rating, COUNT(*) AS count
                FROM reviews
                WHERE product_id = ?
                GROUP BY rating
                ORDER BY rating DESC
                """,
                (product_id,),
            )
        ).fetchall()
        distribution = {int(row['rating']): int(row['count']) for row in distribution_rows}
        return {
            'avg_rating': float(avg_row['avg_rating'] or 0.0),
            'reviews_count': int(avg_row['reviews_count'] or 0),
            'distribution': distribution,
        }
    finally:
        await db.close()


async def get_product_reviews(product_id: int, limit: int = 20) -> list[dict[str, Any]]:
    db = await get_db()
    try:
        rows = await (
            await db.execute(
                """
                SELECT r.id, r.user_id, u.username, r.order_id, r.rating, r.comment, r.created_at
                FROM reviews r
                LEFT JOIN users u ON u.user_id = r.user_id
                WHERE r.product_id = ?
                ORDER BY r.id DESC
                LIMIT ?
                """,
                (product_id, limit),
            )
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def can_user_review_order(user_id: int, order_id: int) -> dict[str, Any] | None:
    db = await get_db()
    try:
        row = await (
            await db.execute(
                """
                SELECT o.id, o.user_id, o.product_id, o.status, p.name AS product_name
                FROM orders o
                JOIN products p ON p.id = o.product_id
                WHERE o.id = ? AND o.user_id = ? AND o.status = 'delivered'
                """,
                (order_id, user_id),
            )
        ).fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def save_review(
    user_id: int,
    product_id: int,
    order_id: int,
    rating: int,
    comment: str | None,
) -> int:
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO reviews (user_id, product_id, order_id, rating, comment)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(order_id) DO UPDATE SET
                rating = excluded.rating,
                comment = excluded.comment,
                created_at = CURRENT_TIMESTAMP
            """,
            (user_id, product_id, order_id, rating, (comment or '').strip() or None),
        )
        await db.commit()
        row = await (await db.execute('SELECT id FROM reviews WHERE order_id = ?', (order_id,))).fetchone()
        return int(row[0])
    finally:
        await db.close()


async def delete_review(review_id: int) -> bool:
    db = await get_db()
    try:
        cursor = await db.execute('DELETE FROM reviews WHERE id = ?', (review_id,))
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def get_order_for_review(order_id: int) -> dict[str, Any] | None:
    db = await get_db()
    try:
        row = await (
            await db.execute(
                """
                SELECT o.id, o.user_id, o.product_id, o.status, p.name AS product_name
                FROM orders o
                JOIN products p ON p.id = o.product_id
                WHERE o.id = ?
                """,
                (order_id,),
            )
        ).fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_sales_overview() -> dict[str, Any]:
    db = await get_db()
    try:
        base_row = await (
            await db.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM users) AS users_total,
                    (SELECT COUNT(*) FROM orders) AS orders_total,
                    (SELECT COALESCE(SUM(total_price), 0) FROM orders) AS sales_total
                """
            )
        ).fetchone()
        top_products_rows = await (
            await db.execute(
                """
                SELECT p.name, SUM(o.quantity) AS qty
                FROM orders o
                JOIN products p ON p.id = o.product_id
                GROUP BY o.product_id
                ORDER BY qty DESC
                LIMIT 5
                """
            )
        ).fetchall()
        top_categories_rows = await (
            await db.execute(
                """
                SELECT COALESCE(c.name, 'Без категории') AS name, SUM(o.total_price) AS total
                FROM orders o
                JOIN products p ON p.id = o.product_id
                LEFT JOIN categories c ON c.id = p.category_id
                GROUP BY COALESCE(c.id, -1)
                ORDER BY total DESC
                LIMIT 5
                """
            )
        ).fetchall()
        statuses_rows = await (
            await db.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM orders
                GROUP BY status
                """
            )
        ).fetchall()
        last_7 = await (
            await db.execute(
                """
                SELECT COUNT(*) AS count, COALESCE(SUM(total_price), 0) AS amount
                FROM orders
                WHERE datetime(created_at) >= datetime('now', '-7 day')
                """
            )
        ).fetchone()
        last_30 = await (
            await db.execute(
                """
                SELECT COUNT(*) AS count, COALESCE(SUM(total_price), 0) AS amount
                FROM orders
                WHERE datetime(created_at) >= datetime('now', '-30 day')
                """
            )
        ).fetchone()
        sales_total = float(base_row['sales_total'] or 0.0)
        orders_total = int(base_row['orders_total'] or 0)
        return {
            'users_total': int(base_row['users_total'] or 0),
            'orders_total': orders_total,
            'sales_total': sales_total,
            'avg_check': sales_total / orders_total if orders_total else 0.0,
            'top_products': [dict(row) for row in top_products_rows],
            'top_categories': [dict(row) for row in top_categories_rows],
            'statuses': {str(row['status']): int(row['count']) for row in statuses_rows},
            'last_7': {'count': int(last_7['count'] or 0), 'amount': float(last_7['amount'] or 0.0)},
            'last_30': {'count': int(last_30['count'] or 0), 'amount': float(last_30['amount'] or 0.0)},
        }
    finally:
        await db.close()


def _period_modifier(period: str) -> str:
    mapping = {
        'day': '-1 day',
        'week': '-7 day',
        'month': '-30 day',
        'year': '-365 day',
    }
    return mapping.get(period, '-7 day')


async def get_sales_by_period(period: str) -> dict[str, Any]:
    db = await get_db()
    try:
        modifier = _period_modifier(period)
        total_row = await (
            await db.execute(
                """
                SELECT
                    COUNT(*) AS orders_count,
                    COALESCE(SUM(total_price), 0) AS sales_total
                FROM orders
                WHERE datetime(created_at) >= datetime('now', ?)
                """,
                (modifier,),
            )
        ).fetchone()
        top_rows = await (
            await db.execute(
                """
                SELECT p.name, SUM(o.quantity) AS qty
                FROM orders o
                JOIN products p ON p.id = o.product_id
                WHERE datetime(o.created_at) >= datetime('now', ?)
                GROUP BY o.product_id
                ORDER BY qty DESC
                LIMIT 5
                """,
                (modifier,),
            )
        ).fetchall()
        orders_count = int(total_row['orders_count'] or 0)
        sales_total = float(total_row['sales_total'] or 0.0)
        return {
            'period': period,
            'orders_count': orders_count,
            'sales_total': sales_total,
            'avg_check': sales_total / orders_count if orders_count else 0.0,
            'top_products': [dict(row) for row in top_rows],
        }
    finally:
        await db.close()


async def get_users_stats() -> dict[str, Any]:
    db = await get_db()
    try:
        day_row = await (
            await db.execute(
                """
                SELECT
                    SUM(CASE WHEN datetime(created_at) >= datetime('now', '-1 day') THEN 1 ELSE 0 END) AS day_count,
                    SUM(CASE WHEN datetime(created_at) >= datetime('now', '-7 day') THEN 1 ELSE 0 END) AS week_count,
                    SUM(CASE WHEN datetime(created_at) >= datetime('now', '-30 day') THEN 1 ELSE 0 END) AS month_count,
                    COUNT(*) AS total_count
                FROM users
                """
            )
        ).fetchone()
        active_row = await (
            await db.execute(
                """
                SELECT
                    COUNT(DISTINCT CASE WHEN datetime(created_at) >= datetime('now', '-1 day') THEN user_id END) AS active_day,
                    COUNT(DISTINCT CASE WHEN datetime(created_at) >= datetime('now', '-7 day') THEN user_id END) AS active_week,
                    COUNT(DISTINCT CASE WHEN datetime(created_at) >= datetime('now', '-30 day') THEN user_id END) AS active_month,
                    COUNT(DISTINCT user_id) AS active_total
                FROM orders
                """
            )
        ).fetchone()
        total_users = int(day_row['total_count'] or 0)
        users_with_orders = int(active_row['active_total'] or 0)
        return {
            'new_day': int(day_row['day_count'] or 0),
            'new_week': int(day_row['week_count'] or 0),
            'new_month': int(day_row['month_count'] or 0),
            'active_day': int(active_row['active_day'] or 0),
            'active_week': int(active_row['active_week'] or 0),
            'active_month': int(active_row['active_month'] or 0),
            'total_users': total_users,
            'conversion': (users_with_orders / total_users * 100) if total_users else 0.0,
        }
    finally:
        await db.close()


async def save_payment(
    order_id: int,
    amount: float,
    payment_url: str,
    payment_id: str,
    currency: str = 'RUB',
    status: str = 'pending',
) -> int:
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO payments (order_id, amount, currency, payment_url, payment_id, status)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(order_id) DO UPDATE SET
                amount = excluded.amount,
                currency = excluded.currency,
                payment_url = excluded.payment_url,
                payment_id = excluded.payment_id,
                status = excluded.status
            """,
            (order_id, amount, currency, payment_url, payment_id, status),
        )
        await db.commit()
        row = await (await db.execute('SELECT id FROM payments WHERE order_id = ?', (order_id,))).fetchone()
        return int(row[0])
    finally:
        await db.close()


async def get_payment_by_order(order_id: int) -> dict[str, Any] | None:
    db = await get_db()
    try:
        row = await (await db.execute('SELECT * FROM payments WHERE order_id = ?', (order_id,))).fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_payment_by_payment_id(payment_id: str) -> dict[str, Any] | None:
    db = await get_db()
    try:
        row = await (await db.execute('SELECT * FROM payments WHERE payment_id = ?', (payment_id,))).fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def list_payments() -> list[dict[str, Any]]:
    db = await get_db()
    try:
        rows = await (
            await db.execute(
                """
                SELECT p.*, o.user_id
                FROM payments p
                LEFT JOIN orders o ON o.id = p.order_id
                ORDER BY p.id DESC
                """
            )
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def update_payment_status(order_id: int, status: str, paid: bool = False) -> bool:
    db = await get_db()
    try:
        if paid:
            cursor = await db.execute(
                """
                UPDATE payments
                SET status = ?, paid_at = CURRENT_TIMESTAMP
                WHERE order_id = ?
                """,
                (status, order_id),
            )
        else:
            cursor = await db.execute('UPDATE payments SET status = ? WHERE order_id = ?', (status, order_id))
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def add_delivery_zone(zone_name: str, cost: float, description: str | None = None) -> int:
    db = await get_db()
    try:
        await db.execute(
            'INSERT INTO delivery_zones (zone_name, cost, description) VALUES (?, ?, ?)',
            (zone_name.strip(), cost, (description or '').strip() or None),
        )
        await db.commit()
        row = await (await db.execute('SELECT last_insert_rowid()')).fetchone()
        return int(row[0])
    finally:
        await db.close()


async def get_delivery_zones() -> list[dict[str, Any]]:
    db = await get_db()
    try:
        rows = await (await db.execute('SELECT * FROM delivery_zones ORDER BY id ASC')).fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def delete_delivery_zone(zone_id: int) -> bool:
    db = await get_db()
    try:
        cursor = await db.execute('DELETE FROM delivery_zones WHERE id = ?', (zone_id,))
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def add_user_address(user_id: int, address: str, zone_id: int, is_default: bool = False) -> int:
    db = await get_db()
    try:
        if is_default:
            await db.execute('UPDATE user_addresses SET is_default = 0 WHERE user_id = ?', (user_id,))
        await db.execute(
            """
            INSERT INTO user_addresses (user_id, address, zone_id, is_default)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, address.strip(), zone_id, 1 if is_default else 0),
        )
        await db.commit()
        row = await (await db.execute('SELECT last_insert_rowid()')).fetchone()
        return int(row[0])
    finally:
        await db.close()


async def get_user_addresses(user_id: int) -> list[dict[str, Any]]:
    db = await get_db()
    try:
        rows = await (
            await db.execute(
                """
                SELECT a.*, z.zone_name, z.cost
                FROM user_addresses a
                LEFT JOIN delivery_zones z ON z.id = a.zone_id
                WHERE a.user_id = ?
                ORDER BY a.is_default DESC, a.id DESC
                """,
                (user_id,),
            )
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_user_address(address_id: int, user_id: int) -> dict[str, Any] | None:
    db = await get_db()
    try:
        row = await (
            await db.execute(
                """
                SELECT a.*, z.zone_name, z.cost
                FROM user_addresses a
                LEFT JOIN delivery_zones z ON z.id = a.zone_id
                WHERE a.id = ? AND a.user_id = ?
                """,
                (address_id, user_id),
            )
        ).fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def delete_user_address(address_id: int, user_id: int) -> bool:
    db = await get_db()
    try:
        cursor = await db.execute('DELETE FROM user_addresses WHERE id = ? AND user_id = ?', (address_id, user_id))
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def set_default_user_address(address_id: int, user_id: int) -> bool:
    db = await get_db()
    try:
        await db.execute('UPDATE user_addresses SET is_default = 0 WHERE user_id = ?', (user_id,))
        cursor = await db.execute(
            'UPDATE user_addresses SET is_default = 1 WHERE id = ? AND user_id = ?',
            (address_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def get_delivery_stats() -> list[dict[str, Any]]:
    db = await get_db()
    try:
        rows = await (
            await db.execute(
                """
                SELECT
                    COALESCE(z.zone_name, 'Не определена') AS zone_name,
                    COUNT(o.id) AS orders_count,
                    COALESCE(SUM(o.shipping_cost), 0) AS shipping_total,
                    COALESCE(SUM(o.total_price), 0) AS sales_total
                FROM orders o
                LEFT JOIN user_addresses a ON a.address = o.address AND a.user_id = o.user_id
                LEFT JOIN delivery_zones z ON z.id = a.zone_id
                GROUP BY COALESCE(z.zone_name, 'Не определена')
                ORDER BY orders_count DESC
                """
            )
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()
