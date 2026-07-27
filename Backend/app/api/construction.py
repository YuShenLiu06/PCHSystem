"""施工进度上报层 HTTP 端点（``/v1/construction``）。

端点契约见 [`Docs/architecture/api/construction.md`]，设计见
[`Docs/architecture/flows/construction-progress.md`]。

11 端点（9 唯一路径）：

- ``POST /report`` —— 上报（双通道：service-token 多玩家 / JWT[mod_id] 强制 active_uuid）
- ``GET /active-sheets`` —— 归因查询（启发式：恰 1 个 constructing）
- ``GET /{sheet_id}/progress`` —— 进度展示（任意登录玩家/服务端组件）
- ``GET`` / ``PATCH /settings`` —— admin 读/改运行时开关
- ``GET`` / ``POST /mod-sources`` + ``DELETE /mod-sources/{name}`` —— admin 白名单 CRUD
- ``POST /source/switch-server`` —— admin 切某玩家服务端源（D9）
- ``POST /source/switch-self`` —— 玩家切自己上报模式（D9）
- ``GET /source/me`` —— 玩家查活跃源 + 历史

权限（R-9/RS-2）：admin 端点挂 ``require_role("admin")``（后端真实拒绝 403）；
report 走专用 ``get_construction_reporter``；其余任意登录玩家可读。
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_active_uuid,
    get_construction_reporter,
    get_current_player,
    require_role,
)
from app.core.db import get_session
from app.models.sheet import Sheet
from app.models.user import Player
from app.repositories import construction_repo, player_repo
from app.schemas.construction import (
    ActiveSheetsResult,
    ConstructionProgress,
    ConstructionSettings,
    ConstructionSettingsUpdate,
    PlacementReport,
    PlacementReportResult,
    ServerModSourceCreate,
    ServerModSourceEntry,
    SourceMeResult,
    SourceState,
    SourceSwitchSelfRequest,
    SourceSwitchServerRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/construction", tags=["construction"])


# ===========================================================================
# 上报 + 归因查询
# ===========================================================================

@router.post("/report", response_model=PlacementReportResult)
async def report_placements(
    body: PlacementReport,
    session: AsyncSession = Depends(get_session),
    reporter=Depends(get_construction_reporter),
) -> PlacementReportResult:
    """施工方块净放置上报。

    显式 ``sheet_id`` 须存在且处于 constructing（否则 404/409）；``None`` → 启发式
    归因。严格单源（C-7）：非活跃源的玩家 entries 全 skip，不隐式切源。
    """
    if body.sheet_id is not None:
        sheet = (
            await session.execute(select(Sheet).where(Sheet.id == body.sheet_id))
        ).scalar_one_or_none()
        if sheet is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
        if sheet.status != "constructing":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"项目不在施工阶段（当前：{sheet.status}）",
            )
    result = await construction_repo.submit_report(
        session, reporter=reporter, body=body
    )
    await session.commit()
    return result


@router.get("/active-sheets", response_model=ActiveSheetsResult)
async def get_active_sheets(
    _player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> ActiveSheetsResult:
    """当前 constructing 项目列表 + 启发式归因可用性（追踪器启动/定期查）。"""
    return await construction_repo.list_active_for_attribution(session)


# ===========================================================================
# admin 设置
# ===========================================================================

@router.get("/settings", response_model=ConstructionSettings)
async def get_settings(
    _admin: Player = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> ConstructionSettings:
    return await construction_repo.get_settings_snapshot(session)


@router.patch("/settings", response_model=ConstructionSettings)
async def patch_settings(
    body: ConstructionSettingsUpdate,
    _admin: Player = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> ConstructionSettings:
    result = await construction_repo.update_settings(session, body)
    await session.commit()
    return result


# ===========================================================================
# admin 白名单
# ===========================================================================

@router.get("/mod-sources", response_model=list[ServerModSourceEntry])
async def list_mod_sources(
    _admin: Player = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> list[ServerModSourceEntry]:
    rows = await construction_repo.list_server_mod_sources(session)
    return [
        ServerModSourceEntry(
            name=r.name,
            approved_by_uuid=r.approved_by_uuid,
            approved_at=r.approved_at,
            notes=r.notes,
        )
        for r in rows
    ]


@router.post(
    "/mod-sources", response_model=ServerModSourceEntry, status_code=status.HTTP_201_CREATED
)
async def create_mod_source(
    body: ServerModSourceCreate,
    admin: Player = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> ServerModSourceEntry:
    row = await construction_repo.create_server_mod_source(
        session, name=body.name, approved_by_uuid=admin.uuid, notes=body.notes
    )
    await session.commit()
    return ServerModSourceEntry(
        name=row.name,
        approved_by_uuid=row.approved_by_uuid,
        approved_at=row.approved_at,
        notes=row.notes,
    )


@router.delete("/mod-sources/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mod_source(
    name: str,
    _admin: Player = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> None:
    deleted = await construction_repo.delete_server_mod_source(session, name)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "source not found")
    await session.commit()


# ===========================================================================
# 切源（D9）
# ===========================================================================

@router.post("/source/switch-server", response_model=SourceState)
async def switch_server_source(
    body: SourceSwitchServerRequest,
    _admin: Player = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> SourceState:
    """admin 切某玩家服务端源（mcdr→official / server_mod→白名单内 source_id）。"""
    if body.source_type == "server_mod":
        if not body.source_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "server_mod 须提供 source_id",
            )
        if not await construction_repo.is_whitelisted_mod(session, body.source_id):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"server mod '{body.source_id}' 不在白名单",
            )
    player = await player_repo.get_by_uuid(session, body.player_uuid)
    if player is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "player not found")
    state = await construction_repo.switch_server_source(
        session,
        player_uuid=body.player_uuid,
        source_type=body.source_type,
        source_id=body.source_id,
        reason=body.reason,
    )
    await session.commit()
    return state


@router.post("/source/switch-self", response_model=SourceState)
async def switch_self_source(
    body: SourceSwitchSelfRequest,
    player_uuid: uuid.UUID = Depends(get_active_uuid),
    session: AsyncSession = Depends(get_session),
) -> SourceState:
    """玩家切自己的上报模式（server=退回官方代报 / local=客户端 mod）。"""
    if body.mode == "local":
        if not body.source_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "local 模式须提供 source_id（mod_id）",
            )
        snapshot = await construction_repo.get_settings_snapshot(session)
        if not snapshot.allow_client_mods:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "客户端模组上报已被服主关闭",
            )
    state = await construction_repo.switch_self_source(
        session,
        player_uuid=player_uuid,
        mode=body.mode,
        source_id=body.source_id,
        reason=body.reason,
    )
    await session.commit()
    return state


@router.get("/source/me", response_model=SourceMeResult)
async def get_source_me(
    player_uuid: uuid.UUID = Depends(get_active_uuid),
    session: AsyncSession = Depends(get_session),
) -> SourceMeResult:
    return await construction_repo.get_source_me(session, player_uuid)


# ===========================================================================
# 进度查询（放最后，避免 {sheet_id} 与上方字面路由歧义）
# ===========================================================================

@router.get("/{sheet_id}/progress", response_model=ConstructionProgress)
async def get_construction_progress(
    sheet_id: int,
    _player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> ConstructionProgress:
    exists = (
        await session.execute(select(Sheet.id).where(Sheet.id == sheet_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    return await construction_repo.get_progress(session, sheet_id)
