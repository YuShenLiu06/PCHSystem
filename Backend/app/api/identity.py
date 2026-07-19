"""身份管理 API（注册、登录、绑定）。

端点契约（冻结，权威：方案 §一.10 + 跨端对端实现）：
- ``POST /auth/login``                公开               用户名密码登录 → 永久账号 AuthResponse
- ``POST /web-accounts/register``     JWT（临时账号）    临时账号转永久（设用户名+密码）
- ``GET  /web-accounts/me``           JWT                当前账号 + 绑定 players（{account, players}）
- ``POST /bind/token``                service-token      游戏内 !!PCH bind → 出短码（game_init）；body {uuid, name}
- ``POST /bind/issue``                JWT（永久账号）    Web 发起 → 出短码（web_init）
- ``POST /bind/confirm``              JWT（永久账号）    Web 确认 game_init 短码 → {player, account}（无 token）
- ``POST /bind/consume``              service-token+X-Player-UUID  游戏内 !!PCH bind <code> 消费 web_init → {status, player, account}
- ``POST /bind/claim``                JWT（临时账号）    临时账号 → 永久账号（凭据校验+迁移）

JWT 契约：``sub=account_id``（str）、``active_uuid`` 可选 claim（会话来源 UUID）。
"""
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_account, require_service_token
from app.core.config import get_settings
from app.core.db import get_session
from app.core.jwt import create_access_token, create_refresh_token
from app.core.security import hash_password, verify_password
from app.models.user import Player, WebAccount
from app.repositories import bind_token_repo, player_repo, web_account_repo
from app.schemas.auth import AccountBrief, PlayerBrief, TokenExchangeResponse
from app.schemas.identity import (
    BindConsumeRequest,
    BindConsumeResponse,
    BindConfirmRequest,
    BindConfirmResponse,
    BindTokenIssueResponse,
    BindTokenRequest,
    ClaimBindRequest,
    MyAccountResponse,
    PasswordLoginRequest,
    RegisterRequest,
    UpdateDisplayNameRequest,
)

router = APIRouter(prefix="/web-accounts", tags=["identity"])
bind_router = APIRouter(prefix="/bind", tags=["bind"])
auth_router = APIRouter(prefix="/auth", tags=["auth"])
_settings = get_settings()


def _account_brief(account: WebAccount) -> AccountBrief:
    """构造 AccountBrief（含 role + display_name）。"""
    return AccountBrief(
        id=account.id,
        is_temporary=account.is_temporary,
        username=account.username,
        display_name=account.display_name,
        role=account.role,
    )


def _player_brief(player: Player, role: str) -> PlayerBrief:
    """构造 PlayerBrief，role 取 account 权威值。"""
    return PlayerBrief(uuid=player.uuid, name=player.current_name, role=role)


def _issue_jwt_for_account(
    account: WebAccount, active_uuid: uuid.UUID | None
) -> tuple[str, str]:
    """为账号签发 access + refresh，返回 (access, refresh)。"""
    access = create_access_token(account.id, account.role, active_uuid=active_uuid)
    refresh, _ = create_refresh_token(account.id, account.role, active_uuid=active_uuid)
    return access, refresh


# ===== POST /auth/login（密码登录）=====


@auth_router.post("/login", response_model=TokenExchangeResponse)
async def password_login(
    body: PasswordLoginRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenExchangeResponse:
    """用户名+密码登录（必须永久账号）→ 完整 AuthResponse。

    契约：永久账号必有至少一个绑定 player（!!PCH login 即自动挂临时账号 → register 转永久），
    player 取该账号第一个绑定 player；active_uuid 留空（玩家后续可在身份管理页选/绑）。
    """
    account = await web_account_repo.get_by_username(session, body.username)
    if account is None or account.is_temporary:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    if not account.password_hash or not verify_password(body.password, account.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")

    players = await web_account_repo.list_players(session, account.id)
    if not players:
        # 永久账号必有至少一个绑定 player；防御性兜底（数据异常时显式 401 而非 500）
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account has no bound player")

    first = players[0]
    access, refresh = _issue_jwt_for_account(account, None)
    return TokenExchangeResponse(
        access_token=access,
        refresh_token=refresh,
        player=_player_brief(first, account.role),
        account=_account_brief(account),
    )


# ===== POST /web-accounts/register（临时账号转永久）=====


@router.post("/register", response_model=TokenExchangeResponse)
async def register(
    body: RegisterRequest,
    account: WebAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
) -> TokenExchangeResponse:
    """临时账号注册为永久账号（设置用户名+密码）。

    换发新 JWT（active_uuid 保持当前会话的绑定 UUID；若账号尚无 player 则无 active_uuid）。
    """
    if not account.is_temporary:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "account already permanent")

    pwd_hash = hash_password(body.password)
    try:
        updated = await web_account_repo.register(
            session, account.id, body.username, pwd_hash
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()

    players = await web_account_repo.list_players(session, updated.id)
    active_uuid = players[0].uuid if players else None
    access, refresh = _issue_jwt_for_account(updated, active_uuid)
    first = players[0] if players else None
    return TokenExchangeResponse(
        access_token=access,
        refresh_token=refresh,
        player=_player_brief(first, updated.role) if first is not None else None,
        account=_account_brief(updated),
    )


# ===== GET /web-accounts/me（当前账号 + players）=====


@router.get("/me", response_model=MyAccountResponse)
async def get_my_account(
    account: WebAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
) -> MyAccountResponse:
    """返回当前账号 + 绑定 players 列表（前端 MyAccountResponse shape）。"""
    players = await web_account_repo.list_players(session, account.id)
    return MyAccountResponse(
        account=_account_brief(account),
        players=[_player_brief(p, account.role) for p in players],
    )


@router.patch("/me", response_model=MyAccountResponse)
async def update_my_account(
    body: UpdateDisplayNameRequest,
    account: WebAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
) -> MyAccountResponse:
    """更新当前账号的自定义昵称（display_name，sheets 三端显示名主源）。

    空白 strip；schema ``min_length=1`` 拒纯空白（与迁移 CHECK 一致）。
    """
    account.display_name = body.display_name.strip()
    await session.commit()
    await session.refresh(account)
    players = await web_account_repo.list_players(session, account.id)
    return MyAccountResponse(
        account=_account_brief(account),
        players=[_player_brief(p, account.role) for p in players],
    )


# ===== POST /bind/token（游戏内发起，service-token）=====


@bind_router.post(
    "/token",
    response_model=BindTokenIssueResponse,
    dependencies=[Depends(require_service_token)],
)
async def issue_bind_token_from_game(
    body: BindTokenRequest,
    session: AsyncSession = Depends(get_session),
) -> BindTokenIssueResponse:
    """游戏内发起绑定（!!PCH bind）→ 生成 game_init 短码。

    body {uuid, name} 由 MCDR bind_client.request_bind_token POST JSON 传入；
    若 Player 不存在则 get_or_create（自动挂临时账号）。
    """
    player = await player_repo.get_or_create(session, body.uuid, body.name)
    token = await bind_token_repo.issue_game_init(session, player.uuid)
    await session.commit()
    return BindTokenIssueResponse(
        short_code=token.short_code,
        expires_in=_settings.bind_token_ttl_seconds,
    )


# ===== POST /bind/issue（Web 发起）=====


@bind_router.post("/issue", response_model=BindTokenIssueResponse)
async def issue_bind_token_from_web(
    account: WebAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
) -> BindTokenIssueResponse:
    """Web 发起绑定 → 生成 web_init 短码（供游戏内 !!PCH bind <code> 消费）。"""
    if account.is_temporary:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "temporary accounts cannot issue bind codes"
        )
    token = await bind_token_repo.issue_web_init(session, account.id)
    await session.commit()
    return BindTokenIssueResponse(
        short_code=token.short_code,
        expires_in=_settings.bind_token_ttl_seconds,
    )


# ===== POST /bind/confirm（Web 确认 game_init）=====


@bind_router.post("/confirm", response_model=BindConfirmResponse)
async def confirm_bind_from_game(
    body: BindConfirmRequest,
    account: WebAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
) -> BindConfirmResponse:
    """Web 确认游戏内发起的绑定（输入短码）→ 挂接 player_uuid 到当前账号。

    契约：返回 {player, account}（**不含 token**，绑定不改 account，前端继续用现有 JWT）。
    临时账号 → 403（必须先 register 或 claim 到永久账号）。
    """
    if account.is_temporary:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "temporary accounts cannot bind players"
        )

    player_uuid = await bind_token_repo.consume_game_init(
        session, body.short_code, account.id
    )
    if player_uuid is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invalid or expired code")

    await web_account_repo.attach_player(session, account.id, player_uuid)
    await session.commit()

    # 查真实 Player 拿 current_name（不塞占位空串）
    player = await player_repo.get_by_uuid(session, player_uuid)
    if player is None:  # defensive：刚绑成功却查不到
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "post-bind state lost")
    return BindConfirmResponse(
        player=_player_brief(player, account.role),
        account=_account_brief(account),
    )


# ===== POST /bind/consume（游戏内消费 web_init，service-token + X-Player-UUID）=====


@bind_router.post(
    "/consume",
    response_model=BindConsumeResponse,
    dependencies=[Depends(require_service_token)],
)
async def consume_bind_from_web(
    body: BindConsumeRequest,
    x_player_uuid: uuid.UUID = Header(..., alias="X-Player-UUID"),
    session: AsyncSession = Depends(get_session),
) -> BindConsumeResponse:
    """游戏内消费 Web 发起的绑定（!!PCH bind <code>）。

    契约：MCDR bind_client.consume_bind_code 双头通道（X-Service-Token + X-Player-UUID）POST {short_code}；
    成功响应 {status, account, player}（MCDR 客户端依赖 account+player 渲染回执）。

    注：MCDR 仅传 UUID（不传 name）；player 不存在时 get_or_create 用 UUID 字符串作占位名，
    下次 !!PCH login 会刷新 current_name。
    """
    # 取或建 player —— get_or_create 保证 UUID 命中 Player（自动挂临时账号，attach 时迁移到目标账号）
    player = await player_repo.get_or_create(session, x_player_uuid, str(x_player_uuid))

    target_account_id = await bind_token_repo.consume_web_init(
        session, body.short_code, x_player_uuid
    )
    if target_account_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invalid or expired code")

    await web_account_repo.attach_player(session, target_account_id, x_player_uuid)
    await session.commit()

    # 重新加载 account（确保 is_temporary/username 等字段最新）+ player（current_name）
    target_account = await web_account_repo.get_by_id(session, target_account_id)
    if target_account is None:  # defensive
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "target account lost")
    refreshed_player = await player_repo.get_by_uuid(session, x_player_uuid)
    if refreshed_player is None:  # defensive
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "player state lost")
    return BindConsumeResponse(
        status="ok",
        player=_player_brief(refreshed_player, target_account.role),
        account=_account_brief(target_account),
    )


# ===== POST /bind/claim（临时账号绑定到永久账号）=====


@bind_router.post("/claim", response_model=TokenExchangeResponse)
async def claim_bind_to_permanent(
    body: ClaimBindRequest,
    account: WebAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
) -> TokenExchangeResponse:
    """临时账号绑定到已有永久账号（输入永久账号用户名+密码）。

    将当前临时账号名下所有 player 迁移到目标永久账号；返回目标账号的 JWT。
    """
    if not account.is_temporary:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "account already permanent")

    target = await web_account_repo.get_by_username(session, body.username)
    if target is None or target.is_temporary:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    if not target.password_hash or not verify_password(body.password, target.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")

    # 将当前临时账号名下所有 player 挂到目标账号
    players = await web_account_repo.list_players(session, account.id)
    for p in players:
        await web_account_repo.attach_player(session, target.id, p.uuid)
    await session.commit()

    # 临时账号变孤儿（保留审计，不删；不再被任何 player 引用）
    first = players[0] if players else None
    access, refresh = _issue_jwt_for_account(target, first.uuid if first else None)
    return TokenExchangeResponse(
        access_token=access,
        refresh_token=refresh,
        player=_player_brief(first, target.role) if first is not None else None,
        account=_account_brief(target),
    )
