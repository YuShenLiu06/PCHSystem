import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.db import async_session_factory
from app.models.user import AuthToken
from app.repositories.auth_token_repo import exchange, issue
from app.repositories.player_repo import get_or_create


async def _seed_player(name="bob"):
    u = uuid.uuid4()
    async with async_session_factory() as s:
        await get_or_create(s, u, name)
        await s.commit()
    return u


@pytest.mark.asyncio
async def test_issue_and_exchange_success():
    u = await _seed_player()
    async with async_session_factory() as s:
        tok, _ = await issue(s, u, issued_ip="1.1.1.1")
        await s.commit()
        result = await exchange(s, tok.token, exchanged_ip="2.2.2.2")
        await s.commit()
    assert result is not None
    assert result.uuid == u


@pytest.mark.asyncio
async def test_exchange_is_one_time():
    u = await _seed_player()
    async with async_session_factory() as s:
        tok, _ = await issue(s, u)
        await s.commit()
        await exchange(s, tok.token)
        second = await exchange(s, tok.token)
        await s.commit()
    assert second is None   # 已 used，拒绝重放


@pytest.mark.asyncio
async def test_exchange_rejects_expired():
    u = await _seed_player()
    async with async_session_factory() as s:
        tok, _ = await issue(s, u)
        # 直接把过期时间改到过去
        tok.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await s.commit()
        assert await exchange(s, tok.token) is None
