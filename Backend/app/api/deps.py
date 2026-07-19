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
from app.models.user import Player, WebAccount
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


async def _account_and_active_uuid_from_jwt(
    session: AsyncSession, token: str
) -> tuple[WebAccount, uuid.UUID | None]:
    """从 JWT 解析 (WebAccount, active_uuid|None)。

    sub=account_id；active_uuid 可选（密码登录 / 注册路径无 active_uuid）。
    """
    try:
        payload = decode_token(token)
    except pyjwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong token type")
    sub = payload.get("sub")
    if not isinstance(sub, str):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token subject")
    try:
        account_id = int(sub)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token subject")
    account = (
        await session.execute(select(WebAccount).where(WebAccount.id == account_id))
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account not found")
    active_uuid_str = payload.get("active_uuid")
    active_uuid: uuid.UUID | None = None
    if isinstance(active_uuid_str, str):
        try:
            active_uuid = uuid.UUID(active_uuid_str)
        except ValueError:
            # 非法 active_uuid 当作无（让需要它的端点自行 401）
            active_uuid = None
    return account, active_uuid


async def _account_from_jwt(session: AsyncSession, token: str) -> WebAccount:
    """从 JWT 解析 WebAccount（保留旧入口，内部转发）。"""
    account, _ = await _account_and_active_uuid_from_jwt(session, token)
    return account


async def _player_from_jwt(session: AsyncSession, token: str) -> Player:
    """从 JWT 解析 Player（sub 现在是 account_id → 查 account → active_uuid）。

    改动：sub 不再是 player_uuid，而是 account_id。需先查 WebAccount，
    再从 payload 取 active_uuid 查 Player。
    """
    try:
        payload = decode_token(token)
    except pyjwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong token type")
    sub = payload.get("sub")
    if not isinstance(sub, str):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token subject")
    try:
        account_id = int(sub)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token subject")

    # 先查 account（保证存在）
    account = (
        await session.execute(select(WebAccount).where(WebAccount.id == account_id))
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account not found")

    # 再取 active_uuid
    active_uuid_str = payload.get("active_uuid")
    if not active_uuid_str or not isinstance(active_uuid_str, str):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing active_uuid")
    try:
        player_uuid = uuid.UUID(active_uuid_str)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid active_uuid")

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
    """双通道身份解析（JWT 通道已适配 account_id 主锚）：

    1. 优先 ``Authorization: Bearer <jwt>``（Web）：
       JWT sub=account_id → 查 account → active_uuid 查 Player。
    2. 否则 ``X-Service-Token`` + ``X-Player-UUID``（MCDR 代理）：
       直接查 Player（不变）。

    H-2：Authorization 头存在（即便非 Bearer/过期/非法）也只走 JWT 通道报 401，
    **绝不静默降级**到 service-token。
    """
    if authorization is not None:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer")
        token = authorization.removeprefix("Bearer ").strip()
        return await _player_from_jwt(session, token)
    return await _player_from_service_token(
        session, request, x_service_token, x_player_uuid
    )


async def get_current_account(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> WebAccount:
    """从 JWT 解析当前 WebAccount（Web 账号级端点用）。

    仅接受 Bearer JWT（sub=account_id）；不支持 service-token 通道。
    未登 → 401。
    """
    if authorization is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing authorization")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer")
    token = authorization.removeprefix("Bearer ").strip()
    return await _account_from_jwt(session, token)


async def get_active_uuid(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> uuid.UUID:
    """从 JWT 的 active_uuid claim 解析当前会话来源 Player UUID。

    无 active_uuid → 401（用于需要具体 Player 的 Web 账号级端点，如 /me）。
    """
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer")
    token = authorization.removeprefix("Bearer ").strip()
    _, active_uuid = await _account_and_active_uuid_from_jwt(session, token)
    if active_uuid is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing active_uuid")
    return active_uuid


def _resolve_role(player: Player, account: WebAccount | None = None) -> str:
    """解析 role（权威源 = WebAccount；未绑回退 Player.role）。"""
    if account is not None:
        return account.role
    if player.web_account is not None:
        return player.web_account.role
    return player.role


def require_role(role: str):
    """RBAC 权限检查（role 权威源改为 WebAccount）。"""
    async def _check(
        player: Player = Depends(get_current_player),
    ) -> Player:
        effective_role = _resolve_role(player)
        if effective_role != role and effective_role != "owner":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
        return player
    return _check
