"""积分层统一写入口（score_service）。

红线声明：
- ``write_ledger`` 是 ``scoring.score_ledger`` 的**唯一写入口**（RS-9 范式，同
  ``notification_service.notify``），禁止其他路径直接 INSERT score_ledger；
  所有写必须**先 ``acquire_account_lock`` 再读余额**（防并发丢失更新）。
- R-2 append-only：流水只增不改，任何积分变动记一条含 ``balance_after``，
  可审计重建余额（DB 触发器拒绝行级 UPDATE/DELETE 兜底）。
- R-5 身份主锚 = Web 账号（``account_id``）；离线改名换 UUID 积分不丢。
- R-10 单库事务：本模块**不 commit**，由调用方在同一事务内统一 commit/rollback
  （业务改库 + 记流水原子）。

调用契约：API 层 pydantic 用 Literal 限定 reason 枚举（用户触发不了 ValueError，
此处方向守卫只拦编程错误 → 上层 500）；金额一律 ``Decimal``，内部统一
quantize 到 0.01（Numeric(18,2) 精度）。
"""
import logging
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scoring import (
    REASON_BUILD_A,
    REASON_COLLECT,
    REASON_LEADER_BONUS,
    REASON_MANUAL_ADJ,
    REASON_SEASON_RESET,
    REASON_SETTLE,
    ScoreLedger,
)
from app.repositories import score_repo

_logger = logging.getLogger(__name__)

#: reason → 记账方向（+1 入账 / −1 出账）。delta 符号必须与之一致。
LEDGER_REASON_SIGN: dict[str, int] = {
    REASON_COLLECT: 1,
    REASON_BUILD_A: 1,
    REASON_LEADER_BONUS: 1,
    REASON_SETTLE: 1,
    REASON_MANUAL_ADJ: -1,
    REASON_SEASON_RESET: -1,
}

_TWO_PLACES = Decimal("0.01")


class ScoreIdempotencyConflict(Exception):
    """同 idempotency_key 但 payload（delta/reason/sheet_id）不一致。

    幂等键是调用方防重放（MCDR HTTP 重试，R-12）的承诺：同 key 必同 payload；
    不一致说明键被复用或逻辑分叉，拒绝静默吞掉。API 层应映射 409。
    """


class InsufficientBalance(Exception):
    """余额不足且未允许透支（``allow_overdraft=False``）。API 层应映射 422。"""


@dataclass(frozen=True)
class WriteLedgerResult:
    """写流水结果：``entry`` 为落库条目；``replayed=True`` 表示幂等命中返回原条目。"""

    entry: ScoreLedger
    replayed: bool


async def write_ledger(
    session: AsyncSession,
    *,
    account_id: int,
    delta: Decimal,
    reason: str,
    sheet_id: int | None = None,
    operator_uuid: UUID | None = None,
    idempotency_key: str | None = None,
    note: str | None = None,
    allow_overdraft: bool = False,
) -> WriteLedgerResult:
    """记一笔积分变动（唯一写入口；不 commit，由调用方事务决定原子性）。

    内部顺序：
    ① 方向守卫（编程错误 → ValueError）；
    ② 取账号 advisory lock（串行化并发写）；
    ③ 幂等检查（同 key 同 payload → 回放原条目；不一致 → 冲突）；
    ④ 读锁内最新余额 → 算 ``balance_after``（透支守卫）；
    ⑤ 落行返回。
    """
    # ① 方向守卫：reason 合法 + delta 符号匹配 + delta ≠ 0
    sign = LEDGER_REASON_SIGN.get(reason)
    if sign is None:
        raise ValueError(
            f"未知积分流水 reason：{reason!r}（合法值：{sorted(LEDGER_REASON_SIGN)}）"
        )
    if delta == 0:
        raise ValueError("delta 不得为 0（无意义的流水行）")
    if (delta > 0) != (sign > 0):
        raise ValueError(
            f"reason={reason!r} 的记账方向为 {'+' if sign > 0 else '-'}，"
            f"与 delta={delta} 符号不符"
        )

    # ② 事务级 advisory lock：同账号并发写在此串行化
    await score_repo.acquire_account_lock(session, account_id)

    # ③ 幂等回放：同 key 同 payload 返回原条目，不重复记账
    if idempotency_key is not None:
        existing = await score_repo.get_by_idempotency_key(
            session, account_id, idempotency_key
        )
        if existing is not None:
            payload_match = (
                existing.delta == delta
                and existing.reason == reason
                and existing.sheet_id == sheet_id
            )
            if payload_match:
                return WriteLedgerResult(entry=existing, replayed=True)
            raise ScoreIdempotencyConflict(
                f"idempotency_key={idempotency_key!r} 已用于不同 payload："
                f"已存 delta={existing.delta} reason={existing.reason!r} "
                f"sheet_id={existing.sheet_id}，本次 delta={delta} "
                f"reason={reason!r} sheet_id={sheet_id}"
            )

    # ④ 锁内读余额 → 算新余额（quantize 后运算，与 Numeric(18,2) 对齐）
    delta = delta.quantize(_TWO_PLACES)
    balance = await score_repo.get_latest_balance(session, account_id)
    balance_after = balance + delta
    # 透支守卫仅限出账（delta<0）：入账方向不检查余额正负——负余额账号的
    # 部分额度 credit 合法（scoring.md：allow_overdraft 仅 debit 语义）
    if delta < 0 and balance_after < 0 and not allow_overdraft:
        raise InsufficientBalance(
            f"账号 {account_id} 余额 {balance} 不足以扣减 {abs(delta)}"
            f"（未允许透支）"
        )

    # ⑤ 落行（add + flush 拿 id；commit 归调用方）
    entry = await score_repo.create(
        session,
        account_id=account_id,
        delta=delta,
        reason=reason,
        balance_after=balance_after,
        sheet_id=sheet_id,
        operator_uuid=operator_uuid,
        idempotency_key=idempotency_key,
        note=note,
    )
    return WriteLedgerResult(entry=entry, replayed=False)


# ---------------------------------------------------------------------------
# 结算编排（settle）——归档 post-commit best-effort
# ---------------------------------------------------------------------------

# 结算参数默认值（system.settings 无值时回退；公式依据见 scoring-settlement.md §4.1）
_DEFAULT_TOTAL_SCORE_POOL = Decimal("1000.00")
_DEFAULT_LEADER_K = Decimal("0.10")
_DEFAULT_ALPHA = Decimal("0.00")  # 时间贡献暂未接入，α=0
_DEFAULT_BETA = Decimal("1.00")   # 纯材料占比


def _to_decimal(value: object, default: Decimal) -> Decimal:
    """DB JSONB 值 → Decimal（防御性：已是 Decimal 直接返回，float 用 str 中转防精度丢失）。"""
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


async def _get_scoring_params(session: AsyncSession) -> dict[str, Decimal]:
    """从 system.settings 读结算参数（DB 无值回退默认常量）。

    键：scoring.total_score_pool / scoring.leader_k / scoring.alpha / scoring.beta。
    """
    from app.models.system import SystemSetting

    keys = [
        "scoring.total_score_pool",
        "scoring.leader_k",
        "scoring.alpha",
        "scoring.beta",
    ]
    rows = (
        await session.execute(
            select(SystemSetting.key, SystemSetting.value).where(
                SystemSetting.key.in_(keys)
            )
        )
    ).all()
    db_values = {r.key: r.value for r in rows}
    return {
        "total_score_pool": _to_decimal(
            db_values.get("scoring.total_score_pool"), _DEFAULT_TOTAL_SCORE_POOL
        ),
        "leader_k": _to_decimal(
            db_values.get("scoring.leader_k"), _DEFAULT_LEADER_K
        ),
        "alpha": _to_decimal(
            db_values.get("scoring.alpha"), _DEFAULT_ALPHA
        ),
        "beta": _to_decimal(
            db_values.get("scoring.beta"), _DEFAULT_BETA
        ),
    }


async def settle(
    session: AsyncSession,
    sheet_id: int,
    *,
    owner_account_id: int | None,
    contributor_totals: list[tuple[int, str, int]],
    placement_totals: list[tuple[int, str, int]],
) -> int:
    """归档积分终算（settle 编排）。

    幂等：查询 (sheet_id, reason='settle') 已有流水 → 跳过（归档终态只读，
    正常不会重算；异常重试安全）。

    编排顺序（对齐 scoring-settlement.md §4.1）：
    1. collect（独立）
    2. build_a（独立，与 collect 并行但此处串行）
    3. leader_bonus（依赖 1+2 的实际产出 entries，注入而非自行重算——
       避免硬编码 α/β 忽略用户配置）

    参数：
    - session：独立 session（归档已 commit，settle 在新事务内执行）。
    - sheet_id：归档目标 sheet。
    - owner_account_id：负责人 WebAccount ID（从 Sheet.owner_uuid 解析）。
    - contributor_totals：收集贡献聚合，来自 sheet_repo.aggregate_contributor_totals
      经 archive service 转换为 [(account_id, display_name, qty)]。
    - placement_totals：施工贡献聚合，来自 construction_repo.aggregate_placement_totals
      转换为 [(account_id, display_name, net_qty)]。

    返回：写入的 ledger 条数（0 = 幂等跳过或无贡献）。
    """
    # 幂等检查：已有 settle 流水 → 跳过
    existing = (
        await session.execute(
            select(ScoreLedger.id)
            .where(
                ScoreLedger.sheet_id == sheet_id,
                ScoreLedger.reason == REASON_SETTLE,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        _logger.info(
            "settle: sheet=%s already settled (ledger id=%s), skipping", sheet_id, existing
        )
        return 0

    # 读结算参数
    params = await _get_scoring_params(session)

    # 构建上下文（tuple 保证不可变，对齐 SettlementContext frozen 契约）
    from app.services.score_calculators import (
        BuildAScoreCalculator,
        CollectScoreCalculator,
        LeaderBonusCalculator,
        SettlementContext,
    )

    ctx = SettlementContext(
        sheet_id=sheet_id,
        total_score_pool=params["total_score_pool"],
        contributor_totals=tuple(contributor_totals),
        placement_totals=tuple(placement_totals),
        leader_account_id=owner_account_id,
        leader_k=params["leader_k"],
    )

    # ① collect + ② build_a（独立，各自产出 entries）
    collect_calc = CollectScoreCalculator()
    build_a_calc = BuildAScoreCalculator(params["alpha"], params["beta"])
    collect_entries = collect_calc.calculate(ctx)
    build_a_entries = build_a_calc.calculate(ctx)
    _logger.info(
        "settle: sheet=%s collect=%d build_a=%d entries",
        sheet_id, len(collect_entries), len(build_a_entries),
    )

    # ③ leader_bonus（依赖上游实际 entries，注入而非自行重算）
    upstream = collect_entries + build_a_entries
    leader_calc = LeaderBonusCalculator(params["leader_k"], upstream)
    leader_entries = leader_calc.calculate(ctx)
    _logger.info(
        "settle: sheet=%s leader_bonus=%d entries", sheet_id, len(leader_entries),
    )

    all_entries = collect_entries + build_a_entries + leader_entries
    if not all_entries:
        _logger.info("settle: sheet=%s no entries to write (no contributions)", sheet_id)
        return 0

    # 逐条写流水（每条独立 acquire lock + 计算 balance_after；唯一写入口，R-2）
    count = 0
    for entry in all_entries:
        try:
            result = await write_ledger(
                session,
                account_id=entry.account_id,
                delta=entry.delta,
                reason=entry.reason,
                sheet_id=sheet_id,
                note=entry.note,
            )
            count += 1
            if result.replayed:
                _logger.warning(
                    "settle: sheet=%s account=%s reason=%s unexpected replay",
                    sheet_id, entry.account_id, entry.reason,
                )
        except Exception:
            _logger.exception(
                "settle: sheet=%s account=%s reason=%s write failed, "
                "continuing with remaining entries",
                sheet_id, entry.account_id, entry.reason,
            )

    _logger.info("settle: sheet=%s wrote %d/%d entries", sheet_id, count, len(all_entries))
    return count
