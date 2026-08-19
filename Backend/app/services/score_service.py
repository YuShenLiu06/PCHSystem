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
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

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
