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


@pytest.mark.asyncio
async def test_from_items_passes_registry_id_through(client):
    """items 带 registry_id → 行落库 + RowDetail 回显；仅 registry_id 时后端翻译补中文名。"""
    _, bearer = await _make_player("leader")
    resp = await client.post(
        "/sheets/from-items",
        json={
            "title": "投影",
            "items": [
                # 同时给中文名 + registry_id（投影解析路径）
                {"item_name": "石头", "registry_id": "minecraft:stone", "need_qty": 64},
                # 仅 registry_id（MCDR addhand 风格）→ 后端翻译表补默认中文名
                {"registry_id": "minecraft:oak_planks", "need_qty": 32},
            ],
        },
        headers=_auth(bearer),
    )
    assert resp.status_code == 201, resp.text
    rows = resp.json()["rows"]
    by_reg = {r["registry_id"]: r for r in rows}
    # 带中文名的行：registry_id 透传
    assert by_reg["minecraft:stone"]["item_name"] == "石头"
    # 仅 registry_id 的行：后端翻译补名（命中→中文；未命中→回退 registry_id 本身，均非空）
    assert by_reg["minecraft:oak_planks"]["item_name"]


@pytest.mark.asyncio
async def test_from_items_requires_name_or_registry_per_item(client):
    """单条 item 既无 item_name 又无 registry_id → 422（model_validator）。"""
    _, bearer = await _make_player("leader")
    resp = await client.post(
        "/sheets/from-items",
        json={"title": "x", "items": [{"need_qty": 1}]},  # 缺 name 与 registry_id
        headers=_auth(bearer),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_from_items_rejects_row_id_in_item(client):
    """issue #20 回归：SheetItemIn 继承了 row_id，但批量建行每条均新建，row_id 无意义。

    旧 bug：携带 row_id 会绕过「name/registry 至少一个」校验（该豁免仅服务更新路径），
    使 item_name=None & registry_id=None 直抵 _resolve_item_name 的防御点 → AssertionError → 500。
    现 SheetItemIn._forbid_row_id_in_batch_create 拒绝 → 干净 422（而非 500）。
    """
    _, bearer = await _make_player("leader")
    # 仅 row_id（既无 name 也无 registry）—— 旧实现会绕过校验后崩 500
    resp = await client.post(
        "/sheets/from-items",
        json={"title": "x", "items": [{"row_id": 1}]},
        headers=_auth(bearer),
    )
    assert resp.status_code == 422, resp.text
    # 带 name 也带 row_id —— 同样应被拒（row_id 在此场景无意义）
    resp2 = await client.post(
        "/sheets/from-items",
        json={"title": "y", "items": [{"row_id": 1, "item_name": "石头", "need_qty": 1}]},
        headers=_auth(bearer),
    )
    assert resp2.status_code == 422, resp2.text
