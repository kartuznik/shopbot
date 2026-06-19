# ShopBot - Telegram бот магазин

ShopBot - базовый Telegram бот-магазин с каталогом товаров, корзиной, заказами и админ-функциями управления товарами.

## Установка

```bash
cd /opt/bots/shopbot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Настройка

Заполните `.env`:

- `TELEGRAM_BOT_TOKEN`
- `ADMIN_IDS`
- `DB_PATH`
- `LANGUAGE`

## Команды

Пользователь:

- `/start`
- `/catalog`
- `/cart`
- `/orders`

Админ:

- `/admin`
- `/add_product`
- `/edit_product <id> <field> <value>`
- `/delete_product <id>`
- `/orders` (все)
- `/users`

## Структура БД

- `users`: пользователи бота
- `products`: каталог товаров
- `cart_items`: корзины пользователей
- `orders`: заказы
