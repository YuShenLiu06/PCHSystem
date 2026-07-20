"""协作状态机 6 端点（原 sheets.py 块4）。

account 级统一（R-5 主锚落地，2026-07-19）：
- tier B 权限经 ``_can_operate``（owner/超管/manager）；claimant 判断用 ``account_uuids``。
- lock 行账号锁定：同 account 任一 UUID 认领的行，其他 UUID 可 deliver/release/reject。
- 通知显示名用 ``resolve_display_name``（自定义昵称优先）；contributors 按 account 聚合，
  通知时展开 ``member_uuids``；recipient 与 actor 同 account 自动跳过。
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_account_uuids, get_current_player
from app.api.sheets._shared import (
    _can_operate,
    _load_sheet_or_404,
    _row_dict,
    _row_response,
    notify_uuids,
)
from app.core.db import get_session
from app.models.user import Player
from app.repositories import sheet_repo, web_account_repo
from app.repositories.sheet_repo import SheetArchived, SheetRowConflict
from app.schemas.sheet import (
    RowContributeRequest,
    RowDeliveryRequest,
    RowDetail,
    RowProgressRequest,
)

router = APIRouter(prefix="")
logger = logging.getLogger(__name__)


@router.post("/{sheet_id}/rows/{row_id}/claim", response_model=RowDetail)
async def claim_row(
    sheet_id: int,
    row_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
    account_uuids: set[uuid.UUID] = Depends(get_current_account_uuids),
) -> RowDetail:
    sheet = await _load_sheet_or_404(session, sheet_id)
    actor_name = await web_account_repo.resolve_display_name(session, player.uuid)
    try:
        row = await sheet_repo.claim_row(session, sheet_id, row_id, player.uuid)
        if row is not None:
            result = await sheet_repo.get_row(session, sheet_id, row_id)
            item_name = result[0].item_name if result else ""
            await notify_uuids(
                session,
                [sheet.owner_uuid],
                actor=player,
                actor_name=actor_name,
                account_uuids=account_uuids,
                category="sheet_claimed",
                title="物品被认领",
                body=f"{actor_name} 认领了 [{item_name}]",
                sheet_id=sheet_id,
                sheet_title=sheet.title,
                row_id=row_id,
                item_name=item_name,
            )
        await session.commit()
    except SheetArchived:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "项目已归档，只读")
    except SheetRowConflict as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict") from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    return await _row_response(session, sheet_id, row)


@router.patch("/{sheet_id}/rows/{row_id}/delivery", response_model=RowDetail)
async def set_row_delivery(
    sheet_id: int,
    row_id: int,
    body: RowDeliveryRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
    account_uuids: set[uuid.UUID] = Depends(get_current_account_uuids),
) -> RowDetail:
    sheet = await _load_sheet_or_404(session, sheet_id)
    current = await sheet_repo.get_row(session, sheet_id, row_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    if current[0].mode == sheet_repo.MODE_PROGRESS:
        pass
    elif current[0].claimant_uuid not in account_uuids:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not claimant")
    actor_name = await web_account_repo.resolve_display_name(session, player.uuid)
    try:
        row = await sheet_repo.set_row_delivery(
            session, sheet_id, row_id, body.delivered_qty
        )
        if row is not None:
            is_done = body.delivered_qty >= row.need_qty
            await notify_uuids(
                session,
                [sheet.owner_uuid],
                actor=player,
                actor_name=actor_name,
                account_uuids=account_uuids,
                category="sheet_done" if is_done else "sheet_delivered",
                title="物品已备齐" if is_done else "物品上报交付",
                body=(
                    f"{actor_name} 已备齐 [{row.item_name}]"
                    if is_done
                    else f"{actor_name} 上报交付 {body.delivered_qty}/{row.need_qty} [{row.item_name}]"
                ),
                sheet_id=sheet_id,
                sheet_title=sheet.title,
                row_id=row_id,
                item_name=row.item_name,
                delivered=body.delivered_qty,
                need=row.need_qty,
            )
        await session.commit()
    except SheetArchived:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "项目已归档，只读")
    except SheetRowConflict as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict") from exc
    return await _row_response(session, sheet_id, row)


@router.post("/{sheet_id}/rows/{row_id}/release", response_model=RowDetail)
async def release_row(
    sheet_id: int,
    row_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
    account_uuids: set[uuid.UUID] = Depends(get_current_account_uuids),
) -> RowDetail:
    sheet = await _load_sheet_or_404(session, sheet_id)
    current = await sheet_repo.get_row(session, sheet_id, row_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    old_row = current[0]
    prev_claimant = old_row.claimant_uuid
    prev_item = old_row.item_name
    prev_mode = old_row.mode
    is_claimant_in_account = (
        prev_claimant in account_uuids if prev_claimant is not None else False
    )
    if not is_claimant_in_account and not _can_operate(
        sheet, player, account_uuids
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    actor_name = await web_account_repo.resolve_display_name(session, player.uuid)
    progress_contributors: list[tuple[int | None, str, list[uuid.UUID], int]] = []
    if prev_mode == sheet_repo.MODE_PROGRESS:
        _contrib_map = await sheet_repo.list_contributors(session, [old_row.id])
        progress_contributors = _contrib_map.get(old_row.id, [])
    try:
        row = await sheet_repo.release_row(session, sheet_id, row_id)
        if row is not None and prev_claimant is not None:
            if is_claimant_in_account:
                await notify_uuids(
                    session,
                    [sheet.owner_uuid],
                    actor=player,
                    actor_name=actor_name,
                    account_uuids=account_uuids,
                    category="sheet_released",
                    title="认领已取消",
                    body=f"{actor_name} 取消了对 [{prev_item}] 的认领",
                    sheet_id=sheet_id,
                    sheet_title=sheet.title,
                    row_id=row_id,
                    item_name=prev_item,
                )
            else:
                await notify_uuids(
                    session,
                    [prev_claimant],
                    actor=player,
                    actor_name=actor_name,
                    account_uuids=account_uuids,
                    category="sheet_released",
                    title="锁定已被拥有者解除",
                    body=f"拥有者解除了你对 [{prev_item}] 的锁定",
                    sheet_id=sheet_id,
                    sheet_title=sheet.title,
                    row_id=row_id,
                    item_name=prev_item,
                )
        if row is not None and prev_mode == sheet_repo.MODE_PROGRESS:
            for _aid, _dn, member_uuids, _qty in progress_contributors:
                await notify_uuids(
                    session,
                    member_uuids,
                    actor=player,
                    actor_name=actor_name,
                    account_uuids=account_uuids,
                    category="sheet_progress_reset",
                    title="贡献已被拥有者清空",
                    body=f"拥有者解除了 [{prev_item}] 的进度行，你的贡献已清空",
                    sheet_id=sheet_id,
                    sheet_title=sheet.title,
                    row_id=row_id,
                    item_name=prev_item,
                )
        await session.commit()
    except SheetArchived:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "项目已归档，只读")
    except SheetRowConflict as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict") from exc
    return await _row_response(session, sheet_id, row)


@router.post("/{sheet_id}/rows/{row_id}/reject", response_model=RowDetail)
async def reject_row(
    sheet_id: int,
    row_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
    account_uuids: set[uuid.UUID] = Depends(get_current_account_uuids),
) -> RowDetail:
    sheet = await _load_sheet_or_404(session, sheet_id)
    current = await sheet_repo.get_row(session, sheet_id, row_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    old_row = current[0]
    if (
        old_row.claimant_uuid not in account_uuids
        and not _can_operate(sheet, player, account_uuids)
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    actor_name = await web_account_repo.resolve_display_name(session, player.uuid)
    try:
        row = await sheet_repo.reject_row(session, sheet_id, row_id)
        if row is not None and old_row.claimant_uuid is not None:
            await notify_uuids(
                session,
                [old_row.claimant_uuid],
                actor=player,
                actor_name=actor_name,
                account_uuids=account_uuids,
                category="sheet_rejected",
                title="物品已打回",
                body=f"[{old_row.item_name}] 已打回，delivered 归零，可重做",
                sheet_id=sheet_id,
                sheet_title=sheet.title,
                row_id=row_id,
                item_name=old_row.item_name,
            )
        await session.commit()
    except SheetArchived:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "项目已归档，只读")
    except SheetRowConflict as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict") from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    return await _row_response(session, sheet_id, row)


@router.post("/{sheet_id}/rows/{row_id}/contribute", response_model=RowDetail)
async def contribute_to_row(
    sheet_id: int,
    row_id: int,
    body: RowContributeRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
    account_uuids: set[uuid.UUID] = Depends(get_current_account_uuids),
) -> RowDetail:
    """progress 行增量上交（任意登录玩家）。"""
    sheet = await _load_sheet_or_404(session, sheet_id)
    actor_name = await web_account_repo.resolve_display_name(session, player.uuid)
    try:
        row = await sheet_repo.contribute_row(
            session, sheet_id, row_id, player.uuid, body.qty
        )
        if row is not None:
            is_done = row.delivered_qty >= row.need_qty
            await notify_uuids(
                session,
                [sheet.owner_uuid],
                actor=player,
                actor_name=actor_name,
                account_uuids=account_uuids,
                category="sheet_done" if is_done else "sheet_delivered",
                title="物品已备齐" if is_done else "物品收到上交",
                body=(
                    f"{actor_name} 上交 {body.qty}，已备齐 [{row.item_name}]"
                    f"（累计 {row.delivered_qty}/{row.need_qty}）"
                    if is_done
                    else f"{actor_name} 上交 {body.qty}"
                    f"（累计 {row.delivered_qty}/{row.need_qty}）[{row.item_name}]"
                ),
                sheet_id=sheet_id,
                sheet_title=sheet.title,
                row_id=row_id,
                item_name=row.item_name,
                delta=body.qty,
                delivered=row.delivered_qty,
                need=row.need_qty,
            )
        await session.commit()
    except SheetArchived:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "项目已归档，只读")
    except SheetRowConflict as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict") from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    return await _row_response(session, sheet_id, row, with_contributors=True)


@router.patch("/{sheet_id}/rows/{row_id}/progress", response_model=RowDetail)
async def set_row_progress(
    sheet_id: int,
    row_id: int,
    body: RowProgressRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
    account_uuids: set[uuid.UUID] = Depends(get_current_account_uuids),
) -> RowDetail:
    """progress 行 owner 直接修正进度（绝对值，可增可减）。"""
    sheet = await _load_sheet_or_404(session, sheet_id)
    if not _can_operate(sheet, player, account_uuids):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    actor_name = await web_account_repo.resolve_display_name(session, player.uuid)
    current = await sheet_repo.get_row(session, sheet_id, row_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    old_row = current[0]
    if old_row.mode != sheet_repo.MODE_PROGRESS:
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict")
    old_delivered = old_row.delivered_qty
    prev_item = old_row.item_name
    contrib_snapshot: list[tuple[int | None, str, list[uuid.UUID], int]] = (
        (await sheet_repo.list_contributors(session, [old_row.id])).get(old_row.id, [])
    )
    try:
        row = await sheet_repo.set_row_progress(
            session, sheet_id, row_id, body.delivered_qty
        )
        if row is not None:
            await notify_uuids(
                session,
                [sheet.owner_uuid],
                actor=player,
                actor_name=actor_name,
                account_uuids=account_uuids,
                category="sheet_qty_changed",
                title="进度已调整",
                body=(
                    f"{actor_name} 将 [{row.item_name}] 的进度"
                    f"调整为 {body.delivered_qty}/{row.need_qty}"
                ),
                sheet_id=sheet_id,
                sheet_title=sheet.title,
                row_id=row_id,
                item_name=row.item_name,
                delivered=body.delivered_qty,
                need=row.need_qty,
            )
            if body.delivered_qty != old_delivered:
                for _aid, _dn, member_uuids, _qty in contrib_snapshot:
                    await notify_uuids(
                        session,
                        member_uuids,
                        actor=player,
                        actor_name=actor_name,
                        account_uuids=account_uuids,
                        category="sheet_progress_changed",
                        title="进度已被拥有者调整",
                        body=(
                            f"拥有者将 [{prev_item}] 的进度调整为 "
                            f"{body.delivered_qty}/{row.need_qty}（原 {old_delivered}）"
                        ),
                        sheet_id=sheet_id,
                        sheet_title=sheet.title,
                        row_id=row_id,
                        item_name=prev_item,
                        old=old_delivered,
                        new=body.delivered_qty,
                        need=row.need_qty,
                    )
        await session.commit()
    except SheetArchived:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "项目已归档，只读")
    except SheetRowConflict as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict") from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    return await _row_response(session, sheet_id, row, with_contributors=True)
