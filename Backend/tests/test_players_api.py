"""``GET /players?q=`` 玩家名联想端点测试（协管员授予用）。

覆盖：前缀匹配、大小写不敏感、空 q 返空、LIKE 通配符转义、需 JWT。
"""
import uuid
from datetime import datetime, timezone

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


async def _make_player_with_display(
    name: str, display_name: str | None, *, last_seen_at: datetime | None = None
) -> uuid.UUID:
    """建 Player + WebAccount（display_name 可设），返回 player_uuid（仅作被搜目标，不签 token）。"""
    u = uuid.uuid4()
    async with async_session_factory() as s:
        account = WebAccount(role="user", display_name=display_name)
        s.add(account)
        await s.flush()
        s.add(
            Player(
                uuid=u,
                current_name=name,
                role="user",
                web_account_id=account.id,
                last_seen_at=last_seen_at,
            )
        )
        await s.commit()
    return u


async def _make_unbound_player(name: str) -> uuid.UUID:
    """建未绑 WebAccount 的 Player（web_account_id=None），用于验证被联想过滤。"""
    u = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=u, current_name=name, role="user"))
        await s.commit()
    return u


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


@pytest.mark.asyncio
async def test_search_returns_display_name_when_set(client):
    """account 设了 display_name → 响应带 display_name（#41 三端显示名主源）。"""
    _, bearer = await _make_player("alice")
    await _make_player_with_display("alex", "亚历克斯")
    resp = await client.get("/players?q=al", headers=_auth(bearer))
    body = resp.json()
    alex = next(p for p in body if p["player_name"] == "alex")
    assert alex["display_name"] == "亚历克斯"


@pytest.mark.asyncio
async def test_search_display_name_fallback_to_own_name(client):
    """display_name 为空 → 回退到自身 current_name（多数用户场景）。"""
    _, bearer = await _make_player("alice")
    resp = await client.get("/players?q=al", headers=_auth(bearer))
    body = resp.json()
    alice = next(p for p in body if p["player_name"] == "alice")
    assert alice["display_name"] == "alice"


@pytest.mark.asyncio
async def test_search_display_name_fallback_to_latest_sibling(client):
    """display_name 空 + 同 account 多 UUID → 回退到 last_seen_at 最新 member 的 current_name。"""
    _, bearer = await _make_player("searcher")
    async with async_session_factory() as s:
        account = WebAccount(role="user")  # display_name=None
        s.add(account)
        await s.flush()
        s.add(
            Player(
                uuid=uuid.uuid4(),
                current_name="older",
                role="user",
                web_account_id=account.id,
                last_seen_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        s.add(
            Player(
                uuid=uuid.uuid4(),
                current_name="newer",
                role="user",
                web_account_id=account.id,
                last_seen_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
            )
        )
        await s.commit()
    # 搜 "old" 命中 older；其 display_name 回退 = 最新 member "newer"（非 older 自己）
    resp = await client.get("/players?q=old", headers=_auth(bearer))
    body = resp.json()
    assert len(body) == 1
    assert body[0]["player_name"] == "older"
    assert body[0]["display_name"] == "newer"


@pytest.mark.asyncio
async def test_search_matches_display_name_prefix(client):
    """按昵称前缀也能命中（双向匹配，不止 current_name）。"""
    _, bearer = await _make_player("searcher")
    await _make_player_with_display("carol", "凯萝")  # current_name=carol / display_name=凯萝
    resp = await client.get("/players?q=凯", headers=_auth(bearer))  # 不匹配 carol，仅匹配昵称
    body = resp.json()
    assert len(body) == 1
    assert body[0]["player_name"] == "carol"
    assert body[0]["display_name"] == "凯萝"


@pytest.mark.asyncio
async def test_search_excludes_unbound_players(client):
    """未绑 WebAccount 的玩家不可授予 manager → 不出现在联想（过滤未绑）。"""
    _, bearer = await _make_player("alice")
    await _make_unbound_player("alex_unbound")
    resp = await client.get("/players?q=al", headers=_auth(bearer))
    names = {p["player_name"] for p in resp.json()}
    assert names == {"alice"}  # alex_unbound 被过滤掉
