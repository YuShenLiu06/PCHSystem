import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Player
from app.repositories import web_account_repo


async def get_or_create(
    session: AsyncSession, player_uuid: uuid.UUID, name: str
) -> Player:
    """获取或创建 Player；若 web_account_id 为 NULL 则自动挂临时账号。"""
    stmt = select(Player).where(Player.uuid == player_uuid)
    player = (await session.execute(stmt)).scalar_one_or_none()
    if player is None:
        player = Player(uuid=player_uuid, current_name=name)
        session.add(player)
        await session.flush()
        # 新建 Player 自动挂临时账号
        temp_account = await web_account_repo.create_temp(session)
        player.web_account_id = temp_account.id
        await session.flush()
    else:
        player.current_name = name
        player.last_seen_at = datetime.now(timezone.utc)
        # 若未绑定，自动挂临时账号
        if player.web_account_id is None:
            temp_account = await web_account_repo.create_temp(session)
            player.web_account_id = temp_account.id
            await session.flush()
    return player


async def get_by_uuid(
    session: AsyncSession, player_uuid: uuid.UUID
) -> Player | None:
    stmt = select(Player).where(Player.uuid == player_uuid)
    return (await session.execute(stmt)).scalar_one_or_none()


async def set_last_sheet(session: AsyncSession, player: Player, sheet_id: int) -> None:
    """记录玩家最后查看的表格 ID（R-5 account 级：同步到同 account 全部 UUID）。

    同 account 任一 UUID 打开表 → 该账号下所有 UUID 的 last_sheet_id 同步，
    使 ``!!sheet`` / ``!!submit`` 无参重开在多 UUID 间共享（lidrem 打开的表，
    LiuYuShen_06 也能重开）。未绑 account（web_account_id IS NULL）回退仅当前 UUID。
    直接收 ``player``（调用方已持有，避免重复 SELECT）；IS DISTINCT FROM 守卫避免
    Web 详情轮询（usePolling 默认 2s）写放大。
    """
    scope = (
        Player.web_account_id == player.web_account_id
        if player.web_account_id is not None
        else Player.uuid == player.uuid
    )
    await session.execute(
        update(Player)
        .where(scope)
        .where(Player.last_sheet_id.is_distinct_from(sheet_id))
        .values(last_sheet_id=sheet_id)
    )
    await session.flush()


async def get_last_sheet(session: AsyncSession, player_uuid: uuid.UUID) -> int | None:
    """获取玩家最后查看的表格 ID（无历史则返回 None）。"""
    stmt = select(Player.last_sheet_id).where(Player.uuid == player_uuid)
    return (await session.execute(stmt)).scalar_one_or_none()
