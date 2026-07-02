"""POST /sheets/from-items 端点测试（批量建表 + 行，mode 默认 lock）。

复用 test_sheets_api.py 的 _svc_token / _make_player / _auth 模式。
"""
from __future__ import annotations

import uuid

import pytest

import app.api.deps as deps
from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.jwt import create_access_token
from app.models.user import Player


@pytest.fixture(autouse=True)
def _svc_token(monkeypatch):
    deps._settings = get_settings()
    deps._settings.mcdr_service_token = "svc"


async def _make_player(name: str = "alice", role: str = "user") -> tuple[uuid.UUID, str]:
    u = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=u, current_name=name, role=role))
        await s.commit()
    return u, f"Bearer {create_access_token(u, role)}"


def _auth(bearer: str) -> dict[str, str]:
    return {"Authorization": bearer}


@pytest.mark.asyncio
async def test_from_items_creates_sheet_with_rows_default_lock(client):
    _, bearer = await _make_player("leader")
    resp = await client.post(
        "/sheets/from-items",
        json={
            "title": "建材·方块",
            "items": [
                {"item_name": "石头", "need_qty": 100, "mode": 0, "sort_order": 0},
                {"item_name": "传送带", "need_qty": 32, "sort_order": 1},  # mode 省略 → 默认 0
            ],
        },
        headers=_auth(bearer),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "建材·方块"
    assert body["owner_name"] == "leader"
    assert len(body["rows"]) == 2

    by_name = {r["item_name"]: r for r in body["rows"]}
    assert by_name["石头"]["need_qty"] == 100
    assert by_name["石头"]["mode"] == 0  # 默认 lock
    assert by_name["石头"]["status"] == "open"
    assert by_name["传送带"]["mode"] == 0  # 省略也落 lock
    assert by_name["传送带"]["sort_order"] == 1


@pytest.mark.asyncio
async def test_from_items_empty_items_creates_empty_sheet(client):
    _, bearer = await _make_player("l2")
    resp = await client.post(
        "/sheets/from-items",
        json={"title": "空表", "items": []},
        headers=_auth(bearer),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "空表"
    assert body["rows"] == []


@pytest.mark.asyncio
async def test_from_items_requires_jwt(client):
    resp = await client.post("/sheets/from-items", json={"title": "x", "items": []})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_from_items_validates_empty_title(client):
    _, bearer = await _make_player("l3")
    resp = await client.post(
        "/sheets/from-items",
        json={"title": "", "items": []},
        headers=_auth(bearer),
    )
    assert resp.status_code == 422
