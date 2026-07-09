"""表级 CRUD + CSV 导出（原 sheets.py 块2）。"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_player, require_service_token
from app.api.sheets._shared import (
    _can_edit,
    _load_sheet_or_404,
    _resolve_item_name,
    _to_detail,
    _to_summary,
    notify_uuids,
)
from app.core.db import get_session
from app.models.user import Player
from app.repositories import sheet_repo
from app.repositories.player_repo import set_last_sheet
from app.schemas.sheet import (
    SheetCreateRequest,
    SheetDetail,
    SheetFromItemsRequest,
    SheetPatchRequest,
    SheetSummary,
)
from app.services.sheet_row_order import sort_sheet_rows

router = APIRouter(prefix="")
logger = logging.getLogger(__name__)


@router.post("/", response_model=SheetDetail, status_code=status.HTTP_201_CREATED)
async def create_sheet(
    body: SheetCreateRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> SheetDetail:
    sheet = await sheet_repo.create_sheet(session, player.uuid, body.title)
    await session.commit()
    await session.refresh(sheet)
    return _to_detail(sheet, [], player.current_name)


@router.post("/from-items", response_model=SheetDetail, status_code=status.HTTP_201_CREATED)
async def create_sheet_from_items(
    body: SheetFromItemsRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> SheetDetail:
    """按材料清单一次性建表 + 批量行（mode 默认 lock）；调用方=拥有者。单事务 commit。"""
    sheet = await sheet_repo.create_sheet(session, player.uuid, body.title)
    for item in body.items:
        item_name = _resolve_item_name(item.item_name, item.registry_id)
        try:
            await sheet_repo.create_row(
                session,
                sheet_id=sheet.id,
                item_name=item_name,
                need_qty=item.need_qty if item.need_qty is not None else 0,
                mode=item.mode if item.mode is not None else sheet_repo.MODE_LOCK,
                sort_order=item.sort_order if item.sort_order is not None else 0,
                registry_id=item.registry_id,
            )
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"物品名重复：{item_name} 已存在",
            ) from exc
    await session.commit()
    result = await sheet_repo.get_sheet(session, sheet.id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    sheet_obj, owner_name = result
    rows_with_names = await sheet_repo.list_rows(session, sheet.id)
    return _to_detail(sheet_obj, rows_with_names, owner_name)


@router.get("/", response_model=list[SheetSummary])
async def list_sheets(
    owner: str | None = Query(default=None, description="过滤：传 me 只看自己"),
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="阶段过滤：collecting / constructing / archived / active",
    ),
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> list[SheetSummary]:
    owner_uuid = player.uuid if owner == "me" else None
    sheets_with_names = await sheet_repo.list_sheets(
        session, owner_uuid=owner_uuid, status_filter=status_filter, player_uuid=player.uuid
    )
    return [_to_summary(s, name) for s, name in sheets_with_names]


@router.get("/export", response_class=PlainTextResponse)
async def export_all(
    session: AsyncSession = Depends(get_session),
    _svc: None = Depends(require_service_token),
) -> PlainTextResponse:
    bundled = await sheet_repo.list_all_sheets_with_rows(session)
    csv_str = sheet_repo.export_all_csv(bundled)
    return PlainTextResponse(content=csv_str, media_type="text/csv")


@router.get("/{sheet_id}", response_model=SheetDetail)
async def get_sheet(
    sheet_id: int,
    format: str | None = Query(default=None, description="传 csv 返回 text/csv"),
    q: str | None = Query(default=None, description="按 item_name/registry_id 大小写不敏感过滤行"),
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
):
    result = await sheet_repo.get_sheet(session, sheet_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    sheet, owner_name = result
    rows_with_names = await sheet_repo.list_rows(session, sheet_id, search=q)
    if format == "csv":
        csv_str = sheet_repo.export_csv(sheet_id, [r for r, _ in rows_with_names])
        return PlainTextResponse(content=csv_str, media_type="text/csv")
    contributors_map = await sheet_repo.list_contributors(
        session, [r.id for r, _ in rows_with_names]
    )
    my_row_ids = {
        rid
        for rid, members in contributors_map.items()
        if any(pu == player.uuid for pu, _ in members)
    }
    ordered = sort_sheet_rows(rows_with_names, player.uuid, my_row_ids)
    try:
        await set_last_sheet(session, player.uuid, sheet_id)
        await session.commit()
    except Exception:
        logger.exception("record last_sheet_id failed player=%s sheet=%s", player.uuid, sheet_id)
    return _to_detail(sheet, ordered, owner_name, contributors_map)


@router.patch("/{sheet_id}", response_model=SheetDetail)
async def patch_sheet(
    sheet_id: int,
    body: SheetPatchRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> SheetDetail:
    sheet = await _load_sheet_or_404(session, sheet_id)
    if not _can_edit(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    sheet.title = body.title
    await session.flush()
    await session.commit()
    await session.refresh(sheet)
    result = await sheet_repo.get_sheet(session, sheet_id)
    owner_name = result[1] if result is not None else ""
    rows_with_names = await sheet_repo.list_rows(session, sheet_id)
    contributors_map = await sheet_repo.list_contributors(
        session, [r.id for r, _ in rows_with_names]
    )
    return _to_detail(sheet, rows_with_names, owner_name, contributors_map)


@router.delete("/{sheet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sheet(
    sheet_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> Response:
    from sqlalchemy.exc import IntegrityError

    sheet = await _load_sheet_or_404(session, sheet_id)
    if not _can_edit(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    rows_with_names = await sheet_repo.list_rows(session, sheet_id)
    progress_row_ids = [
        r.id for r, _ in rows_with_names if r.mode == sheet_repo.MODE_PROGRESS
    ]
    contributors_map = (
        await sheet_repo.list_contributors(session, progress_row_ids)
        if progress_row_ids
        else {}
    )
    for old_row, _name in rows_with_names:
        item_name = old_row.item_name
        old_row_id = old_row.id
        claimant = old_row.claimant_uuid
        if claimant is not None:
            await notify_uuids(
                session,
                [claimant],
                actor=player,
                category="sheet_row_deleted",
                title="认领的行已被删除",
                body=f"[{item_name}] 已被拥有者删除，认领取消",
                sheet_id=sheet_id,
                sheet_title=sheet.title,
                row_id=old_row_id,
                item_name=item_name,
            )
        for contrib_uuid, _contrib_name in contributors_map.get(old_row_id, []):
            await notify_uuids(
                session,
                [contrib_uuid],
                actor=player,
                category="sheet_row_deleted",
                title="贡献的行已被删除",
                body=f"[{item_name}] 已被拥有者删除，贡献取消",
                sheet_id=sheet_id,
                sheet_title=sheet.title,
                row_id=old_row_id,
                item_name=item_name,
            )
    await sheet_repo.delete_sheet(session, sheet_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
