from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from flask.testing import FlaskClient

from bot import webhook
from bot.webhook_security import (
    PAYMENT_NOT_FOUND,
    client_ip,
    is_yookassa_ip,
    verify_signature,
)
from tests.conftest import WEBHOOK_TOKEN, YOOKASSA_IP, notification, order_state

UNPAID_STATE = ('new', 'pending', None)


def post_notification(
    client: FlaskClient,
    *,
    token: str | None = WEBHOOK_TOKEN,
    sender_ip: str = YOOKASSA_IP,
    peer: str = '127.0.0.1',
    body: dict[str, Any] | None = None,
) -> Any:
    url = '/webhook/yookassa' if token is None else f'/webhook/yookassa?token={token}'
    return client.post(
        url,
        json=notification() if body is None else body,
        headers={'X-Real-IP': sender_ip},
        environ_base={'REMOTE_ADDR': peer},
    )


def test_valid_signature_is_processed(client: FlaskClient, db_path: Path) -> None:
    response = post_notification(client)

    assert response.status_code == 200
    assert response.get_json() == {'ok': True}
    order_status, payment_status, paid_at = order_state(db_path)
    assert (order_status, payment_status) == ('paid', 'succeeded')
    assert paid_at is not None


def test_invalid_signature_is_rejected_without_state_change(
    client: FlaskClient, db_path: Path
) -> None:
    response = post_notification(client, token='wrong-token')

    assert response.status_code == 400
    assert order_state(db_path) == UNPAID_STATE


def test_missing_signature_is_rejected_without_state_change(
    client: FlaskClient, db_path: Path
) -> None:
    """Регрессия боевого долга аудита 14.09: анонимный POST помечал заказ оплаченным."""
    response = post_notification(client, token=None)

    assert response.status_code == 400
    assert order_state(db_path) == UNPAID_STATE


def test_empty_token_setting_closes_endpoint(
    client: FlaskClient, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot.config import get_settings

    monkeypatch.setenv('WEBHOOK_SECRET_TOKEN', '')
    get_settings.cache_clear()

    response = post_notification(client, token='')

    assert response.status_code == 400
    assert order_state(db_path) == UNPAID_STATE


def test_ip_outside_yookassa_networks_is_rejected(client: FlaskClient, db_path: Path) -> None:
    response = post_notification(client, sender_ip='203.0.113.7')

    assert response.status_code == 400
    assert order_state(db_path) == UNPAID_STATE


def test_forged_real_ip_header_from_direct_peer_is_ignored(
    client: FlaskClient, db_path: Path
) -> None:
    response = post_notification(client, sender_ip=YOOKASSA_IP, peer='203.0.113.7')

    assert response.status_code == 400
    assert order_state(db_path) == UNPAID_STATE


def test_status_mismatch_with_kassa_is_rejected(
    client: FlaskClient, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(webhook, 'fetch_payment_status', lambda payment_id: 'pending')

    response = post_notification(client)

    assert response.status_code == 400
    assert order_state(db_path) == UNPAID_STATE


def test_unknown_payment_in_kassa_is_rejected(
    client: FlaskClient, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(webhook, 'fetch_payment_status', lambda payment_id: PAYMENT_NOT_FOUND)

    response = post_notification(client)

    assert response.status_code == 400
    assert order_state(db_path) == UNPAID_STATE


def test_confirmed_status_is_processed(
    client: FlaskClient, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(webhook, 'fetch_payment_status', lambda payment_id: 'succeeded')

    response = post_notification(client)

    assert response.status_code == 200
    assert order_state(db_path)[:2] == ('paid', 'succeeded')


def test_repeated_delivery_is_idempotent(client: FlaskClient, db_path: Path) -> None:
    first = post_notification(client)
    state_after_first = order_state(db_path)
    second = post_notification(client)

    assert (first.status_code, second.status_code) == (200, 200)
    assert order_state(db_path) == state_after_first


def test_authorized_but_empty_payload_is_rejected(client: FlaskClient, db_path: Path) -> None:
    response = post_notification(client, body={'type': 'notification', 'object': {}})

    assert response.status_code == 400
    assert order_state(db_path) == UNPAID_STATE


@pytest.mark.parametrize(
    'address, expected',
    [
        ('185.71.76.1', True),
        ('185.71.77.31', True),
        ('77.75.153.20', True),
        ('77.75.156.11', True),
        ('77.75.154.200', True),
        ('2a02:5180::1', True),
        ('185.71.76.32', False),
        ('77.75.156.12', False),
        ('77.75.154.127', False),
        ('203.0.113.7', False),
    ],
)
def test_yookassa_network_boundaries(address: str, expected: bool) -> None:
    from ipaddress import ip_address

    assert is_yookassa_ip(ip_address(address)) is expected


def test_client_ip_prefers_proxy_headers_only_behind_loopback() -> None:
    assert str(client_ip('127.0.0.1', YOOKASSA_IP, None)) == YOOKASSA_IP
    assert str(client_ip('203.0.113.7', YOOKASSA_IP, None)) == '203.0.113.7'


def test_client_ip_takes_address_appended_by_proxy() -> None:
    forwarded = f'185.71.76.1, {YOOKASSA_IP}'
    assert str(client_ip('127.0.0.1', None, forwarded)) == YOOKASSA_IP

    spoofed = f'{YOOKASSA_IP}, 203.0.113.7'
    assert str(client_ip('127.0.0.1', None, spoofed)) == '203.0.113.7'


def test_verify_signature_uses_constant_time_compare() -> None:
    assert verify_signature(None, WEBHOOK_TOKEN) is False
    assert verify_signature('', WEBHOOK_TOKEN) is False
    assert verify_signature(WEBHOOK_TOKEN, '') is False
    assert verify_signature(WEBHOOK_TOKEN, WEBHOOK_TOKEN) is True
    assert verify_signature(WEBHOOK_TOKEN + 'x', WEBHOOK_TOKEN) is False
    assert verify_signature('short', WEBHOOK_TOKEN) is False
