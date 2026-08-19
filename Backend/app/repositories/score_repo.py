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
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Numeric, desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scoring import ScoreLedger
from app.models.user import Player, WebAccount


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


@dataclass(frozen=True)
class BalanceRow:
    """账号余额聚合行（``list_balances`` 返回；display_name 已按 #41 回退链解析）。"""

    account_id: int
    display_name: str
    player_names: tuple[str, ...]
    balance: Decimal
    entries_count: int
    last_entry_at: datetime | None


async def list_balances(
    session: AsyncSession, *, page: int, limit: int
) -> tuple[list[BalanceRow], int]:
    """所有已绑定玩家的 WebAccount 当前余额排名（只读，供 admin/balances）。

    - 行集 = 有 ≥1 绑定玩家的账号（未绑定玩家 credit/debit 本就 skip，
      无积分语义）；余额归属锚 = WebAccount（R-5），同账号多玩家一行。
    - ``balance`` = SUM(delta)（append-only 可审计重建，R-2，与该账号最新
      ``balance_after`` 恒一致；无流水 → 0.00）。
    - 排序 balance DESC、account_id ASC（榜单 + 平分稳定序）。
    - ``player_names`` 按 last_seen_at DESC（最新在前，同名去重）；
      ``display_name`` 空 → 回退首个玩家名（#41 链最新成员名）。
    """
    bound_ids = select(Player.web_account_id).where(
        Player.web_account_id.is_not(None)
    )
    total = (
        await session.execute(
            select(func.count())
            .select_from(WebAccount)
            .where(WebAccount.id.in_(bound_ids))
        )
    ).scalar_one()

    agg = (
        select(
            WebAccount.id,
            WebAccount.display_name,
            func.coalesce(func.sum(ScoreLedger.delta), 0)
            .cast(Numeric(18, 2))
            .label("balance"),
            func.count(ScoreLedger.id).label("entries_count"),
            func.max(ScoreLedger.created_at).label("last_entry_at"),
        )
        .join_from(
            WebAccount, ScoreLedger, ScoreLedger.account_id == WebAccount.id,
            isouter=True,
        )
        .where(WebAccount.id.in_(bound_ids))
        .group_by(WebAccount.id, WebAccount.display_name)
        .order_by(desc("balance"), WebAccount.id)
        .offset((page - 1) * limit)
        .limit(limit)
    )
    rows = (await session.execute(agg)).all()
    if not rows:
        return [], total

    players = (
        (
            await session.execute(
                select(Player)
                .where(Player.web_account_id.in_([r.id for r in rows]))
                .order_by(Player.last_seen_at.desc())
            )
        )
        .scalars()
        .all()
    )
    names_by_account: dict[int, list[str]] = {}
    for p in players:
        names_by_account.setdefault(p.web_account_id, []).append(p.current_name)

    return [
        BalanceRow(
            account_id=r.id,
            display_name=r.display_name
            or (names_by_account.get(r.id) or [str(r.id)])[0],
            player_names=tuple(dict.fromkeys(names_by_account.get(r.id, []))),
            balance=r.balance,
            entries_count=r.entries_count,
            last_entry_at=r.last_entry_at,
        )
        for r in rows
    ], total
