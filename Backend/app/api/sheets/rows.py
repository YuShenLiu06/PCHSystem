"""行 upsert/delete + 编辑通知（原 sheets.py 块3）。"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_player
from app.api.sheets._shared import (
    _can_edit,
    _load_sheet_or_404,
    _resolve_item_name,
    _row_dict,
    notify_uuids,
)
from app.core.db import get_session
from app.models.sheet import Sheet, SheetRow
from app.models.user import Player
from app.repositories import sheet_repo
from app.repositories.sheet_repo import SheetArchived
from app.schemas.sheet import RowDetail, RowUpsertRequest

router = APIRouter(prefix="")
logger = logging.getLogger(__name__)


async def _collect_progress_contributors(
    session: AsyncSession, row: SheetRow
) -> list[tuple[uuid.UUID, str]]:
    """mode 从 progress 变走时，repo 会 clear_contributors —— 预取名单用于事后通知。"""
    _contrib_map = await sheet_repo.list_contributors(session, [row.id])
    return _contrib_map.get(row.id, [])


async def _dispatch_row_edit_notifications(
    session: AsyncSession,
    *,
    sheet: Sheet,
    player: Player,
    row: SheetRow,
    item_name: str,
    mode_changed: bool,
    prev_mode: int | None,
    old_need: int | None,
    new_need: int,
    claimant_uuid: uuid.UUID | None,
    progress_contributors: list[tuple[uuid.UUID, str]],
) -> None:
    """行编辑后的通知派发（新建/更新两路径共用，DRY）。"""
    sheet_id = sheet.id
    if mode_changed and claimant_uuid is not None:
        await notify_uuids(
            session,
            [claimant_uuid],
            actor=player,
            category="sheet_released",
            title="模式变更，认领已重置",
            body=f"[{item_name}] 拥有者调整了模式，认领/进度已重置",
            sheet_id=sheet_id,
            sheet_title=sheet.title,
            row_id=row.id,
            item_name=item_name,
        )
    if mode_changed and prev_mode == sheet_repo.MODE_PROGRESS:
        for contrib_uuid, _cname in progress_contributors:
            await notify_uuids(
                session,
                [contrib_uuid],
                actor=player,
                category="sheet_progress_reset",
                title="贡献已被拥有者清空",
                body=f"拥有者调整了 [{item_name}] 的模式，进度与贡献已重置",
                sheet_id=sheet_id,
                sheet_title=sheet.title,
                row_id=row.id,
                item_name=item_name,
            )
    elif (
        not mode_changed
        and claimant_uuid is not None
        and old_need is not None
        and old_need != new_need
    ):
        await notify_uuids(
            session,
            [claimant_uuid],
            actor=player,
            category="sheet_qty_changed",
            title="所需数量已调整",
            body=f"[{item_name}] 所需数量变为 {new_need}（原 {old_need}）",
            sheet_id=sheet_id,
            sheet_title=sheet.title,
            row_id=row.id,
            item_name=item_name,
            old=old_need,
            new=new_need,
        )


async def _create_row_by_item(
    session: AsyncSession, sheet: Sheet, body: RowUpsertRequest
) -> SheetRow:
    """新建路径（无 row_id）：严格 INSERT。"""
    # 子物品路径：parent_row_id 非空时 item_name 可缺失（用 registry_id 翻译）
    if body.parent_row_id is not None:
        item_name = _resolve_item_name(body.item_name, body.registry_id)
    else:
        item_name = _resolve_item_name(body.item_name, body.registry_id)
    # 子物品模式继承：mode=None 传 None 让 repo 处理继承；顶层默认 MODE_LOCK
    if body.parent_row_id is not None and body.mode is None:
        mode = None  # 子物品：repo 层继承父 mode
    else:
        mode = body.mode if body.mode is not None else sheet_repo.MODE_LOCK
    row = await sheet_repo.create_row(
        session,
        sheet_id=sheet.id,
        item_name=item_name,
        need_qty=body.need_qty if body.need_qty is not None else 0,
        mode=mode,
        sort_order=body.sort_order if body.sort_order is not None else 0,
        registry_id=body.registry_id,
        parent_row_id=body.parent_row_id,
        qty_per_unit=body.qty_per_unit,
    )
    return row


async def _update_row_by_id(
    session: AsyncSession, sheet: Sheet, player: Player, body: RowUpsertRequest
) -> SheetRow:
    """更新路径（带 row_id）：按主键部分更新。"""
    prev = await sheet_repo.get_row(session, sheet.id, body.row_id)
    if prev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    prev_row, _ = prev
    old_need = prev_row.need_qty
    claimant_uuid = prev_row.claimant_uuid
    prev_mode = prev_row.mode
    mode_changed = body.mode is not None and prev_mode != body.mode
    progress_contributors: list[tuple[uuid.UUID, str]] = (
        await _collect_progress_contributors(session, prev_row)
        if mode_changed and prev_mode == sheet_repo.MODE_PROGRESS
        else []
    )
    item_name = body.item_name if body.item_name is not None else prev_row.item_name
    new_need = body.need_qty if body.need_qty is not None else old_need
    row = await sheet_repo.update_row(
        session,
        sheet_id=sheet.id,
        row_id=body.row_id,
        item_name=body.item_name,
        registry_id=body.registry_id,
        need_qty=body.need_qty,
        mode=body.mode,
        sort_order=body.sort_order,
        parent_row_id=body.parent_row_id,
        qty_per_unit=body.qty_per_unit,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    await _dispatch_row_edit_notifications(
        session,
        sheet=sheet,
        player=player,
        row=row,
        item_name=item_name,
        mode_changed=mode_changed,
        prev_mode=prev_mode,
        old_need=old_need,
        new_need=new_need,
        claimant_uuid=claimant_uuid,
        progress_contributors=progress_contributors,
    )
    return row


@router.put("/{sheet_id}/rows", response_model=RowDetail)
async def upsert_row(
    sheet_id: int,
    body: RowUpsertRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> RowDetail:
    """行新建 / 更新（单端点按 ``row_id`` 分流）。"""
    sheet = await _load_sheet_or_404(session, sheet_id)
    if not _can_edit(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    try:
        if body.row_id is not None:
            row = await _update_row_by_id(session, sheet, player, body)
        else:
            row = await _create_row_by_item(session, sheet, body)
        await session.commit()
    except SheetArchived:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "项目已归档，只读"
        )
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "物品名重复：该项目已存在同名行，请编辑该行而非新建",
        ) from exc
    await session.refresh(row)
    return RowDetail(**_row_dict(row, None))


@router.delete("/{sheet_id}/rows/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_row(
    sheet_id: int,
    row_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
):
    sheet = await _load_sheet_or_404(session, sheet_id)
    if not _can_edit(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    current = await sheet_repo.get_row(session, sheet_id, row_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    old_row = current[0]
    if old_row.claimant_uuid is not None:
        await notify_uuids(
            session,
            [old_row.claimant_uuid],
            actor=player,
            category="sheet_row_deleted",
            title="认领的行已被删除",
            body=f"[{old_row.item_name}] 已被拥有者删除，认领取消",
            sheet_id=sheet_id,
            sheet_title=sheet.title,
            row_id=row_id,
            item_name=old_row.item_name,
        )
    if old_row.mode == sheet_repo.MODE_PROGRESS:
        contrib_map = await sheet_repo.list_contributors(session, [old_row.id])
        for contrib_uuid, _name in contrib_map.get(old_row.id, []):
            await notify_uuids(
                session,
                [contrib_uuid],
                actor=player,
                category="sheet_row_deleted",
                title="贡献的行已被删除",
                body=f"[{old_row.item_name}] 已被拥有者删除，贡献取消",
                sheet_id=sheet_id,
                sheet_title=sheet.title,
                row_id=row_id,
                item_name=old_row.item_name,
            )
    try:
        count = await sheet_repo.delete_row(session, sheet_id, row_id)
        if count == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
        await session.commit()
    except SheetArchived:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "项目已归档，只读")
