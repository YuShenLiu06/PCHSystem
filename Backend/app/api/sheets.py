"""sheets 路由（在线表格 CRUD + CSV 导出）。

本文件当前为 L2 契约冻结桩 —— 全端点签名 + response_model 齐全，
body 抛 501，供 OpenAPI 冻结与前端契约开发。B3 任务替换为真实实现。

权限（D-3）：
- 读（GET /sheets, GET /sheets/{id}）：JWT 已登录玩家
- 写（POST/PATCH/DELETE 表与行）：表的 owner_uuid 或 admin/owner 角色
- CSV 全量导出（GET /sheets/export）：service token（外部系统读取，MVP §4 硬约束）
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_player, require_service_token
from app.core.db import get_session
from app.models.sheet import Sheet
from app.models.user import Player
from app.schemas.sheet import (
    RowDetail,
    RowUpsertRequest,
    SheetCreateRequest,
    SheetDetail,
    SheetPatchRequest,
    SheetSummary,
)

router = APIRouter(prefix="/sheets", tags=["sheets"])

_NOT_IMPL = HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "not implemented")


def _can_edit(sheet: Sheet, player: Player) -> bool:
    """表的 owner 或 admin/owner 角色可编辑（D-3，复用 deps.require_role 的 owner 隐式超级语义）。"""
    return sheet.owner_uuid == player.uuid or player.role in ("admin", "owner")


@router.post("", response_model=SheetDetail, status_code=status.HTTP_201_CREATED)
async def create_sheet(
    body: SheetCreateRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> SheetDetail:
    raise _NOT_IMPL


@router.get("", response_model=list[SheetSummary])
async def list_sheets(
    owner: str | None = Query(default=None, description="过滤：传 me 只看自己"),
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> list[SheetSummary]:
    raise _NOT_IMPL


# 注意：/export 必须注册在 /{sheet_id} 之前，否则被动态路径吞掉
@router.get("/export", response_class=PlainTextResponse)
async def export_all(
    _svc: None = Depends(require_service_token),
) -> PlainTextResponse:
    raise _NOT_IMPL


@router.get("/{sheet_id}", response_model=SheetDetail)
async def get_sheet(
    sheet_id: int,
    format: str | None = Query(default=None, description="传 csv 返回 text/csv"),
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> SheetDetail:
    raise _NOT_IMPL


@router.patch("/{sheet_id}", response_model=SheetDetail)
async def patch_sheet(
    sheet_id: int,
    body: SheetPatchRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> SheetDetail:
    raise _NOT_IMPL


@router.delete("/{sheet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sheet(
    sheet_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> None:
    raise _NOT_IMPL


@router.put("/{sheet_id}/rows", response_model=RowDetail)
async def upsert_row(
    sheet_id: int,
    body: RowUpsertRequest,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> RowDetail:
    raise _NOT_IMPL


@router.delete("/{sheet_id}/rows/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_row(
    sheet_id: int,
    row_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> None:
    raise _NOT_IMPL
