"""ScoreRepository 函数式实现（scoring schema，积分层数据访问原语）。

镜像 ``notification_repo.py`` 风格：函数收 ``AsyncSession``，只 ``add + flush()``，
不 commit，由调用方（service 层 / API 写端点）在同一事务内决定 commit/rollback
（R-10 单库事务一致性）。

红线：
- R-2 append-only：本模块**不提供**任何 UPDATE/DELETE 方法（DB 触发器兜底，
  迁移 0024 ``scoring.prevent_ledger_modify``）。
- 写入仅经 ``app/services/score_service.write_ledger`` 入口（锁内算余额），
  禁止其他路径直接 ``create``。
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scoring import ScoreLedger


async def acquire_account_lock(session: AsyncSession, account_id: int) -> None:
    """取该账号的积分事务级 advisory lock（串行化同账号并发写）。

    ``pg_advisory_xact_lock`` 事务结束（commit/rollback）自动释放，无需手工解锁；
    ``write_ledger`` 必须**先取锁再读余额**，防两笔并发写读到同一 ``balance_after``
    造成丢失更新。锁键 = (hashtext('scoring.score_ledger'), account_id)，
    与其他业务的 advisory lock 天然隔离。
    """
    await session.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtext('scoring.score_ledger'), :account_id)"
        ),
        {"account_id": account_id},
    )


async def get_latest_balance(session: AsyncSession, account_id: int) -> Decimal:
    """读账号最新余额（流水表最后一条 ``balance_after``；无历史 → 0.00）。

    依赖 ``ix_score_ledger_account_id_desc`` 索引（account_id + id DESC）。
    调用方必须已持有 ``acquire_account_lock``，否则读到的是竞态快照。
    """
    stmt = (
        select(ScoreLedger.balance_after)
        .where(ScoreLedger.account_id == account_id)
        .order_by(ScoreLedger.id.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    return Decimal("0.00") if row is None else row


async def get_by_idempotency_key(
    session: AsyncSession, account_id: int, key: str
) -> ScoreLedger | None:
    """按 (account_id, idempotency_key) 查已存流水（幂等回放检查）。

    命中 partial unique index ``uq_score_ledger_idem``，至多一行。
    """
    stmt = select(ScoreLedger).where(
        ScoreLedger.account_id == account_id,
        ScoreLedger.idempotency_key == key,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def create(session: AsyncSession, **columns: object) -> ScoreLedger:
    """落一条流水（add + flush 拿 id，不 commit）。

    仅 ``score_service.write_ledger`` 允许调用（锁内、算好 ``balance_after``）；
    列值合法性（delta ≠ 0、reason 枚举）由模型 CHECK 兜底。
    """
    record = ScoreLedger(**columns)  # type: ignore[arg-type]
    session.add(record)
    await session.flush()
    return record


async def list_entries(
    session: AsyncSession,
    *,
    account_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: int,
    limit: int,
) -> tuple[list[ScoreLedger], int]:
    """分页查询流水（只读，供 /ledger 端点与审计）。

    过滤：``account_id`` 等值 / ``created_at >= since`` / ``created_at < until``；
    排序 ``id DESC``（最新在前）；``offset = (page - 1) * limit``。
    返回 ``(当页行, 同条件 COUNT)``——COUNT 不受分页截断影响。
    """
    conditions = []
    if account_id is not None:
        conditions.append(ScoreLedger.account_id == account_id)
    if since is not None:
        conditions.append(ScoreLedger.created_at >= since)
    if until is not None:
        conditions.append(ScoreLedger.created_at < until)

    total = (
        await session.execute(
            select(func.count()).select_from(ScoreLedger).where(*conditions)
        )
    ).scalar_one()
    stmt = (
        select(ScoreLedger)
        .where(*conditions)
        .order_by(ScoreLedger.id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return rows, total
