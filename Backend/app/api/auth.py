import uuid

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_active_uuid, get_current_account, get_current_player
from app.core.config import get_settings
from app.core.db import get_session
from app.core.jwt import create_access_token, create_refresh_token, decode_token
from app.api.deps import require_service_token
from app.core.web_probe import probe_web
from app.models.user import Player, WebAccount
from app.repositories.auth_token_repo import exchange as exchange_token, issue
from app.repositories import web_account_repo
from app.repositories.player_repo import get_or_create, get_last_sheet
from app.schemas.auth import (
    AccountBrief,
    LastSheetResponse,
    MeResponse,
    PlayerBrief,
    RefreshRequest,
    TokenExchangeRequest,
    TokenExchangeResponse,
    TokenIssueRequest,
    TokenIssueResponse,
)
from app.services.auth_service import check_whitelist, rate_limiter

router = APIRouter(prefix="/auth", tags=["auth"])
top_router = APIRouter(tags=["me"])
_settings = get_settings()


@router.post("/token", response_model=TokenIssueResponse)
async def post_token(
    body: TokenIssueRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _svc=Depends(require_service_token),
) -> TokenIssueResponse:
    if not rate_limiter.check_and_record(body.uuid):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limited")
    player = await get_or_create(session, body.uuid, body.name)
    if not await check_whitelist(session, body.uuid):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "player removed")
    token, revoked_count = await issue(
        session, body.uuid, issued_ip=request.client.host if request.client else None
    )
    await session.commit()
    url = f"{_settings.web_base_url.rstrip('/')}/auth?token={token.token}"
    return TokenIssueResponse(
        login_url=url,
        expires_in=_settings.auth_token_ttl_seconds,
        previous_tokens_revoked=revoked_count,
        # 需求 4：!!PCH login 时后端顺便探前端，挂了则插件回执明确提示「前端未启用」
        frontend_online=(await probe_web(_settings.web_probe_url)).online,
    )


@router.post("/exchange", response_model=TokenExchangeResponse)
async def post_exchange(
    body: TokenExchangeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TokenExchangeResponse:
    ip = request.client.host if request.client else None
    player = await exchange_token(session, body.token, exchanged_ip=ip)
    if player is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or used token")
    await session.commit()

    # get_or_create 已确保 player 有 web_account_id
    account = player.web_account
    if account is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "account missing")

    access = create_access_token(account.id, account.role, active_uuid=player.uuid)
    refresh, _ = create_refresh_token(account.id, account.role, active_uuid=player.uuid)
    return TokenExchangeResponse(
        access_token=access,
        refresh_token=refresh,
        player=PlayerBrief(uuid=player.uuid, name=player.current_name, role=account.role),
        account=AccountBrief(
            id=account.id,
            is_temporary=account.is_temporary,
            username=account.username,
            role=account.role,
        ),
    )


@router.post("/refresh", response_model=TokenExchangeResponse)
async def post_refresh(
    body: RefreshRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenExchangeResponse:
    try:
        payload = decode_token(body.refresh_token)
    except pyjwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid refresh")
    if payload.get("type") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong token type")

    # sub 现在是 account_id
    try:
        account_id = int(payload["sub"])
    except (ValueError, KeyError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token subject")

    account = await web_account_repo.get_by_id(session, account_id)
    if account is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account not found")

    # active_uuid 可能不存在（旧 token），拒绝让其重登
    active_uuid_str = payload.get("active_uuid")
    if not active_uuid_str:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "legacy token, please re-login")
    active_uuid = uuid.UUID(active_uuid_str)

    # 验证 UUID 仍属于该账号
    player_stmt = select(Player).where(
        Player.uuid == active_uuid, Player.web_account_id == account_id
    )
    player = (await session.execute(player_stmt)).scalar_one_or_none()
    if player is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "player not bound to account")

    access = create_access_token(account.id, account.role, active_uuid=active_uuid)
    refresh, _ = create_refresh_token(account.id, account.role, active_uuid=active_uuid)
    return TokenExchangeResponse(
        access_token=access,
        refresh_token=refresh,
        player=PlayerBrief(uuid=player.uuid, name=player.current_name, role=account.role),
        account=AccountBrief(
            id=account.id,
            is_temporary=account.is_temporary,
            username=account.username,
            role=account.role,
        ),
    )


@top_router.get("/me", response_model=MeResponse)
async def get_me(
    account: WebAccount = Depends(get_current_account),
    active_uuid: uuid.UUID = Depends(get_active_uuid),
    session: AsyncSession = Depends(get_session),
) -> MeResponse:
    """返回当前账号 + 绑定 players + active_uuid（会话来源 UUID）。"""
    players = await web_account_repo.list_players(session, account.id)
    return MeResponse(
        account=AccountBrief(
            id=account.id,
            is_temporary=account.is_temporary,
            username=account.username,
            role=account.role,
        ),
        players=[
            PlayerBrief(uuid=p.uuid, name=p.current_name, role=account.role)
            for p in players
        ],
        active_uuid=active_uuid,
    )


@top_router.get("/me/last_sheet", response_model=LastSheetResponse)
async def get_my_last_sheet(
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> LastSheetResponse:
    sheet_id = await get_last_sheet(session, player.uuid)
    return LastSheetResponse(sheet_id=sheet_id)
