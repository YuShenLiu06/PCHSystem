import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Player


async def get_or_create(
    session: AsyncSession, player_uuid: uuid.UUID, name: str
) -> Player:
    stmt = select(Player).where(Player.uuid == player_uuid)
    player = (await session.execute(stmt)).scalar_one_or_none()
    if player is None:
        player = Player(uuid=player_uuid, current_name=name)
        session.add(player)
        await session.flush()
    else:
        player.current_name = name
        player.last_seen_at = datetime.now(timezone.utc)
    return player


async def get_by_uuid(
    session: AsyncSession, player_uuid: uuid.UUID
) -> Player | None:
    stmt = select(Player).where(Player.uuid == player_uuid)
    return (await session.execute(stmt)).scalar_one_or_none()


async def set_last_sheet(session: AsyncSession, player_uuid: uuid.UUID, sheet_id: int) -> None:
    """记录玩家最后查看的表格 ID（尽力写，仅 flush，由 api 层 commit）。"""
    await session.execute(
        update(Player).where(Player.uuid == player_uuid).values(last_sheet_id=sheet_id)
    )
    await session.flush()


async def get_last_sheet(session: AsyncSession, player_uuid: uuid.UUID) -> int | None:
    """获取玩家最后查看的表格 ID（无历史则返回 None）。"""
    stmt = select(Player.last_sheet_id).where(Player.uuid == player_uuid)
    return (await session.execute(stmt)).scalar_one_or_none()
