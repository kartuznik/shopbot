from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGES_DIR = PROJECT_ROOT / 'demo' / 'images'

PICKUP_OFFSET_MINUTES = 30
PICKUP_ROUND_MINUTES = 5

_REQUIRED_TOP = ('shop_name', 'greeting', 'about', 'reminder_template', 'confirmation_template', 'items')
_REQUIRED_ITEM = ('id', 'section', 'title', 'description', 'price', 'image_path', 'emoji')
_TEMPLATE_KEYS = {
    'shop_name': '',
    'order_id': 0,
    'items': '',
    'total': '0',
    'pickup_time': '00:00',
}


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class DemoItem:
    id: str
    section: str
    title: str
    description: str
    price: int
    image_path: str
    emoji: str


@dataclass(frozen=True)
class DemoCatalog:
    shop_name: str
    greeting: str
    about: str
    reminder_template: str
    confirmation_template: str
    items: tuple[DemoItem, ...]

    def sections(self) -> list[str]:
        return list(dict.fromkeys(item.section for item in self.items))

    def items_in(self, section: str) -> list[DemoItem]:
        return [item for item in self.items if item.section == section]

    def get(self, item_id: str) -> DemoItem | None:
        return next((item for item in self.items if item.id == item_id), None)

    def by_title(self, title: str) -> DemoItem | None:
        return next((item for item in self.items if item.title == title), None)


def resolve_path(raw: str | Path) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _parse_item(raw: Any, index: int) -> DemoItem:
    if not isinstance(raw, dict):
        raise CatalogError(f'items[{index}]: ожидается словарь')
    missing = [key for key in _REQUIRED_ITEM if raw.get(key) in (None, '')]
    if missing:
        raise CatalogError(f'items[{index}]: нет полей {", ".join(missing)}')
    try:
        price = int(raw['price'])
    except (TypeError, ValueError) as error:
        raise CatalogError(f'items[{index}]: price должен быть числом') from error
    if price <= 0:
        raise CatalogError(f'items[{index}]: price должен быть больше нуля')
    image_path = str(raw['image_path']).strip()
    # Картинка ищется только внутри demo/images: путь из конфига не должен выводить наружу.
    if Path(image_path).name != image_path:
        raise CatalogError(f'items[{index}]: image_path — только имя файла, без каталогов')
    return DemoItem(
        id=str(raw['id']).strip(),
        section=str(raw['section']).strip(),
        title=str(raw['title']).strip(),
        description=str(raw['description']).strip(),
        price=price,
        image_path=image_path,
        emoji=str(raw['emoji']).strip(),
    )


def parse_catalog(data: Any) -> DemoCatalog:
    if not isinstance(data, dict):
        raise CatalogError('catalog.yaml: ожидается словарь верхнего уровня')
    missing = [key for key in _REQUIRED_TOP if not data.get(key)]
    if missing:
        raise CatalogError(f'catalog.yaml: нет полей {", ".join(missing)}')
    if not isinstance(data['items'], list):
        raise CatalogError('catalog.yaml: items должен быть списком')

    items = tuple(_parse_item(raw, index) for index, raw in enumerate(data['items']))
    ids = [item.id for item in items]
    duplicates = sorted({item_id for item_id in ids if ids.count(item_id) > 1})
    if duplicates:
        raise CatalogError(f'catalog.yaml: повторяются id {", ".join(duplicates)}')

    catalog = DemoCatalog(
        shop_name=str(data['shop_name']).strip(),
        greeting=str(data['greeting']).strip(),
        about=str(data['about']).strip(),
        reminder_template=str(data['reminder_template']).strip(),
        confirmation_template=str(data['confirmation_template']).strip(),
        items=items,
    )
    # Опечатка в плейсхолдере должна ронять старт, а не запись ролика на последнем шаге.
    for name in ('reminder_template', 'confirmation_template'):
        try:
            getattr(catalog, name).format(**_TEMPLATE_KEYS)
        except (KeyError, IndexError, ValueError) as error:
            raise CatalogError(f'catalog.yaml: {name} содержит неизвестный плейсхолдер {error}') from error
    return catalog


def load_catalog(path: str | Path) -> DemoCatalog:
    resolved = resolve_path(path)
    try:
        data = yaml.safe_load(resolved.read_text(encoding='utf-8'))
    except FileNotFoundError as error:
        raise CatalogError(f'Каталог не найден: {resolved}') from error
    except yaml.YAMLError as error:
        raise CatalogError(f'catalog.yaml: ошибка YAML: {error}') from error
    return parse_catalog(data)


def image_file(item: DemoItem, images_dir: Path | None = None) -> Path | None:
    path = (images_dir or DEFAULT_IMAGES_DIR) / item.image_path
    return path if path.is_file() else None


def format_price(value: float) -> str:
    text = f'{value:,.0f}' if float(value).is_integer() else f'{value:,.2f}'
    return text.replace(',', ' ')


def plural_positions(count: int) -> str:
    tail = count % 100
    if 11 <= tail <= 14:
        word = 'позиций'
    elif count % 10 == 1:
        word = 'позиция'
    elif 2 <= count % 10 <= 4:
        word = 'позиции'
    else:
        word = 'позиций'
    return f'{count} {word}'


def card_caption(item: DemoItem) -> str:
    return f'{item.emoji} {item.title}\n\n{item.description}\n\n💳 {format_price(item.price)} ₽'


def card_text_fallback(item: DemoItem) -> str:
    return f'{item.emoji * 3}\n\n{card_caption(item)}'


def format_line(catalog: DemoCatalog, title: str, quantity: int, line_total: float) -> str:
    item = catalog.by_title(title)
    emoji = f'{item.emoji} ' if item else ''
    return f'{emoji}{title} × {quantity} — {format_price(line_total)} ₽'


def format_lines(catalog: DemoCatalog, lines: list[dict[str, Any]]) -> str:
    """Строки корзины или заказа: ключи name/product_name, quantity, line_total/total_price."""
    return '\n'.join(
        format_line(
            catalog,
            str(line.get('product_name') or line.get('name')),
            int(line['quantity']),
            float(line['total_price'] if 'total_price' in line else line['line_total']),
        )
        for line in lines
    )


def parse_db_timestamp(value: str | datetime) -> datetime:
    """SQLite CURRENT_TIMESTAMP пишет UTC без зоны; приводим к часам сервера."""
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone()


def pickup_time(ordered_at: datetime) -> datetime:
    target = ordered_at + timedelta(minutes=PICKUP_OFFSET_MINUTES)
    step = PICKUP_ROUND_MINUTES * 60
    base = target.replace(minute=0, second=0, microsecond=0)
    seconds = (target - base).total_seconds()
    return base + timedelta(seconds=math.ceil(seconds / step) * step)


def _template_values(
    catalog: DemoCatalog, order_id: int, items: str, total: float, pickup: datetime
) -> dict[str, Any]:
    return {
        'shop_name': catalog.shop_name,
        'order_id': order_id,
        'items': items,
        'total': format_price(total),
        'pickup_time': pickup.strftime('%H:%M'),
    }


def render_confirmation(
    catalog: DemoCatalog, order_id: int, items: str, total: float, pickup: datetime
) -> str:
    return catalog.confirmation_template.format(**_template_values(catalog, order_id, items, total, pickup))


def render_reminder(
    catalog: DemoCatalog, order_id: int, items: str, total: float, pickup: datetime
) -> str:
    return catalog.reminder_template.format(**_template_values(catalog, order_id, items, total, pickup))


class PurchaseState(str, Enum):
    MENU = 'menu'
    CATEGORY = 'category'
    CARD = 'card'
    CART = 'cart'
    CHECKOUT = 'checkout'
    AWAITING_PAYMENT = 'awaiting_payment'
    CONFIRMED = 'confirmed'


class PurchaseEvent(str, Enum):
    OPEN_MENU = 'open_menu'
    OPEN_CATEGORY = 'open_category'
    OPEN_CARD = 'open_card'
    OPEN_CART = 'open_cart'
    ADD_TO_CART = 'add_to_cart'
    CHECKOUT = 'checkout'
    PAY = 'pay'
    PAID = 'paid'


class InvalidTransition(Exception):
    pass


# Меню, раздел, карточка и корзина доступны из любого состояния: это кнопки «Назад»,
# и новый дубль записи не должен упираться в прошлый шаг. Чек — с карточки или из корзины,
# оплата — только из чека. «В корзину» на экране раздела оставляет покупателя в разделе.
_TRANSITIONS: dict[tuple[PurchaseState, PurchaseEvent], PurchaseState] = {
    **{(state, PurchaseEvent.OPEN_MENU): PurchaseState.MENU for state in PurchaseState},
    **{(state, PurchaseEvent.OPEN_CATEGORY): PurchaseState.CATEGORY for state in PurchaseState},
    **{(state, PurchaseEvent.OPEN_CARD): PurchaseState.CARD for state in PurchaseState},
    **{(state, PurchaseEvent.OPEN_CART): PurchaseState.CART for state in PurchaseState},
    (PurchaseState.CATEGORY, PurchaseEvent.ADD_TO_CART): PurchaseState.CATEGORY,
    (PurchaseState.CARD, PurchaseEvent.ADD_TO_CART): PurchaseState.CARD,
    (PurchaseState.CARD, PurchaseEvent.CHECKOUT): PurchaseState.CHECKOUT,
    (PurchaseState.CART, PurchaseEvent.CHECKOUT): PurchaseState.CHECKOUT,
    (PurchaseState.CHECKOUT, PurchaseEvent.PAY): PurchaseState.AWAITING_PAYMENT,
    (PurchaseState.AWAITING_PAYMENT, PurchaseEvent.PAID): PurchaseState.CONFIRMED,
}


def can_advance(state: PurchaseState, event: PurchaseEvent) -> bool:
    return (state, event) in _TRANSITIONS


def advance(state: PurchaseState, event: PurchaseEvent) -> PurchaseState:
    try:
        return _TRANSITIONS[(state, event)]
    except KeyError as error:
        raise InvalidTransition(f'{event.value} недопустимо из {state.value}') from error
