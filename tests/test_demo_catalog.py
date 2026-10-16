from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot import demo_catalog, demo_flow
from bot.config import get_settings
from bot.database import (
    get_last_order_for_products,
    get_order_summary,
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


def _order_status(db_path: Path, order_id: int) -> tuple[str, str]:
    db = sqlite3.connect(db_path)
    try:
        order = db.execute('SELECT status FROM orders WHERE id = ?', (order_id,)).fetchone()
        payment = db.execute('SELECT status FROM payments WHERE order_id = ?', (order_id,)).fetchone()
        return order[0], payment[0]
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


def test_storefront_texts_do_not_mention_demo(catalog):
    texts = [catalog.shop_name, catalog.greeting, catalog.reminder_template, catalog.confirmation_template]
    texts += [f'{item.title} {item.description}' for item in catalog.items]
    for text in texts:
        assert 'демо' not in text.lower()
        assert 'shopbot' not in text.lower()


def test_catalog_rejects_missing_fields_and_bad_placeholders():
    base = {
        'shop_name': 'X',
        'greeting': 'hi',
        'reminder_template': '{order_id}',
        'confirmation_template': '{order_id}',
        'items': [
            {'id': 'a', 'section': 'S', 'title': 'T', 'description': 'D', 'price': 1, 'image_path': 'a.jpg', 'emoji': '☕'}
        ],
    }
    assert len(parse_catalog(base).items) == 1

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


# --- машина состояний ------------------------------------------------------


def test_purchase_state_machine_happy_path():
    state = PurchaseState.MENU
    state = advance(state, PurchaseEvent.OPEN_CARD)
    assert state is PurchaseState.CARD
    state = advance(state, PurchaseEvent.RESERVE)
    assert state is PurchaseState.AWAITING_PAYMENT
    state = advance(state, PurchaseEvent.PAID)
    assert state is PurchaseState.CONFIRMED
    assert advance(state, PurchaseEvent.OPEN_MENU) is PurchaseState.MENU


def test_purchase_state_machine_blocks_skipped_steps():
    with pytest.raises(InvalidTransition):
        advance(PurchaseState.MENU, PurchaseEvent.RESERVE)
    with pytest.raises(InvalidTransition):
        advance(PurchaseState.CARD, PurchaseEvent.PAID)
    assert not can_advance(PurchaseState.CONFIRMED, PurchaseEvent.PAID)


@pytest.mark.parametrize('state', list(PurchaseState))
def test_menu_and_card_reachable_from_any_state(state):
    assert advance(state, PurchaseEvent.OPEN_MENU) is PurchaseState.MENU
    assert advance(state, PurchaseEvent.OPEN_CARD) is PurchaseState.CARD


# --- шаблоны ---------------------------------------------------------------


@pytest.mark.parametrize(
    ('ordered', 'expected'),
    [('10:00', '10:30'), ('10:01', '10:35'), ('10:05', '10:35'), ('23:47', '00:20')],
)
def test_pickup_is_plus_30_rounded_up_to_5(ordered, expected):
    hours, minutes = map(int, ordered.split(':'))
    assert pickup_time(datetime(2026, 10, 16, hours, minutes, 12 if minutes % 5 else 0)).strftime('%H:%M') == expected


def test_confirmation_and_reminder_render(catalog):
    pickup = datetime(2026, 10, 16, 10, 35)
    items = '🥐 Булочка с карамелью × 1'
    confirmation = render_confirmation(catalog, 7, items, 320.0, pickup)
    reminder = render_reminder(catalog, 7, items, 320.0, pickup)
    for text in (confirmation, reminder):
        assert '#7' in text and items in text and '10:35' in text and catalog.shop_name in text
    assert '320 ₽' in confirmation
    assert 'скучает и ждёт подтверждения времени' in reminder


# --- цепочка покупки с моком YooKassa --------------------------------------


def test_purchase_with_mocked_yookassa(catalog, demo_db):
    db_path, product_ids = demo_db
    item = catalog.get('tart_dome')
    yookassa = _fake_yookassa()

    reservation = asyncio.run(demo_flow.reserve(catalog, item, product_ids[item.id], CUSTOMER_ID, yookassa))
    assert reservation.payment_url == PAY_URL and not reservation.simulated
    assert reservation.total == 480.0
    assert yookassa.await_args.args[:2] == (reservation.order_id, 480.0)
    assert _order_status(db_path, reservation.order_id) == ('new', 'pending')

    not_yet = asyncio.run(demo_flow.confirm_payment(catalog, reservation.order_id, lambda _pid: 'pending'))
    assert not_yet is None
    assert _order_status(db_path, reservation.order_id) == ('new', 'pending')

    checked: list[str] = []

    def kassa_status(payment_id: str) -> str:
        checked.append(payment_id)
        return 'succeeded'

    confirmation = asyncio.run(demo_flow.confirm_payment(catalog, reservation.order_id, kassa_status))
    assert checked == ['pid-1']
    assert confirmation is not None and confirmation.user_id == CUSTOMER_ID
    assert f'#{reservation.order_id}' in confirmation.text and 'Тарталетка с глянцевым куполом × 1' in confirmation.text
    assert _order_status(db_path, reservation.order_id) == ('paid', 'succeeded')

    order = asyncio.run(get_order_summary(reservation.order_id))
    expected_pickup = pickup_time(demo_catalog.parse_db_timestamp(order['created_at'])).strftime('%H:%M')
    assert expected_pickup in confirmation.text


def test_purchase_simulated_without_keys(catalog, demo_db):
    db_path, product_ids = demo_db
    assert demo_flow.payments_simulated(get_settings())
    item = catalog.get('coffee_affogato')

    reservation = asyncio.run(demo_flow.reserve(catalog, item, product_ids[item.id], CUSTOMER_ID, None))
    assert reservation.simulated

    def kassa_must_not_be_called(_pid: str) -> str:
        raise AssertionError('имитация оплаты не должна ходить в кассу')

    confirmation = asyncio.run(demo_flow.confirm_payment(catalog, reservation.order_id, kassa_must_not_be_called))
    assert confirmation is not None
    assert _order_status(db_path, reservation.order_id) == ('paid', 'succeeded')


def test_reserve_keeps_one_item_per_order(catalog, demo_db):
    _, product_ids = demo_db
    first = catalog.get('bun_caramel')
    second = catalog.get('raf_coconut')
    asyncio.run(demo_flow.reserve(catalog, first, product_ids[first.id], CUSTOMER_ID, None))
    reservation = asyncio.run(demo_flow.reserve(catalog, second, product_ids[second.id], CUSTOMER_ID, None))
    order = asyncio.run(get_order_summary(reservation.order_id))
    assert order['product_name'] == 'Раф кокосовый' and order['quantity'] == 1


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


def test_demo_remind_targets_last_order_customer(catalog, demo_db):
    _, product_ids = demo_db
    for item_id in ('bun_caramel', 'tart_stripe'):
        item = catalog.get(item_id)
        last = asyncio.run(demo_flow.reserve(catalog, item, product_ids[item.id], CUSTOMER_ID, None))

    owner = _command_message(ADMIN_ID)
    asyncio.run(demo_handlers.cmd_demo_remind(owner))
    kwargs = owner.bot.send_message.await_args.kwargs
    assert kwargs['chat_id'] == CUSTOMER_ID
    assert f'#{last.order_id}' in kwargs['text'] and 'Тарт с карамельными полосками × 1' in kwargs['text']
    assert 'скучает и ждёт подтверждения времени' in kwargs['text']


def test_demo_reset_clears_only_storefront_orders(catalog, demo_db):
    db_path, product_ids = demo_db
    item = catalog.get('bun_berliner')
    asyncio.run(demo_flow.reserve(catalog, item, product_ids[item.id], CUSTOMER_ID, None))

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
    assert 'заказов 1, платежей 1' in owner.answer.await_args.args[0]
    assert asyncio.run(get_last_order_for_products(product_ids.values())) is None
    assert asyncio.run(reset_demo_orders(product_ids.values()))['orders'] == 0

    db = sqlite3.connect(db_path)
    try:
        assert db.execute('SELECT COUNT(*) FROM orders WHERE product_id = ?', (foreign_product,)).fetchone()[0] == 1
    finally:
        db.close()
