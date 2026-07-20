"""``GET /players?q=`` 玩家名联想端点测试（协管员授予用）。

覆盖：前缀匹配、大小写不敏感、空 q 返空、LIKE 通配符转义、需 JWT。
"""
import uuid

import pytest

import app.api.deps as deps
from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.jwt import create_access_token
from app.models.user import Player, WebAccount


@pytest.fixture(autouse=True)
def _svc_token(monkeypatch):
    deps._settings = get_settings()
    deps._settings.mcdr_service_token = "svc"


async def _make_player(name: str) -> tuple[uuid.UUID, str]:
    # HEAD JWT 契约：sub=account_id（必须先建 WebAccount 再签 token）。
    u = uuid.uuid4()
    async with async_session_factory() as s:
        account = WebAccount(role="user")
        s.add(account)
        await s.flush()
        s.add(Player(uuid=u, current_name=name, role="user", web_account_id=account.id))
        await s.commit()
        account_id = account.id
    return u, f"Bearer {create_access_token(account_id, 'user', active_uuid=u)}"


def _auth(bearer: str) -> dict[str, str]:
    return {"Authorization": bearer}


@pytest.mark.asyncio
async def test_search_by_prefix(client):
    _, bearer = await _make_player("alice")
    await _make_player("alex")
    await _make_player("bob")
    resp = await client.get("/players?q=al", headers=_auth(bearer))
    assert resp.status_code == 200
    names = {p["player_name"] for p in resp.json()}
    assert names == {"alice", "alex"}  # 前缀 al 命中两个，不含 bob


@pytest.mark.asyncio
async def test_search_case_insensitive(client):
    _, bearer = await _make_player("Alice")
    resp = await client.get("/players?q=AL", headers=_auth(bearer))
    assert {p["player_name"] for p in resp.json()} == {"Alice"}


@pytest.mark.asyncio
async def test_search_empty_q_returns_empty(client):
    _, bearer = await _make_player("alice")
    resp = await client.get("/players?q=", headers=_auth(bearer))
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_search_requires_jwt(client):
    assert (await client.get("/players?q=a")).status_code == 401


@pytest.mark.asyncio
async def test_search_underscore_literal_not_wildcard(client):
    """玩家名含下划线时，下划线作为字面量匹配（不被当 LIKE 单字符通配）。"""
    _, bearer = await _make_player("player_one")
    await _make_player("playerXone")  # 若下划线被当通配符会误命中
    resp = await client.get("/players?q=player_", headers=_auth(bearer))
    names = {p["player_name"] for p in resp.json()}
    assert names == {"player_one"}


@pytest.mark.asyncio
async def test_search_returns_uuid_and_name(client):
    target_u, bearer = await _make_player("carol")
    resp = await client.get("/players?q=car", headers=_auth(bearer))
    body = resp.json()
    assert len(body) == 1
    assert body[0]["player_uuid"] == str(target_u)
    assert body[0]["player_name"] == "carol"
