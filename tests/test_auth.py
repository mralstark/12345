"""Проверка подписи Telegram initData (ТЗ §2) — единственный барьер входа в Mini App."""

import hashlib
import hmac
import time
from urllib.parse import urlencode

import pytest

from fastapi import HTTPException

from api.auth import InitDataError, ensure_impersonation_read_only, validate_init_data
from database.models import User

TOKEN = "123456:TEST-TOKEN-FOR-TESTS"


def build_init_data(token: str = TOKEN, auth_date: int | None = None, user: str = '{"id":1001}') -> str:
    fields = {"auth_date": str(auth_date or int(time.time())), "query_id": "AAF", "user": user}
    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_valid_init_data_passes():
    data = validate_init_data(build_init_data(), TOKEN)
    assert data["user"] == '{"id":1001}'


def test_tampered_payload_rejected():
    raw = build_init_data()
    tampered = raw.replace("1001", "9999")
    with pytest.raises(InitDataError):
        validate_init_data(tampered, TOKEN)


def test_foreign_token_rejected():
    with pytest.raises(InitDataError):
        validate_init_data(build_init_data(token="999:OTHER"), TOKEN)


def test_missing_hash_rejected():
    with pytest.raises(InitDataError):
        validate_init_data("auth_date=1&user=%7B%7D", TOKEN)


def test_stale_init_data_rejected():
    stale = build_init_data(auth_date=int(time.time()) - 48 * 3600)
    with pytest.raises(InitDataError):
        validate_init_data(stale, TOKEN)
    # С отключённой проверкой возраста те же данные проходят.
    assert validate_init_data(stale, TOKEN, max_age=0)


def test_future_init_data_rejected():
    future = build_init_data(auth_date=int(time.time()) + 5 * 60)
    with pytest.raises(InitDataError):
        validate_init_data(future, TOKEN)


@pytest.mark.parametrize("auth_date", ["", "not-a-number", "0"])
def test_invalid_auth_date_rejected(auth_date):
    fields = {"auth_date": auth_date, "query_id": "AAF", "user": '{"id":1001}'}
    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    with pytest.raises(InitDataError):
        validate_init_data(urlencode(fields), TOKEN)


def test_impersonation_is_read_only_except_for_exit():
    target = User(full_name="Цель", telegram_id=1001)
    target._impersonated_by = User(full_name="Администратор", telegram_id=1002)

    ensure_impersonation_read_only(target, "GET", "/api/members")
    ensure_impersonation_read_only(target, "POST", "/api/me/stop-impersonation")
    with pytest.raises(HTTPException) as exc:
        ensure_impersonation_read_only(target, "PATCH", "/api/members/1")
    assert exc.value.status_code == 403


def test_oversized_or_duplicate_init_data_rejected():
    with pytest.raises(InitDataError, match="слишком велики"):
        validate_init_data("x=" + "a" * 9000, TOKEN)
    with pytest.raises(InitDataError, match="повторяющиеся"):
        validate_init_data("auth_date=1&auth_date=2&hash=x", TOKEN)
