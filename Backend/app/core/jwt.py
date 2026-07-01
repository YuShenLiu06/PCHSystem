import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt as pyjwt

from app.core.config import get_settings

_settings = get_settings()
_ALGO = "HS256"


def _create(player_uuid: uuid.UUID, role: str, ttl: int, typ: Literal["access", "refresh"]) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    jti = str(uuid.uuid4())
    payload = {
        "sub": str(player_uuid),
        "role": role,
        "type": typ,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "jti": jti,
    }
    return pyjwt.encode(payload, _settings.jwt_secret, algorithm=_ALGO), jti


def create_access_token(player_uuid: uuid.UUID, role: str) -> str:
    token, _ = _create(player_uuid, role, _settings.jwt_access_ttl_seconds, "access")
    return token


def create_refresh_token(player_uuid: uuid.UUID, role: str) -> tuple[str, str]:
    """返回 (token, jti)。"""
    return _create(player_uuid, role, _settings.jwt_refresh_ttl_seconds, "refresh")


def decode_token(token: str) -> dict:
    return pyjwt.decode(token, _settings.jwt_secret, algorithms=[_ALGO])
