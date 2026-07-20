import uuid

import pytest

from app.services.auth_service import (
    RateLimiter,
    WindowRateLimiter,
    check_whitelist,
)
from app.models.user import Player
from app.core.db import async_session_factory


@pytest.mark.asyncio
async def test_rate_limiter_blocks_within_window():
    rl = RateLimiter(window_seconds=30)
    u = uuid.uuid4()
    assert rl.check_and_record(u) is True
    assert rl.check_and_record(u) is False   # 窗口内拒绝


@pytest.mark.asyncio
async def test_whitelist_blocks_removed():
    u = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=u, current_name="x", whitelist_state="removed"))
        await s.commit()
        assert await check_whitelist(s, u) is False


# ===== WindowRateLimiter（/auth/login 密码登录双维度限频）=====


def test_window_rate_limiter_allows_up_to_max_then_blocks():
    """窗口内允许 max_count 次，第 max_count+1 次拒绝。"""
    rl = WindowRateLimiter(window_seconds=30, max_count=3)
    assert rl.check_and_record("k") is True
    assert rl.check_and_record("k") is True
    assert rl.check_and_record("k") is True
    assert rl.check_and_record("k") is False   # 第 4 次超限


def test_window_rate_limiter_keys_are_independent():
    """不同 key（如不同 username / IP）计数互不影响。"""
    rl = WindowRateLimiter(window_seconds=30, max_count=1)
    assert rl.check_and_record(("alice", "1.2.3.4")) is True
    assert rl.check_and_record(("bob", "1.2.3.4")) is True    # 不同 credential 独立
    assert rl.check_and_record(("alice", "1.2.3.4")) is False
    assert rl.check_and_record(("alice", "5.6.7.8")) is True  # 不同 IP 独立


def test_window_rate_limiter_reset_clears_count():
    """reset 清除计数后恢复（登录成功调用，避免正常用户累积触发限频）。"""
    rl = WindowRateLimiter(window_seconds=30, max_count=2)
    rl.check_and_record("k")
    rl.check_and_record("k")
    assert rl.check_and_record("k") is False
    rl.reset("k")
    assert rl.check_and_record("k") is True
