"""积分结算计算器（ScoreCalculator Strategy Protocol）。

纯函数：从 SettlementContext 取聚合数据，返回该计算器贡献的 ledger 条目。
不查库、不写库、不改入参（不可变）。

三个内置计算器：
- CollectScoreCalculator：收集类，S_i = S_总 × n_i / N
- BuildAScoreCalculator：建造类，G_i = α·(t_i/T) + β·(p_i/P)（当前 α=0，仅材料占比）
- LeaderBonusCalculator：负责人增发，S_负责人 = S_全体 × k（全体 = 上游 entries 总 delta）

设计契约见 `Docs/architecture/flows/scoring-settlement.md` §4。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from app.models.scoring import REASON_BUILD_A, REASON_COLLECT, REASON_LEADER_BONUS

_logger = logging.getLogger(__name__)

_TWO_PLACES = Decimal("0.01")


@dataclass(frozen=True)
class LedgerEntry:
    """单条积分流水条目（Calculator → settle 中间产物）。

    account_id = web_account_id（R-5 归属锚）；delta > 0（入账）。
    """

    account_id: int
    delta: Decimal
    reason: str
    note: str | None = None


@dataclass(frozen=True)
class SettlementContext:
    """结算上下文（service 预算后注入，Calculator 不查库）。

    字段命名对齐 `scoring-settlement.md` §4。
    不可变：所有容器字段用 tuple（frozen dataclass 只防字段重赋值，不防 list 内容修改）。
    """

    sheet_id: int
    total_score_pool: Decimal
    contributor_totals: tuple[tuple[int, str, int], ...]  # ((account_id, display_name, qty), ...)
    placement_totals: tuple[tuple[int, str, int], ...]  # ((account_id, display_name, net_qty), ...)
    leader_account_id: int | None
    leader_k: Decimal


# ---------------------------------------------------------------------------
# ScoreCalculator Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ScoreCalculator(Protocol):
    """计算器协议：name + calculate（纯函数）。"""

    name: str

    def calculate(self, ctx: SettlementContext) -> list[LedgerEntry]: ...


# ---------------------------------------------------------------------------
# 内置计算器
# ---------------------------------------------------------------------------


class CollectScoreCalculator:
    """收集类积分：S_i = S_总 × n_i / N（n_i = 账号 i 收集量，N = 总收集量）。

    数据源：SettlementContext.contributor_totals（来自 sheet_repo.aggregate_contributor_totals）。
    """

    name = "collect"

    def calculate(self, ctx: SettlementContext) -> list[LedgerEntry]:
        if not ctx.contributor_totals:
            return []
        total_qty = sum(qty for _, _, qty in ctx.contributor_totals)
        if total_qty <= 0:
            return []
        entries: list[LedgerEntry] = []
        for account_id, display_name, qty in ctx.contributor_totals:
            if qty <= 0:
                continue
            share = Decimal(qty) / Decimal(total_qty)
            delta = (ctx.total_score_pool * share).quantize(_TWO_PLACES)
            if delta > 0:
                entries.append(
                    LedgerEntry(
                        account_id=account_id,
                        delta=delta,
                        reason=REASON_COLLECT,
                        note=f"收集占比 {share:.2%}",
                    )
                )
                _logger.debug(
                    "collect: account=%s name=%s qty=%s share=%.4f delta=%s",
                    account_id, display_name, qty, share, delta,
                )
        return entries


class BuildAScoreCalculator:
    """建造类积分：G_i = α·(t_i/T) + β·(p_i/P)。

    - t_i/T = 时间贡献占比（施工快照时序，暂未接入 → α=0）
    - p_i/P = 材料贡献占比（placement_records 净放置量）
    - α+β=1

    当 α=0 时退化为 G_i = β × (p_i/P) = p_i/P（纯材料占比）。
    数据源：SettlementContext.placement_totals（来自 construction_repo.aggregate_placement_totals）。
    """

    name = "build_a"

    def __init__(self, alpha: Decimal, beta: Decimal) -> None:
        self.alpha = alpha
        self.beta = beta

    def calculate(self, ctx: SettlementContext) -> list[LedgerEntry]:
        if not ctx.placement_totals:
            return []
        total_net = sum(net for _, _, net in ctx.placement_totals)
        if total_net <= 0:
            return []
        entries: list[LedgerEntry] = []
        for account_id, display_name, net_qty in ctx.placement_totals:
            if net_qty <= 0:
                continue
            material_share = Decimal(net_qty) / Decimal(total_net)
            # α=0（时间贡献暂未接入）+ β × 材料占比
            weighted = self.alpha * Decimal(0) + self.beta * material_share
            delta = (ctx.total_score_pool * weighted).quantize(_TWO_PLACES)
            if delta > 0:
                entries.append(
                    LedgerEntry(
                        account_id=account_id,
                        delta=delta,
                        reason=REASON_BUILD_A,
                        note=f"建造占比 {material_share:.2%}",
                    )
                )
                _logger.debug(
                    "build_a: account=%s name=%s net=%s share=%.4f delta=%s",
                    account_id, display_name, net_qty, material_share, delta,
                )
        return entries


class LeaderBonusCalculator:
    """负责人增发：S_负责人 = S_全体 × k。

    S_全体 = 上游 Calculator（collect + build_a）实际产出的 entries 总 delta。
    上游 entries 由 settle 编排注入（避免自行重算时硬编码 α/β 忽略用户配置）。

    leader_account_id 由 settle 从 Sheet.owner_uuid 解析（Player.web_account_id）。
    负责人若无贡献（不在 contributor/placement_totals 中）仍获增发（荣誉激励）。
    """

    name = "leader_bonus"

    def __init__(self, k: Decimal, upstream_entries: list[LedgerEntry]) -> None:
        self.k = k
        self._upstream_entries = upstream_entries

    def calculate(self, ctx: SettlementContext) -> list[LedgerEntry]:
        if ctx.leader_account_id is None or self.k <= 0:
            return []
        total_from_others = sum(e.delta for e in self._upstream_entries)
        if total_from_others <= 0:
            return []
        bonus = (total_from_others * self.k).quantize(_TWO_PLACES)
        if bonus <= 0:
            return []
        _logger.debug(
            "leader_bonus: account=%s k=%.2f total=%.2f bonus=%s",
            ctx.leader_account_id, self.k, total_from_others, bonus,
        )
        return [
            LedgerEntry(
                account_id=ctx.leader_account_id,
                delta=bonus,
                reason=REASON_LEADER_BONUS,
                note=f"负责人增发 k={self.k}",
            )
        ]
