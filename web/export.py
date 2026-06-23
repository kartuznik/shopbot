from __future__ import annotations

import csv
import tempfile
from datetime import datetime
from io import BytesIO, StringIO
from typing import Any

import matplotlib
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage

matplotlib.use('Agg')
from matplotlib import pyplot as plt


def _csv_bytes(headers: list[str], rows: list[list[Any]]) -> BytesIO:
    text_buffer = StringIO()
    writer = csv.writer(text_buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return BytesIO(text_buffer.getvalue().encode('utf-8-sig'))


def _xlsx_bytes(sheet_name: str, headers: list[str], rows: list[list[Any]]) -> BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(headers)
    for row in rows:
        ws.append(row)
    payload = BytesIO()
    wb.save(payload)
    payload.seek(0)
    return payload


def export_products_csv(products: list[dict[str, Any]]) -> BytesIO:
    rows = [
        [
            item.get('id'),
            item.get('name'),
            item.get('description') or '',
            item.get('price'),
            item.get('category_name') or '',
            item.get('photo_id') or '',
            item.get('created_at') or '',
        ]
        for item in products
    ]
    return _csv_bytes(
        ['ID', 'Название', 'Описание', 'Цена', 'Категория', 'Фото', 'Создан'],
        rows,
    )


def export_products_excel(products: list[dict[str, Any]]) -> BytesIO:
    rows = [
        [
            item.get('id'),
            item.get('name'),
            item.get('description') or '',
            item.get('price'),
            item.get('category_name') or '',
            item.get('photo_id') or '',
            item.get('created_at') or '',
        ]
        for item in products
    ]
    return _xlsx_bytes(
        'Товары',
        ['ID', 'Название', 'Описание', 'Цена', 'Категория', 'Фото', 'Создан'],
        rows,
    )


def export_orders_csv(orders: list[dict[str, Any]]) -> BytesIO:
    rows = [
        [
            o.get('id'),
            o.get('user_id'),
            o.get('username') or '',
            o.get('name') or '',
            o.get('quantity'),
            o.get('total_price'),
            o.get('shipping_cost'),
            o.get('address') or '',
            o.get('status'),
            o.get('created_at'),
        ]
        for o in orders
    ]
    return _csv_bytes(
        ['ID', 'User ID', 'Username', 'Товар', 'Кол-во', 'Сумма', 'Доставка', 'Адрес', 'Статус', 'Создан'],
        rows,
    )


def export_orders_excel(orders: list[dict[str, Any]]) -> BytesIO:
    rows = [
        [
            o.get('id'),
            o.get('user_id'),
            o.get('username') or '',
            o.get('name') or '',
            o.get('quantity'),
            o.get('total_price'),
            o.get('shipping_cost'),
            o.get('address') or '',
            o.get('status'),
            o.get('created_at'),
        ]
        for o in orders
    ]
    return _xlsx_bytes(
        'Заказы',
        ['ID', 'User ID', 'Username', 'Товар', 'Кол-во', 'Сумма', 'Доставка', 'Адрес', 'Статус', 'Создан'],
        rows,
    )


def export_users_csv(users: list[dict[str, Any]]) -> BytesIO:
    rows = [[u.get('user_id'), u.get('username') or '', u.get('created_at'), u.get('last_activity')] for u in users]
    return _csv_bytes(['User ID', 'Username', 'Создан', 'Активность'], rows)


def export_users_excel(users: list[dict[str, Any]]) -> BytesIO:
    rows = [[u.get('user_id'), u.get('username') or '', u.get('created_at'), u.get('last_activity')] for u in users]
    return _xlsx_bytes('Пользователи', ['User ID', 'Username', 'Создан', 'Активность'], rows)


def export_reviews_csv(reviews: list[dict[str, Any]]) -> BytesIO:
    rows = [
        [
            r.get('id'),
            r.get('product_id'),
            r.get('product_name') or '',
            r.get('order_id'),
            r.get('user_id'),
            r.get('username') or '',
            r.get('rating'),
            r.get('comment') or '',
            r.get('created_at'),
        ]
        for r in reviews
    ]
    return _csv_bytes(
        ['ID', 'Product ID', 'Товар', 'Order ID', 'User ID', 'Username', 'Рейтинг', 'Комментарий', 'Создан'],
        rows,
    )


def export_reviews_excel(reviews: list[dict[str, Any]]) -> BytesIO:
    rows = [
        [
            r.get('id'),
            r.get('product_id'),
            r.get('product_name') or '',
            r.get('order_id'),
            r.get('user_id'),
            r.get('username') or '',
            r.get('rating'),
            r.get('comment') or '',
            r.get('created_at'),
        ]
        for r in reviews
    ]
    return _xlsx_bytes(
        'Отзывы',
        ['ID', 'Product ID', 'Товар', 'Order ID', 'User ID', 'Username', 'Рейтинг', 'Комментарий', 'Создан'],
        rows,
    )


def export_sales_report_excel(
    period: str,
    summary: dict[str, Any],
    timeline: list[tuple[str, float]],
) -> BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = 'Отчет'

    ws.append(['Период', period])
    ws.append(['Сформирован', datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
    ws.append(['Заказов', summary.get('orders_count', 0)])
    ws.append(['Выручка', summary.get('sales_total', 0.0)])
    ws.append(['Средний чек', summary.get('avg_check', 0.0)])
    ws.append([])
    ws.append(['Дата', 'Выручка'])
    for date, amount in timeline:
        ws.append([date, amount])

    if timeline:
        dates = [row[0] for row in timeline]
        values = [float(row[1]) for row in timeline]
        plt.figure(figsize=(9, 3))
        plt.plot(dates, values, marker='o')
        plt.title(f'Продажи за {period}')
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            chart_path = tmp.name
        plt.savefig(chart_path)
        plt.close()
        ws.add_image(XLImage(chart_path), 'D2')

    payload = BytesIO()
    wb.save(payload)
    payload.seek(0)
    return payload
