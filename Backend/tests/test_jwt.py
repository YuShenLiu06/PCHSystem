import uuid

import pytest

from app.core.jwt import create_access_token, decode_token


def test_access_token_roundtrip():
    # JWT 契约升级：sub = account_id（int 字符串）
    account_id = 42
    player_uuid = uuid.uuid4()
    token = create_access_token(account_id, role="user", active_uuid=player_uuid)
    payload = decode_token(token)
    assert payload["sub"] == str(account_id)
    assert payload["role"] == "user"
    assert payload["type"] == "access"
    assert payload["active_uuid"] == str(player_uuid)


def test_access_token_without_active_uuid():
    """密码登录/注册路径无 active_uuid，claim 应缺省（不在 payload）。"""
    token = create_access_token(7, role="user")
    payload = decode_token(token)
    assert payload["sub"] == "7"
    assert "active_uuid" not in payload


def test_decode_rejects_tampered():
    token = create_access_token(uuid.uuid4().int, role="user")
    bad = token[:-3] + "aaa"
    with pytest.raises(Exception):
        decode_token(bad)
