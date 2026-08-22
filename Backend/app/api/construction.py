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

权限（R-9/RS-2）：admin 端点挂 ``require_role("admin")``（account 级 JWT，
仅 Bearer、admin ≠ service-token、无绑定玩家的托管账号可用，issue #74；
后端真实拒绝 403）；report 走专用 ``get_construction_reporter``；
``/{sheet_id}/progress`` 走 ``get_current_viewer``（player-less 托管账号可浏览）；
其余任意登录玩家可读。
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ViewerIdentity,
    get_active_uuid,
    get_active_uuid_optional,
    get_construction_reporter,
    get_current_player,
    get_current_viewer,
    require_role,
    require_service_token,
)
from app.core.db import get_session
from app.models.sheet import Sheet
from app.models.user import Player, WebAccount
from app.repositories import construction_repo, player_repo
from app.schemas.construction import (
    ActiveByUuidsRequest,
    ActiveByUuidsResult,
    ActiveSheetsResult,
    ConstructionProgress,
    ConstructionSettings,
    ConstructionSettingsUpdate,
    JoinRequest,
    MyConstructionResult,
    MyReportHistoryItem,
    ParticipantState,
    PlacementReport,
    PlacementReportResult,
    ReportEventItem,
    ServerModSourceCreate,
    ServerModSourceEntry,
    ServerModSourceToggle,
    SourceMeResult,
    SourceState,
    SourceSwitchSelfRequest,
    SourceSwitchServerRequest,
    SwitchRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/construction", tags=["construction"])


# ===========================================================================
# 上报 + 归因查询
# ===========================================================================

@router.post("/report", response_model=PlacementReportResult, summary="方块净放置批量上报（双通道鉴权，严格单源）")
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


@router.get("/active-sheets", response_model=ActiveSheetsResult, summary="上报归属查询（活跃施工表）")
async def get_active_sheets(
    _player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> ActiveSheetsResult:
    """当前 constructing 项目列表 + 启发式归因可用性（追踪器启动/定期查）。"""
    return await construction_repo.list_active_for_attribution(session)


# ===========================================================================
# admin 设置
# ===========================================================================

@router.get("/settings", response_model=ConstructionSettings, summary="施工设置读取（admin）")
async def get_settings(
    _admin: WebAccount = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> ConstructionSettings:
    return await construction_repo.get_settings_snapshot(session)


@router.patch("/settings", response_model=ConstructionSettings, summary="施工设置更新（admin）")
async def patch_settings(
    body: ConstructionSettingsUpdate,
    _admin: WebAccount = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> ConstructionSettings:
    result = await construction_repo.update_settings(session, body)
    await session.commit()
    return result


# ===========================================================================
# admin 白名单
# ===========================================================================

@router.get("/mod-sources", response_model=list[ServerModSourceEntry], summary="服务端 mod 源白名单列表（admin）")
async def list_mod_sources(
    _admin: WebAccount = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> list[ServerModSourceEntry]:
    rows = await construction_repo.list_server_mod_sources(session)
    return [
        ServerModSourceEntry(
            name=r.name,
            enabled=r.enabled,
            approved_by_uuid=r.approved_by_uuid,
            approved_at=r.approved_at,
            notes=r.notes,
        )
        for r in rows
    ]


@router.post(
    "/mod-sources",
    response_model=ServerModSourceEntry,
    status_code=status.HTTP_201_CREATED,
    summary="新增服务端 mod 源（admin）",
)
async def create_mod_source(
    body: ServerModSourceCreate,
    _admin: WebAccount = Depends(require_role("admin")),
    approver_uuid: uuid.UUID | None = Depends(get_active_uuid_optional),
    session: AsyncSession = Depends(get_session),
) -> ServerModSourceEntry:
    # 审批人 = 会话来源玩家 UUID；player-less 托管账号（admin 面板）→ None
    # （列本就可空，审计另有 jwt-account 请求日志）。M1 复验：
    # get_active_uuid_optional 只解码不校验归属，须确认 active_uuid 仍属
    # _admin 账号（防玩家迁到别的账号后旧 token 继续冒充审批人），不属则 None。
    if approver_uuid is not None:
        bound = (
            await session.execute(
                select(Player).where(
                    Player.uuid == approver_uuid,
                    Player.web_account_id == _admin.id,
                )
            )
        ).scalar_one_or_none()
        if bound is None:
            approver_uuid = None
    row = await construction_repo.create_server_mod_source(
        session, name=body.name, approved_by_uuid=approver_uuid, notes=body.notes
    )
    await session.commit()
    return ServerModSourceEntry(
        name=row.name,
        enabled=row.enabled,
        approved_by_uuid=row.approved_by_uuid,
        approved_at=row.approved_at,
        notes=row.notes,
    )


@router.patch("/mod-sources/{name}", response_model=ServerModSourceEntry, summary="启停服务端 mod 源（admin）")
async def toggle_mod_source(
    name: str,
    body: ServerModSourceToggle,
    _admin: WebAccount = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> ServerModSourceEntry:
    """逐源启停（迭代 3 卡片开关）。不存在 → 404。"""
    row = await construction_repo.set_server_mod_source_enabled(
        session, name=name, enabled=body.enabled
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "source not found")
    await session.commit()
    return ServerModSourceEntry(
        name=row.name,
        enabled=row.enabled,
        approved_by_uuid=row.approved_by_uuid,
        approved_at=row.approved_at,
        notes=row.notes,
    )


@router.delete("/mod-sources/{name}", status_code=status.HTTP_204_NO_CONTENT, summary="删除服务端 mod 源（admin）")
async def delete_mod_source(
    name: str,
    _admin: WebAccount = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> None:
    deleted = await construction_repo.delete_server_mod_source(session, name)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "source not found")
    await session.commit()


# ===========================================================================
# 切源（D9）
# ===========================================================================

@router.post("/source/switch-server", response_model=SourceState, summary="管理员切换玩家上报源（admin）")
async def switch_server_source(
    body: SourceSwitchServerRequest,
    _admin: WebAccount = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
) -> SourceState:
    """admin 切某玩家服务端源（mcdr→official / server_mod→白名单内 source_id）。"""
    if body.source_type == "server_mod":
        if not body.source_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "server_mod 须提供 source_id",
            )
        if not await construction_repo.is_server_mod_usable(session, body.source_id):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"server mod '{body.source_id}' 不在白名单或已停用",
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


@router.post("/source/switch-self", response_model=SourceState, summary="玩家自助切换上报源（JWT）")
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


@router.get("/source/me", response_model=SourceMeResult, summary="我的上报源状态（含休眠源）")
async def get_source_me(
    player_uuid: uuid.UUID = Depends(get_active_uuid),
    session: AsyncSession = Depends(get_session),
) -> SourceMeResult:
    return await construction_repo.get_source_me(session, player_uuid)


@router.get("/me/reports", response_model=list[MyReportHistoryItem], summary="我的上报记录")
async def get_my_reports(
    limit: int = Query(default=50, ge=1, le=200, description="最近 N 条上报事件"),
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> list[MyReportHistoryItem]:
    """玩家个人的上报历史（每次 report 成功 = 一条 ``placement_snapshot``）。

    归因锚 account（R-5）：未绑 Web 账号的玩家 403。展示用，非权威源。字面路由
    ``/me/reports`` 置于 ``/{sheet_id}/progress`` 之前注册，避免被路径参数吞掉。
    """
    if player.web_account_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "player not bound to web account")
    return await construction_repo.get_my_report_history(
        session, player.web_account_id, limit
    )


@router.get("/me/report-events", response_model=list[ReportEventItem], summary="我的上报事件流水（含 skip 原因）")
async def get_my_report_events(
    limit: int = Query(default=50, ge=1, le=200, description="最近 N 条上报事件流水"),
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> list[ReportEventItem]:
    """玩家个人的完整上报事件流水（accepted + 所有 skip 原因，迭代 5）。

    每次 ``POST /report`` 产出的 ``PlacementOutcome`` 逐条落 ``report_events`` ——
    让玩家看到「为什么我的上报被拒」。归因锚 account（R-5）：未绑 Web 账号 403。
    字面路由置于 ``/{sheet_id}/progress`` 之前注册，避免被路径参数吞掉。
    """
    if player.web_account_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "player not bound to web account")
    return await construction_repo.get_my_report_events(
        session, player.web_account_id, limit
    )


# ===========================================================================
# 加入施工（plan BLOCK 1，迁移 0021）
# 所有 ``/me/...`` 字面路由 + ``/active-by-uuids`` 必须在 ``/{sheet_id}/progress``
# （下方）之前注册，避免被 ``{sheet_id}`` 路径参数吞没（仿 ``/me/reports`` 前置）。
# ===========================================================================

@router.get("/me/construction", response_model=MyConstructionResult, summary="查询我的活跃施工加入")
async def get_my_construction(
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> MyConstructionResult:
    """查自己当前活跃加入的施工项目（Me.vue「当前施工项目」卡片）。

    字面路由前置（避免被 ``/{sheet_id}`` 吞掉）。未绑 Web 账号 / 未加入 → 空态。
    """
    if player.web_account_id is None:
        return MyConstructionResult(active=ParticipantState())
    got = await construction_repo.get_my_active_participant(
        session, player.web_account_id
    )
    if got is None:
        return MyConstructionResult(active=ParticipantState())
    participant, sheet = got
    return MyConstructionResult(
        active=ParticipantState(
            sheet_id=participant.sheet_id,
            sheet_title=sheet.title,
            joined_at=participant.joined_at,
            join_source=participant.join_source,
        )
    )


async def _assert_sheet_joinable(
    session: AsyncSession, sheet_id: int
) -> Sheet:
    """sheet 存在 + 非 archived（否则 404/409）。供 join/switch 共用。"""
    sheet = (
        await session.execute(select(Sheet).where(Sheet.id == sheet_id))
    ).scalar_one_or_none()
    if sheet is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    if sheet.status == "archived":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "项目已归档，只读",
        )
    return sheet


@router.post("/me/join", response_model=MyConstructionResult, summary="加入施工")
async def join_construction(
    body: JoinRequest,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> MyConstructionResult:
    """手动加入指定 sheet 的施工。

    - 未绑 Web 账号 → 403；sheet 不存在 → 404；archived → 409。
    - ``enforce_single_construction=True`` 且已活跃加入他 sheet → 409
      「已活跃加入项目 X，先退出或切换」（ParticipantConflict）。
    - 已活跃加入本 sheet → 幂等返回当前状态（不报错）。
    """
    if player.web_account_id is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "player not bound to web account"
        )
    await _assert_sheet_joinable(session, body.sheet_id)
    try:
        await construction_repo.join_construction(
            session, player.web_account_id, body.sheet_id, source="manual"
        )
    except construction_repo.ParticipantConflict as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"已活跃加入项目 id={exc.current_sheet_id}，先退出或切换",
        )
    await session.commit()
    # 重查拿到 sheet_title（join_construction 返回 Participant 但未 join sheet.title）
    got = await construction_repo.get_my_active_participant(
        session, player.web_account_id
    )
    if got is None:
        return MyConstructionResult(active=ParticipantState())
    participant, active_sheet = got
    return MyConstructionResult(
        active=ParticipantState(
            sheet_id=participant.sheet_id,
            sheet_title=active_sheet.title,
            joined_at=participant.joined_at,
            join_source=participant.join_source,
        )
    )


@router.post("/me/switch", response_model=MyConstructionResult, summary="切换施工项目")
async def switch_construction(
    body: SwitchRequest,
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> MyConstructionResult:
    """切换到指定 sheet（leave 旧活跃 + join 新 sheet 同事务）。

    与 ``/me/join`` 的区别：``enforce_single_construction=True`` 且已活跃加入他 sheet
    时，``/me/join`` 抛 409，``/me/switch`` 自动切换（enforce=False 时两者等价）。
    实现统一走显式 leave + join：先 ``leave_construction`` 当前（如存在），再
    ``join_construction`` 目标，绕开 enforce=True 的 ParticipantConflict。
    """
    if player.web_account_id is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "player not bound to web account"
        )
    await _assert_sheet_joinable(session, body.sheet_id)
    await construction_repo.leave_construction(
        session, player.web_account_id, reason="switched"
    )
    # 与 /me/join 一致：并发场景（leave→join 之间他人插入活跃行）join 仍可能抛
    # ParticipantConflict → 409。未到 commit 即抛 → 整事务回滚，leave 一并撤销（原子）。
    try:
        await construction_repo.join_construction(
            session, player.web_account_id, body.sheet_id, source="manual"
        )
    except construction_repo.ParticipantConflict as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"已活跃加入项目 id={exc.current_sheet_id}，先退出或切换",
        )
    await session.commit()
    got = await construction_repo.get_my_active_participant(
        session, player.web_account_id
    )
    if got is None:
        return MyConstructionResult(active=ParticipantState())
    participant, active_sheet = got
    return MyConstructionResult(
        active=ParticipantState(
            sheet_id=participant.sheet_id,
            sheet_title=active_sheet.title,
            joined_at=participant.joined_at,
            join_source=participant.join_source,
        )
    )


@router.post("/me/leave", response_model=MyConstructionResult, summary="退出施工")
async def leave_construction(
    player: Player = Depends(get_current_player),
    session: AsyncSession = Depends(get_session),
) -> MyConstructionResult:
    """退出当前活跃加入（未活跃加入 → 幂等返回空态）。"""
    if player.web_account_id is None:
        return MyConstructionResult(active=ParticipantState())
    await construction_repo.leave_construction(
        session, player.web_account_id, reason="manual_leave"
    )
    await session.commit()
    return MyConstructionResult(active=ParticipantState())


@router.post("/active-by-uuids", response_model=ActiveByUuidsResult, summary="批量 UUID→sheet_id（tracker 按玩家路由）")
async def get_active_by_uuids(
    body: ActiveByUuidsRequest,
    _ok: None = Depends(require_service_token),
    session: AsyncSession = Depends(get_session),
) -> ActiveByUuidsResult:
    """批量 UUID → 当前活跃 sheet_id（tracker 按玩家路由用，service-token 单头）。

    非敏感数据（仅 sheet_id，无 account 信息）。未绑账号 / 未加入任何项目 → None。
    """
    mappings = await construction_repo.lookup_active_by_uuids(session, body.player_uuids)
    return ActiveByUuidsResult(mappings=mappings)


# ===========================================================================
# 进度查询（放最后，避免 {sheet_id} 与上方字面路由歧义）
# ===========================================================================

@router.get("/{sheet_id}/progress", response_model=ConstructionProgress, summary="施工进度（材料完成度 + 时间线）")
async def get_construction_progress(
    sheet_id: int,
    _viewer: ViewerIdentity = Depends(get_current_viewer),
    session: AsyncSession = Depends(get_session),
) -> ConstructionProgress:
    exists = (
        await session.execute(select(Sheet.id).where(Sheet.id == sheet_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sheet not found")
    return await construction_repo.get_progress(session, sheet_id)
