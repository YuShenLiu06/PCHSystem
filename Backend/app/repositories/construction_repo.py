"""施工进度上报层 repository（construction schema）。

所有 DB 读写集中于此（R-1）。核心函数：

- :func:`submit_report`：上报主流程（归因 + 严格单源 + 批量预取 + upsert 聚合）。
- :func:`list_active_for_attribution`：归因查询（启发式：恰 1 个 constructing）。
- :func:`get_progress` / :func:`aggregate_placement_totals` /
  :func:`get_placement_breakdown`：进度展示 + 归档/结算消费读契约（D8）。
- :func:`get_settings_snapshot` / :func:`update_settings`：运行时开关。
- :func:`list_server_mod_sources` / :func:`create_server_mod_source` /
  :func:`delete_server_mod_source`：服务端 mod 白名单。
- :func:`switch_server_source` / :func:`switch_self_source` /
  :func:`get_source_me`：显式切源（D9）。

设计要点见 [`Docs/architecture/api/construction.md`] 与
[`Docs/architecture/flows/construction-progress.md`]。
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import bindparam, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.construction import (
    Participant,
    PlacementRecord,
    PlacementSnapshot,
    PlayerSource,
    PlayerSourceHistory,
    ReportEvent,
    ServerModSource,
)
from app.models.sheet import Sheet, SheetRow
from app.models.system import SystemSetting
from app.models.user import Player
from app.repositories import web_account_repo
from app.schemas.construction import (
    ActiveSheet,
    ActiveSheetsResult,
    ConstructionProgress,
    ConstructionSettings,
    ConstructionSettingsUpdate,
    DormantSource,
    PlacementOutcome,
    PlacementReport,
    PlacementReportResult,
    PlacementTotal,
    ProgressAccountTotal,
    ProgressBreakdownItem,
    ProgressMaterialItem,
    ProgressTimelinePoint,
    MyReportHistoryItem,
    ReportEventItem,
    SourceHistoryEntry,
    SourceMeResult,
    SourceState,
)

if TYPE_CHECKING:
    from app.api.deps import ReporterIdentity

logger = logging.getLogger(__name__)

# 默认活跃源（玩家无 player_sources 记录时的逻辑占位，仅当 official_tracker_enabled）
OFFICIAL_SOURCE_TYPE = "mcdr"
OFFICIAL_SOURCE_ID = "official"

# construction.* 设置键 ↔ schema 字段名映射
_SETTING_KEYS: dict[str, str] = {
    "allow_client_mods": "construction.allow_client_mods",
    "official_tracker_enabled": "construction.official_tracker_enabled",
    "allow_server_mods": "construction.allow_server_mods",
    "report_interval_seconds": "construction.report_interval_seconds",
    "anti_cheat_threshold": "construction.anti_cheat_threshold",
    "enforce_single_construction": "construction.enforce_single_construction",
}


# ===========================================================================
# 设置（system.settings construction.*）
# ===========================================================================

def _config_defaults() -> dict[str, object]:
    s = get_settings()
    return {
        "allow_client_mods": s.construction_allow_client_mods,
        "official_tracker_enabled": s.construction_official_tracker_enabled,
        "allow_server_mods": s.construction_allow_server_mods,
        "report_interval_seconds": s.construction_report_interval_seconds,
        "anti_cheat_threshold": s.construction_anti_cheat_threshold,
        "enforce_single_construction": s.construction_enforce_single_construction,
    }


async def get_settings_snapshot(session: AsyncSession) -> ConstructionSettings:
    """读 construction.* 设置：DB 值优先，缺失键回退 ``config.py`` 默认。"""
    rows = (
        await session.execute(
            select(SystemSetting).where(SystemSetting.key.like("construction.%"))
        )
    ).scalars().all()
    db_values = {r.key: r.value for r in rows}
    defaults = _config_defaults()

    def val(field: str) -> object:
        return db_values.get(_SETTING_KEYS[field], defaults[field])

    return ConstructionSettings(
        allow_client_mods=bool(val("allow_client_mods")),
        official_tracker_enabled=bool(val("official_tracker_enabled")),
        allow_server_mods=bool(val("allow_server_mods")),
        report_interval_seconds=int(val("report_interval_seconds")),
        anti_cheat_threshold=val("anti_cheat_threshold"),  # None 或 int
        enforce_single_construction=bool(val("enforce_single_construction")),
    )


async def update_settings(
    session: AsyncSession, patch: ConstructionSettingsUpdate
) -> ConstructionSettings:
    """部分更新（``exclude_unset=True`` 仅写客户端实际提供的键）。"""
    data = patch.model_dump(exclude_unset=True)
    for field, value in data.items():
        key = _SETTING_KEYS[field]
        await session.execute(
            pg_insert(SystemSetting)
            .values(key=key, value=value)
            .on_conflict_do_update(
                index_elements=["key"],
                set_={"value": value, "updated_at": func.now()},
            )
        )
    await session.flush()
    return await get_settings_snapshot(session)


# ===========================================================================
# 归因查询（GET /v1/construction/active-sheets）
# ===========================================================================

async def list_active_for_attribution(session: AsyncSession) -> ActiveSheetsResult:
    """当前 constructing 项目列表 + 启发式归因可用性（恰 1 个 → True）。"""
    rows = (
        await session.execute(
            select(Sheet.id, Sheet.title)
            .where(Sheet.status == "constructing")
            .order_by(Sheet.id)
        )
    ).all()
    sheets = [ActiveSheet(id=r.id, title=r.title) for r in rows]
    return ActiveSheetsResult(sheets=sheets, heuristic_eligible=len(sheets) == 1)


# ===========================================================================
# 进度查询 + 归档/结算读契约（D8）
# ===========================================================================

async def aggregate_placement_totals(
    session: AsyncSession, sheet_id: int
) -> list[PlacementTotal]:
    """按 account 聚合净放置（BuildAScoreCalculator 的 ``placement_totals`` 源）。

    返回 ``[(account_id, display_name, net_qty)]``，scoring-settlement.md §4
    SettlementContext 消费形状。归档/结算时调用（post-archive, best-effort）。
    """
    rows = (
        await session.execute(
            select(
                PlacementRecord.account_id,
                func.sum(PlacementRecord.net_qty).label("net"),
            )
            .where(PlacementRecord.sheet_id == sheet_id)
            .group_by(PlacementRecord.account_id)
        )
    ).all()
    briefs = await web_account_repo.resolve_account_briefs(
        session, [r.account_id for r in rows]
    )
    return [
        PlacementTotal(
            account_id=r.account_id,
            display_name=briefs.get(r.account_id, (f"账号#{r.account_id}", []))[0],
            net_qty=int(r.net or 0),
        )
        for r in rows
    ]


async def get_placement_breakdown(
    session: AsyncSession, sheet_id: int
) -> list[ProgressBreakdownItem]:
    """按 account × registry 明细（进度展示 + 归档 md 复用）。"""
    rows = (
        await session.execute(
            select(
                PlacementRecord.account_id,
                PlacementRecord.registry_id,
                PlacementRecord.placed_qty,
                PlacementRecord.broken_qty,
                PlacementRecord.net_qty,
            )
            .where(PlacementRecord.sheet_id == sheet_id)
            .order_by(PlacementRecord.account_id, PlacementRecord.net_qty.desc())
        )
    ).all()
    briefs = await web_account_repo.resolve_account_briefs(
        session, list({r.account_id for r in rows})
    )
    return [
        ProgressBreakdownItem(
            account_id=r.account_id,
            display_name=briefs.get(r.account_id, (f"账号#{r.account_id}", []))[0],
            registry_id=r.registry_id,
            placed_qty=r.placed_qty,
            broken_qty=r.broken_qty,
            net_qty=r.net_qty,
        )
        for r in rows
    ]


async def get_material_completion(
    session: AsyncSession, sheet_id: int
) -> list[ProgressMaterialItem]:
    """材料完成度：按 registry 聚合 need（sheet_rows）vs net（placement_records）。

    两步查询避免 LEFT JOIN 笛卡尔放大 need（一 registry 多 account 行）：
    sheet_rows 按 registry 聚合 need + placement 按 registry 聚合 net，内存 join。
    ``completion_pct`` 视觉封顶 100%（``need=0`` → None）。
    """
    sr_rows = (
        await session.execute(
            select(
                SheetRow.registry_id,
                func.max(SheetRow.item_name).label("item_name"),
                func.sum(SheetRow.need_qty).label("need"),
            )
            .where(SheetRow.sheet_id == sheet_id, SheetRow.registry_id.is_not(None))
            .group_by(SheetRow.registry_id)
        )
    ).all()
    need_map = {r.registry_id: (r.item_name, int(r.need or 0)) for r in sr_rows}

    pr_rows = (
        await session.execute(
            select(
                PlacementRecord.registry_id,
                func.sum(PlacementRecord.net_qty).label("net"),
            )
            .where(PlacementRecord.sheet_id == sheet_id)
            .group_by(PlacementRecord.registry_id)
        )
    ).all()
    net_map = {r.registry_id: int(r.net or 0) for r in pr_rows}

    result: list[ProgressMaterialItem] = []
    for rid, (name, need) in need_map.items():
        net = net_map.get(rid, 0)
        if need > 0:
            ratio = min(max(net / need, 0), 1)
            pct = round(ratio * 100, 1)
        else:
            pct = None
        result.append(
            ProgressMaterialItem(
                registry_id=rid, item_name=name, need_qty=need,
                net_qty=net, completion_pct=pct,
            )
        )
    return result


async def get_placement_timeline(
    session: AsyncSession, sheet_id: int, limit: int = 200
) -> list[ProgressTimelinePoint]:
    """时序快照（折线图数据源）：取**最近** ``limit`` 点后转升序返回。

    取最近而非最早——活跃项目快照持续增长，取最早会把近期活动截掉；
    配合前端前向填充（inactive 时段水平保持）显示最新活动窗口。前端按
    account_id 拆线 + 前向填充。MVP 不做采样，超量后续优化。
    """
    rows = (
        await session.execute(
            select(
                PlacementSnapshot.account_id,
                PlacementSnapshot.total_net,
                PlacementSnapshot.recorded_at,
            )
            .where(PlacementSnapshot.sheet_id == sheet_id)
            .order_by(PlacementSnapshot.recorded_at.desc())
            .limit(limit)
        )
    ).all()
    rows = list(reversed(rows))
    return [
        ProgressTimelinePoint(
            account_id=r.account_id, total_net=r.total_net, recorded_at=r.recorded_at,
        )
        for r in rows
    ]


async def get_my_report_history(
    session: AsyncSession, account_id: int, limit: int = 50
) -> list[MyReportHistoryItem]:
    """玩家个人的上报事件历史（``placement_snapshots``：每次 report 成功落一条）。

    取最近 ``limit`` 条（跨所有项目），按时间倒序。``delta`` = 本次相对同项目上一条
    快照的净增量（列表已倒序，下一项更旧；同 sheet 才算；最旧/首条 None）。展示用，
    非权威源（snapshot 写入 best-effort）。
    """
    limit = max(1, min(int(limit), 200))
    rows = (
        await session.execute(
            select(
                PlacementSnapshot.recorded_at,
                PlacementSnapshot.sheet_id,
                PlacementSnapshot.total_net,
                Sheet.title.label("sheet_title"),
            )
            .join(Sheet, Sheet.id == PlacementSnapshot.sheet_id)
            .where(PlacementSnapshot.account_id == account_id)
            .order_by(PlacementSnapshot.recorded_at.desc())
            .limit(limit)
        )
    ).all()
    items: list[MyReportHistoryItem] = []
    for i, r in enumerate(rows):
        older = rows[i + 1] if i + 1 < len(rows) else None
        delta = (
            r.total_net - older.total_net
            if older is not None and older.sheet_id == r.sheet_id
            else None
        )
        items.append(
            MyReportHistoryItem(
                recorded_at=r.recorded_at,
                sheet_id=r.sheet_id,
                sheet_title=r.sheet_title or "",
                total_net=r.total_net,
                delta=delta,
            )
        )
    return items


async def get_my_report_events(
    session: AsyncSession, account_id: int, limit: int = 50
) -> list[ReportEventItem]:
    """玩家个人的完整上报事件流水（``report_events``：accepted + 所有 skip 原因）。

    取最近 ``limit`` 条（跨所有项目），按时间倒序。``sheet_title`` 经 LEFT JOIN
    （``sheet_id`` 可能为 None——归因失败 / 客户端 mod 全局关闭场景）。展示用，非权威源
    （事件写入 best-effort，权威仍是 ``placement_records`` 聚合）。
    """
    limit = max(1, min(int(limit), 200))
    rows = (
        await session.execute(
            select(
                ReportEvent.recorded_at,
                ReportEvent.sheet_id,
                ReportEvent.registry_id,
                ReportEvent.action,
                ReportEvent.reason,
                ReportEvent.net_delta,
                Sheet.title.label("sheet_title"),
            )
            .outerjoin(Sheet, Sheet.id == ReportEvent.sheet_id)
            .where(ReportEvent.account_id == account_id)
            .order_by(ReportEvent.recorded_at.desc(), ReportEvent.id.desc())
            .limit(limit)
        )
    ).all()
    return [
        ReportEventItem(
            recorded_at=r.recorded_at,
            sheet_id=r.sheet_id,
            sheet_title=r.sheet_title,
            registry_id=r.registry_id,
            action=r.action,
            reason=r.reason,
            net_delta=r.net_delta,
        )
        for r in rows
    ]


async def get_progress(session: AsyncSession, sheet_id: int) -> ConstructionProgress:
    """进度端点响应：account 聚合 + 明细 + 材料完成度 + 时序（迭代 2 扩展）。"""
    total_rows = (
        await session.execute(
            select(
                PlacementRecord.account_id,
                func.sum(PlacementRecord.placed_qty).label("placed"),
                func.sum(PlacementRecord.broken_qty).label("broken"),
                func.sum(PlacementRecord.net_qty).label("net"),
            )
            .where(PlacementRecord.sheet_id == sheet_id)
            .group_by(PlacementRecord.account_id)
            .order_by(func.sum(PlacementRecord.net_qty).desc())
        )
    ).all()
    briefs = await web_account_repo.resolve_account_briefs(
        session, [r.account_id for r in total_rows]
    )
    account_totals = [
        ProgressAccountTotal(
            account_id=r.account_id,
            display_name=briefs.get(r.account_id, (f"账号#{r.account_id}", []))[0],
            placed_qty=int(r.placed or 0),
            broken_qty=int(r.broken or 0),
            net_qty=int(r.net or 0),
        )
        for r in total_rows
    ]
    breakdown = await get_placement_breakdown(session, sheet_id)
    material_completion = await get_material_completion(session, sheet_id)
    timeline = await get_placement_timeline(session, sheet_id)
    # 迁移 0020：读 sheet 两时间字段，供前端图表 xAxis 范围（左：施工开始，右：归档/当前）。
    sheet_row = (
        await session.execute(
            select(Sheet.constructing_at, Sheet.archived_at).where(Sheet.id == sheet_id)
        )
    ).first()
    return ConstructionProgress(
        sheet_id=sheet_id,
        account_totals=account_totals,
        breakdown=breakdown,
        material_completion=material_completion,
        timeline=timeline,
        construction_started_at=sheet_row.constructing_at if sheet_row else None,
        archived_at=sheet_row.archived_at if sheet_row else None,
    )


# ===========================================================================
# 上报（POST /v1/construction/report）
# ===========================================================================

async def _upsert_placement(
    session: AsyncSession,
    sheet_id: int,
    account_id: int,
    registry_id: str,
    placed: int,
    broken: int,
) -> None:
    """按 (sheet, account, registry) 聚合 upsert：净量累加。"""
    net = placed - broken
    await session.execute(
        pg_insert(PlacementRecord)
        .values(
            sheet_id=sheet_id,
            account_id=account_id,
            registry_id=registry_id,
            placed_qty=placed,
            broken_qty=broken,
            net_qty=net,
        )
        .on_conflict_do_update(
            index_elements=["sheet_id", "account_id", "registry_id"],
            set_={
                "placed_qty": PlacementRecord.placed_qty + placed,
                "broken_qty": PlacementRecord.broken_qty + broken,
                "net_qty": PlacementRecord.net_qty + net,
                "updated_at": func.now(),
            },
        )
    )


async def _flush_report_events(
    session: AsyncSession,
    outcomes: list[PlacementOutcome],
    players_map: dict[uuid.UUID, Player],
    sheet_id_resolved: int | None,
) -> None:
    """批量写 ``construction.report_events``（迭代 5：玩家可见事件流水）。

    策略：对 ``outcomes`` 里**已绑定 Web 账号**的玩家逐条落一行
    （``accepted`` + 所有 ``skipped`` reason）。未绑账号 / 玩家不存在 → 不落
    （``account_id`` 是 ``/me/report-events`` 查询锚，无 account 无法查询）。

    - ``sheet_id`` 列允许 null：归因失败 / 客户端 mod 全局关闭场景照落（让玩家看到
      「本次上报因未归因被整体跳过」）。
    - best-effort：失败仅日志不阻断上报（与 ``placement_snapshots`` 同哲学）。
    - ``PlacementOutcome.net_delta`` 对 skipped「无活跃源 / 其他源 / 不在清单」分支
      默认为 0；对封顶 skipped 分支为「尝试量」（over 部分或全部 delta）。照搬即可。
    """
    events: list[ReportEvent] = []
    for oc in outcomes:
        player = players_map.get(oc.player_uuid)
        if player is None or player.web_account_id is None:
            continue
        events.append(
            ReportEvent(
                sheet_id=sheet_id_resolved,
                account_id=player.web_account_id,
                player_uuid=oc.player_uuid,
                registry_id=oc.registry_id,
                action=oc.action,
                reason=oc.reason,
                net_delta=oc.net_delta,
            )
        )
    if not events:
        return
    try:
        # SAVEPOINT 隔离：事件 INSERT 失败（FK / 约束 / 死锁）只回滚 savepoint，
        # 不污染外层事务——否则 session 进 PendingRollbackError，后续 commit 把整次
        # 上报回滚（含已落 placement_records），tracker 不推进 baseline → 增量堆积。
        async with session.begin_nested():
            session.add_all(events)
            await session.flush()
    except Exception:
        logger.warning(
            "report_events write failed sheet_id=%s events=%d",
            sheet_id_resolved, len(events), exc_info=True,
        )


async def submit_report(
    session: AsyncSession,
    *,
    reporter: "ReporterIdentity",
    body: PlacementReport,
) -> PlacementReportResult:
    """上报主流程：归因 → 方块清单校验 → 严格单源校验 → 批量 upsert → 时序快照。

    严格单源（D2/C-7）：上报方 ≠ 玩家当前活跃源 → 该玩家 entries 全 skip，
    **不隐式切源**。无 ``player_sources`` 记录时默认活跃 = (mcdr, official)
    （仅当 ``official_tracker_enabled``）。

    方块清单校验（迭代 2 需求 2）：``registry_id`` 必须在该 sheet 收集清单内
    （``sheet_rows.registry_id`` 集合，含子物品），否则 skip
    （reason `方块不在项目材料清单内`）。

    JWT 通道强制 ``player_uuid = reporter.active_uuid``（D1）。
    归因三分支（D3）：显式 sheet_id / 启发式恰 1 个 / 0 或 >1 全 skip。
    落库语义（D4）：``account_id = player.web_account_id``，未绑 → skip。

    时序快照（迭代 2 需求 4）：本轮 accepted 的 account 各写一条
    ``placement_snapshots``（best-effort，失败仅日志不阻断上报）。
    """
    settings = await get_settings_snapshot(session)

    # --- 1. 归因 ---
    if body.sheet_id is not None:
        # api 层已校验存在 + constructing（此处信任）
        sheet_id_resolved: int | None = body.sheet_id
        attribution_source = "explicit"
        no_attribution_reason: str | None = None
    else:
        active = await list_active_for_attribution(session)
        if active.heuristic_eligible:
            sheet_id_resolved = active.sheets[0].id
            attribution_source = "heuristic"
            no_attribution_reason = None
        else:
            sheet_id_resolved = None
            attribution_source = "none"
            no_attribution_reason = (
                "多个施工项目并发，须显式指定 sheet_id"
                if len(active.sheets) > 1
                else "当前无施工中项目"
            )

    # --- 2. JWT 通道全局开关 ---
    jwt_channel = reporter.channel == "jwt"
    client_mod_closed = jwt_channel and not settings.allow_client_mods

    # --- 3. 聚合 (player_uuid, registry_id) → (placed, broken)，保序去重 ---
    agg: dict[tuple[uuid.UUID, str], list[int]] = {}
    order: list[tuple[uuid.UUID, str]] = []
    for entry in body.placements:
        puuid = reporter.active_uuid if jwt_channel else entry.player_uuid
        key = (puuid, entry.registry_id)
        if key not in agg:
            agg[key] = [0, 0]
            order.append(key)
        agg[key][0] += entry.placed_qty
        agg[key][1] += entry.broken_qty

    outcomes: list[PlacementOutcome] = []
    counts_accepted = 0
    counts_skipped = 0

    # --- 3.7 批量预取 player（提前到 skip_all 之前：no_attribution / client_mod_closed
    # 场景仍需为 bound 玩家落 report_events，让玩家看到「本次上报因 X 被整体跳过」）---
    all_uuids = list({puuid for (puuid, _rid) in order})
    players_map: dict[uuid.UUID, Player] = {}
    if all_uuids:
        p_rows = (
            await session.execute(select(Player).where(Player.uuid.in_(all_uuids)))
        ).scalars().all()
        players_map = {p.uuid: p for p in p_rows}

    def skip_all(reason: str) -> PlacementReportResult:
        nonlocal counts_skipped
        for (puuid, rid) in order:
            outcomes.append(
                PlacementOutcome(
                    player_uuid=puuid, registry_id=rid,
                    action="skipped", reason=reason, net_delta=0,
                )
            )
            counts_skipped += 1
        return PlacementReportResult(
            sheet_id=sheet_id_resolved,
            attribution_source=attribution_source,
            totals={"accepted": 0, "skipped": counts_skipped},
            outcomes=outcomes,
        )

    # 无归因 → 全 skip
    if sheet_id_resolved is None:
        result = skip_all(no_attribution_reason or "无法归因")
        await _flush_report_events(
            session, outcomes, players_map, sheet_id_resolved
        )
        return result
    # 客户端 mod 全局关闭 → 全 skip
    if client_mod_closed:
        result = skip_all("客户端模组上报已被服主关闭")
        await _flush_report_events(
            session, outcomes, players_map, sheet_id_resolved
        )
        return result

    # --- 3.5 方块清单 + need_map 预取（迭代 2 需求 2 + 迭代 4 按材料封顶）---
    # need_map = registry_id → sum(need_qty)（含子物品：子行也有 registry_id，
    # 与 get_material_completion 同口径 GROUP BY registry_id SUM(need_qty) 自然包含）
    manifest_rows = (
        await session.execute(
            select(
                SheetRow.registry_id,
                func.sum(SheetRow.need_qty).label("need"),
            )
            .where(SheetRow.sheet_id == sheet_id_resolved)
            .where(SheetRow.registry_id.is_not(None))
            .group_by(SheetRow.registry_id)
        )
    ).all()
    allowed_set: set[str] = set()
    need_map: dict[str, int] = {}
    for r in manifest_rows:
        allowed_set.add(r.registry_id)
        need_map[r.registry_id] = int(r.need or 0)

    # --- 3.6 当前跨账号合计净放置（按材料封顶用）---
    # 封顶粒度 = (sheet_id, registry_id) 跨全部账号合计；逐条循环同批次共享容量
    material_total_rows = (
        await session.execute(
            select(
                PlacementRecord.registry_id,
                func.sum(PlacementRecord.net_qty).label("net"),
            )
            .where(PlacementRecord.sheet_id == sheet_id_resolved)
            .group_by(PlacementRecord.registry_id)
        )
    ).all()
    material_totals: dict[str, int] = {
        r.registry_id: int(r.net or 0) for r in material_total_rows
    }

    # --- 4. 批量预取 active source（避免 N+1；players_map 已在 3.7 取得）---
    active_map: dict[uuid.UUID, tuple[str | None, str | None]] = {}
    if all_uuids:
        s_rows = (
            await session.execute(
                select(
                    PlayerSource.player_uuid,
                    PlayerSource.source_type,
                    PlayerSource.source_id,
                )
                .where(PlayerSource.player_uuid.in_(all_uuids))
                .where(PlayerSource.disabled_at.is_(None))
            )
        ).all()
        active_map = {r.player_uuid: (r.source_type, r.source_id) for r in s_rows}

    # --- 5. 逐条处理 ---
    accepted_account_ids: set[int] = set()
    for (puuid, rid) in order:
        placed, broken = agg[(puuid, rid)]
        player = players_map.get(puuid)
        if player is None:
            outcomes.append(
                PlacementOutcome(player_uuid=puuid, registry_id=rid, action="skipped", reason="玩家不存在")
            )
            counts_skipped += 1
            continue
        if player.web_account_id is None:
            outcomes.append(
                PlacementOutcome(player_uuid=puuid, registry_id=rid, action="skipped", reason="玩家未绑 Web 账号")
            )
            counts_skipped += 1
            continue
        # 方块清单校验（迭代 2 需求 2）
        if rid not in allowed_set:
            outcomes.append(
                PlacementOutcome(
                    player_uuid=puuid, registry_id=rid,
                    action="skipped", reason="方块不在项目材料清单内",
                )
            )
            counts_skipped += 1
            continue
        # 严格单源
        src_type, src_id = active_map.get(puuid, (None, None))
        if src_type is None and settings.official_tracker_enabled:
            src_type, src_id = OFFICIAL_SOURCE_TYPE, OFFICIAL_SOURCE_ID
        if src_type is None:
            skip_reason = "玩家当前无活跃上报源"
        elif src_type != reporter.source_type or src_id != reporter.source_id:
            skip_reason = "玩家当前由其他源上报"
        else:
            skip_reason = None
        if skip_reason is not None:
            outcomes.append(
                PlacementOutcome(
                    player_uuid=puuid, registry_id=rid,
                    action="skipped", reason=skip_reason,
                )
            )
            counts_skipped += 1
            continue

        # 按材料封顶（迭代 4 需求：跨账号合计 net 不得超过 sum(need_qty)）
        # - delta > 0（净放置）：accepted = min(delta, available)；满额时整条 skip
        # - delta <= 0（拆毁/中性）：照常接受，释放容量（material_totals 同步下调）
        # 同批次内逐条累加 material_totals → 后续条目共享已更新容量
        delta = placed - broken
        if delta > 0:
            available = max(need_map.get(rid, 0) - material_totals.get(rid, 0), 0)
            accepted_delta = min(delta, available)
            over = delta - accepted_delta
        else:
            accepted_delta = delta
            over = 0
        material_totals[rid] = material_totals.get(rid, 0) + accepted_delta

        if accepted_delta == 0 and delta > 0:
            # 整条 skip（已达材料上限，无任何接受量）
            outcomes.append(
                PlacementOutcome(
                    player_uuid=puuid, registry_id=rid,
                    action="skipped", reason="已达材料上限",
                    net_delta=delta,
                )
            )
            counts_skipped += 1
            continue

        # 落库 + accepted 回执
        # _upsert_placement 是累加 `+=`（placed_qty/broken_qty/net_qty 都 += 入参）。
        # 传 placed_eff = accepted_delta + broken、broken_eff = broken：
        #   new_net += (accepted_delta + broken) - broken = accepted_delta ✓
        # 拆毁时（delta<0）accepted_delta = delta，placed_eff = delta + broken = placed
        # → 与原行为完全一致（零回归）。
        placed_eff = accepted_delta + broken
        await _upsert_placement(
            session, sheet_id_resolved, player.web_account_id, rid, placed_eff, broken
        )
        accepted_account_ids.add(player.web_account_id)
        outcomes.append(
            PlacementOutcome(
                player_uuid=puuid, registry_id=rid,
                action="accepted", reason="", net_delta=accepted_delta,
            )
        )
        counts_accepted += 1
        if over > 0:
            # 部分接受 + 剩余被拒：额外 emit 一条 skipped 让玩家看到「这部分满额被拒」
            outcomes.append(
                PlacementOutcome(
                    player_uuid=puuid, registry_id=rid,
                    action="skipped", reason="已达材料上限",
                    net_delta=over,
                )
            )
            counts_skipped += 1

    await session.flush()

    # --- 6. 时序快照（迭代 2 需求 4，best-effort 不阻断上报）---
    if accepted_account_ids:
        try:
            # SAVEPOINT 隔离（同 _flush_report_events）：快照 INSERT 失败只回滚
            # savepoint，不污染外层事务致整次上报回滚。
            async with session.begin_nested():
                await session.execute(
                    text(
                        "INSERT INTO construction.placement_snapshots "
                        "(sheet_id, account_id, total_net, recorded_at) "
                        "SELECT :sid, account_id, sum(net_qty), now() "
                        "FROM construction.placement_records "
                        "WHERE sheet_id = :sid AND account_id IN :aids "
                        "GROUP BY account_id"
                    ).bindparams(bindparam("aids", expanding=True)),
                    {"sid": sheet_id_resolved, "aids": sorted(accepted_account_ids)},
                )
                await session.flush()
        except Exception:
            logger.warning(
                "placement_snapshots write failed sheet_id=%s accounts=%s",
                sheet_id_resolved, sorted(accepted_account_ids), exc_info=True,
            )

    # --- 7. 玩家可见事件流水（迭代 5：accepted + 所有 skip 原因逐条落库）---
    await _flush_report_events(session, outcomes, players_map, sheet_id_resolved)

    return PlacementReportResult(
        sheet_id=sheet_id_resolved,
        attribution_source=attribution_source,
        totals={"accepted": counts_accepted, "skipped": counts_skipped},
        outcomes=outcomes,
    )


# ===========================================================================
# 服务端 mod 白名单（admin CRUD）
# ===========================================================================

async def list_server_mod_sources(session: AsyncSession) -> list[ServerModSource]:
    rows = (
        await session.execute(
            select(ServerModSource).order_by(ServerModSource.name)
        )
    ).scalars().all()
    return list(rows)


async def is_server_mod_usable(session: AsyncSession, name: str) -> bool:
    """服务端 mod 是否可用：在白名单 **且** ``enabled=true``（迭代 3 逐源启停）。

    ``get_construction_reporter``（service-token + X-Source-Id 通道）与
    ``switch-server``（admin 分配 server_mod）消费——未启用视为不可用（403/422）。
    """
    found = (
        await session.execute(
            select(ServerModSource.name).where(
                ServerModSource.name == name,
                ServerModSource.enabled.is_(True),
            )
        )
    ).scalar_one_or_none()
    return found is not None


async def set_server_mod_source_enabled(
    session: AsyncSession, name: str, enabled: bool
) -> ServerModSource | None:
    """逐源启停（PATCH /mod-sources/{name}）；不存在返 None（api 层 404）。"""
    row = (
        await session.execute(
            select(ServerModSource).where(ServerModSource.name == name)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    row.enabled = enabled
    await session.flush()
    return row


async def create_server_mod_source(
    session: AsyncSession,
    *,
    name: str,
    approved_by_uuid: uuid.UUID | None,
    notes: str | None,
) -> ServerModSource:
    """幂等 upsert（PK=name）：重新审批 → touch approved_at/by + notes。"""
    await session.execute(
        pg_insert(ServerModSource)
        .values(name=name, approved_by_uuid=approved_by_uuid, notes=notes)
        .on_conflict_do_update(
            index_elements=["name"],
            set_={
                "approved_by_uuid": approved_by_uuid,
                "approved_at": func.now(),
                "notes": notes,
            },
        )
    )
    await session.flush()
    return (
        await session.execute(
            select(ServerModSource).where(ServerModSource.name == name)
        )
    ).scalar_one()


async def delete_server_mod_source(session: AsyncSession, name: str) -> bool:
    result = await session.execute(
        delete(ServerModSource).where(ServerModSource.name == name)
    )
    return (result.rowcount or 0) > 0


# ===========================================================================
# 切源（D9：switch-server / switch-self / source me）
# ===========================================================================

async def _disable_active_source(
    session: AsyncSession, player_uuid: uuid.UUID
) -> tuple[str | None, str | None]:
    """禁用当前活跃源并返回其 (type, id)（无活跃源返 (None, None)）。

    先 flush 确保 ``uq_player_sources_active`` 部分唯一索引在新 INSERT 前已解除冲突
    （SQLAlchemy flush 不保证同表 UPDATE 先于 INSERT，故显式分两次 flush）。
    """
    cur = (
        await session.execute(
            select(PlayerSource)
            .where(PlayerSource.player_uuid == player_uuid)
            .where(PlayerSource.disabled_at.is_(None))
        )
    ).scalar_one_or_none()
    if cur is None:
        return None, None
    cur.disabled_at = datetime.now(timezone.utc)
    await session.flush()
    return cur.source_type, cur.source_id


async def _switch_source(
    session: AsyncSession,
    player_uuid: uuid.UUID,
    to_type: str,
    to_id: str | None,
    reason: str | None,
) -> None:
    """核心切源：disable 旧活跃 → 插新活跃 → 写 history（append-only）。

    已是目标源则 no-op（避免噪声 history）。
    """
    cur = (
        await session.execute(
            select(PlayerSource)
            .where(PlayerSource.player_uuid == player_uuid)
            .where(PlayerSource.disabled_at.is_(None))
        )
    ).scalar_one_or_none()
    from_type = cur.source_type if cur is not None else None
    from_id = cur.source_id if cur is not None else None
    # 已是目标源 → no-op
    if from_type == to_type and from_id == to_id:
        return
    if cur is not None:
        cur.disabled_at = datetime.now(timezone.utc)
        await session.flush()  # 先解除部分唯一索引冲突
    session.add(
        PlayerSource(
            player_uuid=player_uuid, source_type=to_type, source_id=to_id
        )
    )
    session.add(
        PlayerSourceHistory(
            player_uuid=player_uuid,
            from_type=from_type,
            from_id=from_id,
            to_type=to_type,
            to_id=to_id,
            reason=reason,
        )
    )
    await session.flush()


async def get_source_state(
    session: AsyncSession,
    player_uuid: uuid.UUID,
    settings: ConstructionSettings,
) -> SourceState:
    """当前活跃源（无记录 → 默认 mcdr/official，``is_default=True``）。"""
    cur = (
        await session.execute(
            select(PlayerSource.source_type, PlayerSource.source_id)
            .where(PlayerSource.player_uuid == player_uuid)
            .where(PlayerSource.disabled_at.is_(None))
        )
    ).first()
    if cur is None:
        if settings.official_tracker_enabled:
            return SourceState(
                source_type=OFFICIAL_SOURCE_TYPE,
                source_id=OFFICIAL_SOURCE_ID,
                is_default=True,
            )
        return SourceState(source_type=None, source_id=None, is_default=True)
    return SourceState(
        source_type=cur.source_type, source_id=cur.source_id, is_default=False
    )


async def switch_server_source(
    session: AsyncSession,
    *,
    player_uuid: uuid.UUID,
    source_type: str,
    source_id: str | None,
    reason: str | None,
) -> SourceState:
    """admin 切某玩家服务端源（api 层已校验白名单；mcdr 强制 official）。"""
    to_id = OFFICIAL_SOURCE_ID if source_type == "mcdr" else source_id
    await _switch_source(session, player_uuid, source_type, to_id, reason)
    settings = await get_settings_snapshot(session)
    return await get_source_state(session, player_uuid, settings)


async def switch_self_source(
    session: AsyncSession,
    *,
    player_uuid: uuid.UUID,
    mode: str,
    source_id: str | None,
    reason: str | None,
) -> SourceState:
    """玩家切自己的上报模式（api 层校验 allow_client_mods + source_id 必填）。"""
    if mode == "server":
        to_type, to_id = OFFICIAL_SOURCE_TYPE, OFFICIAL_SOURCE_ID
    else:  # local
        to_type, to_id = "client_mod", source_id
    await _switch_source(session, player_uuid, to_type, to_id, reason)
    settings = await get_settings_snapshot(session)
    return await get_source_state(session, player_uuid, settings)


async def get_source_me(
    session: AsyncSession, player_uuid: uuid.UUID
) -> SourceMeResult:
    """玩家查活跃源 + 切换历史（最近 50 条）+ 休眠源列表（迭代 2 需求 1）。

    休眠源 = ``source_type='client_mod'`` 且 ``disabled_at IS NOT NULL`` 的历史
    ``player_sources``，按 ``source_id`` 去重取最近 ``activated_at``。严格单源
    不变——休眠源仅供前端「快速切回历史 mod_id」展示，不参与 report 单源校验。
    """
    settings = await get_settings_snapshot(session)
    active = await get_source_state(session, player_uuid, settings)
    history_rows = (
        await session.execute(
            select(PlayerSourceHistory)
            .where(PlayerSourceHistory.player_uuid == player_uuid)
            .order_by(PlayerSourceHistory.switched_at.desc())
            .limit(50)
        )
    ).scalars().all()
    history = [
        SourceHistoryEntry(
            from_type=h.from_type,
            from_id=h.from_id,
            to_type=h.to_type,
            to_id=h.to_id,
            switched_at=h.switched_at,
            reason=h.reason,
        )
        for h in history_rows
    ]
    dormant_rows = (
        await session.execute(
            select(
                PlayerSource.source_id,
                func.max(PlayerSource.activated_at).label("last_active_at"),
            )
            .where(
                PlayerSource.player_uuid == player_uuid,
                PlayerSource.source_type == "client_mod",
                PlayerSource.disabled_at.is_not(None),
                PlayerSource.source_id.is_not(None),
            )
            .group_by(PlayerSource.source_id)
            .order_by(func.max(PlayerSource.activated_at).desc())
        )
    ).all()
    dormant = [
        DormantSource(source_id=r.source_id, last_active_at=r.last_active_at)
        for r in dormant_rows
    ]
    return SourceMeResult(active=active, history=history, dormant_sources=dormant)


# ===========================================================================
# 加入施工（plan BLOCK 1，迁移 0021）
# ===========================================================================

class ParticipantConflict(Exception):
    """manual join 冲突：玩家已活跃加入他 sheet 且 ``enforce_single_construction=True``。

    api 层翻译为 409「已活跃加入项目 X，先退出或切换」。
    """

    def __init__(self, current_sheet_id: int) -> None:
        super().__init__(f"already active in sheet {current_sheet_id}")
        self.current_sheet_id = current_sheet_id


async def _get_active_participant_row(
    session: AsyncSession, web_account_id: int
) -> Participant | None:
    """读该 account 当前活跃加入行（``left_at IS NULL``）。"""
    return (
        await session.execute(
            select(Participant)
            .where(
                Participant.web_account_id == web_account_id,
                Participant.left_at.is_(None),
            )
        )
    ).scalar_one_or_none()


# join_construction 并发兜底重试上限（IntegrityError 分支）：活跃行被并发插入后
# 又 CASCADE 删除的极端抖动时，重查 cur2 仍 None → 有界重试，防无限递归。
MAX_JOIN_RETRIES = 2


async def join_construction(
    session: AsyncSession,
    web_account_id: int,
    sheet_id: int,
    *,
    source: str,
    _depth: int = 0,
) -> Participant:
    """统一 join 入口（auto/manual 共用，差异在冲突处理）。

    - 用 ``begin_nested`` SAVEPOINT 隔离写操作，并发/冲突只回滚 savepoint 不污染主事务。
    - ``enforce_single_construction=True``（默认）:
      * 已活跃加入本 sheet → NOP 幂等返回（含 join_source 保留首次的 'manual'/'auto'）
      * 已活跃加入他 sheet + manual → raise ParticipantConflict（api 409）
      * 已活跃加入他 sheet + auto → silent skip（返回现有行）
    - ``enforce_single_construction=False``:
      * 已活跃加入他 sheet → 自动切换（旧行 left_at=now/left_reason='switched' 后插新行）
    - 并发兜底：捕获 ``uq_participants_active`` IntegrityError → rollback savepoint →
      重查降级（按 source 决定抛 ParticipantConflict 或 silent skip）。重查仍 None
      （被并发插入后又 CASCADE 删除的极端抖动）按 ``MAX_JOIN_RETRIES`` 有界重试，
      超限重抛 IntegrityError（上游 500），防无限递归。

    ``source`` 必须 'auto' 或 'manual'（DB CHECK 兜底）。``_depth`` 仅供内部重试计数。
    """
    settings = await get_settings_snapshot(session)
    cur = await _get_active_participant_row(session, web_account_id)
    if cur is not None:
        if cur.sheet_id == sheet_id:
            # 幂等：已活跃加入本 sheet，NOP（保留首行 join_source 不覆盖）
            return cur
        # 已活跃加入他 sheet
        if settings.enforce_single_construction:
            if source == "manual":
                raise ParticipantConflict(cur.sheet_id)
            # auto: silent skip（返回现有行）
            return cur
        # enforce=False：自动切换（落到下面 SAVEPOINT 逻辑）

    try:
        async with session.begin_nested():
            # enforce=False 自动切换：旧活跃行先置 left_at
            if cur is not None and not settings.enforce_single_construction:
                switched_at = datetime.now(timezone.utc)
                cur.left_at = switched_at
                cur.left_reason = "switched"
                cur.updated_at = switched_at  # model 无 onupdate，显式刷新（仿 PlacementRecord）
                await session.flush()  # 解除 uq_participants_active 冲突
            new_p = Participant(
                web_account_id=web_account_id,
                sheet_id=sheet_id,
                join_source=source,
            )
            session.add(new_p)
            await session.flush()
            return new_p
    except IntegrityError:
        # 并发：另一事务已先插入该 account 的活跃行（uq_participants_active 兜底）。
        # begin_nested 上下文管理器已自动 ROLLBACK TO SAVEPOINT，外层事务完好、
        # session 仍可用——**不可**调 session.rollback()（会回滚整个外层事务，
        # 例如 auto-join 嵌套在 contribute 事务内时会连带丢失玩家上交）。
        cur2 = await _get_active_participant_row(session, web_account_id)
        if cur2 is None:
            # 极端：被并发插入后又 CASCADE 删除；有界重试防无限递归
            if _depth >= MAX_JOIN_RETRIES:
                logger.warning(
                    "join_construction retries exhausted (CASCADE thrash) "
                    "account=%s sheet=%s", web_account_id, sheet_id, exc_info=True,
                )
                raise  # 重抛 IntegrityError → 上游 500（极端，已重试上限）
            return await join_construction(
                session, web_account_id, sheet_id, source=source, _depth=_depth + 1,
            )
        if cur2.sheet_id == sheet_id:
            return cur2
        if source == "manual":
            raise ParticipantConflict(cur2.sheet_id)
        return cur2


async def leave_construction(
    session: AsyncSession, web_account_id: int, *, reason: str = "manual_leave"
) -> bool:
    """退出当前活跃加入（UPDATE left_at + left_reason；保留历史行）。

    返回是否实际退出了 1 行（未活跃加入 → False，不报错）。
    """
    cur = await _get_active_participant_row(session, web_account_id)
    if cur is None:
        return False
    now = datetime.now(timezone.utc)
    cur.left_at = now
    cur.left_reason = reason
    cur.updated_at = now  # model 无 onupdate，显式刷新（仿 PlacementRecord SET）
    await session.flush()
    return True


async def get_my_active_participant(
    session: AsyncSession, web_account_id: int
) -> tuple[Participant, Sheet] | None:
    """读该 account 当前活跃加入行 + sheet（未加入 → None）。"""
    row = (
        await session.execute(
            select(Participant, Sheet)
            .join(Sheet, Sheet.id == Participant.sheet_id)
            .where(
                Participant.web_account_id == web_account_id,
                Participant.left_at.is_(None),
            )
        )
    ).first()
    return row if row is not None else None


async def lookup_active_by_uuids(
    session: AsyncSession, player_uuids: list[uuid.UUID]
) -> dict[uuid.UUID, int | None]:
    """批量 UUID → 该账号当前活跃 sheet_id（tracker 用，非敏感）。

    经 ``Player.uuid → web_account_id → participants 活跃行 → sheet_id``。
    未绑账号 / 未加入任何项目 → ``None``。
    """
    if not player_uuids:
        return {}
    rows = (
        await session.execute(
            select(Player.uuid, Participant.sheet_id)
            .outerjoin(
                Participant,
                (Participant.web_account_id == Player.web_account_id)
                & (Participant.left_at.is_(None)),
            )
            .where(Player.uuid.in_(player_uuids))
        )
    ).all()
    # 一个 account 同时最多 1 个活跃参与者（DB 兜底），但 outerjoin 在多 UUID 同 account
    # 时可能返回多行；取首个非 None sheet_id per uuid（防御性）
    result: dict[uuid.UUID, int | None] = {}
    for r in rows:
        # 已存在且非 None 时不覆盖；首次见到 None 后允许被后续非 None 覆盖
        existing = result.get(r.uuid)
        if existing is None:
            result[r.uuid] = r.sheet_id
    # 补全输入中未命中的 UUID（理论 outerjoin 应全覆盖，防御性）
    for u in player_uuids:
        result.setdefault(u, None)
    return result


async def close_all_participants(
    session: AsyncSession, sheet_id: int, *, reason: str
) -> int:
    """归档时批量退出该 sheet 所有活跃参与者（UPDATE left_at + left_reason）。

    返回实际退出行数。保留历史行（仅 UPDATE，不 DELETE）。归档经 archive service
    调用，与 advance_sheet 同事务。
    """
    cur_rows = (
        await session.execute(
            select(Participant).where(
                Participant.sheet_id == sheet_id,
                Participant.left_at.is_(None),
            )
        )
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for p in cur_rows:
        p.left_at = now
        p.left_reason = reason
        p.updated_at = now  # model 无 onupdate，显式刷新（仿 PlacementRecord SET）
    if cur_rows:
        await session.flush()
    return len(cur_rows)
