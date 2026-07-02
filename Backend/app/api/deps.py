import logging
import secrets
import uuid

import jwt as pyjwt
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.core.jwt import decode_token
from app.models.user import Player
from app.repositories import player_repo

logger = logging.getLogger(__name__)
_settings: Settings = get_settings()


def require_service_token(x_service_token: str | None = Header(default=None)) -> None:
    """校验 X-Service-Token（外部系统如 MCDR 调用 /sheets/export、/notifications/*）。

    用 ``secrets.compare_digest`` 防时序攻击（红线：复用 settings.mcdr_service_token）。
    """
    if not x_service_token or not secrets.compare_digest(
        x_service_token, _settings.mcdr_service_token
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid service token")


async def _player_from_jwt(session: AsyncSession, token: str) -> Player:
    try:
        payload = decode_token(token)
    except pyjwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong token type")
    # M-1'：sub 缺失/非法 → 401，绝不 500
    sub = payload.get("sub")
    if not isinstance(sub, str):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token subject")
    try:
        player_uuid = uuid.UUID(sub)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token subject")
    player = (
        await session.execute(select(Player).where(Player.uuid == player_uuid))
    ).scalar_one_or_none()
    if player is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "player not found")
    return player


async def _player_from_service_token(
    session: AsyncSession,
    request: Request,
    x_service_token: str | None,
    x_player_uuid: str | None,
) -> Player:
    """MCDR 代理通道：校验 service token 后用 X-Player-UUID 查 Player 注入。

    service token 与 UUID 缺一不可；token 用 ``secrets.compare_digest``，
    UUID 必须命中 Player 表（防止注入不存在的身份，R-5 身份锚 = player.uuid）。
    H-1'：命中后落结构化审计日志（不含 token），便于追查代玩家写操作的爆炸半径。
    """
    if not x_service_token or not secrets.compare_digest(
        x_service_token, _settings.mcdr_service_token
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid service token")
    if not x_player_uuid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing player uuid")
    try:
        parsed = uuid.UUID(x_player_uuid)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid player uuid")
    player = await player_repo.get_by_uuid(session, parsed)
    if player is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "player not found")
    logger.info(
        "service_token_proxy player_uuid=%s path=%s",
        player.uuid,
        request.url.path,
    )
    return player


async def get_current_player(
    request: Request,
    authorization: str | None = Header(default=None),
    x_service_token: str | None = Header(default=None),
    x_player_uuid: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Player:
    """双通道身份解析（业务层零改动）：

    1. 优先 ``Authorization: Bearer <jwt>``（Web，沿用 core/jwt 解码）。
    2. 否则 ``X-Service-Token`` + ``X-Player-UUID``（MCDR 代理）：
       校验 service token → 查 Player → 注入同型 ``Player``（R-5 身份锚=uuid）。

    H-2：Authorization 头存在（即便非 Bearer/过期/非法）也只走 JWT 通道报 401，
    **绝不静默降级**到 service-token——防止 JWT 失效被悄悄当作 MCDR 代理放过。
    RBAC 不变：sheets 的 ``_can_edit`` / ``claimant_uuid == player.uuid`` 均基于
    ``Player``，与凭证来源无关。``/sheets/export`` 与 notifications 端点仍独占
    ``require_service_token``（无身份）。
    """
    if authorization is not None:
        # 有 Authorization 头：必须是合法 Bearer JWT，否则 401（H-2 不降级）
        if not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer")
        token = authorization.removeprefix("Bearer ").strip()
        return await _player_from_jwt(session, token)
    return await _player_from_service_token(
        session, request, x_service_token, x_player_uuid
    )


def require_role(role: str):
    async def _check(player: Player = Depends(get_current_player)) -> Player:
        if player.role != role and player.role != "owner":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
        return player
    return _check
