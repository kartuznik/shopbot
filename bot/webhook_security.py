from __future__ import annotations

import base64
import hmac
import json
import logging
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from bot.config import get_settings

logger = logging.getLogger('shopbot.webhook.security')

IPAddress = IPv4Address | IPv6Address

# Официальный список отправителей уведомлений:
# https://yookassa.ru/developers/using-api/webhooks (раздел «Проверка IP-адреса»).
YOOKASSA_NETWORKS = tuple(
    ip_network(cidr)
    for cidr in (
        '185.71.76.0/27',
        '185.71.77.0/27',
        '77.75.153.0/25',
        '77.75.156.11/32',
        '77.75.156.35/32',
        '77.75.154.128/25',
        '2a02:5180::/32',
    )
)

_LOOPBACK_NETWORKS = (ip_network('127.0.0.0/8'), ip_network('::1/128'))

# ЮKassa не может вернуть такой статус, поэтому сентинел не столкнётся с настоящим:
# он означает «платежа с таким id в кассе нет», то есть уведомление поддельное.
PAYMENT_NOT_FOUND = 'not_found'


def _parse_ip(raw: str | None) -> IPAddress | None:
    if not raw:
        return None
    try:
        return ip_address(raw.strip())
    except ValueError:
        return None


def client_ip(
    remote_addr: str | None,
    real_ip_header: str | None = None,
    forwarded_for_header: str | None = None,
) -> IPAddress | None:
    """Адрес настоящего отправителя уведомления.

    Заголовкам верим только когда соединение пришло с петли, то есть от нашего же
    Nginx: он перезаписывает X-Real-IP значением $remote_addr, подделать его снаружи
    нельзя. У X-Forwarded-For берём последний элемент — Nginx дописывает адрес,
    который увидел сам, а всё левее приходит от клиента и доверия не заслуживает.
    """
    peer = _parse_ip(remote_addr)
    if peer is None or not any(peer in network for network in _LOOPBACK_NETWORKS):
        return peer

    real_ip = _parse_ip(real_ip_header)
    if real_ip is not None:
        return real_ip

    if forwarded_for_header:
        appended_by_proxy = _parse_ip(forwarded_for_header.split(',')[-1])
        if appended_by_proxy is not None:
            return appended_by_proxy

    return peer


def is_yookassa_ip(ip: IPAddress | None) -> bool:
    if ip is None:
        return False
    return any(ip in network for network in YOOKASSA_NETWORKS)


def verify_signature(provided: str | None, secret: str) -> bool:
    """Сверяет подпись уведомления за постоянное время.

    В документации ЮKassa нет HMAC-заголовка: подлинность предлагается
    подтверждать IP отправителя и статусом объекта в кассе. Подпись здесь —
    общий секрет из SENSITIVE_KEYS (`WEBHOOK_SECRET_TOKEN` в хвосте URL),
    который сравнивается через hmac.compare_digest, не через ``==``.

    Пустой секрет означает незаконфигуренный вебхук: проверка не проходит
    никогда, чтобы отсутствие настройки не превращалось в открытый эндпоинт.
    """
    if not secret or not provided:
        return False
    return hmac.compare_digest(provided.encode('utf-8'), secret.encode('utf-8'))


def fetch_payment_status(payment_id: str) -> str | None:
    """Статус платежа по данным самой кассы.

    Возвращает None, если проверку выполнить нельзя (ключи не заданы или API
    недоступно), PAYMENT_NOT_FOUND — если кассе такой платёж неизвестен.
    """
    settings = get_settings()
    shop_id = settings.YUKASSA_SHOP_ID.strip()
    secret_key = settings.YUKASSA_SECRET_KEY.strip()
    if not shop_id or not secret_key or not payment_id:
        return None

    auth_b64 = base64.b64encode(f'{shop_id}:{secret_key}'.encode('utf-8')).decode('ascii')
    request = urllib_request.Request(
        f'https://api.yookassa.ru/v3/payments/{payment_id}',
        headers={'Authorization': f'Basic {auth_b64}'},
        method='GET',
    )
    try:
        with urllib_request.urlopen(request, timeout=10) as response:
            body: dict[str, Any] = json.loads(response.read().decode('utf-8'))
    except urllib_error.HTTPError as http_error:
        if http_error.code == 404:
            return PAYMENT_NOT_FOUND
        logger.warning('Касса ответила %s на проверку платежа %s', http_error.code, payment_id)
        return None
    except (OSError, ValueError) as call_error:
        logger.warning('Не удалось перепроверить платёж %s: %s', payment_id, call_error)
        return None

    return str(body.get('status') or '') or None
