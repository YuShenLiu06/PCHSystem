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
    session: AsyncSession, recipient_uuid: uuid.UUID, limit: int = 50
) -> list[Notification]:
    """拉取某玩家未投递（delivered_at IS NULL）通知，按 created_at asc。"""
    stmt = (
        select(Notification)
        .where(
            Notification.recipient_uuid == recipient_uuid,
            Notification.delivered_at.is_(None),
        )
        .order_by(Notification.created_at.asc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def mark_delivered(
    session: AsyncSession,
    ids: list[int],
    recipient_uuid: uuid.UUID,
) -> int:
    """批量标投递（delivered_at=now），仅限 recipient_uuid 名下（防越权）。

    跨玩家的 id 不命中，返回值不含它们（调用方据此判断越权 ack）。
    """
    if not ids:
        return 0
    now = datetime.now(timezone.utc)
    stmt = (
        update(Notification)
        .where(
            Notification.id.in_(ids),
            Notification.recipient_uuid == recipient_uuid,
            Notification.delivered_at.is_(None),
        )
        .values(delivered_at=now)
    )
    result = await session.execute(stmt)
    return result.rowcount or 0


async def mark_read(
    session: AsyncSession,
    notification_id: int,
    recipient_uuid: uuid.UUID,
) -> bool:
    """单条标已读（read_at=now），仅当归属 recipient_uuid（防越权，跨玩家返 False）。

    L-2：已读必然已投递，同步幂等置 delivered_at=now（若尚未投递）。
    """
    now = datetime.now(timezone.utc)
    stmt = (
        update(Notification)
        .where(
            Notification.id == notification_id,
            Notification.recipient_uuid == recipient_uuid,
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
