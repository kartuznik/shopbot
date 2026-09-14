# ShopBot — Runbook

Операционное руководство для self-hosted деплоя. Секреты, публичные IP и домены сюда не пишем — используйте `.env`, Nginx и инфраструктуру окружения.

Деплой продукта: **venv + systemd** (Docker Compose в репозитории нет). Оба HTTP-слушателя сидят на петле: webhook YooKassa — **`127.0.0.1:8080`** (наружу через Nginx), веб-админка — **`127.0.0.1:5001`** (наружу через ssh-туннель или reverse proxy с TLS).

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
- Админка открывается на `127.0.0.1:5001` через ssh-туннель: `ssh -N -L 5001:127.0.0.1:5001 root@<YOUR_HOST> -p 2222`.
- Webhook слушает `127.0.0.1:8080` (адрес задаётся `WEBHOOK_HOST`/`WEBHOOK_PORT` в `.env`). Снаружи — только через Nginx; bind на `0.0.0.0` открывает порт в обход reverse proxy и запрещён.

### Nginx (webhook)

- Внешний путь: `/webhook/yookassa`
- Upstream: `127.0.0.1:8080`
- Публичный URL вида `https://<YOUR_DOMAIN>/webhook/yookassa?token=<WEBHOOK_SECRET_TOKEN>` укажите в кабинете YooKassa.
- Проксируйте `X-Real-IP $remote_addr` — на нём держится проверка отправителя (см. ниже).

> YooKassa шлёт уведомления только на HTTPS:443 или :8443. Текущий Nginx слушает `80` без TLS, поэтому в бою касса до эндпоинта не достучится. Блокер зафиксирован и закрывается отдельной задачей.

### Безопасность вебхука

YooKassa не подписывает уведомления — [документация](https://yookassa.ru/developers/using-api/webhooks) предлагает проверять статус объекта и IP отправителя. Эндпоинт проверяет три вещи подряд, и любая непройденная означает `400`, запись в лог и отсутствие изменений в базе:

1. **Подпись адреса** — `?token=` сверяется с `WEBHOOK_SECRET_TOKEN` (SENSITIVE_KEYS) через `hmac.compare_digest`, не через `==`.
2. **IP отправителя** — только официальные подсети кассы; реальный адрес берётся из `X-Real-IP` и только когда запрос пришёл с петли (от Nginx).
3. **Статус в кассе** — `GET /v3/payments/{id}` с Basic Auth `YUKASSA_SHOP_ID:YUKASSA_SECRET_KEY`; расхождение статуса или неизвестный платёж отбрасываются.

Диагностика отказов — по логу `shopbot`:

```bash
journalctl -u shopbot --since "1 hour ago" | grep -i "Уведомление отклонено"
```

Сообщение называет причину (`подпись не совпала`, `секрет не задан`, адрес вне подсетей, расхождение статуса). Само значение секрета в логи не попадает.

Проверка «плохая подпись даёт 400» с самого сервера:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  'http://127.0.0.1:8080/webhook/yookassa?token=wrong' \
  -H 'Content-Type: application/json' -H 'X-Real-IP: 185.71.76.1' -d '{}'   # ожидаем 400
```

### Firewall (типовой)

```bash
ufw allow 80/tcp
ufw reload
```

`5001` наружу не открываем: админка слушает петлю, снаружи — только ssh-туннель или reverse proxy с TLS.

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
| `WEBHOOK_SECRET_TOKEN` | `openssl rand -hex 32` → `.env` → обновить URL уведомлений в кабинете YooKassa (`?token=`) → `systemctl restart shopbot`. До обновления URL касса получает `400` и повторяет доставку сутки, поэтому меняйте оба места в одно окно |
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
