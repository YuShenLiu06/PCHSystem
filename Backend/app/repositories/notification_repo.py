"""NotificationRepository 函数式实现（notifications schema）。

镜像 ``sheet_repo.py`` 风格：函数收 ``AsyncSession``，只 ``flush()``，
由调用方（service 层）在同一事务内决定 commit/rollback。
"""
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification


async def create(
    session: AsyncSession,
    recipient_uuid: uuid.UUID,
    category: str,
    title: str,
    body: str,
    payload: dict[str, Any] | None = None,
) -> Notification:
    """落库一条通知（不 commit，由调用方事务决定原子性）。"""
    record = Notification(
        recipient_uuid=recipient_uuid,
        category=category,
        title=title,
        body=body,
        payload=payload or {},
    )
    session.add(record)
    await session.flush()
    return record


async def fetch_pending(
    session: AsyncSession, recipient_uuids: list[uuid.UUID], limit: int = 50
) -> list[Notification]:
    """拉取某账号（多 UUID）未投递通知，按 created_at asc。

    recipient_uuids：账号绑定的全部 UUID 列表（由 web_account_repo.list_uuids 获取）。
    """
    if not recipient_uuids:
        return []
    stmt = (
        select(Notification)
        .where(
            Notification.recipient_uuid.in_(recipient_uuids),
            Notification.delivered_at.is_(None),
        )
        .order_by(Notification.created_at.asc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def mark_delivered(
    session: AsyncSession,
    ids: list[int],
    recipient_uuids: uuid.UUID | list[uuid.UUID],
) -> int:
    """批量标投递（delivered_at=now），仅限 recipient_uuids 名下（防越权）。

    接受单 UUID（向后兼容）或列表（账号级聚合，C-1 仍防跨账号）。
    跨账号的 id 不命中，返回值不含它们。
    """
    if not ids:
        return 0
    uuids: list[uuid.UUID] = [recipient_uuids] if isinstance(recipient_uuids, uuid.UUID) else list(recipient_uuids)
    if not uuids:
        return 0
    now = datetime.now(timezone.utc)
    stmt = (
        update(Notification)
        .where(
            Notification.id.in_(ids),
            Notification.recipient_uuid.in_(uuids),
            Notification.delivered_at.is_(None),
        )
        .values(delivered_at=now)
    )
    result = await session.execute(stmt)
    return result.rowcount or 0


async def mark_read(
    session: AsyncSession,
    notification_id: int,
    recipient_uuids: uuid.UUID | list[uuid.UUID],
) -> bool:
    """单条标已读（read_at=now），仅当归属 recipient_uuids（防越权，跨账号返 False）。

    L-2：已读必然已投递，同步幂等置 delivered_at=now（若尚未投递）。
    """
    uuids: list[uuid.UUID] = [recipient_uuids] if isinstance(recipient_uuids, uuid.UUID) else list(recipient_uuids)
    if not uuids:
        return False
    now = datetime.now(timezone.utc)
    stmt = (
        update(Notification)
        .where(
            Notification.id == notification_id,
            Notification.recipient_uuid.in_(uuids),
        )
        .values(read_at=now, delivered_at=now)
    )
    result = await session.execute(stmt)
    return (result.rowcount or 0) > 0


async def get_by_id(
    session: AsyncSession, notification_id: int
) -> Notification | None:
    return (
        await session.execute(
            select(Notification).where(Notification.id == notification_id)
        )
    ).scalar_one_or_none()
