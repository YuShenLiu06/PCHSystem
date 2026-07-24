"""项目阶段 advance + 归档读（原 sheets.py 块5）。"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_account_uuids, get_current_player
from app.api.sheets._shared import (
    _can_manage,
    _can_operate,
    _load_sheet_or_404,
    _to_detail,
    notify_uuids,
)
from app.core.config import get_settings
from app.core.db import get_session
from app.models.sheet import (
    SHEET_PHASE_ARCHIVED,
    SHEET_PHASE_COLLECTING,
    SHEET_PHASE_CONSTRUCTING,
)
from app.models.user import Player
from app.repositories import sheet_repo, web_account_repo
from app.repositories.sheet_repo import SheetArchived, SheetRowConflict
from app.schemas.sheet import SheetDetail
from app.services.archive import (
    ArchiveNotConfigured,
    SheetNotFoundError,
    SheetStatusError,
    archive_sheet,
    read_archive_bytes,
    read_archive_file,
)

router = APIRouter(prefix="")
logger = logging.getLogger(__name__)

# 阶段过滤合法值
_VALID_STATUS_FILTERS = frozenset(
    {SHEET_PHASE_COLLECTING, SHEET_PHASE_CONSTRUCTING, SHEET_PHASE_ARCHIVED, "active"}
)
# advance ?to= 合法目标
_VALID_ADVANCE_TARGETS = frozenset({SHEET_PHASE_CONSTRUCTING, SHEET_PHASE_ARCHIVED})

# 资产白名单
_ARCHIVE_ASSET_WHITELIST = frozenset({"contributions.png"})


def _infer_advance_target(current_status: str) -> str:
    """缺省 ``to`` 时按状态机推进下一态。"""
    if current_status == SHEET_PHASE_COLLECTING:
        return SHEET_PHASE_CONSTRUCTING
    return SHEET_PHASE_ARCHIVED


async def _sheet_detail_or_404(
    session: AsyncSession, sheet_id: int, *, viewer_uuids: set[uuid.UUID]
):
    """重新构造 SheetDetail（advance 后取最新 sheet + rows + contributors + managers）。"""
    result = await sheet_repo.get_sheet(session, sheet_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    sheet, owner_name = result
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
        viewer_uuids=viewer_uuids,
    )


@router.post("/{sheet_id}/advance", response_model=SheetDetail)
async def advance_sheet_phase(
    sheet_id: int,
    to: str | None = Query(
        default=None, description="目标阶段：constructing / archived；缺省按状态机推进"
    ),
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
    account_uuids: set[uuid.UUID] = Depends(get_current_account_uuids),
) -> SheetDetail:
    """项目阶段流转。

    tier 分流（迁移 0014）：归档（→archived）是 tier A 高危操作，仅 owner/超管；
    进入施工（→constructing）是 tier B 常规操作，manager 也可触发。先按 target
    分流权限，再做状态机校验——manager 调 ``?to=archived`` 立即收到 403，不被
    先报 409 误导。
    """
    if to is not None and to not in _VALID_ADVANCE_TARGETS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"invalid 'to' target: {to} (expected constructing|archived)",
        )
    sheet = await _load_sheet_or_404(session, sheet_id)
    target = to if to is not None else _infer_advance_target(sheet.status)
    if target == SHEET_PHASE_ARCHIVED:
        if not _can_manage(sheet, player, account_uuids):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    else:
        if not _can_operate(sheet, player, account_uuids):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")

    if target == SHEET_PHASE_ARCHIVED:
        try:
            await archive_sheet(
                session,
                sheet_id,
                archive_root=get_settings().archive_root,
                player=player,
                actor_account_uuids=account_uuids,
            )
        except SheetNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
        except SheetArchived:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "sheet is archived, read-only"
            )
        except SheetStatusError:
            raise HTTPException(status.HTTP_409_CONFLICT, "illegal phase transition")
        except ArchiveNotConfigured:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "archive root not configured"
            )
    else:
        try:
            advanced = await sheet_repo.advance_sheet(
                session, sheet_id, SHEET_PHASE_CONSTRUCTING
            )
        except SheetArchived:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "sheet is archived, read-only"
            )
        except SheetRowConflict as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        if advanced is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
        # 通知全部参与者进入施工阶段（owner + managers + 认领者 + 贡献者）；
        # 触发者同 account 跳过（含 owner/manager 自推进）。issue #4
        participants = await sheet_repo.collect_participant_uuids(session, sheet_id)
        actor_name = await web_account_repo.resolve_display_name(session, player.uuid)
        await notify_uuids(
            session,
            list(participants),
            actor=player,
            actor_name=actor_name,
            account_uuids=account_uuids,
            category="sheet_advanced_constructing",
            title="项目已进入施工阶段",
            body=f"{actor_name} 将 [{advanced.title}] 推进至施工阶段",
            sheet_id=sheet_id,
            sheet_title=advanced.title,
        )
        await session.commit()
        await session.refresh(advanced)
    return await _sheet_detail_or_404(session, sheet_id, viewer_uuids=account_uuids)


@router.get("/{sheet_id}/archive", response_class=PlainTextResponse)
async def get_sheet_archive(
    sheet_id: int,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> Response:
    """读归档 markdown（text/markdown）。未归档 / 文件缺失 → 404。"""
    result = await sheet_repo.get_sheet(session, sheet_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    sheet, _owner_name = result
    if sheet.status != SHEET_PHASE_ARCHIVED or not sheet.archived_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet is not archived")
    md = read_archive_file(get_settings().archive_root, sheet.archived_path)
    if md is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "archive file missing")
    return Response(content=md, media_type="text/markdown")


@router.get(
    "/{sheet_id}/archive/assets/{filename}",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
async def get_sheet_archive_asset(
    sheet_id: int,
    filename: str,
    session: AsyncSession = Depends(get_session),
    player: Player = Depends(get_current_player),
) -> Response:
    """读归档产物（如 contributions.png，image/png）。"""
    if filename not in _ARCHIVE_ASSET_WHITELIST:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invalid asset filename")
    result = await sheet_repo.get_sheet(session, sheet_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    sheet, _owner_name = result
    if sheet.status != SHEET_PHASE_ARCHIVED or not sheet.archived_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet is not archived")
    parent = sheet.archived_path.rsplit("/", 1)[0]
    data = read_archive_bytes(get_settings().archive_root, f"{parent}/{filename}")
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    return Response(content=data, media_type="image/png")
