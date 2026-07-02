"""sheets 路由（在线表格 CRUD + CSV 导出）。

权限（D-3）：
- 读（GET /sheets, GET /sheets/{id}）：JWT 已登录玩家
- 写（POST/PATCH/DELETE 表与行）：表的 owner_uuid 或 admin/owner 角色
- CSV 全量导出（GET /sheets/export）：service token（外部系统读取，MVP §4 硬约束）

分层（红线）：api 调 repo，**commit 在 api 层**，repo 只 flush。
upsert 并发同名 insert 触发 IntegrityError → 翻译为 409。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_player, require_service_token
from app.core.db import get_session
from app.models.sheet import Sheet
from app.models.user import Player
from app.repositories import sheet_repo
from app.schemas.sheet import (
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
    sheet = await sheet_repo.get_sheet(session, sheet_id)
    if sheet is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    return sheet


def _to_summary(sheet: Sheet) -> SheetSummary:
    return SheetSummary(
        id=sheet.id,
        owner_uuid=sheet.owner_uuid,
        title=sheet.title,
        created_at=sheet.created_at,
        updated_at=sheet.updated_at,
    )


def _to_detail(sheet: Sheet, rows: list) -> SheetDetail:
    return SheetDetail(
        id=sheet.id,
        owner_uuid=sheet.owner_uuid,
        title=sheet.title,
        created_at=sheet.created_at,
        updated_at=sheet.updated_at,
        rows=[RowDetail(**_row_dict(r)) for r in rows],
    )


def _row_dict(r) -> dict:
    return {
        "id": r.id,
        "item_name": r.item_name,
        "need_qty": r.need_qty,
        "done_flag": r.done_flag,
        "sort_order": r.sort_order,
        "updated_at": r.updated_at,
    }


@router.post("", response_model=SheetDetail, status_code=status.HTTP_201_CREATED)
async def create_sheet(
    body: SheetCreateRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> SheetDetail:
    sheet = await sheet_repo.create_sheet(session, player.uuid, body.title)
    await session.commit()
    await session.refresh(sheet)
    return _to_detail(sheet, [])


@router.get("", response_model=list[SheetSummary])
async def list_sheets(
    owner: str | None = Query(default=None, description="过滤：传 me 只看自己"),
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> list[SheetSummary]:
    owner_uuid = player.uuid if owner == "me" else None
    sheets = await sheet_repo.list_sheets(session, owner_uuid=owner_uuid)
    return [_to_summary(s) for s in sheets]


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
) -> SheetDetail:
    sheet = await _load_sheet_or_404(session, sheet_id)
    rows = await sheet_repo.list_rows(session, sheet_id)
    if format == "csv":
        csv_str = sheet_repo.export_csv(sheet_id, rows)
        return PlainTextResponse(content=csv_str, media_type="text/csv")
    return _to_detail(sheet, rows)


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
    rows = await sheet_repo.list_rows(session, sheet_id)
    return _to_detail(sheet, rows)


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
            done_flag=body.done_flag,
            sort_order=body.sort_order,
        )
        await session.commit()
    except IntegrityError as exc:  # 并发同名 insert 命中 UNIQUE
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "row conflict") from exc
    await session.refresh(row)
    return RowDetail(**_row_dict(row))


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
