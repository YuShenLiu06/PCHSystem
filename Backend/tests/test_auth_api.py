import uuid

import pytest

import app.api.deps as deps
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _svc_token(monkeypatch):
    deps._settings = get_settings()
    deps._settings.mcdr_service_token = "svc"


@pytest.mark.asyncio
async def test_auth_token_success(client):
    u = uuid.uuid4()
    resp = await client.post(
        "/auth/token",
        json={"uuid": str(u), "name": "alice"},
        headers={"X-Service-Token": "svc"},
    )
    assert resp.status_code == 200
    assert "/auth?token=" in resp.json()["login_url"]


@pytest.mark.asyncio
async def test_auth_token_rate_limited(client):
    u = uuid.uuid4()
    headers = {"X-Service-Token": "svc"}
    first = await client.post("/auth/token", json={"uuid": str(u), "name": "a"}, headers=headers)
    second = await client.post("/auth/token", json={"uuid": str(u), "name": "a"}, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 429


@pytest.mark.asyncio
async def test_auth_token_blocked_for_removed(client):
    # 直接写一个 removed 玩家
    from app.core.db import async_session_factory
    from app.models.user import Player
    u = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=u, current_name="ghost", whitelist_state="removed"))
        await s.commit()
    resp = await client.post(
        "/auth/token",
        json={"uuid": str(u), "name": "ghost"},
        headers={"X-Service-Token": "svc"},
    )
    assert resp.status_code == 403
