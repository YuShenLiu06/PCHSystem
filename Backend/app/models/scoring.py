"""积分层 ORM（scoring schema）。

对应迁移 ``0024_score_ledger``。表语义见迁移 docstring 与
``Docs/architecture/flows/scoring-settlement.md`` §6。

- ``ScoreLedger``：积分流水（append-only，R-2；行级 UPDATE/DELETE 由迁移里的
  触发器 ``scoring.prevent_ledger_modify`` 强制拒绝，ORM 侧不提供任何改写方法）。
- 归属锚 = ``account_id``（Web 账号，R-5）；``balance_after`` 由
  ``score_service.write_ledger`` 入口内计算（锁内 SELECT 最新余额 + delta）。

写入口唯一：``app/services/score_service.py::write_ledger``，禁止任何路径直接
INSERT 本表。
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Numeric, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

REASON_COLLECT = "collect"
REASON_BUILD_A = "build_a"
REASON_LEADER_BONUS = "leader_bonus"
REASON_SETTLE = "settle"
REASON_MANUAL_ADJ = "manual_adj"
REASON_SEASON_RESET = "season_reset"

LEDGER_REASONS = (
    REASON_COLLECT,
    REASON_BUILD_A,
    REASON_LEADER_BONUS,
    REASON_SETTLE,
    REASON_MANUAL_ADJ,
    REASON_SEASON_RESET,
)


class ScoreLedger(Base):
    """积分流水（scoring.score_ledger，迁移 0024）。

    append-only：任何积分变动记一条，含 ``balance_after``，可审计重建余额
    与榜单（R-1 derived view / R-2）。
    """

    __tablename__ = "score_ledger"
    __table_args__ = (
        CheckConstraint("delta <> 0", name="ck_score_ledger_delta_nonzero"),
        CheckConstraint(
            "reason IN ('collect','build_a','leader_bonus','settle',"
            "'manual_adj','season_reset')",
            name="ck_score_ledger_reason",
        ),
        {"schema": "scoring"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.web_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    delta: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    sheet_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    operator_uuid: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
