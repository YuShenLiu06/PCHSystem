from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.api.deps import require_service_token
from app.repositories.auth_token_repo import issue
from app.repositories.player_repo import get_or_create
from app.schemas.auth import TokenIssueRequest, TokenIssueResponse
from app.services.auth_service import check_whitelist, rate_limiter

router = APIRouter(prefix="/auth", tags=["auth"])
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
    token = await issue(
        session, body.uuid, issued_ip=request.client.host if request.client else None
    )
    await session.commit()
    url = f"{_settings.web_base_url.rstrip('/')}/auth?token={token.token}"
    return TokenIssueResponse(login_url=url)
