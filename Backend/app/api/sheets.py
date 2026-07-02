"""sheets 路由（在线表格 CRUD + CSV 导出 + 行认领协作）。

权限（spec §5.3）：
- 读（GET /sheets, GET /sheets/{id}）：JWT 已登录玩家
- 写表/行 upsert/删（POST/PATCH/DELETE 表、PUT 行、DELETE 行）：表的 owner_uuid 或 admin/owner 角色
- 认领 claim（POST .../claim）：任意登录玩家
- 上报交付（PATCH .../delivery）：当前认领人 only
- 解除锁定（POST .../release）：认领人自放 或 拥有者
- 打回（POST .../reject）：认领人 或 拥有者（done→claimed，delivered 归零，认领人保留重做）
- CSV 全量导出（GET /sheets/export）：service token（外部系统读取，MVP §4 硬约束）

分层（红线）：api 调 repo，**commit 在 api 层**，repo 只 flush。
状态机转移 + with_for_update 在 repo；非法转移 raise SheetRowConflict → api 翻译为 409。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_player, require_service_token
from app.core.db import get_session
from app.models.sheet import Sheet, SheetRow
from app.models.user import Player
from app.repositories import sheet_repo
from app.repositories.sheet_repo import SheetRowConflict
from app.schemas.sheet import (
    RowDeliveryRequest,
    RowDetail,
    RowUpsertRequest,
    SheetCreateRequest,
    SheetDetail,
    SheetPatchRequest,
    SheetSummary,
)

router = APIRouter(prefix="/sheets", tags=["sheets"])


def _can_edit(sheet: Sheet, player: Player) -> bool:
    """表的 owner 或 admin/owner 角色可编辑（D-3，复用 deps.require_role 的 owner 隐式超级语义）。"""
    return sheet.owner_uuid == player.uuid or player.role in ("admin", "owner")


async def _load_sheet_or_404(session: AsyncSession, sheet_id: int) -> Sheet:
    result = await sheet_repo.get_sheet(session, sheet_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    return result[0]


def _row_dict(row: SheetRow, claimant_name: str | None = None) -> dict:
    return {
        "id": row.id,
        "item_name": row.item_name,
        "need_qty": row.need_qty,
        "mode": row.mode,
        "status": row.status,
        "claimant_uuid": row.claimant_uuid,
        "claimant_name": claimant_name,
        "delivered_qty": row.delivered_qty,
        "sort_order": row.sort_order,
        "updated_at": row.updated_at,
    }


def _to_summary(sheet: Sheet, owner_name: str) -> SheetSummary:
    return SheetSummary(
        id=sheet.id,
        owner_uuid=sheet.owner_uuid,
        owner_name=owner_name,
        title=sheet.title,
        created_at=sheet.created_at,
        updated_at=sheet.updated_at,
    )


def _to_detail(
    sheet: Sheet,
    rows_with_names: list[tuple[SheetRow, str | None]],
    owner_name: str,
) -> SheetDetail:
    return SheetDetail(
        id=sheet.id,
        owner_uuid=sheet.owner_uuid,
        owner_name=owner_name,
        title=sheet.title,
        created_at=sheet.created_at,
        updated_at=sheet.updated_at,
        rows=[RowDetail(**_row_dict(r, name)) for r, name in rows_with_names],
    )


@router.post("", response_model=SheetDetail, status_code=status.HTTP_201_CREATED)
async def create_sheet(
    body: SheetCreateRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> SheetDetail:
    sheet = await sheet_repo.create_sheet(session, player.uuid, body.title)
    await session.commit()
    await session.refresh(sheet)
    return _to_detail(sheet, [], player.current_name)


@router.get("", response_model=list[SheetSummary])
async def list_sheets(
    owner: str | None = Query(default=None, description="过滤：传 me 只看自己"),
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> list[SheetSummary]:
    owner_uuid = player.uuid if owner == "me" else None
    sheets_with_names = await sheet_repo.list_sheets(
        session, owner_uuid=owner_uuid
    )
    return [_to_summary(s, name) for s, name in sheets_with_names]


# 注意：/export 必须注册在 /{sheet_id} 之前，否则被动态路径吞掉
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
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
):
    result = await sheet_repo.get_sheet(session, sheet_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    sheet, owner_name = result
    rows_with_names = await sheet_repo.list_rows(session, sheet_id)
    if format == "csv":
        csv_str = sheet_repo.export_csv(sheet_id, [r for r, _ in rows_with_names])
        return PlainTextResponse(content=csv_str, media_type="text/csv")
    return _to_detail(sheet, rows_with_names, owner_name)


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
    return _to_detail(sheet, rows_with_names, owner_name)


@router.delete("/{sheet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sheet(
    sheet_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> Response:
    sheet = await _load_sheet_or_404(session, sheet_id)
    if not _can_edit(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    await sheet_repo.delete_sheet(session, sheet_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{sheet_id}/rows", response_model=RowDetail)
async def upsert_row(
    sheet_id: int,
    body: RowUpsertRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> RowDetail:
    sheet = await _load_sheet_or_404(session, sheet_id)
    if not _can_edit(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    try:
        row = await sheet_repo.upsert_row(
            session,
            sheet_id=sheet_id,
            item_name=body.item_name,
            need_qty=body.need_qty,
            mode=body.mode,
            sort_order=body.sort_order,
        )
        await session.commit()
    except IntegrityError as exc:  # 并发同名 insert 命中 UNIQUE
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict") from exc
    await session.refresh(row)
    return RowDetail(**_row_dict(row, None))


@router.delete("/{sheet_id}/rows/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_row(
    sheet_id: int,
    row_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> Response:
    sheet = await _load_sheet_or_404(session, sheet_id)
    if not _can_edit(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    count = await sheet_repo.delete_row(session, sheet_id, row_id)
    if count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- 行认领协作（spec §5.4） ----------
@router.post(
    "/{sheet_id}/rows/{row_id}/claim",
    response_model=RowDetail,
)
async def claim_row(
    sheet_id: int,
    row_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> RowDetail:
    await _load_sheet_or_404(session, sheet_id)
    try:
        row = await sheet_repo.claim_row(session, sheet_id, row_id, player.uuid)
        await session.commit()
    except SheetRowConflict as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict") from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    result = await sheet_repo.get_row(session, sheet_id, row_id)
    name = result[1] if result is not None else None
    await session.refresh(row)
    return RowDetail(**_row_dict(row, name))


@router.patch(
    "/{sheet_id}/rows/{row_id}/delivery",
    response_model=RowDetail,
)
async def set_row_delivery(
    sheet_id: int,
    row_id: int,
    body: RowDeliveryRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> RowDetail:
    await _load_sheet_or_404(session, sheet_id)
    current = await sheet_repo.get_row(session, sheet_id, row_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    if current[0].claimant_uuid != player.uuid:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not claimant")
    try:
        row = await sheet_repo.set_row_delivery(
            session, sheet_id, row_id, body.delivered_qty
        )
        await session.commit()
    except SheetRowConflict as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict") from exc
    result = await sheet_repo.get_row(session, sheet_id, row_id)
    name = result[1] if result is not None else None
    await session.refresh(row)
    return RowDetail(**_row_dict(row, name))


@router.post(
    "/{sheet_id}/rows/{row_id}/release",
    response_model=RowDetail,
)
async def release_row(
    sheet_id: int,
    row_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> RowDetail:
    sheet = await _load_sheet_or_404(session, sheet_id)
    current = await sheet_repo.get_row(session, sheet_id, row_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    if current[0].claimant_uuid != player.uuid and not _can_edit(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    try:
        row = await sheet_repo.release_row(session, sheet_id, row_id)
        await session.commit()
    except SheetRowConflict as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict") from exc
    result = await sheet_repo.get_row(session, sheet_id, row_id)
    name = result[1] if result is not None else None
    await session.refresh(row)
    return RowDetail(**_row_dict(row, name))


@router.post(
    "/{sheet_id}/rows/{row_id}/reject",
    response_model=RowDetail,
)
async def reject_row(
    sheet_id: int,
    row_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> RowDetail:
    sheet = await _load_sheet_or_404(session, sheet_id)
    current = await sheet_repo.get_row(session, sheet_id, row_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    # 认领人（自取消备齐）或拥有者（打回）均可；其余 403
    if current[0].claimant_uuid != player.uuid and not _can_edit(sheet, player):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    try:
        row = await sheet_repo.reject_row(session, sheet_id, row_id)
        await session.commit()
    except SheetRowConflict as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict") from exc
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "row not found")
    result = await sheet_repo.get_row(session, sheet_id, row_id)
    name = result[1] if result is not None else None
    await session.refresh(row)
    return RowDetail(**_row_dict(row, name))
