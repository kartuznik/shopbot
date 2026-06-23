from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from bot.config import get_settings
from bot.database import (
    add_to_cart,
    clear_cart,
    create_order_from_cart,
    get_categories,
    get_cart_items,
    get_product,
    get_product_rating_summary,
    get_products,
    search_products,
    get_user_orders,
    upsert_user,
)
from bot.keyboards.inline import (
    ORDER_STATUS_LABELS,
    cart_keyboard,
    catalog_categories_keyboard,
    catalog_products_keyboard,
    product_keyboard_with_reviews,
)

router = Router()


def _status_label(status: str) -> str:
    return ORDER_STATUS_LABELS.get(status, status)


async def _notify_admins_new_order(message: Message, order_data: dict) -> None:
    if not message.from_user:
        return
    settings = get_settings()
    order_no = order_data['order_ids'][0] if order_data['order_ids'] else '-'
    username = f"@{message.from_user.username}" if message.from_user.username else str(message.from_user.id)
    items_text = ', '.join(
        f"{item['name']} x{item['quantity']} ({item['line_total']:.2f})" for item in order_data['items']
    )
    text = (
        f"🆕 Новый заказ #{order_no} от {username}\n"
        f"Товары: {items_text}\n"
        f"Сумма: {order_data['total']:.2f}₽"
    )
    for admin_id in settings.ADMIN_IDS:
        try:
            await message.bot.send_message(chat_id=admin_id, text=text)
        except Exception:
            continue


@router.message(CommandStart())
@router.message(Command('catalog'))
async def cmd_catalog(message: Message) -> None:
    if not message.from_user:
        return
    await upsert_user(message.from_user.id, message.from_user.username)
    categories = await get_categories()
    await message.answer('Выберите категорию:', reply_markup=catalog_categories_keyboard(categories))


@router.message(Command('cart'))
async def cmd_cart(message: Message) -> None:
    if not message.from_user:
        return
    items = await get_cart_items(message.from_user.id)
    if not items:
        await message.answer('Корзина пуста.')
        return
    lines = ['Ваша корзина:']
    total = 0.0
    for item in items:
        line_total = float(item['price']) * int(item['quantity'])
        total += line_total
        lines.append(f"- {item['name']} x{item['quantity']} = {line_total:.2f}")
    lines.append(f"Итого: {total:.2f}")
    await message.answer('\n'.join(lines), reply_markup=cart_keyboard())


@router.message(Command('orders'))
async def cmd_orders(message: Message) -> None:
    if not message.from_user:
        return
    orders = await get_user_orders(message.from_user.id)
    if not orders:
        await message.answer('У вас пока нет заказов.')
        return
    lines = ['Ваши заказы:']
    for order in orders[:20]:
        lines.append(
            f"#{order['id']} | {order['name']} x{order['quantity']} | "
            f"{order['total_price']:.2f} | {_status_label(order['status'])}"
        )
    await message.answer('\n'.join(lines))


@router.message(Command('search'))
async def cmd_search(message: Message) -> None:
    parts = (message.text or '').split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ''
    if not query:
        await message.answer('Использование: /search <запрос>')
        return
    products = await search_products(query)
    if not products:
        await message.answer(f'По запросу "{query}" ничего не найдено.')
        return
    await message.answer(
        f'Найдено товаров: {len(products)}',
        reply_markup=catalog_products_keyboard(products, scope='search'),
    )


@router.callback_query(F.data.startswith('catalog_category:'))
async def callback_catalog_category(callback: CallbackQuery) -> None:
    category_id = int(callback.data.split(':', maxsplit=1)[1])
    products = await get_products(category_id=category_id)
    await callback.answer()
    if not products:
        await callback.message.answer('В этой категории пока нет товаров.')
        return
    await callback.message.answer(
        'Товары категории:',
        reply_markup=catalog_products_keyboard(products, scope=f'cat-{category_id}'),
    )


@router.callback_query(F.data == 'catalog_all')
async def callback_catalog_all(callback: CallbackQuery) -> None:
    products = await get_products()
    await callback.answer()
    if not products:
        await callback.message.answer('Каталог пока пуст. Загляните позже.')
        return
    await callback.message.answer('Все товары:', reply_markup=catalog_products_keyboard(products, scope='all'))


@router.callback_query(F.data == 'catalog_search')
async def callback_catalog_search(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer('Введите команду: /search <запрос>')


@router.callback_query(F.data.startswith('product:'))
async def callback_product(callback: CallbackQuery) -> None:
    parts = (callback.data or '').split(':')
    product_id = int(parts[1])
    scope = parts[2] if len(parts) > 2 else 'all'
    product = await get_product(product_id)
    if not product:
        await callback.answer('Товар не найден', show_alert=True)
        return
    text = (
        f"{product['name']}\n\n"
        f"{product['description'] or '-'}\n"
        f"Цена: {float(product['price']):.2f}"
    )
    rating = await get_product_rating_summary(product_id)
    if rating['reviews_count'] > 0:
        text += f"\nРейтинг: ⭐ {rating['avg_rating']:.1f}/5 ({rating['reviews_count']} отзывов)"
    await callback.answer()
    if product.get('photo_id'):
        await callback.message.answer_photo(
            photo=product['photo_id'],
            caption=text,
            reply_markup=product_keyboard_with_reviews(
                product_id=product_id,
                scope=scope,
                has_reviews=rating['reviews_count'] > 0,
            ),
        )
    else:
        await callback.message.answer(
            text,
            reply_markup=product_keyboard_with_reviews(
                product_id=product_id,
                scope=scope,
                has_reviews=rating['reviews_count'] > 0,
            ),
        )


@router.callback_query(F.data == 'back_catalog')
async def callback_back_catalog(callback: CallbackQuery) -> None:
    await callback.answer()
    categories = await get_categories()
    await callback.message.answer('Выберите категорию:', reply_markup=catalog_categories_keyboard(categories))


@router.callback_query(F.data.startswith('back_scope:'))
async def callback_back_scope(callback: CallbackQuery) -> None:
    scope = (callback.data or '').split(':', maxsplit=1)[1]
    await callback.answer()
    if scope == 'search':
        await callback.message.answer('Повторите поиск командой: /search <запрос>')
        return
    if scope == 'all':
        products = await get_products()
    elif scope.startswith('cat-'):
        products = await get_products(category_id=int(scope.replace('cat-', '', 1)))
    else:
        products = await get_products()
    if not products:
        await callback.message.answer('Каталог пуст.')
        return
    await callback.message.answer('Товары:', reply_markup=catalog_products_keyboard(products, scope=scope))


@router.callback_query(F.data == 'open_cart')
async def callback_open_cart(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    items = await get_cart_items(callback.from_user.id)
    await callback.answer()
    if not items:
        await callback.message.answer('Корзина пуста.')
        return
    lines = ['Ваша корзина:']
    total = 0.0
    for item in items:
        line_total = float(item['price']) * int(item['quantity'])
        total += line_total
        lines.append(f"- {item['name']} x{item['quantity']} = {line_total:.2f}")
    lines.append(f"Итого: {total:.2f}")
    await callback.message.answer('\n'.join(lines), reply_markup=cart_keyboard())


@router.callback_query(F.data.startswith('buy:'))
async def callback_buy(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    product_id = int((callback.data or '').split(':')[1])
    await add_to_cart(callback.from_user.id, product_id, 1)
    await callback.answer('Товар добавлен в корзину')


@router.callback_query(F.data == 'clear_cart')
async def callback_clear_cart(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    await clear_cart(callback.from_user.id)
    await callback.answer('Корзина очищена')
    await callback.message.answer('Корзина очищена.')


@router.callback_query(F.data == 'checkout_cart')
async def callback_checkout(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    count = await create_order_from_cart(callback.from_user.id)
    await callback.answer()
    if count['count'] == 0:
        await callback.message.answer('Корзина пуста, нечего оформлять.')
    else:
        await callback.message.answer(f"✅ Заказ оформлен. Позиции: {count['count']}")
        await _notify_admins_new_order(callback.message, count)
