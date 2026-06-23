from __future__ import annotations

import os
import secrets
import sqlite3
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from web.auth import get_csrf_token, login_required, validate_csrf
from web.export import (
    export_orders_csv,
    export_orders_excel,
    export_products_csv,
    export_products_excel,
    export_reviews_csv,
    export_reviews_excel,
    export_sales_report_excel,
    export_users_csv,
    export_users_excel,
)
from web.forms import CategoryForm, DeliveryZoneForm, ProductForm

BASE_DIR = Path('/opt/bots/shopbot')
ENV_PATH = BASE_DIR / '.env'
DB_PATH = Path(os.getenv('DB_PATH', 'data/shopbot.db'))
if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH
UPLOAD_DIR = BASE_DIR / 'web' / 'static' / 'uploads'
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_BASE_URL = os.getenv('WEB_PUBLIC_BASE_URL', 'http://109.120.184.82:5001')

app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', secrets.token_hex(16))
app.config['PER_PAGE'] = 20
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024

SENSITIVE_KEYS = {'TELEGRAM_BOT_TOKEN', 'YUKASSA_SECRET_KEY', 'YUKASSA_SHOP_ID'}
EDITABLE_SETTINGS = {'LANGUAGE', 'DB_PATH', 'ADMIN_IDS', 'ADMIN_WEB_PASSWORD', 'WEB_PUBLIC_BASE_URL'}


def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH), timeout=5)
    db.row_factory = sqlite3.Row
    return db


def paginate(items: list[dict[str, Any]], page: int) -> tuple[list[dict[str, Any]], int]:
    per_page = int(app.config['PER_PAGE'])
    total = len(items)
    pages = max(1, (total + per_page - 1) // per_page)
    current = min(max(page, 1), pages)
    start = (current - 1) * per_page
    end = start + per_page
    return items[start:end], pages


def _env_read() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    result: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        result[key.strip()] = value.strip()
    return result


def _env_write(values: dict[str, str]) -> None:
    lines = [f'{key}={value}' for key, value in values.items()]
    ENV_PATH.write_text('\n'.join(lines) + '\n', encoding='utf-8')


@app.before_request
def check_csrf() -> None:
    if request.method == 'POST':
        if request.endpoint in {'static'}:
            return
        validate_csrf()


@app.context_processor
def inject_common() -> dict[str, Any]:
    return {'csrf_token': get_csrf_token}


@app.route('/login', methods=['GET', 'POST'])
def login() -> str:
    if request.method == 'POST':
        password = (request.form.get('password') or '').strip()
        configured = os.getenv('ADMIN_WEB_PASSWORD', '').strip()
        if configured and password == configured:
            session['is_admin'] = True
            flash('Вход выполнен.', 'success')
            return redirect(url_for('dashboard'))
        flash('Неверный пароль.', 'danger')
    return render_template('login.html')


@app.post('/logout')
@login_required
def logout() -> Any:
    session.clear()
    flash('Вы вышли из системы.', 'info')
    return redirect(url_for('login'))


@app.route('/')
@login_required
def dashboard() -> str:
    db = get_db()
    try:
        users_total = int(db.execute('SELECT COUNT(*) FROM users').fetchone()[0])
        orders_total = int(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0])
        sales_total = float(db.execute('SELECT COALESCE(SUM(total_price), 0) FROM orders').fetchone()[0])
        top_products = db.execute(
            """
            SELECT p.name, SUM(o.quantity) AS qty
            FROM orders o
            JOIN products p ON p.id = o.product_id
            GROUP BY o.product_id
            ORDER BY qty DESC
            LIMIT 5
            """
        ).fetchall()
    finally:
        db.close()
    return render_template(
        'dashboard.html',
        users_total=users_total,
        orders_total=orders_total,
        sales_total=sales_total,
        top_products=[dict(x) for x in top_products],
    )


@app.route('/products')
@login_required
def products() -> str:
    page = int(request.args.get('page', '1') or 1)
    q = (request.args.get('q') or '').strip()
    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT p.*, c.name AS category_name
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id
            ORDER BY p.id DESC
            """
        ).fetchall()
        categories = [dict(row) for row in db.execute('SELECT * FROM categories ORDER BY name ASC').fetchall()]
    finally:
        db.close()
    items = [dict(row) for row in rows]
    if q:
        query_lower = q.lower()
        items = [x for x in items if query_lower in str(x.get('name', '')).lower()]
    page_items, pages = paginate(items, page)
    return render_template(
        'products.html',
        products=page_items,
        categories=categories,
        page=page,
        pages=pages,
        q=q,
    )


@app.route('/products/add', methods=['GET', 'POST'])
@login_required
def products_add() -> Any:
    db = get_db()
    try:
        categories = [dict(row) for row in db.execute('SELECT * FROM categories ORDER BY name ASC').fetchall()]
        if request.method == 'POST':
            try:
                form = ProductForm.from_request(request)
            except Exception as error:
                flash(f'Ошибка формы: {error}', 'danger')
                return render_template('product_form.html', categories=categories, product=None)
            if not form.name:
                flash('Название товара обязательно.', 'warning')
                return render_template('product_form.html', categories=categories, product=None)
            photo_id = form.photo_id
            uploaded = request.files.get('photo_file')
            if uploaded and uploaded.filename:
                filename = secure_filename(uploaded.filename)
                target = UPLOAD_DIR / f'{secrets.token_hex(6)}_{filename}'
                uploaded.save(target)
                photo_id = f'{PUBLIC_BASE_URL}/static/uploads/{target.name}'
            db.execute(
                """
                INSERT INTO products (name, description, price, photo_id, category_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (form.name, form.description, form.price, photo_id, form.category_id),
            )
            db.commit()
            flash('Товар добавлен.', 'success')
            return redirect(url_for('products'))
    finally:
        db.close()
    return render_template('product_form.html', categories=categories, product=None)


@app.route('/products/<int:product_id>/edit', methods=['GET', 'POST'])
@login_required
def products_edit(product_id: int) -> Any:
    db = get_db()
    try:
        product_row = db.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
        if product_row is None:
            flash('Товар не найден.', 'warning')
            return redirect(url_for('products'))
        product = dict(product_row)
        categories = [dict(row) for row in db.execute('SELECT * FROM categories ORDER BY name ASC').fetchall()]
        if request.method == 'POST':
            form = ProductForm.from_request(request)
            photo_id = form.photo_id
            uploaded = request.files.get('photo_file')
            if uploaded and uploaded.filename:
                filename = secure_filename(uploaded.filename)
                target = UPLOAD_DIR / f'{secrets.token_hex(6)}_{filename}'
                uploaded.save(target)
                photo_id = f'{PUBLIC_BASE_URL}/static/uploads/{target.name}'
            db.execute(
                """
                UPDATE products
                SET name = ?, description = ?, price = ?, photo_id = ?, category_id = ?
                WHERE id = ?
                """,
                (form.name, form.description, form.price, photo_id, form.category_id, product_id),
            )
            db.commit()
            flash('Товар обновлен.', 'success')
            return redirect(url_for('products'))
    finally:
        db.close()
    return render_template('product_form.html', categories=categories, product=product)


@app.post('/products/<int:product_id>/delete')
@login_required
def products_delete(product_id: int) -> Any:
    db = get_db()
    try:
        db.execute('DELETE FROM products WHERE id = ?', (product_id,))
        db.commit()
    finally:
        db.close()
    flash('Товар удален.', 'info')
    return redirect(url_for('products'))


@app.get('/products/export/<fmt>')
@login_required
def products_export(fmt: str) -> Any:
    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT p.*, c.name AS category_name
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id
            ORDER BY p.id DESC
            """
        ).fetchall()
    finally:
        db.close()
    products_data = [dict(row) for row in rows]
    if fmt == 'csv':
        payload = export_products_csv(products_data)
        return send_file(payload, mimetype='text/csv', as_attachment=True, download_name='products.csv')
    payload = export_products_excel(products_data)
    return send_file(
        payload,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='products.xlsx',
    )


@app.route('/categories', methods=['GET', 'POST'])
@login_required
def categories() -> Any:
    db = get_db()
    try:
        if request.method == 'POST':
            form = CategoryForm.from_request(request)
            if not form.name:
                flash('Название категории обязательно.', 'warning')
            else:
                db.execute(
                    'INSERT INTO categories (name, description) VALUES (?, ?)',
                    (form.name, form.description or None),
                )
                db.commit()
                flash('Категория добавлена.', 'success')
                return redirect(url_for('categories'))
        rows = db.execute('SELECT * FROM categories ORDER BY id DESC').fetchall()
    finally:
        db.close()
    return render_template('categories.html', categories=[dict(row) for row in rows])


@app.post('/categories/<int:category_id>/delete')
@login_required
def category_delete(category_id: int) -> Any:
    db = get_db()
    try:
        db.execute('UPDATE products SET category_id = NULL WHERE category_id = ?', (category_id,))
        db.execute('DELETE FROM categories WHERE id = ?', (category_id,))
        db.commit()
    finally:
        db.close()
    flash('Категория удалена.', 'info')
    return redirect(url_for('categories'))


@app.route('/orders')
@login_required
def orders() -> str:
    status_filter = (request.args.get('status') or '').strip()
    page = int(request.args.get('page', '1') or 1)
    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT o.*, p.name, u.username
            FROM orders o
            JOIN products p ON p.id = o.product_id
            LEFT JOIN users u ON u.user_id = o.user_id
            ORDER BY o.id DESC
            """
        ).fetchall()
    finally:
        db.close()
    items = [dict(row) for row in rows]
    if status_filter:
        items = [row for row in items if row.get('status') == status_filter]
    page_items, pages = paginate(items, page)
    return render_template('orders.html', orders=page_items, page=page, pages=pages, status_filter=status_filter)


@app.post('/orders/<int:order_id>/status')
@login_required
def order_status_update(order_id: int) -> Any:
    status = (request.form.get('status') or '').strip()
    db = get_db()
    try:
        db.execute('UPDATE orders SET status = ? WHERE id = ?', (status, order_id))
        db.commit()
    finally:
        db.close()
    flash(f'Статус заказа #{order_id} обновлен: {status}', 'success')
    return redirect(url_for('orders'))


@app.get('/orders/<int:order_id>')
@login_required
def order_detail(order_id: int) -> str:
    db = get_db()
    try:
        row = db.execute(
            """
            SELECT o.*, p.name, u.username
            FROM orders o
            JOIN products p ON p.id = o.product_id
            LEFT JOIN users u ON u.user_id = o.user_id
            WHERE o.id = ?
            """,
            (order_id,),
        ).fetchone()
    finally:
        db.close()
    return render_template('order_detail.html', order=dict(row) if row else None)


@app.get('/orders/export/<fmt>')
@login_required
def orders_export(fmt: str) -> Any:
    status_filter = (request.args.get('status') or '').strip()
    date_from = (request.args.get('date_from') or '').strip()
    date_to = (request.args.get('date_to') or '').strip()
    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT o.*, p.name, u.username
            FROM orders o
            JOIN products p ON p.id = o.product_id
            LEFT JOIN users u ON u.user_id = o.user_id
            ORDER BY o.id DESC
            """
        ).fetchall()
    finally:
        db.close()
    items = [dict(row) for row in rows]
    if status_filter:
        items = [row for row in items if row.get('status') == status_filter]
    if date_from:
        items = [row for row in items if str(row.get('created_at', ''))[:10] >= date_from]
    if date_to:
        items = [row for row in items if str(row.get('created_at', ''))[:10] <= date_to]
    if fmt == 'csv':
        payload = export_orders_csv(items)
        return send_file(payload, mimetype='text/csv', as_attachment=True, download_name='orders.csv')
    payload = export_orders_excel(items)
    return send_file(
        payload,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='orders.xlsx',
    )


@app.route('/users')
@login_required
def users() -> str:
    page = int(request.args.get('page', '1') or 1)
    q = (request.args.get('q') or '').strip().lower()
    db = get_db()
    try:
        rows = db.execute('SELECT * FROM users ORDER BY created_at DESC').fetchall()
    finally:
        db.close()
    users_data = [dict(row) for row in rows]
    if q:
        users_data = [
            row
            for row in users_data
            if q in str(row.get('user_id', '')).lower() or q in str(row.get('username', '')).lower()
        ]
    page_items, pages = paginate(users_data, page)
    return render_template('users.html', users=page_items, page=page, pages=pages, q=q)


@app.get('/users/<int:user_id>')
@login_required
def user_detail(user_id: int) -> str:
    db = get_db()
    try:
        user = db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)).fetchone()
        orders = db.execute(
            """
            SELECT o.*, p.name
            FROM orders o
            JOIN products p ON p.id = o.product_id
            WHERE o.user_id = ?
            ORDER BY o.id DESC
            """,
            (user_id,),
        ).fetchall()
    finally:
        db.close()
    return render_template(
        'user_detail.html',
        user=dict(user) if user else None,
        orders=[dict(row) for row in orders],
    )


@app.get('/users/export/<fmt>')
@login_required
def users_export(fmt: str) -> Any:
    db = get_db()
    try:
        users_data = [dict(row) for row in db.execute('SELECT * FROM users ORDER BY created_at DESC').fetchall()]
    finally:
        db.close()
    if fmt == 'csv':
        payload = export_users_csv(users_data)
        return send_file(payload, mimetype='text/csv', as_attachment=True, download_name='users.csv')
    payload = export_users_excel(users_data)
    return send_file(
        payload,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='users.xlsx',
    )


@app.route('/delivery_zones', methods=['GET', 'POST'])
@login_required
def delivery_zones() -> Any:
    db = get_db()
    try:
        if request.method == 'POST':
            form = DeliveryZoneForm.from_request(request)
            db.execute(
                'INSERT INTO delivery_zones (zone_name, cost, description) VALUES (?, ?, ?)',
                (form.zone_name, form.cost, form.description or None),
            )
            db.commit()
            flash('Зона доставки добавлена.', 'success')
            return redirect(url_for('delivery_zones'))
        zones = [dict(row) for row in db.execute('SELECT * FROM delivery_zones ORDER BY id DESC').fetchall()]
    finally:
        db.close()
    return render_template('delivery_zones.html', zones=zones)


@app.post('/delivery_zones/<int:zone_id>/delete')
@login_required
def delivery_zone_delete(zone_id: int) -> Any:
    db = get_db()
    try:
        db.execute('DELETE FROM delivery_zones WHERE id = ?', (zone_id,))
        db.commit()
    finally:
        db.close()
    flash('Зона удалена.', 'info')
    return redirect(url_for('delivery_zones'))


@app.get('/reviews')
@login_required
def reviews() -> str:
    page = int(request.args.get('page', '1') or 1)
    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT r.*, p.name AS product_name, u.username
            FROM reviews r
            JOIN products p ON p.id = r.product_id
            LEFT JOIN users u ON u.user_id = r.user_id
            ORDER BY r.id DESC
            """
        ).fetchall()
    finally:
        db.close()
    page_items, pages = paginate([dict(row) for row in rows], page)
    return render_template('reviews.html', reviews=page_items, page=page, pages=pages)


@app.post('/reviews/<int:review_id>/delete')
@login_required
def review_delete(review_id: int) -> Any:
    db = get_db()
    try:
        db.execute('DELETE FROM reviews WHERE id = ?', (review_id,))
        db.commit()
    finally:
        db.close()
    flash('Отзыв удален.', 'info')
    return redirect(url_for('reviews'))


@app.get('/reviews/export/<fmt>')
@login_required
def reviews_export(fmt: str) -> Any:
    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT r.*, p.name AS product_name, u.username
            FROM reviews r
            JOIN products p ON p.id = r.product_id
            LEFT JOIN users u ON u.user_id = r.user_id
            ORDER BY r.id DESC
            """
        ).fetchall()
    finally:
        db.close()
    reviews_data = [dict(row) for row in rows]
    if fmt == 'csv':
        payload = export_reviews_csv(reviews_data)
        return send_file(payload, mimetype='text/csv', as_attachment=True, download_name='reviews.csv')
    payload = export_reviews_excel(reviews_data)
    return send_file(
        payload,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='reviews.xlsx',
    )


@app.get('/payments')
@login_required
def payments() -> str:
    page = int(request.args.get('page', '1') or 1)
    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT p.*, o.user_id
            FROM payments p
            LEFT JOIN orders o ON o.id = p.order_id
            ORDER BY p.id DESC
            """
        ).fetchall()
    finally:
        db.close()
    page_items, pages = paginate([dict(row) for row in rows], page)
    return render_template('payments.html', payments=page_items, page=page, pages=pages)


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings() -> Any:
    env = _env_read()
    if request.method == 'POST':
        for key in EDITABLE_SETTINGS:
            env[key] = (request.form.get(key) or '').strip()
        _env_write(env)
        flash('Настройки сохранены в .env. Перезапустите сервисы.', 'success')
        return redirect(url_for('settings'))
    visible = {k: v for k, v in env.items() if k not in SENSITIVE_KEYS}
    return render_template('settings.html', settings=visible, editable=sorted(EDITABLE_SETTINGS))


@app.get('/sales/report/<period>')
@login_required
def sales_report(period: str) -> Any:
    if period not in {'day', 'week', 'month', 'year'}:
        period = 'month'
    modifier = {'day': '-1 day', 'week': '-7 day', 'month': '-30 day', 'year': '-365 day'}[period]
    db = get_db()
    try:
        summary_row = db.execute(
            """
            SELECT COUNT(*) AS orders_count, COALESCE(SUM(total_price), 0) AS sales_total
            FROM orders
            WHERE datetime(created_at) >= datetime('now', ?)
            """,
            (modifier,),
        ).fetchone()
        timeline_rows = db.execute(
            """
            SELECT substr(created_at, 1, 10) AS day, COALESCE(SUM(total_price), 0) AS amount
            FROM orders
            WHERE datetime(created_at) >= datetime('now', ?)
            GROUP BY substr(created_at, 1, 10)
            ORDER BY day ASC
            """,
            (modifier,),
        ).fetchall()
    finally:
        db.close()
    summary = dict(summary_row) if summary_row else {'orders_count': 0, 'sales_total': 0.0}
    orders_count = int(summary.get('orders_count', 0))
    sales_total = float(summary.get('sales_total', 0.0))
    summary['avg_check'] = sales_total / orders_count if orders_count else 0.0
    timeline = [(str(row['day']), float(row['amount'])) for row in timeline_rows]
    payload = export_sales_report_excel(period=period, summary=summary, timeline=timeline)
    return send_file(
        payload,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'sales_report_{period}.xlsx',
    )


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False)
