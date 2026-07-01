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


@pytest.mark.asyncio
async def test_exchange_returns_jwt_and_me(client):
    u = uuid.uuid4()
    issue_resp = await client.post(
        "/auth/token", json={"uuid": str(u), "name": "alice"}, headers={"X-Service-Token": "svc"}
    )
    token = issue_resp.json()["login_url"].split("token=")[-1]

    ex = await client.post("/auth/exchange", json={"token": token})
    assert ex.status_code == 200
    body = ex.json()
    assert body["token_type"] == "Bearer"
    assert body["player"]["uuid"] == str(u)

    me = await client.get("/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["name"] == "alice"


@pytest.mark.asyncio
async def test_exchange_one_time(client):
    u = uuid.uuid4()
    token = (await client.post(
        "/auth/token", json={"uuid": str(u), "name": "a"}, headers={"X-Service-Token": "svc"}
    )).json()["login_url"].split("token=")[-1]
    first = await client.post("/auth/exchange", json={"token": token})
    second = await client.post("/auth/exchange", json={"token": token})
    assert first.status_code == 200
    assert second.status_code == 401


@pytest.mark.asyncio
async def test_me_requires_jwt(client):
    assert (await client.get("/me")).status_code == 401
