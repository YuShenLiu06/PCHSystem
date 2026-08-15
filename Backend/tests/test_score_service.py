"""积分层 service 集成测试（``score_service.write_ledger`` / ``score_repo``）。

覆盖契约（见 ``Docs/architecture/flows/scoring-settlement.md`` §6）：
- 写入口方向守卫 / 幂等回放 / 余额链计算 / 透支策略；
- R-2 append-only：行级 UPDATE/DELETE 被迁移 0024 触发器拒绝；
- advisory lock 串行化并发同账号写入（无丢失更新）；
- ``list_entries`` 分页 + account_id / since(>=) / until(<) 过滤。

统一直开 ``async_session_factory``（conftest autouse TRUNCATE 保证隔离），
seed 用 ORM 直插；金额一律 ``Decimal``。
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db import async_session_factory
from app.models.scoring import ScoreLedger
from app.models.user import WebAccount
from app.repositories import score_repo
from app.services.score_service import (
    InsufficientBalance,
    ScoreIdempotencyConflict,
    write_ledger,
)


async def _seed_account(role: str = "user") -> int:
    """seed 一个临时 WebAccount（R-5 身份主锚），返回 account_id。"""
    async with async_session_factory() as s:
        account = WebAccount(role=role)
        s.add(account)
        await s.flush()
        account_id = account.id
        await s.commit()
        return account_id


async def _count_entries(account_id: int) -> int:
    """数某账号在库流水条数（独立会话，读已提交状态）。"""
    async with async_session_factory() as s:
        _, total = await score_repo.list_entries(
            s, account_id=account_id, page=1, limit=1
        )
        return total


async def test_write_ledger_first_entry_zero_base():
    """无历史流水时以 0 为基准：首条 balance_after == delta。"""
    # Arrange
    account_id = await _seed_account()

    # Act
    async with async_session_factory() as s:
        result = await write_ledger(
            s, account_id=account_id, delta=Decimal("10"), reason="collect"
        )
        await s.commit()

    # Assert
    assert result.replayed is False
    assert result.entry.balance_after == Decimal("10.00")


async def test_write_ledger_chain():
    """链式记账：credit +10 → +5.5 → debit −3，余额链 10 → 15.5 → 12.5。"""
    # Arrange
    account_id = await _seed_account()

    # Act
    async with async_session_factory() as s:
        r1 = await write_ledger(
            s, account_id=account_id, delta=Decimal("10"), reason="collect"
        )
        r2 = await write_ledger(
            s, account_id=account_id, delta=Decimal("5.5"), reason="build_a"
        )
        r3 = await write_ledger(
            s,
            account_id=account_id,
            delta=Decimal("-3"),
            reason="manual_adj",
            operator_uuid=uuid.uuid4(),
        )
        await s.commit()

    # Assert
    assert (
        r1.entry.balance_after,
        r2.entry.balance_after,
        r3.entry.balance_after,
    ) == (Decimal("10.00"), Decimal("15.50"), Decimal("12.50"))


async def test_write_ledger_direction_mismatch_raises():
    """方向守卫：collect（入账）不许负 delta，manual_adj（出账）不许正 delta。"""
    # Arrange
    account_id = await _seed_account()

    # Act + Assert
    async with async_session_factory() as s:
        with pytest.raises(ValueError):
            await write_ledger(
                s, account_id=account_id, delta=Decimal("-5"), reason="collect"
            )
        with pytest.raises(ValueError):
            await write_ledger(
                s, account_id=account_id, delta=Decimal("5"), reason="manual_adj"
            )


async def test_write_ledger_zero_delta_raises():
    """delta == 0 是编程错误（DB CHECK delta <> 0 的前置守卫）→ ValueError。"""
    # Arrange
    account_id = await _seed_account()

    # Act + Assert
    async with async_session_factory() as s:
        with pytest.raises(ValueError):
            await write_ledger(
                s, account_id=account_id, delta=Decimal("0"), reason="collect"
            )


async def test_write_ledger_unknown_reason_raises():
    """reason 不在合法枚举 → ValueError（用户侧由 API 层 Literal 挡住，此处兜底）。"""
    # Arrange
    account_id = await _seed_account()

    # Act + Assert
    async with async_session_factory() as s:
        with pytest.raises(ValueError):
            await write_ledger(
                s, account_id=account_id, delta=Decimal("5"), reason="foo"
            )


async def test_write_ledger_replay():
    """幂等回放：同 key 同 payload 第二次返回原条目（replayed=True），库里仍只 1 行。"""
    # Arrange
    account_id = await _seed_account()
    idem_key = "idem-replay-1"

    # Act
    async with async_session_factory() as s:
        first = await write_ledger(
            s,
            account_id=account_id,
            delta=Decimal("10"),
            reason="collect",
            sheet_id=7,
            idempotency_key=idem_key,
        )
        await s.commit()
    async with async_session_factory() as s:
        second = await write_ledger(
            s,
            account_id=account_id,
            delta=Decimal("10"),
            reason="collect",
            sheet_id=7,
            idempotency_key=idem_key,
        )
        await s.commit()

    # Assert
    assert first.replayed is False
    assert second.replayed is True
    assert second.entry.id == first.entry.id
    assert await _count_entries(account_id) == 1


async def test_write_ledger_idempotency_conflict():
    """同 idempotency_key 但 payload 不同（delta 变了）→ ScoreIdempotencyConflict。"""
    # Arrange
    account_id = await _seed_account()
    idem_key = "idem-conflict-1"

    # Act
    async with async_session_factory() as s:
        await write_ledger(
            s,
            account_id=account_id,
            delta=Decimal("10"),
            reason="collect",
            idempotency_key=idem_key,
        )
        await s.commit()

    # Assert
    async with async_session_factory() as s:
        with pytest.raises(ScoreIdempotencyConflict):
            await write_ledger(
                s,
                account_id=account_id,
                delta=Decimal("20"),
                reason="collect",
                idempotency_key=idem_key,
            )


async def test_write_ledger_insufficient_balance():
    """余额不足：默认拒绝（InsufficientBalance）；allow_overdraft=True 允许负余额。"""
    # Arrange
    account_id = await _seed_account()

    # Act
    async with async_session_factory() as s:
        await write_ledger(
            s, account_id=account_id, delta=Decimal("10"), reason="collect"
        )
        # 默认不透支：10 − 25 = −15 < 0 → 拒绝（且不落行）
        with pytest.raises(InsufficientBalance):
            await write_ledger(
                s,
                account_id=account_id,
                delta=Decimal("-25"),
                reason="manual_adj",
            )
        # 允许透支：同笔放行，balance_after == −15.00
        overdraft = await write_ledger(
            s,
            account_id=account_id,
            delta=Decimal("-25"),
            reason="manual_adj",
            allow_overdraft=True,
        )
        await s.commit()

    # Assert
    assert overdraft.entry.balance_after == Decimal("-15.00")
    assert await _count_entries(account_id) == 2


async def test_ledger_update_blocked_by_trigger():
    """R-2 append-only：行级 UPDATE 被触发器 scoring.prevent_ledger_modify 拒绝。"""
    # Arrange：先落一行（触发器是行级，无行不触发）
    account_id = await _seed_account()
    async with async_session_factory() as s:
        await write_ledger(
            s, account_id=account_id, delta=Decimal("1"), reason="collect"
        )
        await s.commit()

    # Act + Assert
    async with async_session_factory() as s:
        with pytest.raises(DBAPIError) as excinfo:
            await s.execute(text("UPDATE scoring.score_ledger SET delta = 1"))
        assert "append-only" in str(excinfo.value)
        await s.rollback()


async def test_ledger_delete_blocked_by_trigger():
    """R-2 append-only：行级 DELETE 同样被触发器拒绝。"""
    # Arrange
    account_id = await _seed_account()
    async with async_session_factory() as s:
        await write_ledger(
            s, account_id=account_id, delta=Decimal("1"), reason="collect"
        )
        await s.commit()

    # Act + Assert
    async with async_session_factory() as s:
        with pytest.raises(DBAPIError) as excinfo:
            await s.execute(text("DELETE FROM scoring.score_ledger"))
        assert "append-only" in str(excinfo.value)
        await s.rollback()


async def _credit_and_commit(account_id: int, delta: Decimal) -> Decimal:
    """独立会话写一笔并入账，返回该行 balance_after（并发测试的最小单元）。"""
    async with async_session_factory() as s:
        result = await write_ledger(
            s, account_id=account_id, delta=delta, reason="collect"
        )
        await s.commit()
        return result.entry.balance_after


async def _wait_writer_holds_lock(task: asyncio.Task, account_id: int) -> None:
    """轮询探测 writer 是否已持有该账号的 xact advisory lock（或已跑完）。

    ``asyncio.gather`` 的启动顺序 ≠ advisory lock 获取顺序；先确认 +10 侧
    已进入锁内临界区，再放 +5 侧并发抢锁，结果才是确定的 {10, 15}。
    探测用 ``pg_try_advisory_xact_lock``：拿不到说明 writer 持锁中；
    拿到了立即 rollback 释放，绝不滞留阻塞 writer。
    """
    async with async_session_factory() as probe:
        while not task.done():
            got_lock = (
                await probe.execute(
                    text(
                        "SELECT pg_try_advisory_xact_lock("
                        "hashtext('scoring.score_ledger'), :account_id)"
                    ),
                    {"account_id": account_id},
                )
            ).scalar()
            await probe.rollback()  # 结束探测事务，释放可能拿到的锁
            if not got_lock:
                return  # writer 已持锁，临界区内
            await asyncio.sleep(0.01)


async def test_concurrent_credit_same_account():
    """并发同账号两笔 credit：advisory lock 串行化，余额链 {10, 15} 无丢失更新。"""
    # Arrange
    account_id = await _seed_account()

    # Act：+10 先进入临界区（探测确认持锁后），+5 并发抢同一把锁
    first_task = asyncio.create_task(_credit_and_commit(account_id, Decimal("10")))
    await _wait_writer_holds_lock(first_task, account_id)
    second_balance = await _credit_and_commit(account_id, Decimal("5"))
    first_balance = await first_task

    # Assert：无论谁先后，两行 balance_after 恰为 10 与 15；最新余额 15
    assert {first_balance, second_balance} == {Decimal("10.00"), Decimal("15.00")}
    assert await _count_entries(account_id) == 2
    async with async_session_factory() as s:
        rows, total = await score_repo.list_entries(
            s, account_id=account_id, page=1, limit=10
        )
        assert total == 2
        assert rows[0].balance_after == Decimal("15.00")  # id DESC 最新在前
        assert rows[1].balance_after == Decimal("10.00")


async def test_list_entries_pagination_and_filters():
    """list_entries：account_id 等值过滤 + since(>=)/until(<) 边界 + page/limit/total。"""
    # Arrange：账号 A 五条（t0..t4 各隔 1h），账号 B 两条
    acc_a = await _seed_account()
    acc_b = await _seed_account()
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    async with async_session_factory() as s:
        for i in range(5):
            s.add(
                ScoreLedger(
                    account_id=acc_a,
                    delta=Decimal("1"),
                    reason="collect",
                    balance_after=Decimal(i + 1),
                    created_at=base + timedelta(hours=i),
                )
            )
        for i in range(2):
            s.add(
                ScoreLedger(
                    account_id=acc_b,
                    delta=Decimal("2"),
                    reason="settle",
                    balance_after=Decimal(2 * (i + 1)),
                    created_at=base + timedelta(hours=i),
                )
            )
        await s.commit()
    t1 = base + timedelta(hours=1)
    t4 = base + timedelta(hours=3)

    # Act + Assert
    async with async_session_factory() as s:
        # account_id 过滤 + 分页：id DESC 最新在前，total 独立于分页
        page1, total = await score_repo.list_entries(
            s, account_id=acc_a, page=1, limit=2
        )
        assert total == 5
        assert [e.created_at for e in page1] == [
            base + timedelta(hours=4),
            base + timedelta(hours=3),
        ]
        page3, _ = await score_repo.list_entries(s, account_id=acc_a, page=3, limit=2)
        assert len(page3) == 1
        assert page3[0].created_at == base  # 最后一页只剩最旧一条

        # since >= t1、until < t4：恰命中 t1、t2（边界：t1 含、t4 不含）
        ranged, total_r = await score_repo.list_entries(
            s, account_id=acc_a, since=t1, until=t4, page=1, limit=10
        )
        assert total_r == 2
        assert [e.created_at for e in ranged] == [
            base + timedelta(hours=2),
            t1,
        ]

        # 不带 account_id：两账号合计 7 条
        _, total_all = await score_repo.list_entries(s, page=1, limit=1)
        assert total_all == 7
