import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import jwt as pyjwt

from app.core.config import get_settings

_settings = get_settings()
_ALGO = "HS256"


def _create(
    sub: str,
    role: str,
    ttl: int,
    typ: Literal["access", "refresh"],
    extra_claims: Optional[dict] = None,
) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    jti = str(uuid.uuid4())
    payload = {
        "sub": sub,  # 现在是 account_id（int 字符串）
        "role": role,
        "type": typ,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "jti": jti,
    }
    if extra_claims:
        payload.update(extra_claims)
    return pyjwt.encode(payload, _settings.jwt_secret, algorithm=_ALGO), jti


def create_access_token(
    account_id: int,
    role: str,
    active_uuid: Optional[uuid.UUID] = None,
) -> str:
    """签发 access token。

    sub = account_id（主锚）；active_uuid 记录当前会话来源 UUID。
    """
    extra = {}
    if active_uuid is not None:
        extra["active_uuid"] = str(active_uuid)
    token, _ = _create(
        str(account_id), role, _settings.jwt_access_ttl_seconds, "access", extra
    )
    return token


def create_refresh_token(
    account_id: int,
    role: str,
    active_uuid: Optional[uuid.UUID] = None,
) -> tuple[str, str]:
    """签发 refresh token，返回 (token, jti)。"""
    extra = {}
    if active_uuid is not None:
        extra["active_uuid"] = str(active_uuid)
    return _create(
        str(account_id), role, _settings.jwt_refresh_ttl_seconds, "refresh", extra
    )


def decode_token(token: str) -> dict:
    return pyjwt.decode(token, _settings.jwt_secret, algorithms=[_ALGO])
