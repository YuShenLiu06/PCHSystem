"""SheetManagerRepository 函数式实现（sheets schema，迁移 0014，account 锚）。

镜像 sheet_repo 风格：函数签名收 ``AsyncSession``，只 ``flush()``，
由 api 层负责 ``commit()``。PRIMARY KEY (sheet_id, web_account_id) 天然防重复授予——
重复 insert 触发 IntegrityError，本 repo pre-check 命中既有关系即视为幂等。

R-5 身份主锚 = Web 账号：manager 锚 ``web_account_id``（非 player_uuid），同账号
任一 UUID 都继承 manager；授予目标必须已绑 Web 账号（列 NOT NULL，应用层未绑 → 422）。
owner account 不能被设为自己项目的 manager（语义互斥，按 account 比对）。
"""
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sheet import SheetManager


class SheetOwnerCannotBeManager(Exception):
    """owner 账号不能被设为自己项目的 manager（语义互斥）。api 层翻译为 409。"""


class SheetManagerNotFound(Exception):
    """撤销目标不存在。api 层翻译为 404。"""


async def list_managers(
    session: AsyncSession, sheet_id: int
) -> list[SheetManager]:
    """列单表全部 manager（按 granted_at 升序，先授予在前）。

    返回 ``SheetManager`` 对象列表（含 ``web_account_id`` / ``granted_at`` /
    ``granted_by_uuid``）；api 层经 ``web_account_repo.resolve_account_briefs``
    解析 display_name + member_uuids 组装响应。
    """
    stmt = (
        select(SheetManager)
        .where(SheetManager.sheet_id == sheet_id)
        .order_by(SheetManager.granted_at.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def add_manager(
    session: AsyncSession,
    sheet_id: int,
    target_web_account_id: int,
    *,
    owner_web_account_id: int,
    granted_by_uuid,
) -> tuple[SheetManager, bool]:
    """授予 manager（account 锚，幂等）。

    - target account == owner account → ``SheetOwnerCannotBeManager``（语义互斥，api 层 409）。
    - 幂等：pre-check 命中既有关系 → 返回 ``(existing, False)``（不重复 insert、不报错），
      api 层据此跳过授予通知。
    - 并发 race（pre-check 与 flush 之间另一事务已插入）的 PK 冲突 IntegrityError
      不在此捕获——rollback 会污染调用方事务；rare race 让其上浮为 500 即可。
    - flush 不 commit（api 层负责 commit + 通知联动）。

    返回 ``(manager, is_new)``：is_new=True 表示本次新建，False 表示已存在（幂等）。
    """
    if target_web_account_id == owner_web_account_id:
        raise SheetOwnerCannotBeManager(
            f"account {target_web_account_id} is the owner of sheet {sheet_id}"
        )
    existing = (
        await session.execute(
            select(SheetManager).where(
                SheetManager.sheet_id == sheet_id,
                SheetManager.web_account_id == target_web_account_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    manager = SheetManager(
        sheet_id=sheet_id,
        web_account_id=target_web_account_id,
        granted_by_uuid=granted_by_uuid,
    )
    session.add(manager)
    await session.flush()
    return manager, True


async def remove_manager(
    session: AsyncSession, sheet_id: int, web_account_id: int
) -> datetime:
    """撤销 manager（account 锚）。不存在 → ``SheetManagerNotFound``（api 层 404）。

    返回被撤销记录的 ``granted_at``（供 api 层审计/日志）。flush 不 commit。
    """
    existing = (
        await session.execute(
            select(SheetManager).where(
                SheetManager.sheet_id == sheet_id,
                SheetManager.web_account_id == web_account_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        raise SheetManagerNotFound(
            f"account {web_account_id} is not a manager of sheet {sheet_id}"
        )
    granted_at = existing.granted_at
    await session.execute(
        delete(SheetManager).where(
            SheetManager.sheet_id == sheet_id,
            SheetManager.web_account_id == web_account_id,
        )
    )
    await session.flush()
    return granted_at
