"""表级 CRUD + CSV 导出（原 sheets.py 块2）。"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_account_uuids,
    get_current_player,
    require_service_token,
)
from app.api.sheets._shared import (
    _can_manage,
    _load_sheet_or_404,
    _resolve_item_name,
    _to_detail,
    _to_summary,
    notify_rows_deleted,
)
from app.core.db import get_session
from app.models.sheet import SHEET_PHASE_ARCHIVED
from app.models.user import Player
from app.repositories import sheet_repo, web_account_repo
from app.repositories.player_repo import set_last_sheet
from app.repositories.sheet_repo import SheetArchived
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


@router.post("", response_model=SheetDetail, status_code=status.HTTP_201_CREATED)
async def create_sheet(
    body: SheetCreateRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
    account_uuids: set[uuid.UUID] = Depends(get_current_account_uuids),
) -> SheetDetail:
    sheet = await sheet_repo.create_sheet(session, player.uuid, body.title)
    await session.commit()
    await session.refresh(sheet)
    owner_name = await web_account_repo.resolve_display_name(session, player.uuid)
    return await _to_detail(
        session,
        sheet,
        [],
        owner_name,
        viewer_uuids=account_uuids,
        managers=[],
    )


@router.post("/from-items", response_model=SheetDetail, status_code=status.HTTP_201_CREATED)
async def create_sheet_from_items(
    body: SheetFromItemsRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
    account_uuids: set[uuid.UUID] = Depends(get_current_account_uuids),
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
                parent_row_id=item.parent_row_id,
                qty_per_unit=item.qty_per_unit,
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
    return await _to_detail(
        session,
        sheet_obj,
        rows_with_names,
        owner_name,
        viewer_uuids=account_uuids,
        managers=[],
    )


@router.get("", response_model=list[SheetSummary])
async def list_sheets(
    owner: str | None = Query(default=None, description="过滤：传 me 只看自己"),
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="阶段过滤：collecting / constructing / archived / active",
    ),
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
    account_uuids: set[uuid.UUID] = Depends(get_current_account_uuids),
) -> list[SheetSummary]:
    owner_uuid = player.uuid if owner == "me" else None

    # 聚合查询：按 account 的 UUID 集合参与优先排序（未绑账号回退 {self.uuid}）。
    # viewer_web_account_id 让 manager 关系表也纳入「参与过」UNION（第 4 源，account 锚）。
    sheets_with_names = await sheet_repo.list_sheets(
        session,
        owner_uuid=owner_uuid,
        status_filter=status_filter,
        player_uuids=list(account_uuids),
        viewer_web_account_id=player.web_account_id,
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
    account_uuids: set[uuid.UUID] = Depends(get_current_account_uuids),
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
    # 「我参与的行」高亮升 account 级：同 account 的 UUID 贡献过的行都算我的
    my_row_ids = {
        rid
        for rid, members in contributors_map.items()
        if any(
            (player.web_account_id is not None and aid == player.web_account_id)
            or bool(account_uuids & set(member_uuids))
            for aid, _dn, member_uuids, _qty in members
        )
    }
    ordered = sort_sheet_rows(rows_with_names, account_uuids, my_row_ids)
    try:
        await set_last_sheet(session, player, sheet_id)
        await session.commit()
    except Exception:
        logger.exception("record last_sheet_id failed player=%s sheet=%s", player.uuid, sheet_id)
    return await _to_detail(
        session,
        sheet,
        ordered,
        owner_name,
        contributors_map,
        viewer_uuids=account_uuids,
    )


@router.patch("/{sheet_id}", response_model=SheetDetail)
async def patch_sheet(
    sheet_id: int,
    body: SheetPatchRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
    account_uuids: set[uuid.UUID] = Depends(get_current_account_uuids),
) -> SheetDetail:
    sheet = await _load_sheet_or_404(session, sheet_id)
    if not _can_manage(sheet, player, account_uuids):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    if sheet.status == SHEET_PHASE_ARCHIVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "项目已归档，只读")
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
    return await _to_detail(
        session,
        sheet,
        rows_with_names,
        owner_name,
        contributors_map,
        viewer_uuids=account_uuids,
    )


@router.delete("/{sheet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sheet(
    sheet_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
    account_uuids: set[uuid.UUID] = Depends(get_current_account_uuids),
) -> Response:
    from sqlalchemy.exc import IntegrityError

    sheet = await _load_sheet_or_404(session, sheet_id)
    if not _can_manage(sheet, player, account_uuids):
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
    actor_name = await web_account_repo.resolve_display_name(session, player.uuid)
    await notify_rows_deleted(
        session,
        sheet=sheet,
        actor=player,
        actor_name=actor_name,
        account_uuids=account_uuids,
        rows_with_names=rows_with_names,
        contributors_map=contributors_map,
    )
    try:
        await sheet_repo.delete_sheet(session, sheet_id)
        await session.commit()
    except SheetArchived:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "项目已归档，只读")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
