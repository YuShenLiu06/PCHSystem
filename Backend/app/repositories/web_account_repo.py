"""WebAccount 聚合查询与持久化。

含临时账号创建、注册（临时→永久）、UUID 挂接、聚合查询。
"""
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.user import Player, WebAccount


async def create_temp(session: AsyncSession) -> WebAccount:
    """创建临时账号（username/password_hash 均为 NULL）。"""
    account = WebAccount(role="user")
    session.add(account)
    await session.flush()
    return account


async def register(
    session: AsyncSession,
    account_id: int,
    username: str,
    password_hash: str,
) -> WebAccount:
    """临时账号转永久（校验 username 唯一性）。"""
    # 校验 username 唯一
    existing = (
        await session.execute(
            select(WebAccount).where(WebAccount.username == username)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"username '{username}' already taken")

    # 更新为永久账号
    account = (
        await session.execute(select(WebAccount).where(WebAccount.id == account_id))
    ).scalar_one()
    account.username = username
    account.password_hash = password_hash
    await session.flush()
    return account


async def get_by_id(session: AsyncSession, account_id: int) -> WebAccount | None:
    stmt = select(WebAccount).where(WebAccount.id == account_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_by_username(session: AsyncSession, username: str) -> WebAccount | None:
    stmt = select(WebAccount).where(WebAccount.username == username)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_players(session: AsyncSession, account_id: int) -> list[Player]:
    """获取账号下绑定的全部 Player（含 inactive）。"""
    stmt = select(Player).where(Player.web_account_id == account_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_uuids(session: AsyncSession, account_id: int) -> list[uuid.UUID]:
    """聚合查询：获取账号下全部 UUID（用于 IN 查询）。"""
    stmt = select(Player.uuid).where(Player.web_account_id == account_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def attach_player(
    session: AsyncSession, account_id: int, player_uuid: uuid.UUID
) -> None:
    """挂接 Player 到 WebAccount（幂等：已挂同账号则 NOP，换账号则 UPDATE）。"""
    await session.execute(
        update(Player)
        .where(Player.uuid == player_uuid)
        .where(Player.web_account_id.is_distinct_from(account_id))
        .values(web_account_id=account_id)
    )
    await session.flush()
