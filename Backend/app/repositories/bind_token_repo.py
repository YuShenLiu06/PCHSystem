"""绑定短码持久化（双向，防并发消费）。"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import generate_short_code
from app.models.user import BindToken

_settings = get_settings()


async def _revoke_active(
    session: AsyncSession, direction: str, identifier: uuid.UUID | int
) -> int:
    """软失效：同方向同标识（UUID 或 account_id）的未使用短码置 used_at（消费校验会拒）。

    返回失效数量（用于回执 previous_tokens_revoked）。
    """
    if direction == "game_init":
        # identifier 是 player_uuid
        stmt = (
            update(BindToken)
            .where(
                BindToken.direction == "game_init",
                BindToken.player_uuid == identifier,  # type: ignore[arg-type]
                BindToken.used_at.is_(None),
            )
            .values(used_at=datetime.now(timezone.utc))
        )
    else:
        # identifier 是 target_account_id
        stmt = (
            update(BindToken)
            .where(
                BindToken.direction == "web_init",
                BindToken.target_account_id == identifier,  # type: ignore[arg-type]
                BindToken.used_at.is_(None),
            )
            .values(used_at=datetime.now(timezone.utc))
        )
    result = await session.execute(stmt)
    return result.rowcount


async def issue_game_init(
    session: AsyncSession, player_uuid: uuid.UUID, ttl: int | None = None
) -> BindToken:
    """游戏内发起绑定：生成短码（方向=game_init）。"""
    if ttl is None:
        ttl = _settings.bind_token_ttl_seconds
    await _revoke_active(session, "game_init", player_uuid)

    token = BindToken(
        token=uuid.uuid4(),
        short_code=generate_short_code(),
        direction="game_init",
        player_uuid=player_uuid,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl),
    )
    session.add(token)
    await session.flush()
    return token


async def issue_web_init(
    session: AsyncSession, account_id: int, ttl: int | None = None
) -> BindToken:
    """Web 发起绑定：生成短码（方向=web_init）。"""
    if ttl is None:
        ttl = _settings.bind_token_ttl_seconds
    await _revoke_active(session, "web_init", account_id)

    token = BindToken(
        token=uuid.uuid4(),
        short_code=generate_short_code(),
        direction="web_init",
        target_account_id=account_id,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl),
    )
    session.add(token)
    await session.flush()
    return token


async def consume_game_init(
    session: AsyncSession, short_code: str, account_id: int
) -> uuid.UUID | None:
    """消费 game_init 短码：Web 确认游戏内发起的绑定。

    返回待绑定的 player_uuid；失败/已用/过期/方向错 → None。
    """
    now = datetime.now(timezone.utc)
    stmt = (
        select(BindToken)
        .where(
            BindToken.short_code == short_code,
            BindToken.direction == "game_init",
            BindToken.used_at.is_(None),
        )
        .with_for_update()
    )
    token = (await session.execute(stmt)).scalar_one_or_none()
    if token is None or token.expires_at < now:
        return None
    token.used_at = now
    await session.flush()
    return token.player_uuid


async def consume_web_init(
    session: AsyncSession, short_code: str, player_uuid: uuid.UUID
) -> int | None:
    """消费 web_init 短码：游戏内确认 Web 发起的绑定。

    返回目标 account_id；失败/已用/过期/方向错 → None。
    """
    now = datetime.now(timezone.utc)
    stmt = (
        select(BindToken)
        .where(
            BindToken.short_code == short_code,
            BindToken.direction == "web_init",
            BindToken.used_at.is_(None),
        )
        .with_for_update()
    )
    token = (await session.execute(stmt)).scalar_one_or_none()
    if token is None or token.expires_at < now:
        return None
    token.used_at = now
    await session.flush()
    return token.target_account_id
