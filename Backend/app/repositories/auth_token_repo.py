import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.user import AuthToken, Player

_settings = get_settings()


async def issue(
    session: AsyncSession, player_uuid: uuid.UUID, issued_ip: str | None = None
) -> AuthToken:
    token = AuthToken(
        token=uuid.uuid4(),
        player_uuid=player_uuid,
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=_settings.auth_token_ttl_seconds),
        issued_ip=issued_ip,
    )
    session.add(token)
    await session.flush()
    return token


async def exchange(
    session: AsyncSession, token: uuid.UUID, exchanged_ip: str | None = None
) -> Player | None:
    stmt = select(AuthToken).where(AuthToken.token == token).with_for_update()
    auth_token = (await session.execute(stmt)).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if (
        auth_token is None
        or auth_token.used_at is not None
        or auth_token.expires_at < now
    ):
        return None
    auth_token.used_at = now
    auth_token.exchanged_ip = exchanged_ip
    player_stmt = select(Player).where(Player.uuid == auth_token.player_uuid)
    return (await session.execute(player_stmt)).scalar_one()
