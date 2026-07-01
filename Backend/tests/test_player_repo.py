import uuid

import pytest

from app.core.db import async_session_factory
from app.repositories.player_repo import get_or_create


@pytest.mark.asyncio
async def test_get_or_create_creates_then_updates_name():
    u = uuid.uuid4()
    async with async_session_factory() as s:
        p1 = await get_or_create(s, u, "alice")
        p2 = await get_or_create(s, u, "alice_renamed")
        await s.commit()
    assert p1.uuid == u
    assert p2.current_name == "alice_renamed"
    assert p1.uuid == p2.uuid   # 同一行
