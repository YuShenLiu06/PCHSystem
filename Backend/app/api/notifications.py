"""notifications 路由（MCDR 轮询拉取/ack/read）。

鉴权：``require_service_token``（MCDR 带 ``X-Service-Token``）；
``player_uuid`` 经 query/body 提供，必须命中 Player 表（防注入不存在身份），
且 ack/read 的目标通知必须归属该 player_uuid（C-1 防越权）。

端点：
- ``GET /notifications/pending?player_uuid=<uuid>&limit=N``：返未投递通知（limit ≤ 50）
- ``POST /notifications/ack`` body ``{player_uuid, ids:[…]}``：标该玩家名下通知投递，返 ``{acked: n}``
- ``POST /notifications/{id}/read?player_uuid=<uuid>``：标已读（归属该玩家，否则 404）
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_service_token
from app.core.db import get_session
from app.repositories import player_repo
from app.schemas.notification import (
    NotificationAckRequest,
    NotificationAckResponse,
    NotificationOut,
)
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


async def _require_player(session: AsyncSession, player_uuid: UUID | None) -> UUID:
    """校验 player_uuid 非空且对应 Player 存在（R-5 身份锚 = player.uuid）。"""
    if player_uuid is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "player_uuid required")
    if await player_repo.get_by_uuid(session, player_uuid) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "player not found")
    return player_uuid


def _to_out(record) -> NotificationOut:
    return NotificationOut(
        id=record.id,
        recipient_uuid=record.recipient_uuid,
        category=record.category,
        title=record.title,
        body=record.body,
        payload=record.payload,
        created_at=record.created_at,
        delivered_at=record.delivered_at,
        read_at=record.read_at,
    )


@router.get(
    "/pending",
    response_model=list[NotificationOut],
    dependencies=[Depends(require_service_token)],
)
async def list_pending(
    player_uuid: UUID | None = Query(default=None, description="目标玩家 UUID"),
    limit: int = Query(default=20, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> list[NotificationOut]:
    recipient = await _require_player(session, player_uuid)
    records = await notification_service.fetch_pending(session, recipient, limit)
    return [_to_out(r) for r in records]


@router.post(
    "/ack",
    response_model=NotificationAckResponse,
    dependencies=[Depends(require_service_token)],
)
async def ack(
    body: NotificationAckRequest,
    session: AsyncSession = Depends(get_session),
) -> NotificationAckResponse:
    """标投递：仅命中 body.player_uuid 名下的 id（C-1 防越权，跨玩家 id 计 0）。"""
    recipient = await _require_player(session, body.player_uuid)
    count = await notification_service.mark_delivered(session, body.ids, recipient)
    await session.commit()
    return NotificationAckResponse(acked=count)


@router.post(
    "/{notification_id}/read",
    response_model=NotificationOut,
    dependencies=[Depends(require_service_token)],
)
async def mark_read(
    notification_id: int,
    player_uuid: UUID = Query(..., description="目标玩家 UUID（归属校验）"),
    session: AsyncSession = Depends(get_session),
) -> NotificationOut:
    """标已读：仅当 notification 归属 player_uuid（C-1 防越权，跨玩家 404）。

    L-2：已读必然已投递，mark_read 同步幂等置 delivered_at。
    """
    recipient = await _require_player(session, player_uuid)
    ok = await notification_service.mark_read(session, notification_id, recipient)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "notification not found")
    await session.commit()
    record = await notification_service.fetch_by_id_or_none(
        session, notification_id, recipient
    )
    # mark_read 命中且归属已校验，记录必然存在；defensive
    assert record is not None
    return _to_out(record)
