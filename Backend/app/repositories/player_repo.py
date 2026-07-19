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


async def search_by_name_prefix(
    session: AsyncSession, prefix: str, limit: int = 10
) -> list[Player]:
    """按 ``current_name`` 前缀（大小写不敏感）搜索玩家，供协管员授予联想。

    空 prefix → 返空列表（不返回全库随机前 N）。LIKE 特殊字符（``%`` ``_`` ``\\``）
    已转义，避免被当通配符。身份锚 = uuid（返回全 Player 对象，调用方按需取字段）。
    """
    prefix = (prefix or "").strip()
    if not prefix:
        return []
    # 转义 LIKE 通配符，escape='\\' 告知 PG 反斜杠为转义符
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    stmt = (
        select(Player)
        .where(Player.current_name.ilike(f"{escaped}%", escape="\\"))
        .order_by(Player.current_name.asc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def set_last_sheet(session: AsyncSession, player_uuid: uuid.UUID, sheet_id: int) -> None:
    """记录玩家最后查看的表格 ID（尽力写，仅 flush，由 api 层 commit）。

    IS DISTINCT FROM 守卫：仅当 last_sheet_id 实际变化时才 UPDATE，避免 Web 详情
    轮询（Frontend usePolling 默认 2s）每轮对同一张表产生写放大。对 NULL 安全
    （NULL → N 首次设置亦命中）。
    """
    await session.execute(
        update(Player)
        .where(Player.uuid == player_uuid)
        .where(Player.last_sheet_id.is_distinct_from(sheet_id))
        .values(last_sheet_id=sheet_id)
    )
    await session.flush()


async def get_last_sheet(session: AsyncSession, player_uuid: uuid.UUID) -> int | None:
    """获取玩家最后查看的表格 ID（无历史则返回 None）。"""
    stmt = select(Player.last_sheet_id).where(Player.uuid == player_uuid)
    return (await session.execute(stmt)).scalar_one_or_none()
