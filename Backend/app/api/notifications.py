"""notifications 路由（MCDR 轮询拉取/ack/read）。

鉴权：``require_service_token``（MCDR 带 ``X-Service-Token``）；
``player_uuid`` 经 query/body 提供，必须命中 Player 表（防注入不存在身份），
且 ack/read 的目标通知必须归属该 player_uuid 或同账号的其他 UUID（C-1 防越权）。

端点：
- ``GET /notifications/pending?player_uuid=<uuid>&limit=N``：返未投递通知（limit ≤ 50）
- ``POST /notifications/ack`` body ``{player_uuid, ids:[…]}``：标该玩家名下通知投递，返 ``{acked: n}``
- ``POST /notifications/{id}/read?player_uuid=<uuid>``：标已读（归属该玩家，否则 404）

身份锚升级后：传入 player_uuid 后解析该账号的全部 UUID 列表，ack/read 对同账号
所有 UUID 名下的通知生效（pending 已聚合，ack/read 须配套；C-1 仍防跨账号越权）。
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_service_token
from app.core.db import get_session
from app.repositories import player_repo, web_account_repo
from app.schemas.notification import (
    NotificationAckRequest,
    NotificationAckResponse,
    NotificationOut,
)
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


async def _resolve_account_uuids(
    session: AsyncSession, player_uuid: UUID | None
) -> list[UUID]:
    """校验 player_uuid 非空且对应 Player 存在；返回该账号（或单 UUID）的全部 UUID 列表。

    用于 pending 聚合 + ack/read 同账号归属校验：
    - 有 WebAccount → 该账号绑定的所有 UUID；
    - 无 WebAccount → 单 UUID（未绑玩家）。
    """
    if player_uuid is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "player_uuid required")
    player = await player_repo.get_by_uuid(session, player_uuid)
    if player is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "player not found")
    if player.web_account_id:
        return await web_account_repo.list_uuids(session, player.web_account_id)
    return [player.uuid]


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
    recipient_uuids = await _resolve_account_uuids(session, player_uuid)
    records = await notification_service.fetch_pending(session, recipient_uuids, limit)
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
    """标投递：仅命中 body.player_uuid 同账号名下的 id（C-1 防跨账号越权）。"""
    recipient_uuids = await _resolve_account_uuids(session, body.player_uuid)
    count = await notification_service.mark_delivered(session, body.ids, recipient_uuids)
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
    """标已读：仅当 notification 归属 player_uuid 同账号（C-1 防跨账号越权，跨账号 404）。

    L-2：已读必然已投递，mark_read 同步幂等置 delivered_at。
    """
    recipient_uuids = await _resolve_account_uuids(session, player_uuid)
    ok = await notification_service.mark_read(session, notification_id, recipient_uuids)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "notification not found")
    await session.commit()
    record = await notification_service.fetch_by_id_or_none(
        session, notification_id, recipient_uuids
    )
    # mark_read 命中且归属已校验，记录必然存在；defensive
    assert record is not None
    return _to_out(record)
