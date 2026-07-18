"""SheetManagerRepository 函数式实现（sheets schema，迁移 0014）。

镜像 sheet_repo 风格：函数签名收 ``AsyncSession``，只 ``flush()``，
由 api 层负责 ``commit()``。PRIMARY KEY (sheet_id, player_uuid) 天然防重复授予——
重复 insert 触发 IntegrityError，本 repo 捕获并视为幂等成功。

身份锚 = player_uuid（FK→users.players.uuid，红线 R-5）。
"""
import uuid
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sheet import SheetManager
from app.models.user import Player


class SheetOwnerCannotBeManager(Exception):
    """owner 不能被设为自己项目的 manager（语义互斥）。api 层翻译为 422。"""


class SheetManagerNotFound(Exception):
    """撤销目标不存在。api 层翻译为 404。"""


async def list_managers(
    session: AsyncSession, sheet_id: int
) -> list[tuple[uuid.UUID, str, datetime]]:
    """列单表全部 manager：inner join players 取游戏名。

    返回 [(player_uuid, player_name, granted_at)]，按 granted_at 升序（先授予在前）。
    granted_by_uuid 仅存表内作审计，不在响应中暴露（YAGNI）。
    """
    stmt = (
        select(SheetManager.player_uuid, Player.current_name, SheetManager.granted_at)
        .join(Player, Player.uuid == SheetManager.player_uuid)
        .where(SheetManager.sheet_id == sheet_id)
        .order_by(SheetManager.granted_at.asc())
    )
    return [
        (pu, pn, gat)
        for pu, pn, gat in (await session.execute(stmt)).all()
    ]


async def add_manager(
    session: AsyncSession,
    sheet_id: int,
    player_uuid: uuid.UUID,
    *,
    owner_uuid: uuid.UUID,
    granted_by_uuid: uuid.UUID,
) -> bool:
    """授予 manager（幂等）。

    - owner_uuid 由调用方传入（避免本函数再查一次 sheet）；owner 自授予 →
      SheetOwnerCannotBeManager（语义互斥，api 层 422）。
    - 幂等：pre-check 命中既有关系 → 返回 False（不重复 insert、不报错）。
      并发 race（pre-check 与 flush 之间另一事务已插入）的 PK 冲突 IntegrityError
      不在此捕获——rollback 会污染调用方事务；rare race 让其上浮为 500 即可。
    - flush 不 commit（api 层负责 commit + 通知联动）。

    返回 True = 新授予；False = 已存在（幂等）。
    """
    if player_uuid == owner_uuid:
        raise SheetOwnerCannotBeManager(
            f"player {player_uuid} is the owner of sheet {sheet_id}"
        )
    existing = (
        await session.execute(
            select(SheetManager).where(
                SheetManager.sheet_id == sheet_id,
                SheetManager.player_uuid == player_uuid,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    session.add(
        SheetManager(
            sheet_id=sheet_id,
            player_uuid=player_uuid,
            granted_by_uuid=granted_by_uuid,
        )
    )
    await session.flush()
    return True


async def remove_manager(
    session: AsyncSession, sheet_id: int, player_uuid: uuid.UUID
) -> None:
    """撤销 manager。不存在 → SheetManagerNotFound（api 层 404）。

    flush 不 commit（api 层负责 commit）。
    """
    result = await session.execute(
        delete(SheetManager)
        .where(
            SheetManager.sheet_id == sheet_id,
            SheetManager.player_uuid == player_uuid,
        )
    )
    if result.rowcount == 0:
        raise SheetManagerNotFound(
            f"player {player_uuid} is not a manager of sheet {sheet_id}"
        )
    await session.flush()


