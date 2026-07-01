import secrets
import uuid

import jwt as pyjwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.core.jwt import decode_token
from app.models.user import Player

_settings: Settings = get_settings()


def require_service_token(x_service_token: str | None = Header(default=None)) -> None:
    if not x_service_token or not secrets.compare_digest(
        x_service_token, _settings.mcdr_service_token
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid service token")


async def get_current_player(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Player:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_token(token)
    except pyjwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong token type")
    player = (
        await session.execute(select(Player).where(Player.uuid == uuid.UUID(payload["sub"])))
    ).scalar_one_or_none()
    if player is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "player not found")
    return player


def require_role(role: str):
    async def _check(player: Player = Depends(get_current_player)) -> Player:
        if player.role != role and player.role != "owner":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
        return player
    return _check
