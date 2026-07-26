"""批量提交端点（``POST /sheets/{sheet_id}/submit-batch``）。

薄壳：鉴权 + 通知，逻辑全在 ``sheet_repo.batch_submit``。

鉴权用 ``get_current_player`` 双通道（镜像 collab.py / construction-report 信任边界，
plan §信任边界）：
- JWT 通道（玩家客户端模组）：身份死锚 ``JWT.active_uuid``，请求体或头里任何
  ``player_uuid`` 字段都被忽略。
- service-token + ``X-Player-UUID`` 通道（MCDR / 服务端 mod / 服主脚本）：代任意
  玩家写（服主守护 token，R-11 不外发客户端）。

权限：任意登录玩家可调（镜像 ``claim_row`` / ``contribute_row`` tier=「任意 auth player」，
行级隐式按 account_uuids 判定）。**不挂** ``_can_operate`` / ``_can_manage``。

事务边界：repo ``batch_submit`` 单事务逐行 FOR UPDATE；``SheetArchived`` → rollback
+ 409 整批回滚；行级 ``SheetRowConflict`` 在 repo 内捕获转 skip「行状态变化」（保部分成功）。
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_account_uuids, get_current_player
from app.api.sheets._shared import (
    _load_sheet_or_404,
    notify_owner_row_event,
)
from app.core.db import get_session
from app.models.user import Player
from app.repositories import sheet_repo, web_account_repo
from app.repositories.sheet_repo import SheetArchived
from app.schemas.sheet import (
    BatchRowOutcome,
    BatchSubmitRequest,
    BatchSubmitResult,
)

router = APIRouter(prefix="")
logger = logging.getLogger(__name__)


@router.post("/{sheet_id}/submit-batch", response_model=BatchSubmitResult)
async def submit_batch(
    sheet_id: int,
    body: BatchSubmitRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
    account_uuids: set[uuid.UUID] = Depends(get_current_account_uuids),
) -> BatchSubmitResult:
    """批量提交：客户端传材料列表，后端按 mode 分发 deliver/contribute。

    鉴权身份决定 actor（JWT=``JWT.active_uuid`` / service-token=``X-Player-UUID``）；
    请求体不带 ``player_uuid``。actor 与 owner 同 account 时跳过 owner 通知。
    """
    sheet = await _load_sheet_or_404(session, sheet_id)
    actor_name = await web_account_repo.resolve_display_name(session, player.uuid)
    try:
        result = await sheet_repo.batch_submit(
            session,
            sheet_id=sheet.id,
            items_map=body.to_map(),
            player_uuid=player.uuid,
            account_uuids=account_uuids,
        )
        # 通知：仅 deliver/contribute 成功行通知 owner；skip 不通知
        for outcome in result.outcomes:
            if outcome.action == "skipped":
                continue
            await _notify_batch_outcome(
                session,
                sheet=sheet,
                actor=player,
                actor_name=actor_name,
                account_uuids=account_uuids,
                outcome=outcome,
            )
        await session.commit()
    except SheetArchived:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "项目已归档，只读")
    return result


async def _notify_batch_outcome(
    session: AsyncSession,
    *,
    sheet,
    actor: Player,
    actor_name: str,
    account_uuids: set[uuid.UUID],
    outcome: BatchRowOutcome,
) -> None:
    """单 outcome 通知 owner（镜像 collab.delivery / contribute 通知文案）。"""
    if outcome.action == "delivered":
        is_done = outcome.delivered_qty >= outcome.need_qty
        await notify_owner_row_event(
            session,
            sheet=sheet,
            actor=actor,
            actor_name=actor_name,
            account_uuids=account_uuids,
            category="sheet_done" if is_done else "sheet_delivered",
            title="物品已备齐" if is_done else "物品上报交付",
            body=(
                f"{actor_name} 已备齐 [{outcome.item_name}]"
                if is_done
                else f"{actor_name} 上报交付 {outcome.delivered_qty}/{outcome.need_qty}"
                f" [{outcome.item_name}]"
            ),
            row_id=outcome.row_id,
            item_name=outcome.item_name,
            delivered=outcome.delivered_qty,
            need=outcome.need_qty,
        )
    elif outcome.action == "contributed":
        # need=0 = 无限收集（永不 done）；对齐 _apply_contribution 的 status 判定，
        # 否则 need=0 行任意 contribute 都会误报「已备齐」但行实际仍 CLAIMED
        is_done = (
            outcome.need_qty > 0
            and outcome.delivered_qty >= outcome.need_qty
        )
        await notify_owner_row_event(
            session,
            sheet=sheet,
            actor=actor,
            actor_name=actor_name,
            account_uuids=account_uuids,
            category="sheet_done" if is_done else "sheet_delivered",
            title="物品已备齐" if is_done else "物品收到上交",
            body=(
                f"{actor_name} 上交 {outcome.qty}，已备齐 [{outcome.item_name}]"
                f"（累计 {outcome.delivered_qty}/{outcome.need_qty}）"
                if is_done
                else f"{actor_name} 上交 {outcome.qty}"
                f"（累计 {outcome.delivered_qty}/{outcome.need_qty}）"
                f" [{outcome.item_name}]"
            ),
            row_id=outcome.row_id,
            item_name=outcome.item_name,
            delta=outcome.qty,
            delivered=outcome.delivered_qty,
            need=outcome.need_qty,
        )
