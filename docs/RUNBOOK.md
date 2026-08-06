# ShopBot — Runbook

Операционное руководство для self-hosted деплоя. Секреты, публичные IP и домены сюда не пишем — используйте `.env`, Nginx и инфраструктуру окружения.

Деплой продукта: **venv + systemd** (Docker Compose в репозитории нет). Webhook YooKassa — порт **`:8080`**. Веб-админка — порт **`:5001`**.

## Deploy (systemd + Nginx)

### Подготовка

1. Клонируйте репозиторий (типичный путь на сервере: `/opt/bots/shopbot`).
2. Создайте venv, установите `requirements.txt`.
3. Скопируйте `.env.example` → `.env` и заполните минимум:
   - `TELEGRAM_BOT_TOKEN`, `ADMIN_IDS`, `DB_PATH`, `LANGUAGE`
   - `ADMIN_WEB_PASSWORD`
   - `YUKASSA_SHOP_ID`, `YUKASSA_SECRET_KEY`
4. Убедитесь, что каталог данных для SQLite существует (родительский путь `DB_PATH`).

### systemd

В репозитории есть примеры unit-файлов:

- `web/shopbot-web.service` — Flask admin
- `systemd/surveybot-check.service` + `systemd/surveybot-check.timer` — периодическая health-проверка (исторические имена файлов)

Установите/адаптируйте unit для бота (процесс `python -m bot.main`) и вебки, затем:

```bash
systemctl daemon-reload
systemctl enable --now shopbot
systemctl enable --now shopbot-web
systemctl status shopbot
systemctl status shopbot-web
```

Проверка:

- Бот отвечает в Telegram (`/start`, `/health` для админа).
- Админка открывается на `:5001`.
- Webhook слушает `127.0.0.1:8080` (или `0.0.0.0:8080` внутри процесса — снаружи только через Nginx).

### Nginx (webhook)

- Внешний путь: `/webhook/yookassa`
- Upstream: `127.0.0.1:8080`
- Публичный URL вида `https://<YOUR_DOMAIN>/webhook/yookassa` укажите в кабинете YooKassa.

### Firewall (типовой)

```bash
ufw allow 80/tcp
ufw allow 5001/tcp
ufw reload
```

После docs-only изменений перезапуск сервисов не нужен. После изменений кода:

```bash
systemctl restart shopbot
systemctl restart shopbot-web
```

## Backup и restore (SQLite)

### Backup

1. Кратко остановите writers (бот и/или веб), либо скопируйте при низкой нагрузке.
2. Скопируйте файл БД из `DB_PATH` (обычно `data/shopbot.db`) в безопасное хранилище вне git.
3. Отдельно бэкапьте `.env` и `credentials.json` (секреты) в шифрованное хранилище.

```bash
systemctl stop shopbot shopbot-web
mkdir -p ./backups
cp -a data/shopbot.db ./backups/shopbot-$(date +%Y%m%d).db
systemctl start shopbot shopbot-web
```

(путь БД сверяйте с `DB_PATH` в `.env`.)

### Restore

1. Остановить `shopbot` и `shopbot-web`.
2. Заменить файл БД из backup.
3. Запустить сервисы.
4. Smoke: `/start`, просмотр каталога, вход в админку `:5001`, `/health`.

## Ротация ключей

Ротируйте один секрет за раз; после правки `.env` — `grep '^VAR=' .env` **до** restart (см. `/opt/standards/RULES.md` §4a).

| Секрет | Шаги |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Новый токен в BotFather → `.env` → `systemctl restart shopbot` |
| `YUKASSA_SHOP_ID` / `YUKASSA_SECRET_KEY` | Обновить в кабинете и `.env` → restart `shopbot` → проверить webhook |
| `ADMIN_WEB_PASSWORD` | Обновить `.env` → `systemctl restart shopbot-web` |
| `ADMIN_IDS` | Обновить список → restart `shopbot` |
| Google `credentials.json` | Новый JSON service account → права на таблицу → `/sheets_sync` |

Живые секреты не вставлять в git, issues и чат.

## Инциденты

### Бот не отвечает

- `systemctl status shopbot`, `journalctl -u shopbot -f`
- `TELEGRAM_BOT_TOKEN`, сеть до api.telegram.org
- `/health` от админа; `bot_health.log`

### Платежи / webhook YooKassa

- Ключи `YUKASSA_*` в `.env`
- Nginx → `127.0.0.1:8080`, доступность внешнего URL webhook
- Логи webhook при ошибке обработки; пользователю при создании платежа показывается честная ошибка

### Google Sheets

- Наличие `credentials.json`, доступ Editor у service account
- Повтор `/sheets_sync <id>`; сбой sync не должен ронять магазин

### Веб-админка

- `systemctl status shopbot-web`, порт `:5001`
- `ADMIN_WEB_PASSWORD` (не печатать в чат)
- При падении вебки бот продолжает работать независимо

## Rollback

```bash
cd /opt/bots/shopbot
git log --oneline -5
git checkout <known-good-sha>
systemctl restart shopbot shopbot-web
```

При повреждении данных — restore SQLite из backup до smoke.
