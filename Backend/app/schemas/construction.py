"""施工进度上报层 Pydantic schema（construction schema 的请求/响应体）。

对应端点见 ``app/api/construction.py``、契约见
[`Docs/architecture/api/construction.md`]。
"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


# --- 上报（POST /v1/construction/report）---

class PlacementEntry(BaseModel):
    """单条方块净放置上报。

    - service-token 通道：``player_uuid`` 为代报目标（后端逐个验证存在）。
    - JWT 通道：``player_uuid`` 被服务端**强制覆盖**为 ``active_uuid``（payload 值忽略）。
    - ``registry_id``：方块 registry id（``namespace:path``）。后端校验必须在
      该 sheet 收集清单内（``sheet_rows.registry_id`` 集合，含子物品），否则 skip
      （reason `方块不在项目材料清单内`）；空气/水等过滤归追踪器侧（C-6）。
    """

    player_uuid: UUID
    registry_id: str = Field(min_length=1, max_length=128)
    placed_qty: int = Field(default=0, ge=0)
    broken_qty: int = Field(default=0, ge=0)


class PlacementReport(BaseModel):
    """``POST /v1/construction/report`` 请求体。

    ``sheet_id`` 非空 → 显式归因（须 constructing，否则 api 层 409/404）；
    ``None`` → 后端启发式归因（恰 1 个 constructing sheet 自动归因，否则全 skip）。
    """

    sheet_id: int | None = None
    placements: list[PlacementEntry] = Field(min_length=1, max_length=2000)


class PlacementOutcome(BaseModel):
    """单条上报结果。``accepted`` 落库；``skipped`` 附 reason（不写库）。"""

    player_uuid: UUID
    registry_id: str
    action: Literal["accepted", "skipped"]
    reason: str = ""
    net_delta: int = 0  # 本次净变化（placed-broken），skipped=0


class PlacementTotals(BaseModel):
    accepted: int = 0
    skipped: int = 0


class PlacementReportResult(BaseModel):
    """上报响应（HTTP 200）。"""

    sheet_id: int | None
    attribution_source: Literal["explicit", "heuristic", "none"]
    totals: PlacementTotals
    outcomes: list[PlacementOutcome]


# --- 归因查询（GET /v1/construction/active-sheets）---

class ActiveSheet(BaseModel):
    id: int
    title: str


class ActiveSheetsResult(BaseModel):
    """活跃 constructing 项目列表 + 启发式归因是否可用（恰 1 个）。"""

    sheets: list[ActiveSheet]
    heuristic_eligible: bool


# --- 进度查询（GET /v1/construction/{sheet_id}/progress）---

class ProgressAccountTotal(BaseModel):
    """按 Web 账号聚合的施工总量（R-5 锚 account）。"""

    account_id: int
    display_name: str
    placed_qty: int
    broken_qty: int
    net_qty: int


class ProgressBreakdownItem(BaseModel):
    """按 account × registry 明细（进度展示 + 归档 md 复用）。"""

    account_id: int
    display_name: str
    registry_id: str
    placed_qty: int
    broken_qty: int
    net_qty: int


class ProgressMaterialItem(BaseModel):
    """材料完成度（按 registry 聚合，对照 ``sheet_rows.need_qty``）。

    - ``need_qty``：该 registry 在收集清单中的需求总量（多行同 registry 求和）。
    - ``net_qty``：该 registry 的净放置（``placement_records`` 聚合）。
    - ``completion_pct``：``need > 0`` 时为 ``clamp(net/need, 0, 1) * 100``（保留 1 位，
      百分比视觉封顶 100%）；``need == 0`` → ``None``（不显示百分比）。
    """

    registry_id: str
    item_name: str
    need_qty: int
    net_qty: int
    completion_pct: float | None


class ProgressTimelinePoint(BaseModel):
    """时序快照点（某 account 截至该时刻的累计净放置，迭代 2）。"""

    account_id: int
    total_net: int
    recorded_at: datetime


class ConstructionProgress(BaseModel):
    """进度端点响应（字段只增不减，向后兼容）。"""

    sheet_id: int
    account_totals: list[ProgressAccountTotal]
    breakdown: list[ProgressBreakdownItem]
    material_completion: list[ProgressMaterialItem]
    timeline: list[ProgressTimelinePoint]


class PlacementTotal(BaseModel):
    """归档/结算消费契约（scoring-settlement.md §4 ``placement_totals`` 元素形状）。

    ``BuildAScoreCalculator`` 消费 ``[(account_id, display_name, net_qty)]``；
    本类型固化字段形状，供 ``construction_repo.aggregate_placement_totals`` 返回。
    """  # noqa: E501

    account_id: int
    display_name: str
    net_qty: int


# --- admin 设置（GET/PATCH /v1/construction/settings）---

class ConstructionSettings(BaseModel):
    """system.settings ``construction.*`` 读出（DB 值优先，回退 config 默认）。"""

    allow_client_mods: bool
    official_tracker_enabled: bool
    allow_server_mods: bool
    report_interval_seconds: int
    anti_cheat_threshold: int | None  # None = 不限


class ConstructionSettingsUpdate(BaseModel):
    """PATCH body：部分更新（仅给定字段写入 system.settings）。

    ``model_dump(exclude_unset=True)`` 区分「未提供」与「显式 null」：
    ``anti_cheat_threshold=None`` 表示关闭阈值限制（合法写入）。
    """

    allow_client_mods: bool | None = None
    official_tracker_enabled: bool | None = None
    allow_server_mods: bool | None = None
    report_interval_seconds: int | None = Field(default=None, ge=1)
    anti_cheat_threshold: int | None = Field(default=None, ge=1)


# --- admin 白名单（GET/POST/DELETE /v1/construction/mod-sources）---

class ServerModSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    notes: str | None = Field(default=None, max_length=500)


class ServerModSourceToggle(BaseModel):
    """``PATCH /mod-sources/{name}`` body：逐源启停（迭代 3 卡片开关）。"""

    enabled: bool


class ServerModSourceEntry(BaseModel):
    name: str
    enabled: bool
    approved_by_uuid: UUID | None
    approved_at: datetime
    notes: str | None


# --- 切源（POST switch-server / switch-self，GET source/me）---

class SourceSwitchServerRequest(BaseModel):
    """admin 切某玩家的服务端源。

    ``source_type="mcdr"`` → ``source_id`` 强制 "official"（忽略 payload）；
    ``source_type="server_mod"`` → ``source_id`` 必填且须在白名单（api 层校验 → 422）。
    """

    player_uuid: UUID
    source_type: Literal["mcdr", "server_mod"]
    source_id: str | None = Field(default=None, max_length=64)
    reason: str | None = Field(default=None, max_length=200)


class SourceSwitchSelfRequest(BaseModel):
    """玩家切自己的上报模式。

    ``mode="server"`` → 退回服务端代报（mcdr/official）；
    ``mode="local"`` → ``source_id`` 必填（= mod_id；归属校验留 mod-token PR）。
    """

    mode: Literal["server", "local"]
    source_id: str | None = Field(default=None, max_length=64)
    reason: str | None = Field(default=None, max_length=200)


class SourceState(BaseModel):
    """玩家当前活跃上报源。``is_default=True`` 表示无显式记录、走默认 mcdr/official。"""

    source_type: str | None
    source_id: str | None
    is_default: bool


class SourceHistoryEntry(BaseModel):
    from_type: str | None
    from_id: str | None
    to_type: str
    to_id: str | None
    switched_at: datetime
    reason: str | None


class DormantSource(BaseModel):
    """休眠源（曾活跃、当前 ``disabled_at IS NOT NULL`` 的 client_mod 源，可一键切回）。

    严格单源策略不变（同时仅 1 个活跃源）；休眠源仅作「快速切回历史 mod_id」的展示层
    列表。``last_active_at`` = 该 source_id 最近一次激活时间（``activated_at``）。
    """

    source_id: str
    last_active_at: datetime


class SourceMeResult(BaseModel):
    active: SourceState
    history: list[SourceHistoryEntry]
    dormant_sources: list[DormantSource]
