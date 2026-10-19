from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

from bot import demo_catalog, demo_flow
from bot.keyboards import reply
from bot.config import get_settings
from bot.database import (
    get_last_group_for_products,
    get_order_group,
    init_db,
    reset_demo_orders,
    save_payment,
    sync_demo_products,
    upsert_user,
)
from bot.demo_catalog import (
    CatalogError,
    InvalidTransition,
    PurchaseEvent,
    PurchaseState,
    advance,
    can_advance,
    card_text_fallback,
    image_file,
    load_catalog,
    parse_catalog,
    pickup_time,
    plural_positions,
    render_confirmation,
    render_reminder,
)
from bot.handlers import demo as demo_handlers

CATALOG_PATH = Path(__file__).resolve().parents[1] / 'demo' / 'catalog.yaml'

EXPECTED_ITEMS = {
    'bun_caramel.jpg': ('Выпечка', 'Булочка с карамелью', 320),
    'tart_caramel_nut.jpg': ('Выпечка', 'Карамельный тарт с грецким орехом', 420),
    'tart_dome.jpg': ('Выпечка', 'Тарталетка с глянцевым куполом', 480),
    'tart_stripe.jpg': ('Выпечка', 'Тарт с карамельными полосками', 390),
    'bun_berliner.jpg': ('Выпечка', 'Берлинеры с начинкой', 350),
    'raf_coconut.jpg': ('Напитки', 'Раф кокосовый', 320),
    'coffee_iced_layered.jpg': ('Напитки', 'Айс-кофе слоями', 290),
    'coffee_affogato.jpg': ('Напитки', 'Аффогато с пломбиром', 350),
}

PAY_URL = 'https://yoomoney.test/checkout/pid-1'
CUSTOMER_ID = 42
ADMIN_ID = 1


@pytest.fixture(scope='module')
def catalog() -> demo_catalog.DemoCatalog:
    return load_catalog(CATALOG_PATH)


@pytest.fixture
def demo_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, catalog: demo_catalog.DemoCatalog):
    db_path = tmp_path / 'demo.db'
    monkeypatch.setenv('DB_PATH', str(db_path))
    monkeypatch.setenv('ADMIN_IDS', str(ADMIN_ID))
    monkeypatch.setenv('YUKASSA_SHOP_ID', '')
    monkeypatch.setenv('YUKASSA_SECRET_KEY', '')
    get_settings.cache_clear()
    asyncio.run(init_db())
    product_ids = asyncio.run(sync_demo_products(catalog.items))
    demo_flow.activate(catalog, product_ids)
    asyncio.run(upsert_user(CUSTOMER_ID, 'customer'))
    yield db_path, product_ids
    demo_flow.activate(catalog, {})
    get_settings.cache_clear()


def _add(product_ids: dict[str, int], *item_ids: str) -> demo_flow.CartSummary:
    summary = None
    for item_id in item_ids:
        summary = asyncio.run(demo_flow.add_item(CUSTOMER_ID, product_ids[item_id]))
    return summary


def _rows(db_path: Path, sql: str, *params: object) -> list[tuple]:
    db = sqlite3.connect(db_path)
    try:
        return db.execute(sql, params).fetchall()
    finally:
        db.close()


def _fake_yookassa() -> AsyncMock:
    async def create(order_id: int, amount: float, description: str) -> tuple[str, str]:
        await save_payment(order_id=order_id, amount=amount, payment_url=PAY_URL, payment_id='pid-1')
        return PAY_URL, 'pid-1'

    return AsyncMock(side_effect=create)


# --- каталог ---------------------------------------------------------------


def test_catalog_has_both_sections_and_eight_items(catalog):
    assert catalog.sections() == ['Выпечка', 'Напитки']
    assert len(catalog.items) == 8
    assert len(catalog.items_in('Выпечка')) == 5
    assert len(catalog.items_in('Напитки')) == 3


def test_catalog_matches_owner_spec(catalog):
    actual = {item.image_path: (item.section, item.title, item.price) for item in catalog.items}
    assert actual == EXPECTED_ITEMS
    assert catalog.shop_name == 'Пекарня Тёплое тесто и кофе'
    assert all(item.emoji and item.description for item in catalog.items)
    for fact in ('ул. Примерная, 123', '08:00–20:00', '+7 (999) 123-45-67', 'Мы печём с любовью каждое утро!'):
        assert fact in catalog.about


def test_storefront_texts_do_not_mention_demo(catalog):
    texts = [catalog.shop_name, catalog.greeting, catalog.about, catalog.reminder_template, catalog.confirmation_template]
    texts += [f'{item.title} {item.description}' for item in catalog.items]
    for text in texts:
        assert 'демо' not in text.lower()
        assert 'shopbot' not in text.lower()


def test_catalog_rejects_missing_fields_and_bad_placeholders():
    base = {
        'shop_name': 'X',
        'greeting': 'hi',
        'about': 'адрес',
        'reminder_template': '{order_id}',
        'confirmation_template': '{order_id}',
        'items': [
            {'id': 'a', 'section': 'S', 'title': 'T', 'description': 'D', 'price': 1, 'image_path': 'a.jpg', 'emoji': '☕'}
        ],
    }
    assert len(parse_catalog(base).items) == 1

    without_about = {key: value for key, value in base.items() if key != 'about'}
    with pytest.raises(CatalogError, match='нет полей about'):
        parse_catalog(without_about)
    with pytest.raises(CatalogError, match='нет полей about'):
        parse_catalog({**base, 'about': ''})
    with pytest.raises(CatalogError, match='price'):
        parse_catalog({**base, 'items': [{**base['items'][0], 'price': None}]})
    with pytest.raises(CatalogError, match='плейсхолдер'):
        parse_catalog({**base, 'confirmation_template': 'заказ {order_number}'})
    with pytest.raises(CatalogError, match='image_path'):
        parse_catalog({**base, 'items': [{**base['items'][0], 'image_path': '../secret.jpg'}]})
    with pytest.raises(CatalogError, match='повторяются'):
        parse_catalog({**base, 'items': base['items'] * 2})


# --- картинки и фолбэк -----------------------------------------------------


def test_missing_image_falls_back_to_text(catalog, tmp_path):
    item = catalog.get('bun_caramel')
    assert image_file(item, images_dir=tmp_path) is None
    fallback = card_text_fallback(item)
    assert item.emoji in fallback and item.title in fallback and '320 ₽' in fallback

    (tmp_path / item.image_path).write_bytes(b'jpg')
    assert image_file(item, images_dir=tmp_path) == tmp_path / item.image_path


def _fake_message(send_photo: AsyncMock) -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=CUSTOMER_ID),
        bot=SimpleNamespace(send_photo=send_photo),
        answer=AsyncMock(),
    )


def test_send_card_without_file_sends_text(catalog, tmp_path, monkeypatch):
    monkeypatch.setattr(demo_catalog, 'DEFAULT_IMAGES_DIR', tmp_path)
    message = _fake_message(AsyncMock())
    asyncio.run(demo_handlers.send_card(message, catalog.get('raf_coconut')))
    message.bot.send_photo.assert_not_awaited()
    assert '🥥' in message.answer.await_args.args[0]


def test_send_card_with_file_sends_photo_and_survives_telegram_error(catalog, tmp_path, monkeypatch):
    monkeypatch.setattr(demo_catalog, 'DEFAULT_IMAGES_DIR', tmp_path)
    item = catalog.get('raf_coconut')
    (tmp_path / item.image_path).write_bytes(b'\xff\xd8fake')

    message = _fake_message(AsyncMock())
    asyncio.run(demo_handlers.send_card(message, item))
    kwargs = message.bot.send_photo.await_args.kwargs
    assert item.title in kwargs['caption']
    message.answer.assert_not_awaited()

    broken = _fake_message(AsyncMock(side_effect=RuntimeError('Bad Request: IMAGE_PROCESS_FAILED')))
    asyncio.run(demo_handlers.send_card(broken, item))
    assert item.title in broken.answer.await_args.args[0]


# --- кнопки ----------------------------------------------------------------


def _buttons(markup) -> list[list[tuple[str, str | None]]]:
    return [[(button.text, button.callback_data) for button in row] for row in markup.inline_keyboard]


def test_menu_shows_only_categories_and_cart_when_full(catalog):
    rows = _buttons(demo_handlers.menu_keyboard(catalog))
    assert rows == [[('🥐 Выпечка', 'dm:sec:Выпечка')], [('☕ Напитки', 'dm:sec:Напитки')]]

    cart = demo_flow.CartSummary(lines=({'name': 'x'},), quantity=3, total=1090.0)
    full = _buttons(demo_handlers.menu_keyboard(catalog, cart))
    assert full[0] == [('🛒 Корзина · 3 шт · 1 090 ₽', 'dm:cart')]
    assert full[1:] == rows
    assert all('dm:item:' not in (button[1] or '') for row in full for button in row)


def test_every_screen_has_cart_or_back(catalog):
    item = catalog.get('tart_dome')
    full = demo_flow.CartSummary(lines=({'name': 'x'},), quantity=1, total=480.0)
    empty = demo_flow.CartSummary(lines=(), quantity=0, total=0.0)

    card = _buttons(demo_handlers.card_keyboard(item))
    assert card[0] == [('➕ В корзину', 'dm:add:tart_dome'), ('🛒 Оформить заказ', 'dm:buy:tart_dome')]
    assert ('🛒 Корзина', 'dm:cart') in card[1] and ('⬅️ Назад', 'dm:sec:Выпечка') in card[1]

    assert ('🗑 Очистить корзину', 'dm:cart_clear') in sum(_buttons(demo_handlers.cart_keyboard(full)), [])
    assert _buttons(demo_handlers.cart_keyboard(empty)) == [[('⬅️ Назад в меню', 'dm:menu')]]

    receipt = sum(_buttons(demo_handlers.receipt_keyboard(7, 1090.0, None)), [])
    assert receipt[0] == ('💳 Оплатить 1 090 ₽', 'dm:check:7')
    assert ('🗑 Очистить корзину', 'dm:cart_clear') in receipt
    assert ('⬅️ Назад в корзину', 'dm:cart') in receipt


@pytest.mark.parametrize(
    ('count', 'expected'),
    [(1, '1 позиция'), (2, '2 позиции'), (5, '5 позиций'), (11, '11 позиций'), (21, '21 позиция'), (22, '22 позиции')],
)
def test_positions_plural(count, expected):
    assert plural_positions(count) == expected


# --- машина состояний ------------------------------------------------------


def test_purchase_state_machine_cart_path():
    state = PurchaseState.MENU
    for event, expected in [
        (PurchaseEvent.OPEN_CARD, PurchaseState.CARD),
        (PurchaseEvent.ADD_TO_CART, PurchaseState.CARD),
        (PurchaseEvent.OPEN_CART, PurchaseState.CART),
        (PurchaseEvent.CHECKOUT, PurchaseState.CHECKOUT),
        (PurchaseEvent.PAY, PurchaseState.AWAITING_PAYMENT),
        (PurchaseEvent.PAID, PurchaseState.CONFIRMED),
    ]:
        state = advance(state, event)
        assert state is expected


def test_purchase_state_machine_quick_checkout_from_card():
    state = advance(PurchaseState.CARD, PurchaseEvent.CHECKOUT)
    assert state is PurchaseState.CHECKOUT
    assert advance(state, PurchaseEvent.OPEN_CART) is PurchaseState.CART  # «Назад в корзину»


def test_purchase_state_machine_blocks_skipped_steps():
    with pytest.raises(InvalidTransition):
        advance(PurchaseState.MENU, PurchaseEvent.ADD_TO_CART)
    with pytest.raises(InvalidTransition):
        advance(PurchaseState.MENU, PurchaseEvent.CHECKOUT)
    with pytest.raises(InvalidTransition):
        advance(PurchaseState.CART, PurchaseEvent.PAY)
    with pytest.raises(InvalidTransition):
        advance(PurchaseState.CHECKOUT, PurchaseEvent.PAID)
    assert not can_advance(PurchaseState.CONFIRMED, PurchaseEvent.PAID)


def test_category_screen_lists_only_its_items(catalog):
    bakery = _buttons(demo_handlers.category_keyboard(catalog, 'Выпечка'))
    titles = [row[0][0] for row in bakery if row[0][1] and row[0][1].startswith('dm:item:')]
    assert titles == [demo_handlers.item_label(item) for item in catalog.items_in('Выпечка')]
    assert len(titles) == 5
    assert all(row == [('➕ В корзину', f'dm:add:{item.id}')] for row, item in zip(bakery[1::2], catalog.items_in('Выпечка')))
    assert bakery[-1] == [('⬅️ Назад к категориям', 'dm:menu')]
    assert 'Раф кокосовый' not in ' '.join(titles)

    drinks = _buttons(demo_handlers.category_keyboard(catalog, 'Напитки'))
    drink_ids = [row[0][1] for row in drinks if row[0][1] and row[0][1].startswith('dm:item:')]
    assert drink_ids == [f'dm:item:{item.id}' for item in catalog.items_in('Напитки')]
    assert len(drink_ids) == 3


def test_category_round_trip():
    state = advance(PurchaseState.MENU, PurchaseEvent.OPEN_CATEGORY)
    assert state is PurchaseState.CATEGORY
    assert advance(state, PurchaseEvent.ADD_TO_CART) is PurchaseState.CATEGORY
    assert advance(state, PurchaseEvent.OPEN_CARD) is PurchaseState.CARD
    assert advance(PurchaseState.CARD, PurchaseEvent.OPEN_CATEGORY) is PurchaseState.CATEGORY
    assert advance(PurchaseState.CATEGORY, PurchaseEvent.OPEN_MENU) is PurchaseState.MENU


@pytest.mark.parametrize('state', list(PurchaseState))
def test_menu_card_and_cart_reachable_from_any_state(state):
    assert advance(state, PurchaseEvent.OPEN_MENU) is PurchaseState.MENU
    assert advance(state, PurchaseEvent.OPEN_CATEGORY) is PurchaseState.CATEGORY
    assert advance(state, PurchaseEvent.OPEN_CARD) is PurchaseState.CARD
    assert advance(state, PurchaseEvent.OPEN_CART) is PurchaseState.CART


# --- шаблоны ---------------------------------------------------------------


@pytest.mark.parametrize(
    ('ordered', 'expected'),
    [('10:00', '10:30'), ('10:01', '10:35'), ('10:05', '10:35'), ('23:47', '00:20')],
)
def test_pickup_is_plus_30_rounded_up_to_5(ordered, expected):
    hours, minutes = map(int, ordered.split(':'))
    assert pickup_time(datetime(2026, 10, 16, hours, minutes, 12 if minutes % 5 else 0)).strftime('%H:%M') == expected


def test_confirmation_and_reminder_render_multiline_items(catalog):
    pickup = datetime(2026, 10, 16, 10, 35)
    items = '🥐 Булочка с карамелью × 2 — 640 ₽\n🥥 Раф кокосовый × 1 — 320 ₽'
    confirmation = render_confirmation(catalog, 7, items, 960.0, pickup)
    reminder = render_reminder(catalog, 7, items, 960.0, pickup)
    for text in (confirmation, reminder):
        assert '#7' in text and items in text and '10:35' in text and catalog.shop_name in text
    assert 'Итого: 960 ₽' in confirmation
    assert 'скучает и ждёт подтверждения времени' in reminder


# --- корзина ---------------------------------------------------------------


def test_add_several_items_and_view_cart(catalog, demo_db):
    _, product_ids = demo_db
    assert asyncio.run(demo_flow.cart_summary(CUSTOMER_ID)).empty
    assert demo_handlers.cart_text(catalog, asyncio.run(demo_flow.cart_summary(CUSTOMER_ID))) == '🛒 Корзина пуста'

    cart = _add(product_ids, 'bun_caramel', 'raf_coconut', 'bun_caramel')
    assert cart.quantity == 3 and cart.total == 960.0
    assert {line['name']: line['quantity'] for line in cart.lines} == {'Булочка с карамелью': 2, 'Раф кокосовый': 1}

    text = demo_handlers.cart_text(catalog, cart)
    assert '🥐 Булочка с карамелью × 2 — 640 ₽' in text
    assert '🥥 Раф кокосовый × 1 — 320 ₽' in text
    assert 'Итого: 3 позиции на 960 ₽' in text


def test_added_message_reports_cart_totals(catalog, demo_db):
    _, product_ids = demo_db
    cart = _add(product_ids, 'tart_dome', 'raf_coconut')
    assert demo_handlers.added_text(catalog.get('raf_coconut'), cart) == (
        '✅ Добавлено: Раф кокосовый. В корзине: 2 позиции на 800 ₽'
    )


def test_clear_cart_empties_and_cancels_open_receipt(catalog, demo_db):
    db_path, product_ids = demo_db
    _add(product_ids, 'bun_caramel', 'coffee_affogato')
    receipt = asyncio.run(demo_flow.checkout(catalog, CUSTOMER_ID, None))

    asyncio.run(demo_flow.clear_user_cart(CUSTOMER_ID))

    assert asyncio.run(demo_flow.cart_summary(CUSTOMER_ID)).empty
    assert _rows(db_path, 'SELECT DISTINCT status FROM orders WHERE group_id = ?', receipt.group_id) == [('cancelled',)]
    assert _rows(db_path, 'SELECT status FROM payments WHERE order_id = ?', receipt.group_id) == [('cancelled',)]
    assert asyncio.run(demo_flow.confirm_payment(catalog, receipt.group_id, lambda _pid: 'succeeded')) is None


# --- оформление и оплата ---------------------------------------------------


def test_checkout_empty_cart_returns_none(catalog, demo_db):
    assert asyncio.run(demo_flow.checkout(catalog, CUSTOMER_ID, None)) is None


def test_multi_item_checkout_is_one_order_one_payment(catalog, demo_db):
    db_path, product_ids = demo_db
    _add(product_ids, 'bun_caramel', 'bun_caramel', 'tart_dome', 'coffee_iced_layered')

    receipt = asyncio.run(demo_flow.checkout(catalog, CUSTOMER_ID, None))

    assert receipt.simulated and receipt.quantity == 4 and receipt.total == 1410.0
    lines = _rows(db_path, 'SELECT id, quantity, total_price, status FROM orders WHERE group_id = ? ORDER BY id', receipt.group_id)
    assert sorted((qty, total, status) for _, qty, total, status in lines) == [
        (1, 290.0, 'new'),
        (1, 480.0, 'new'),
        (2, 640.0, 'new'),
    ]
    assert lines[0][0] == receipt.group_id
    assert _rows(db_path, 'SELECT order_id, amount, status FROM payments') == [(receipt.group_id, 1410.0, 'pending')]
    text = demo_handlers.receipt_text(receipt)
    assert f'🧾 Заказ #{receipt.group_id}' in text and 'Итого: 4 позиции на 1 410 ₽' in text
    assert f'Самовывоз к {receipt.pickup.strftime("%H:%M")}' in text
    # Корзина живёт до оплаты.
    assert asyncio.run(demo_flow.cart_summary(CUSTOMER_ID)).quantity == 4


def test_simulated_payment_confirms_group_and_clears_cart(catalog, demo_db):
    db_path, product_ids = demo_db
    _add(product_ids, 'tart_stripe', 'raf_coconut')
    receipt = asyncio.run(demo_flow.checkout(catalog, CUSTOMER_ID, None))

    def kassa_must_not_be_called(_pid: str) -> str:
        raise AssertionError('имитация оплаты не должна ходить в кассу')

    confirmation = asyncio.run(demo_flow.confirm_payment(catalog, receipt.group_id, kassa_must_not_be_called))

    assert confirmation is not None and confirmation.user_id == CUSTOMER_ID
    assert f'#{receipt.group_id}' in confirmation.text
    assert '🍰 Тарт с карамельными полосками × 1 — 390 ₽' in confirmation.text
    assert '🥥 Раф кокосовый × 1 — 320 ₽' in confirmation.text
    assert 'Итого: 710 ₽' in confirmation.text
    assert receipt.pickup.strftime('%H:%M') in confirmation.text
    assert _rows(db_path, 'SELECT DISTINCT status FROM orders WHERE group_id = ?', receipt.group_id) == [('paid',)]
    assert _rows(db_path, 'SELECT status FROM payments WHERE order_id = ?', receipt.group_id) == [('succeeded',)]
    assert asyncio.run(demo_flow.cart_summary(CUSTOMER_ID)).empty


def test_multi_item_purchase_with_mocked_yookassa(catalog, demo_db):
    db_path, product_ids = demo_db
    _add(product_ids, 'bun_berliner', 'coffee_affogato', 'coffee_affogato')
    yookassa = _fake_yookassa()

    receipt = asyncio.run(demo_flow.checkout(catalog, CUSTOMER_ID, yookassa))
    assert receipt.payment_url == PAY_URL and not receipt.simulated
    assert yookassa.await_args.args[:2] == (receipt.group_id, 1050.0)

    assert asyncio.run(demo_flow.confirm_payment(catalog, receipt.group_id, lambda _pid: 'pending')) is None
    assert _rows(db_path, 'SELECT DISTINCT status FROM orders WHERE group_id = ?', receipt.group_id) == [('new',)]
    assert not asyncio.run(demo_flow.cart_summary(CUSTOMER_ID)).empty

    checked: list[str] = []

    def kassa_status(payment_id: str) -> str:
        checked.append(payment_id)
        return 'succeeded'

    confirmation = asyncio.run(demo_flow.confirm_payment(catalog, receipt.group_id, kassa_status))
    assert checked == ['pid-1'] and confirmation is not None
    assert _rows(db_path, 'SELECT DISTINCT status FROM orders WHERE group_id = ?', receipt.group_id) == [('paid',)]
    assert asyncio.run(demo_flow.cart_summary(CUSTOMER_ID)).empty


def test_repeated_checkout_cancels_previous_receipt(catalog, demo_db):
    db_path, product_ids = demo_db
    _add(product_ids, 'bun_caramel')
    first = asyncio.run(demo_flow.checkout(catalog, CUSTOMER_ID, None))
    _add(product_ids, 'raf_coconut')
    second = asyncio.run(demo_flow.checkout(catalog, CUSTOMER_ID, None))

    assert second.group_id != first.group_id and second.quantity == 2
    assert _rows(db_path, 'SELECT DISTINCT status FROM orders WHERE group_id = ?', first.group_id) == [('cancelled',)]
    assert _rows(db_path, 'SELECT DISTINCT status FROM orders WHERE group_id = ?', second.group_id) == [('new',)]


def test_sync_demo_products_is_idempotent(catalog, demo_db):
    _, product_ids = demo_db
    assert len(product_ids) == 8
    assert asyncio.run(sync_demo_products(catalog.items)) == product_ids


# --- команды владельца -----------------------------------------------------


def _command_message(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        bot=SimpleNamespace(send_message=AsyncMock()),
        answer=AsyncMock(),
    )


def test_demo_commands_are_owner_only(demo_db):
    stranger = _command_message(CUSTOMER_ID)
    asyncio.run(demo_handlers.cmd_demo_remind(stranger))
    asyncio.run(demo_handlers.cmd_demo_reset(stranger, AsyncMock()))
    assert [call.args[0] for call in stranger.answer.await_args_list] == ['Недостаточно прав.'] * 2
    stranger.bot.send_message.assert_not_awaited()


def test_demo_remind_lists_whole_last_order(catalog, demo_db):
    _, product_ids = demo_db
    _add(product_ids, 'tart_stripe', 'raf_coconut', 'raf_coconut')
    receipt = asyncio.run(demo_flow.checkout(catalog, CUSTOMER_ID, None))

    owner = _command_message(ADMIN_ID)
    asyncio.run(demo_handlers.cmd_demo_remind(owner))
    kwargs = owner.bot.send_message.await_args.kwargs
    assert kwargs['chat_id'] == CUSTOMER_ID
    assert f'#{receipt.group_id}' in kwargs['text']
    assert '🍰 Тарт с карамельными полосками × 1 — 390 ₽' in kwargs['text']
    assert '🥥 Раф кокосовый × 2 — 640 ₽' in kwargs['text']
    assert 'скучает и ждёт подтверждения времени' in kwargs['text']


def test_demo_remind_skips_cancelled_receipts(catalog, demo_db):
    _, product_ids = demo_db
    _add(product_ids, 'bun_caramel')
    paid = asyncio.run(demo_flow.checkout(catalog, CUSTOMER_ID, None))
    asyncio.run(demo_flow.confirm_payment(catalog, paid.group_id, lambda _pid: None))
    _add(product_ids, 'raf_coconut')
    asyncio.run(demo_flow.checkout(catalog, CUSTOMER_ID, None))
    asyncio.run(demo_flow.clear_user_cart(CUSTOMER_ID))

    assert asyncio.run(get_last_group_for_products(product_ids.values())) == paid.group_id


def test_demo_reset_clears_only_storefront_orders(catalog, demo_db):
    db_path, product_ids = demo_db
    _add(product_ids, 'bun_berliner', 'raf_coconut')
    receipt = asyncio.run(demo_flow.checkout(catalog, CUSTOMER_ID, None))

    db = sqlite3.connect(db_path)
    try:
        db.execute("INSERT INTO products (name, price) VALUES ('Чужой товар', 10)")
        foreign_product = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        db.execute(
            "INSERT INTO orders (user_id, product_id, quantity, total_price) VALUES (?, ?, 1, 10)",
            (CUSTOMER_ID, foreign_product),
        )
        db.commit()
    finally:
        db.close()

    owner = _command_message(ADMIN_ID)
    state = AsyncMock()
    asyncio.run(demo_handlers.cmd_demo_reset(owner, state))
    state.clear.assert_awaited_once()
    assert 'заказов 2, платежей 1' in owner.answer.await_args.args[0]
    assert 'позиций в корзинах 2' in owner.answer.await_args.args[0]
    assert asyncio.run(get_order_group(receipt.group_id)) is None
    assert asyncio.run(reset_demo_orders(product_ids.values()))['orders'] == 0
    assert _rows(db_path, 'SELECT COUNT(*) FROM orders WHERE product_id = ?', foreign_product) == [(1,)]


# --- постоянное нижнее меню ------------------------------------------------

BUTTON_HANDLERS = {
    reply.MENU: 'button_menu',
    reply.CART: 'button_cart',
    reply.CLEAR_CART: 'button_clear_cart',
    reply.ABOUT: 'button_about',
    reply.HELP: 'button_help',
}
BUTTON_HANDLER_NAMES = {*BUTTON_HANDLERS.values(), 'button_pay'}


def _customer() -> SimpleNamespace:
    return SimpleNamespace(id=CUSTOMER_ID, username='customer')


def _chat_message(text: str = '') -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        from_user=_customer(),
        chat=SimpleNamespace(id=CUSTOMER_ID),
        bot=SimpleNamespace(send_photo=AsyncMock(), send_message=AsyncMock()),
        answer=AsyncMock(),
    )


def _callback(data: str, message: SimpleNamespace | None = None) -> SimpleNamespace:
    return SimpleNamespace(data=data, from_user=_customer(), message=message or _chat_message(), answer=AsyncMock())


def _state() -> AsyncMock:
    state = AsyncMock()
    state.get_data.return_value = {}
    return state


def _sent(message: SimpleNamespace) -> list[tuple[str, object]]:
    return [(call.args[0], call.kwargs.get('reply_markup')) for call in message.answer.await_args_list]


def _reply_rows(markup: ReplyKeyboardMarkup) -> list[list[str]]:
    return [[button.text for button in row] for row in markup.keyboard]


def _cart_label(markup: ReplyKeyboardMarkup) -> str:
    return markup.keyboard[0][1].text


def test_main_keyboard_layout_and_flags():
    markup = reply.get_main_keyboard()
    assert isinstance(markup, ReplyKeyboardMarkup)
    assert _reply_rows(markup) == [
        ['📋 Меню', '🛒 Корзина'],
        ['🗑 Очистить', 'ℹ️ О пекарне', '❓ Помощь'],
    ]
    assert markup.resize_keyboard is True and markup.one_time_keyboard is False and markup.is_persistent is True

    full = reply.get_main_keyboard(3, 1090.0)
    assert _reply_rows(full) == [
        ['📋 Меню', '💳 Оплатить 1 090 ₽'],
        ['🗑 Очистить', 'ℹ️ О пекарне', '❓ Помощь'],
    ]
    assert full.resize_keyboard is True and full.one_time_keyboard is False and full.is_persistent is True


def test_full_cart_turns_cart_button_into_pay():
    assert _cart_label(reply.get_main_keyboard(0, 0)) == '🛒 Корзина'
    assert _cart_label(reply.get_main_keyboard(3, 1090.0)) == '💳 Оплатить 1 090 ₽'
    assert _cart_label(reply.get_main_keyboard(cart_count=1, cart_total=320)) == '💳 Оплатить 320 ₽'


@pytest.mark.parametrize('text', ['💳 Оплатить 320 ₽', '💳 Оплатить 1 090 ₽', '💳 Оплатить 99.50 ₽'])
def test_pay_button_pattern_accepts_any_total(text):
    assert reply.PAY_BUTTON_PATTERN.match(text)


@pytest.mark.parametrize('text', ['💳 Оплатить', 'Оплатить 320 ₽', '💳 Оплатить 320', '💳 Оплатить сейчас 320 ₽'])
def test_pay_button_pattern_rejects_other_text(text):
    assert not reply.PAY_BUTTON_PATTERN.match(text)


@pytest.mark.parametrize(
    'text',
    ['🛒 Корзина', '🛒 Корзина 3 шт на 1 090 ₽', '🛒 Корзина 12 шт на 12 345 ₽', '🛒 Корзина 1 шт на 99.50 ₽'],
)
def test_cart_button_pattern_accepts_any_counter(text):
    assert reply.CART_BUTTON_PATTERN.match(text)


@pytest.mark.parametrize('text', ['🛒 Корзина пуста', 'Корзина', '🛒 Корзина 3 шт', 'покажи 🛒 Корзина'])
def test_cart_button_pattern_rejects_other_text(text):
    assert not reply.CART_BUTTON_PATTERN.match(text)


def _matching_button_handlers(text: str) -> list[str]:
    matched = []
    for handler in demo_handlers.router.message.handlers:
        if handler.callback.__name__ in BUTTON_HANDLER_NAMES and asyncio.run(handler.check(SimpleNamespace(text=text)))[0]:
            matched.append(handler.callback.__name__)
    return matched


def test_reply_buttons_route_to_their_handlers():
    empty_labels = [text for row in _reply_rows(reply.get_main_keyboard()) for text in row]
    assert {label: _matching_button_handlers(label) for label in empty_labels} == {
        label: [name] for label, name in BUTTON_HANDLERS.items()
    }
    full_labels = [text for row in _reply_rows(reply.get_main_keyboard(2, 640.0)) for text in row]
    expected = {**BUTTON_HANDLERS, '💳 Оплатить 640 ₽': 'button_pay'}
    del expected[reply.CART]
    assert {label: _matching_button_handlers(label) for label in full_labels} == {
        label: [name] for label, name in expected.items()
    }
    for text in ('Привет', 'Меню', 'корзина', 'О пекарне', '❓ Помощь!', '🗑 Очистить всё', 'Оплатить'):
        assert _matching_button_handlers(text) == []


def test_buttons_from_previous_keyboards_still_work():
    assert _matching_button_handlers('🗑 Очистить корзину') == ['button_clear_cart']
    assert _matching_button_handlers('🛒 Корзина 5 шт на 1 600 ₽') == ['button_cart']


def test_start_sends_greeting_with_reply_keyboard_then_inline_menu(catalog, demo_db):
    _, product_ids = demo_db
    _add(product_ids, 'bun_caramel', 'raf_coconut')
    message = _chat_message('/start')

    asyncio.run(demo_handlers.cmd_start(message, _state()))

    (greeting, greeting_markup), (header, menu_markup) = _sent(message)
    assert greeting == catalog.greeting
    assert isinstance(greeting_markup, ReplyKeyboardMarkup)
    assert _cart_label(greeting_markup) == '💳 Оплатить 640 ₽'
    assert catalog.shop_name in header
    assert isinstance(menu_markup, InlineKeyboardMarkup)
    assert _buttons(menu_markup) == _buttons(demo_handlers.menu_keyboard(catalog, asyncio.run(demo_flow.cart_summary(CUSTOMER_ID))))


def test_category_button_lists_bakery_and_rejects_unknown(catalog, demo_db):
    callback = _callback('dm:sec:Выпечка')
    asyncio.run(demo_handlers.callback_section(callback, _state()))
    [(text, markup)] = _sent(callback.message)
    assert text.startswith('🥐 Выпечка')
    shown = [row[0][1] for row in _buttons(markup) if row[0][1] and row[0][1].startswith('dm:item:')]
    assert shown == [f'dm:item:{item.id}' for item in catalog.items_in('Выпечка')]
    assert _buttons(markup)[-1] == [('⬅️ Назад к категориям', 'dm:menu')]

    missing = _callback('dm:sec:Нет такого')
    asyncio.run(demo_handlers.callback_section(missing, _state()))
    missing.answer.assert_awaited_once_with('Раздел не найден', show_alert=True)
    missing.message.answer.assert_not_awaited()


def test_menu_button_repeats_start(catalog, demo_db):
    message = _chat_message(reply.MENU)
    asyncio.run(demo_handlers.button_menu(message, _state()))
    (greeting, greeting_markup), (_, menu_markup) = _sent(message)
    assert greeting == catalog.greeting
    assert isinstance(greeting_markup, ReplyKeyboardMarkup) and isinstance(menu_markup, InlineKeyboardMarkup)


def test_add_to_cart_refreshes_reply_counter(catalog, demo_db):
    callback = _callback('dm:add:bun_caramel')
    asyncio.run(demo_handlers.callback_add(callback, _state()))
    asyncio.run(demo_handlers.callback_add(_callback('dm:add:bun_caramel', callback.message), _state()))

    (first_text, first_markup), (second_text, second_markup) = _sent(callback.message)
    assert first_text == '✅ Добавлено: Булочка с карамелью. В корзине: 1 позиция на 320 ₽'
    assert _cart_label(first_markup) == '💳 Оплатить 320 ₽'
    assert second_text.endswith('В корзине: 2 позиции на 640 ₽')
    assert isinstance(second_markup, ReplyKeyboardMarkup)
    assert _cart_label(second_markup) == '💳 Оплатить 640 ₽'


def test_cart_button_shows_contents_with_inline_actions(catalog, demo_db):
    _, product_ids = demo_db
    _add(product_ids, 'tart_dome', 'raf_coconut')
    message = _chat_message('🛒 Корзина 2 шт на 800 ₽')

    asyncio.run(demo_handlers.button_cart(message, _state()))

    [(text, markup)] = _sent(message)
    assert 'Тарталетка с глянцевым куполом × 1 — 480 ₽' in text
    assert 'Итого: 2 позиции на 800 ₽' in text
    assert isinstance(markup, InlineKeyboardMarkup)
    assert ('🛒 Оформить заказ', 'dm:checkout') in sum(_buttons(markup), [])


def test_cart_button_on_empty_cart(catalog, demo_db):
    message = _chat_message(reply.CART)
    asyncio.run(demo_handlers.button_cart(message, _state()))
    [(text, markup)] = _sent(message)
    assert text == '🛒 Корзина пуста'
    assert isinstance(markup, ReplyKeyboardMarkup) and _cart_label(markup) == '🛒 Корзина'


def test_clear_button_empties_cart_and_cancels_receipt(catalog, demo_db):
    db_path, product_ids = demo_db
    _add(product_ids, 'bun_berliner', 'coffee_affogato')
    receipt = asyncio.run(demo_flow.checkout(catalog, CUSTOMER_ID, None))
    message = _chat_message(reply.CLEAR_CART)
    state = _state()

    asyncio.run(demo_handlers.button_clear_cart(message, state))

    [(text, markup)] = _sent(message)
    assert text.startswith('🗑 Корзина очищена')
    assert isinstance(markup, ReplyKeyboardMarkup) and _cart_label(markup) == '🛒 Корзина'
    state.clear.assert_awaited_once()
    assert asyncio.run(demo_flow.cart_summary(CUSTOMER_ID)).empty
    assert _rows(db_path, 'SELECT DISTINCT status FROM orders WHERE group_id = ?', receipt.group_id) == [('cancelled',)]


def test_inline_clear_reports_with_reply_keyboard(catalog, demo_db):
    _, product_ids = demo_db
    _add(product_ids, 'bun_caramel')
    callback = _callback('dm:cart_clear')

    asyncio.run(demo_handlers.callback_cart_clear(callback, _state()))

    callback.answer.assert_awaited_once_with('Корзина очищена')
    [(text, markup)] = _sent(callback.message)
    assert text.startswith('🗑 Корзина очищена') and isinstance(markup, ReplyKeyboardMarkup)
    assert asyncio.run(demo_flow.cart_summary(CUSTOMER_ID)).empty


def test_about_and_help_buttons(catalog, demo_db):
    _, product_ids = demo_db
    _add(product_ids, 'raf_coconut')

    about = _chat_message(reply.ABOUT)
    asyncio.run(demo_handlers.button_about(about))
    [(about_text, about_markup)] = _sent(about)
    assert about_text == catalog.about
    assert _cart_label(about_markup) == '💳 Оплатить 320 ₽'

    help_message = _chat_message(reply.HELP)
    asyncio.run(demo_handlers.button_help(help_message))
    [(help_text, help_markup)] = _sent(help_message)
    for hint in ('📋 Меню', '➕ В корзину', '🛒 Оформить заказ', '💳 Оплатить', '«🗑 Очистить»', 'ℹ️ О пекарне'):
        assert hint in help_text
    assert 'демо' not in help_text.lower()
    assert isinstance(help_markup, ReplyKeyboardMarkup)


def test_inline_action_screens_keep_inline_buttons(catalog, demo_db, monkeypatch):
    _, product_ids = demo_db
    item = catalog.get('tart_stripe')

    card = _chat_message()
    asyncio.run(demo_handlers.send_card(card, item))
    sent_card = card.bot.send_photo.await_args.kwargs['reply_markup'] if card.bot.send_photo.await_count else _sent(card)[0][1]
    assert isinstance(sent_card, InlineKeyboardMarkup)
    assert ('➕ В корзину', 'dm:add:tart_stripe') in sum(_buttons(sent_card), [])

    menu = _callback('dm:menu')
    asyncio.run(demo_handlers.callback_menu(menu, _state()))
    [(_, menu_markup)] = _sent(menu.message)
    assert isinstance(menu_markup, InlineKeyboardMarkup)

    _add(product_ids, 'tart_stripe')
    cart = _callback('dm:cart')
    asyncio.run(demo_handlers.callback_cart(cart, _state()))
    [(_, cart_markup)] = _sent(cart.message)
    assert isinstance(cart_markup, InlineKeyboardMarkup)

    checkout = _callback('dm:checkout')
    asyncio.run(demo_handlers.callback_checkout(checkout, _state()))
    [(receipt_text, receipt_markup)] = _sent(checkout.message)
    assert receipt_text.startswith('🧾 Заказ #')
    assert isinstance(receipt_markup, InlineKeyboardMarkup)
    assert sum(_buttons(receipt_markup), [])[0] == ('💳 Оплатить 390 ₽', f'dm:check:{receipt_text.split("#")[1].split()[0]}')


def test_quick_buy_keeps_three_taps(catalog, demo_db):
    callback = _callback('dm:buy:raf_coconut')
    asyncio.run(demo_handlers.callback_buy(callback, _state()))
    [(text, markup)] = _sent(callback.message)
    assert text.startswith('🧾 Заказ #') and isinstance(markup, InlineKeyboardMarkup)
    assert sum(_buttons(markup), [])[0][0] == '💳 Оплатить 320 ₽'


def test_payment_confirmation_brings_reply_keyboard(catalog, demo_db, monkeypatch):
    _, product_ids = demo_db
    monkeypatch.setattr(demo_handlers, 'SIMULATED_PAYMENT_DELAY_SECONDS', 0)
    _add(product_ids, 'bun_caramel', 'coffee_iced_layered')
    receipt = asyncio.run(demo_flow.checkout(catalog, CUSTOMER_ID, None))
    callback = _callback(f'dm:check:{receipt.group_id}')

    asyncio.run(demo_handlers.callback_check(callback, _state()))

    (progress, progress_markup), (confirmation, markup) = _sent(callback.message)
    assert progress.startswith('⏳') and progress_markup is None
    assert f'#{receipt.group_id}' in confirmation and 'Итого: 610 ₽' in confirmation
    assert isinstance(markup, ReplyKeyboardMarkup) and _cart_label(markup) == '🛒 Корзина'


def test_pay_button_creates_receipt_with_inline_pay(catalog, demo_db):
    db_path, product_ids = demo_db
    _add(product_ids, 'bun_caramel', 'bun_caramel', 'raf_coconut')
    message = _chat_message('💳 Оплатить 960 ₽')

    asyncio.run(demo_handlers.button_pay(message, _state()))

    [(text, markup)] = _sent(message)
    assert text.startswith('🧾 Заказ #') and 'Итого: 3 позиции на 960 ₽' in text
    group_id = int(text.split('#')[1].split()[0])
    assert isinstance(markup, InlineKeyboardMarkup)
    assert sum(_buttons(markup), [])[0] == ('💳 Оплатить 960 ₽', f'dm:check:{group_id}')
    assert _rows(db_path, 'SELECT COUNT(*) FROM orders WHERE group_id = ?', group_id) == [(2,)]
    assert _rows(db_path, 'SELECT order_id, amount, status FROM payments') == [(group_id, 960.0, 'pending')]


def test_pay_button_with_stale_total_bills_current_cart(catalog, demo_db):
    _, product_ids = demo_db
    _add(product_ids, 'tart_dome', 'raf_coconut')
    message = _chat_message('💳 Оплатить 480 ₽')

    asyncio.run(demo_handlers.button_pay(message, _state()))

    [(text, markup)] = _sent(message)
    assert 'Итого: 2 позиции на 800 ₽' in text
    assert sum(_buttons(markup), [])[0][0] == '💳 Оплатить 800 ₽'


def test_pay_button_replaces_previous_open_receipt(catalog, demo_db):
    db_path, product_ids = demo_db
    _add(product_ids, 'bun_berliner')
    first = asyncio.run(demo_flow.checkout(catalog, CUSTOMER_ID, None))
    _add(product_ids, 'coffee_affogato')

    message = _chat_message('💳 Оплатить 700 ₽')
    asyncio.run(demo_handlers.button_pay(message, _state()))

    assert _rows(db_path, 'SELECT DISTINCT status FROM orders WHERE group_id = ?', first.group_id) == [('cancelled',)]
    assert 'Итого: 2 позиции на 700 ₽' in _sent(message)[0][0]


def test_pay_button_on_empty_cart(catalog, demo_db):
    message = _chat_message('💳 Оплатить 320 ₽')
    asyncio.run(demo_handlers.button_pay(message, _state()))
    [(text, markup)] = _sent(message)
    assert text == '🛒 Корзина пуста'
    assert isinstance(markup, ReplyKeyboardMarkup) and _cart_label(markup) == '🛒 Корзина'


def test_pay_button_then_inline_pay_confirms(catalog, demo_db, monkeypatch):
    _, product_ids = demo_db
    monkeypatch.setattr(demo_handlers, 'SIMULATED_PAYMENT_DELAY_SECONDS', 0)
    _add(product_ids, 'coffee_iced_layered')
    message = _chat_message('💳 Оплатить 290 ₽')
    asyncio.run(demo_handlers.button_pay(message, _state()))
    pay_data = sum(_buttons(_sent(message)[0][1]), [])[0][1]

    callback = _callback(pay_data)
    asyncio.run(demo_handlers.callback_check(callback, _state()))

    confirmation, markup = _sent(callback.message)[-1]
    assert confirmation.startswith('✅ Оплата прошла')
    assert _cart_label(markup) == '🛒 Корзина'
    assert asyncio.run(demo_flow.cart_summary(CUSTOMER_ID)).empty


def test_webhook_confirmation_brings_reply_keyboard(catalog, demo_db):
    _, product_ids = demo_db
    _add(product_ids, 'tart_caramel_nut')
    receipt = asyncio.run(demo_flow.checkout(catalog, CUSTOMER_ID, _fake_yookassa()))
    bot = SimpleNamespace(send_message=AsyncMock())

    asyncio.run(demo_flow.notify_customer_paid(bot, receipt.group_id))

    kwargs = bot.send_message.await_args.kwargs
    assert kwargs['chat_id'] == CUSTOMER_ID and f'#{receipt.group_id}' in kwargs['text']
    assert isinstance(kwargs['reply_markup'], ReplyKeyboardMarkup)
    assert _cart_label(kwargs['reply_markup']) == '🛒 Корзина'
