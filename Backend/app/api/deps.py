import logging
import secrets
import uuid
from dataclasses import dataclass

import jwt as pyjwt
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.core.jwt import decode_token
from app.models.construction import ServerModSource
from app.models.user import Player, WebAccount
from app.repositories import player_repo, web_account_repo

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

    # M1：复验 active_uuid 仍属 JWT sub 账号（与 /auth/refresh 一致）——
    # 防 access token 被窃后、player 迁到别的 account 仍能用旧 token 代写。
    player = (
        await session.execute(
            select(Player).where(
                Player.uuid == player_uuid,
                Player.web_account_id == account_id,
            )
        )
    ).scalar_one_or_none()
    if player is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "player not bound to account")
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


async def get_current_account_uuids(
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> set[uuid.UUID]:
    """解析当前 player 同 WebAccount 的全部 UUID 集合（含自己）。

    用于 sheets 权限/聚合层升级到 account 级（R-5 主锚落地）：owner/claimant 校验
    改为 ``uuid in account_uuids``、contributors 聚合/「我参与的行」高亮按集合命中。

    未绑 account（``web_account_id IS NULL``，历史数据 / !!PCH login 自动挂临时账号前）
    回退 ``{player.uuid}`` 单元素集合，向后兼容。
    """
    if player.web_account_id is None:
        return {player.uuid}
    uuids = await web_account_repo.list_uuids(session, player.web_account_id)
    return set(uuids) | {player.uuid}


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


# ---------------------------------------------------------------------------
# 施工上报鉴权（construction 层专用，不复用 get_current_player）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReporterIdentity:
    """施工上报方身份（:func:`get_construction_reporter` 解析结果）。

    与 ``get_current_player`` 的区别：service-token 通道接受**多玩家** batch
    （actor 由 payload 每条 entry 决定，非单一 X-Player-UUID）；JWT 通道要求
    **mod_id** claim（客户端 mod 令牌，非普通玩家 access token）。

    - ``channel="jwt"``：客户端 mod 通道，``active_uuid`` 强制覆盖批内 player_uuid。
    - ``channel="service_token"``：MCDR / 服务端 mod 多玩家代报。
    - source 标识：``{mcdr, official}``（默认官方追踪器，C-1）/ ``{server_mod, <白名单名>}``
      / ``{client_mod, <mod_id>}``（C-10）。
    """

    channel: str  # "jwt" | "service_token"
    source_type: str  # "client_mod" | "mcdr" | "server_mod"
    source_id: str
    active_uuid: uuid.UUID | None = None  # 仅 jwt 通道


async def get_construction_reporter(
    request: Request,
    authorization: str | None = Header(default=None),
    x_service_token: str | None = Header(default=None),
    x_source_id: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> ReporterIdentity:
    """施工上报双通道鉴权（D1 / construction-progress.md §3）。

    H-2：``Authorization`` 头存在（即便非 Bearer/非法）只走 JWT 通道报 401，
    **绝不静默降级**到 service-token（同 RS-8）。

    - JWT 通道：解 token 取 ``mod_id`` claim（缺 → 401「not a mod token」）+
      ``active_uuid``；source = ``{client_mod, mod_id}``（C-10）。
    - service-token 通道：``secrets.compare_digest`` 校验；无 ``X-Source-Id`` →
      ``{mcdr, official}``（C-1）；有 ``X-Source-Id`` → ``{server_mod, <name>}``，
      须在白名单（防伪造，否则 403）。
    """
    if authorization is not None:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer")
        token = authorization.removeprefix("Bearer ").strip()
        try:
            payload = decode_token(token)
        except pyjwt.PyJWTError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
        if payload.get("type") != "access":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong token type")
        mod_id = payload.get("mod_id")
        if not isinstance(mod_id, str) or not mod_id.strip():
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "not a mod token (missing mod_id claim)",
            )
        active_uuid_str = payload.get("active_uuid")
        if not isinstance(active_uuid_str, str):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing active_uuid")
        try:
            active_uuid = uuid.UUID(active_uuid_str)
        except ValueError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid active_uuid")
        logger.info(
            "construction_reporter channel=jwt mod_id=%s path=%s",
            mod_id, request.url.path,
        )
        return ReporterIdentity(
            channel="jwt",
            source_type="client_mod",
            source_id=mod_id,
            active_uuid=active_uuid,
        )

    if not x_service_token or not secrets.compare_digest(
        x_service_token, _settings.mcdr_service_token
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid service token")
    if x_source_id:
        found = (
            await session.execute(
                select(ServerModSource.name).where(
                    ServerModSource.name == x_source_id,
                    ServerModSource.enabled.is_(True),
                )
            )
        ).scalar_one_or_none()
        if found is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"server mod source '{x_source_id}' not whitelisted or disabled",
            )
        logger.info(
            "construction_reporter channel=service_token server_mod=%s path=%s",
            x_source_id, request.url.path,
        )
        return ReporterIdentity(
            channel="service_token", source_type="server_mod", source_id=x_source_id
        )
    logger.info(
        "construction_reporter channel=service_token mcdr=official path=%s",
        request.url.path,
    )
    return ReporterIdentity(
        channel="service_token", source_type="mcdr", source_id="official"
    )
