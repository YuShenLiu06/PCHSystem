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
    PlacementRecord,
    PlacementSnapshot,
    PlayerSource,
    PlayerSourceHistory,
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
    return ConstructionProgress(
        sheet_id=sheet_id,
        account_totals=account_totals,
        breakdown=breakdown,
        material_completion=material_completion,
        timeline=timeline,
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
        return skip_all(no_attribution_reason or "无法归因")
    # 客户端 mod 全局关闭 → 全 skip
    if client_mod_closed:
        return skip_all("客户端模组上报已被服主关闭")

    # --- 3.5 方块清单预取（迭代 2 需求 2）：仅清单内 registry_id 可上报 ---
    allowed_rows = (
        await session.execute(
            select(func.distinct(SheetRow.registry_id))
            .where(SheetRow.sheet_id == sheet_id_resolved)
            .where(SheetRow.registry_id.is_not(None))
        )
    ).scalars().all()
    allowed_set: set[str] = set(allowed_rows)

    # --- 4. 批量预取 player + active source（避免 N+1）---
    all_uuids = list({puuid for (puuid, _rid) in order})
    players_map: dict[uuid.UUID, Player] = {}
    active_map: dict[uuid.UUID, tuple[str | None, str | None]] = {}
    if all_uuids:
        p_rows = (
            await session.execute(select(Player).where(Player.uuid.in_(all_uuids)))
        ).scalars().all()
        players_map = {p.uuid: p for p in p_rows}
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
        net = placed - broken
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
        # 落库
        await _upsert_placement(
            session, sheet_id_resolved, player.web_account_id, rid, placed, broken
        )
        accepted_account_ids.add(player.web_account_id)
        outcomes.append(
            PlacementOutcome(
                player_uuid=puuid, registry_id=rid,
                action="accepted", reason="", net_delta=net,
            )
        )
        counts_accepted += 1

    await session.flush()

    # --- 6. 时序快照（迭代 2 需求 4，best-effort 不阻断上报）---
    if accepted_account_ids:
        try:
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
