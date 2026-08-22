import logging
import secrets
import uuid
from dataclasses import dataclass

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
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

# ---------------------------------------------------------------------------
# OpenAPI security schemes（纯元数据）：``auto_error=False`` 时 ``APIKeyHeader``
# 返回 ``str | None``，与 ``Header(default=None)`` 完全等价 → 鉴权行为零变，
# /docs 获得 Authorize 按钮。Authorization 故意不用 ``HTTPBearer``：
# ``auto_error=False`` 下它对「无头」与「头存在但非 Bearer」同样返回 None，
# 无法区分，而 H-2（RS-8：Authorization 头存在绝不静默降级）依赖该区分。
# ---------------------------------------------------------------------------

service_token_scheme = APIKeyHeader(
    name="X-Service-Token",
    scheme_name="X-Service-Token",
    auto_error=False,
    description="服务端组件令牌（MCDR / 服务端 mod / 服主脚本）",
)
player_uuid_scheme = APIKeyHeader(
    name="X-Player-UUID",
    scheme_name="X-Player-UUID",
    auto_error=False,
    description="MCDR 代理通道玩家 UUID（与 X-Service-Token 成对）",
)
source_id_scheme = APIKeyHeader(
    name="X-Source-Id",
    scheme_name="X-Source-Id",
    auto_error=False,
    description="施工上报源标识（服务端 mod 白名单名）",
)
bearer_scheme = APIKeyHeader(
    name="Authorization",
    scheme_name="Authorization",
    auto_error=False,
    description='Web JWT 通道，值形如 "Bearer <jwt>"',
)


def require_service_token(
    x_service_token: str | None = Security(service_token_scheme),
) -> None:
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
    authorization: str | None = Security(bearer_scheme),
    x_service_token: str | None = Security(service_token_scheme),
    x_player_uuid: str | None = Security(player_uuid_scheme),
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


@dataclass(frozen=True)
class ViewerIdentity:
    """只读端点查看者身份（account 级，player-less 托管账号可用，issue #74）。

    - ``account``：JWT 通道 = sub 账号；service-token 通道 = player 所属账号
      （未绑 → ``None``，历史数据兼容）。
    - ``uuids``：该账号全部绑定 UUID 集合（未绑回退 ``{player.uuid}``；
      player-less 账号 → 空集）。
    - ``active_uuid``：会话来源 UUID（service-token 通道 = 该 player；
      player-less → ``None``）。已过 M1 复验（仍属 ``account``）。
    - ``player``：会话来源 Player 对象（M1 复验顺手加载，调用方免二次查询；
      player-less → ``None``）。
    """

    account: WebAccount | None
    uuids: frozenset[uuid.UUID]
    active_uuid: uuid.UUID | None
    player: Player | None = None


async def get_current_viewer(
    request: Request,
    authorization: str | None = Security(bearer_scheme),
    x_service_token: str | None = Security(service_token_scheme),
    x_player_uuid: str | None = Security(player_uuid_scheme),
    session: AsyncSession = Depends(get_session),
) -> ViewerIdentity:
    """只读端点身份解析：双通道同 :func:`get_current_player`，但不要求 active_uuid。

    供「浏览型」GET 端点（sheets 列表/详情、construction progress）使用：
    无绑定玩家的托管管理账号 JWT 也能通过（issue #74）。H-2 同样适用——
    Authorization 头存在（即便非 Bearer/过期/非法）只走 JWT 通道报 401，
    绝不静默降级到 service-token。带 active_uuid 时做 M1 复验（同
    ``_player_from_jwt``：仍须属于该账号，防玩家迁移后旧 token 继续
    以玩家视角读）。
    """
    if authorization is not None:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer")
        token = authorization.removeprefix("Bearer ").strip()
        account, active_uuid = await _account_and_active_uuid_from_jwt(session, token)
        player: Player | None = None
        if active_uuid is not None:
            # M1 复验：防 access token 存活期内玩家迁到别的 account
            player = (
                await session.execute(
                    select(Player).where(
                        Player.uuid == active_uuid,
                        Player.web_account_id == account.id,
                    )
                )
            ).scalar_one_or_none()
            if player is None:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, "player not bound to account"
                )
        merged = set(await web_account_repo.list_uuids(session, account.id))
        if player is not None:
            merged.add(player.uuid)
        return ViewerIdentity(
            account=account,
            uuids=frozenset(merged),
            active_uuid=active_uuid,
            player=player,
        )
    player = await _player_from_service_token(
        session, request, x_service_token, x_player_uuid
    )
    if player.web_account_id is None:
        return ViewerIdentity(
            account=None,
            uuids=frozenset({player.uuid}),
            active_uuid=player.uuid,
            player=player,
        )
    account = await web_account_repo.get_by_id(session, player.web_account_id)
    uuids = set(await web_account_repo.list_uuids(session, player.web_account_id))
    uuids.add(player.uuid)
    if account is None:  # 悬空 web_account_id 防御：按未绑回退
        return ViewerIdentity(
            account=None,
            uuids=frozenset({player.uuid}),
            active_uuid=player.uuid,
            player=player,
        )
    return ViewerIdentity(
        account=account,
        uuids=frozenset(uuids),
        active_uuid=player.uuid,
        player=player,
    )


async def get_current_account(
    authorization: str | None = Security(bearer_scheme),
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


async def get_active_uuid_optional(
    authorization: str | None = Security(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> uuid.UUID | None:
    """同 :func:`get_active_uuid`，但无 ``active_uuid`` claim 时返回 ``None``。

    供「账号级端点 + 可选玩家上下文」使用（如 ``GET /me``、mod-sources 审批人）：
    player-less 托管管理账号（``ADMIN_*`` env，issue #74）无 claim 不再 401。
    """
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer")
    token = authorization.removeprefix("Bearer ").strip()
    _, active_uuid = await _account_and_active_uuid_from_jwt(session, token)
    return active_uuid


async def get_active_uuid(
    authorization: str | None = Security(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> uuid.UUID:
    """从 JWT 的 active_uuid claim 解析当前会话来源 Player UUID。

    无 active_uuid → 401（用于需要具体 Player 的 Web 账号级端点）。
    """
    active_uuid = await get_active_uuid_optional(authorization, session)
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
    """RBAC 权限检查（account 级：role 权威源 = WebAccount.role，仅 Bearer JWT）。

    与 scoring ``require_privileged_access`` 同范式（admin ≠ service-token）：
    管理端点不认 ``X-Service-Token`` 通道；无绑定玩家的托管管理账号
    （``ADMIN_*`` env，JWT 无 active_uuid）也能通过（issue #74）。
    """
    async def _check(
        account: WebAccount = Depends(get_current_account),
    ) -> WebAccount:
        if account.role != role and account.role != "owner":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
        return account
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
    authorization: str | None = Security(bearer_scheme),
    x_service_token: str | None = Security(service_token_scheme),
    x_source_id: str | None = Security(source_id_scheme),
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
