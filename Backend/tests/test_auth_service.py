import uuid

import pytest

from app.services.auth_service import RateLimiter, check_whitelist
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
