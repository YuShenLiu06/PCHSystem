import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

import app.api.deps as deps
from app.core.config import get_settings
from app.core.db import async_session_factory
from app.models.user import AuthToken
from app.repositories.auth_token_repo import issue as issue_repo


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
    assert body["player"]["name"] == "alice"
    # account 块含 role（契约：AccountBrief {id, is_temporary, username, role}）
    assert body["account"]["is_temporary"] is True
    assert body["account"]["role"] == "user"

    me = await client.get("/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    me_body = me.json()
    # /me shape：{account, players, active_uuid}
    assert me_body["active_uuid"] == str(u)
    assert me_body["account"]["is_temporary"] is True
    assert any(p["uuid"] == str(u) and p["name"] == "alice" for p in me_body["players"])


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


@pytest.mark.asyncio
async def test_issue_revokes_previous_unused_token(client):
    # 同 UUID 再次 issue：旧 token 应被 revoke（revoked_at 置位），exchange 返回 401
    from app.models.user import Player

    u = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=u, current_name="alice"))
        await s.commit()

    async with async_session_factory() as s:
        token_row, revoked_first = await issue_repo(s, u)
        await s.commit()
    assert revoked_first == 0
    first_token = str(token_row.token)

    # 限流不影响 repo 层，直接再 issue 一次
    async with async_session_factory() as s:
        _, revoked_second = await issue_repo(s, u)
        await s.commit()
    assert revoked_second == 1

    # 旧 token 现在 exchange 应失败
    ex = await client.post("/auth/exchange", json={"token": first_token})
    assert ex.status_code == 401


@pytest.mark.asyncio
async def test_used_token_not_revoked_on_reissue(client):
    # 已 used 的 token，在新一次 issue 后 used_at 应保持不变（revoke 只动 used_at is null）
    u = uuid.uuid4()
    headers = {"X-Service-Token": "svc"}
    resp = await client.post(
        "/auth/token", json={"uuid": str(u), "name": "alice"}, headers=headers
    )
    token_str = resp.json()["login_url"].split("token=")[-1]

    # exchange 使其 used_at 被置位
    ex = await client.post("/auth/exchange", json={"token": token_str})
    assert ex.status_code == 200

    token_uuid = uuid.UUID(token_str)
    async with async_session_factory() as s:
        used_before = (
            await s.execute(select(AuthToken).where(AuthToken.token == token_uuid))
        ).scalar_one()
        used_at_before = used_before.used_at
        assert used_at_before is not None

        # 再 issue：revoke 不应影响已 used 的行
        _, revoked = await issue_repo(s, u)
        await s.commit()
    assert revoked == 0

    async with async_session_factory() as s:
        used_after = (
            await s.execute(select(AuthToken).where(AuthToken.token == token_uuid))
        ).scalar_one()
    assert used_after.used_at == used_at_before
    assert used_after.revoked_at is None


@pytest.mark.asyncio
async def test_revoked_token_exchange_fails(client):
    # 手动把 token 的 revoked_at 置位，exchange 应 401
    u = uuid.uuid4()
    headers = {"X-Service-Token": "svc"}
    resp = await client.post(
        "/auth/token", json={"uuid": str(u), "name": "alice"}, headers=headers
    )
    token_str = resp.json()["login_url"].split("token=")[-1]

    token_uuid = uuid.UUID(token_str)
    async with async_session_factory() as s:
        row = (
            await s.execute(select(AuthToken).where(AuthToken.token == token_uuid))
        ).scalar_one()
        row.revoked_at = datetime.now(timezone.utc)
        await s.commit()

    ex = await client.post("/auth/exchange", json={"token": token_str})
    assert ex.status_code == 401
