"""积分层 schema（/v1/scoring 端点契约）。

- 写端点（credit/debit）请求体：批量 items，**schema 校验失败整批 422**
  （amount 正数两位小数、reason 按方向收紧到子集、批大小 ≤100）；
  业务级失败（玩家不存在/未绑定/幂等冲突/透支）在 API 层逐条转 skip，
  不影响同批其他条目。
- 响应体：``ScoreItemResult``（每条 item 独立成败）+ ``ScoreLedgerEntry``
  （ORM 流水投影；Decimal 字段经 pydantic v2 序列化为 JSON 字符串传输）。
- reason 枚举与 ``app.models.scoring.LEDGER_REASONS`` 对齐：DB CHECK 是全集
  6 种，本层按方向收紧（credit 4 种 / debit 2 种）。
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CREDIT_REASONS = ("collect", "build_a", "leader_bonus", "settle")
DEBIT_REASONS = ("manual_adj", "season_reset")
MAX_BATCH_ITEMS = 100

CreditReason = Literal[CREDIT_REASONS]
DebitReason = Literal[DEBIT_REASONS]
AdjustReason = Literal[CREDIT_REASONS + DEBIT_REASONS]


class ScoreItem(BaseModel):
    """单条积分变动（公共字段；reason 由子类按方向收紧）。"""

    player_uuid: UUID
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    sheet_id: int | None = None
    operator_uuid: UUID | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=200)


class CreditItem(ScoreItem):
    reason: CreditReason


class DebitItem(ScoreItem):
    reason: DebitReason


class CreditBatchRequest(BaseModel):
    items: list[CreditItem] = Field(min_length=1, max_length=MAX_BATCH_ITEMS)
    notify: bool = True


class DebitBatchRequest(BaseModel):
    items: list[DebitItem] = Field(min_length=1, max_length=MAX_BATCH_ITEMS)
    notify: bool = True
    allow_overdraft: bool = False


class AdjustItem(ScoreItem):
    """管理员调控条目：reason 放开到全集（方向由 reason 符号定，单端点双向）。"""

    reason: AdjustReason


class AdminAdjustBatchRequest(BaseModel):
    items: list[AdjustItem] = Field(min_length=1, max_length=MAX_BATCH_ITEMS)
    notify: bool = True
    allow_overdraft: bool = False


class ScoreLedgerEntry(BaseModel):
    """score_ledger 行投影（append-only，R-2；只读，无任何写路径）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    delta: Decimal
    reason: str
    balance_after: Decimal
    sheet_id: int | None
    operator_uuid: UUID | None
    idempotency_key: str | None
    note: str | None
    created_at: datetime


class ScoreItemResult(BaseModel):
    """单条 item 处理结果（accepted / skip 原因 / 幂等重放标记）。"""

    player_uuid: UUID
    accepted: bool
    entry: ScoreLedgerEntry | None = None
    idempotent_replay: bool = False
    skip_reason: str | None = None


class ScoreBatchResult(BaseModel):
    results: list[ScoreItemResult]
    accepted_count: int
    skipped_count: int


class ScoreLedgerPage(BaseModel):
    """流水分页响应（total 为过滤后总数；排序 id DESC 由 repo 保证）。"""

    items: list[ScoreLedgerEntry]
    total: int
    page: int
    limit: int
