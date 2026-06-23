# ShopBot - Telegram-магазин с веб-админкой

ShopBot - готовый Telegram-бот для онлайн-магазина с каталогом, корзиной, заказами, оплатой YooKassa, доставкой, отзывами и веб-админкой на Flask.

## Что это и для чего

Проект позволяет:
- принимать заказы в Telegram;
- управлять ассортиментом и заказами;
- собирать оплаты через YooKassa;
- настраивать зоны доставки;
- контролировать бизнес-метрики через аналитику и веб-панель.

## Возможности

### Для клиента
- каталог с категориями, поиском и карточками товаров;
- корзина и оформление заказа;
- выбор адреса и зоны доставки;
- просмотр статусов заказов;
- отзывы и рейтинги товаров.

### Для администратора в Telegram
- управление товарами и категориями;
- управление статусами заказов;
- статистика продаж и пользователей;
- управление зонами доставки;
- просмотр платежей;
- массовые рассылки пользователям;
- интеграция с Google Sheets (ручной и авто-sync);
- health-мониторинг (`/health`).

### Веб-админка
- авторизация по паролю из `.env`;
- дашборд с ключевыми метриками;
- CRUD по товарам, категориям, зонам доставки;
- список заказов с фильтрацией и сменой статуса;
- список пользователей и история заказов;
- список отзывов и платежей;
- экспорт в CSV/Excel;
- отчеты по продажам в Excel с графиком.

## Требования

- Ubuntu/Debian VPS;
- Python 3.11+;
- `pip`, `venv`;
- SQLite (встроен в Python, отдельный сервер не нужен);
- systemd;
- Nginx (рекомендуется для reverse proxy).

## Установка

### 1) Клонирование

```bash
git clone <repo-url> /opt/bots/shopbot
cd /opt/bots/shopbot
```

### 2) Виртуальное окружение

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3) Зависимости

```bash
pip install -r requirements.txt
```

### 4) Настройка `.env`

```bash
cp .env.example .env
```

Обязательные/важные переменные:

- `TELEGRAM_BOT_TOKEN` - токен бота от @BotFather;
- `ADMIN_IDS` - Telegram ID администраторов через запятую;
- `DB_PATH` - путь к SQLite БД, обычно `data/shopbot.db`;
- `LANGUAGE` - язык (`ru` или `en`);
- `ADMIN_WEB_PASSWORD` - пароль для входа в веб-админку;
- `YUKASSA_SHOP_ID` - ID магазина в YooKassa;
- `YUKASSA_SECRET_KEY` - секретный ключ YooKassa;
- `WEB_PUBLIC_BASE_URL` (опционально) - публичный базовый URL для вебки.

### 5) Инициализация БД

База создается автоматически при старте бота.

## Запуск

### Telegram-бот

```bash
cd /opt/bots/shopbot
source venv/bin/activate
python -m bot.main
```

### Веб-админка

```bash
cd /opt/bots/shopbot
source venv/bin/activate
python -m web.app
```

Веб-панель по умолчанию: `http://<SERVER_IP>:5001`

## Настройка Telegram-бота

1. Откройте @BotFather.
2. Создайте бота: `/newbot`.
3. Скопируйте токен и добавьте в `.env` как `TELEGRAM_BOT_TOKEN`.
4. Добавьте ваш Telegram ID в `ADMIN_IDS`.
5. Перезапустите сервис бота.

## Настройка YooKassa

1. Зарегистрируйтесь в [YooKassa](https://yookassa.ru/).
2. Создайте магазин в кабинете.
3. Скопируйте `shop_id` и `secret_key`.
4. Запишите в `.env`:
   - `YUKASSA_SHOP_ID=...`
   - `YUKASSA_SECRET_KEY=...`
5. Настройте webhook URL:
   - `http://<SERVER_IP>/webhook/yookassa` (через Nginx reverse proxy).

## Настройка Google Sheets API

1. Создайте проект в Google Cloud.
2. Включите Google Sheets API и Google Drive API.
3. Создайте Service Account.
4. Скачайте JSON-ключ и сохраните как:
   - `/opt/bots/shopbot/credentials.json`
5. Пример структуры ключа:
   - `credentials_example.json`
6. Добавьте email сервисного аккаунта в доступ нужной таблицы Google Sheets (Editor).
7. В боте выполните `/sheets_setup` для создания конфигурации синхронизации.

## Использование

### Команды пользователя

- `/start` - старт;
- `/catalog` - каталог;
- `/search <запрос>` - поиск товаров;
- `/cart` - корзина;
- `/orders` - мои заказы;
- `/add_address` - добавить адрес;
- `/my_addresses` - мои адреса;
- `/reviews <product_id>` - отзывы о товаре.

### Команды администратора

- `/admin` - админ-панель;
- `/add_product`, `/edit_product`, `/delete_product`;
- `/add_category`, `/list_categories`, `/delete_category`;
- `/admin_orders`, `/order_status`, `/order_details`;
- `/users`;
- `/stats`, `/stats_sales`, `/stats_users`;
- `/payments`, `/payment_details`;
- `/broadcast`, `/broadcasts`, `/broadcast_details`, `/broadcast_cancel`, `/broadcast_delete`;
- `/sheets_setup`, `/sheets_list`, `/sheets_sync`, `/sheets_delete`;
- `/add_delivery_zone`, `/list_delivery_zones`, `/delete_delivery_zone`, `/delivery_stats`;
- `/product_reviews`, `/delete_review`;
- `/health`.

## Веб-админка: разделы

- `Dashboard` - общая статистика;
- `Товары` - управление товарами + экспорт;
- `Категории` - создание и удаление;
- `Заказы` - фильтры, статусы, детали, экспорт;
- `Пользователи` - поиск и история заказов, экспорт;
- `Доставка` - зоны доставки;
- `Отзывы` - просмотр/удаление, экспорт;
- `Платежи` - статусы оплат;
- `Настройки` - редактирование безопасных `.env` параметров.

## Структура проекта

```text
shopbot/
├── bot/
│   ├── handlers/
│   ├── database.py
│   ├── health.py
│   ├── webhook.py
│   └── main.py
├── web/
│   ├── app.py
│   ├── auth.py
│   ├── forms.py
│   ├── export.py
│   ├── templates/
│   ├── static/
│   └── shopbot-web.service
├── data/
├── credentials_example.json
├── requirements.txt
└── README.md
```

## Деплой на VPS

### systemd

Пример сервисов:
- `shopbot.service` - Telegram-бот;
- `shopbot-web.service` - Flask веб-админка;
- `surveybot-check.timer` - периодическая health-проверка.

```bash
systemctl daemon-reload
systemctl enable --now shopbot
systemctl enable --now shopbot-web
```

### Nginx

Используйте reverse proxy для webhook:
- внешний путь `/webhook/yookassa`;
- внутренний `127.0.0.1:8080`.

### Firewall

```bash
ufw allow 80/tcp
ufw allow 5001/tcp
ufw reload
```

## Мониторинг и диагностика

- Команда `/health` в Telegram;
- Логи:
  - `journalctl -u shopbot -f`
  - `journalctl -u shopbot-web -f`
  - `tail -f /opt/bots/shopbot/bot_health.log`
- Проверка сервисов:
  - `systemctl status shopbot`
  - `systemctl status shopbot-web`

## Экспорт данных

В веб-админке доступны:
- товары: CSV/Excel;
- заказы: CSV/Excel (с фильтрами);
- пользователи: CSV/Excel;
- отзывы: CSV/Excel;
- продажи: Excel отчет с графиком за `day/week/month/year`.

## FAQ

### Бот не отвечает
- Проверьте `TELEGRAM_BOT_TOKEN` в `.env`;
- Проверьте `systemctl status shopbot`.

### Не создается платеж YooKassa
- Проверьте `YUKASSA_SHOP_ID` и `YUKASSA_SECRET_KEY`;
- Проверьте доступ webhook URL снаружи.

### Google Sheets не синхронизируется
- Проверьте наличие `credentials.json` в корне проекта;
- Проверьте, что сервисный аккаунт добавлен в доступ к таблице;
- Проверьте ручной запуск `/sheets_sync <id>`.

### Не открывается веб-админка
- Убедитесь, что запущен `shopbot-web`;
- Проверьте открытый порт `5001/tcp`.

### Неверный пароль в вебке
- Проверьте `ADMIN_WEB_PASSWORD` в `.env`;
- Перезапустите `shopbot-web`.

## Скриншоты

Добавьте реальные скриншоты в:
- `web/static/images/dashboard.png`
- `web/static/images/products.png`
- `web/static/images/orders.png`

Примеры подключений в документации:

```markdown
![Dashboard](web/static/images/dashboard.png)
![Products](web/static/images/products.png)
![Orders](web/static/images/orders.png)
```

## Лицензия

MIT (или ваша внутренняя лицензия проекта).
