import uuid

import pytest

from app.core.jwt import create_access_token, decode_token


def test_access_token_roundtrip():
    player_uuid = uuid.uuid4()
    token = create_access_token(player_uuid, role="user")
    payload = decode_token(token)
    assert payload["sub"] == str(player_uuid)
    assert payload["role"] == "user"
    assert payload["type"] == "access"


def test_decode_rejects_tampered():
    token = create_access_token(uuid.uuid4(), role="user")
    bad = token[:-3] + "aaa"
    with pytest.raises(Exception):
        decode_token(bad)
