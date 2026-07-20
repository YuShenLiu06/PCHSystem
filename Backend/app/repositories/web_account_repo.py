"""WebAccount 聚合查询与持久化。

含临时账号创建、注册（临时→永久）、UUID 挂接、聚合查询。
"""
import uuid
from datetime import datetime

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


async def resolve_display_names(
    session: AsyncSession,
    player_uuids: list[uuid.UUID],
) -> dict[uuid.UUID, str]:
    """批量解析显示名（sheets 三端主源，避免 list 路径 N+1）。

    对每个 ``player_uuid`` 解析（优先级递减）：
    1. 其 ``WebAccount.display_name`` 非空 → 映射到 display_name（同 account 所有 UUID 共享）。
    2. 否则 → 该 account 下 ``last_seen_at`` 最新 Player 的 ``current_name``。
    3. 未绑 account（``web_account_id IS NULL``）→ 自身 ``current_name``。

    返回 ``{player_uuid: name}``；入参为空返 ``{}``。
    """
    if not player_uuids:
        return {}

    unique = list(dict.fromkeys(player_uuids))  # 去重保序

    # 一次查全部目标 player + 其 account 的 display_name
    target_stmt = (
        select(
            Player.uuid,
            Player.current_name,
            Player.web_account_id,
            WebAccount.display_name,
        )
        .select_from(Player)
        .outerjoin(WebAccount, WebAccount.id == Player.web_account_id)
        .where(Player.uuid.in_(unique))
    )
    targets = (await session.execute(target_stmt)).all()

    # 涉及的 account → 回退名（account 下 last_seen_at 最新 player 的 current_name）
    account_ids = {t.web_account_id for t in targets if t.web_account_id is not None}
    fallback_by_account: dict[int, str] = {}
    if account_ids:
        members_stmt = (
            select(Player.web_account_id, Player.current_name, Player.last_seen_at)
            .where(Player.web_account_id.in_(account_ids))
        )
        latest_by_account: dict[int, tuple] = {}  # account_id -> (current_name, last_seen_at)
        for m in (await session.execute(members_stmt)).all():
            cur = latest_by_account.get(m.web_account_id)
            if cur is None or m.last_seen_at > cur[1]:
                latest_by_account[m.web_account_id] = (m.current_name, m.last_seen_at)
        fallback_by_account = {
            aid: name for aid, (name, _ts) in latest_by_account.items()
        }

    result: dict[uuid.UUID, str] = {}
    for t in targets:
        if t.display_name:
            result[t.uuid] = t.display_name
        elif t.web_account_id is not None and t.web_account_id in fallback_by_account:
            result[t.uuid] = fallback_by_account[t.web_account_id]
        else:
            result[t.uuid] = t.current_name
    return result


async def resolve_display_name(
    session: AsyncSession, player_uuid: uuid.UUID
) -> str:
    """单条显示名解析（复用 :func:`resolve_display_names`）。"""
    names = await resolve_display_names(session, [player_uuid])
    return names.get(player_uuid, str(player_uuid))


async def resolve_account_briefs(
    session: AsyncSession,
    account_ids: list[int],
) -> dict[int, tuple[str, list[uuid.UUID]]]:
    """按 Web 账号批量解析 ``(display_name, member_uuids)``。

    供 ``SheetManagerEntry`` 列表组装（协管员显示名 + 成员 UUID）：
    - ``display_name``：``WebAccount.display_name`` 非空 → 取之；否则 account 下
      ``last_seen_at`` 最新 member 的 ``current_name``。
    - ``member_uuids``：account 下全部 UUID（含 inactive），供客户端按 viewer_uuids
      交集判定 is_manager（MCDR 仅持有 viewer_uuids，靠此无需知 account_id）。

    入参为空返 ``{}``；account 无 member 时 member_uuids=[]、display_name 回退占位。
    """
    if not account_ids:
        return {}

    accounts = (
        await session.execute(
            select(WebAccount.id, WebAccount.display_name).where(
                WebAccount.id.in_(account_ids)
            )
        )
    ).all()
    display_by_account: dict[int, str | None] = {a.id: a.display_name for a in accounts}

    members = (
        await session.execute(
            select(
                Player.web_account_id,
                Player.uuid,
                Player.current_name,
                Player.last_seen_at,
            ).where(Player.web_account_id.in_(account_ids))
        )
    ).all()

    uuids_by_account: dict[int, list[uuid.UUID]] = {}
    latest_by_account: dict[int, tuple[str, datetime]] = {}
    for m in members:
        uuids_by_account.setdefault(m.web_account_id, []).append(m.uuid)
        cur = latest_by_account.get(m.web_account_id)
        if cur is None or m.last_seen_at > cur[1]:
            latest_by_account[m.web_account_id] = (m.current_name, m.last_seen_at)

    result: dict[int, tuple[str, list[uuid.UUID]]] = {}
    for aid in account_ids:
        dn = display_by_account.get(aid)
        if not dn:
            latest = latest_by_account.get(aid)
            dn = latest[0] if latest else f"账号#{aid}"
        result[aid] = (dn, uuids_by_account.get(aid, []))
    return result
