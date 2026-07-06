import uuid

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.jwt import create_access_token, create_refresh_token, decode_token
from app.api.deps import get_current_player, require_service_token
from app.models.user import Player
from app.repositories.auth_token_repo import exchange as exchange_token, issue
from app.repositories.player_repo import get_or_create, get_last_sheet
from app.schemas.auth import (
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
    access = create_access_token(player.uuid, player.role)
    refresh, _ = create_refresh_token(player.uuid, player.role)
    return TokenExchangeResponse(
        access_token=access,
        refresh_token=refresh,
        player=PlayerBrief(uuid=player.uuid, name=player.current_name, role=player.role),
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
    # MVP：refresh 未接入 jwt_revocations 吊销表校验；后续可扩展
    player = (
        await session.execute(select(Player).where(Player.uuid == uuid.UUID(payload["sub"])))
    ).scalar_one_or_none()
    if player is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "player not found")
    access = create_access_token(player.uuid, player.role)
    refresh, _ = create_refresh_token(player.uuid, player.role)
    return TokenExchangeResponse(
        access_token=access,
        refresh_token=refresh,
        player=PlayerBrief(uuid=player.uuid, name=player.current_name, role=player.role),
    )


@top_router.get("/me", response_model=MeResponse)
async def get_me(player: Player = Depends(get_current_player)) -> MeResponse:
    return MeResponse(uuid=player.uuid, name=player.current_name, role=player.role)


@top_router.get("/me/last_sheet", response_model=LastSheetResponse)
async def get_my_last_sheet(
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> LastSheetResponse:
    sheet_id = await get_last_sheet(session, player.uuid)
    return LastSheetResponse(sheet_id=sheet_id)
