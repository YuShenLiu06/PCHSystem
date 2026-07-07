"""POST /parsing/litematic 端点测试（multipart 上传 → 翻译预览）。

复用 test_sheets_api.py 的 _svc_token / _make_player / _auth 模式。
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

import app.api.deps as deps
from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.jwt import create_access_token
from app.models.user import Player

_FIXTURE = Path(__file__).parent / "fixtures" / "机械动力仓库_1.litematic"
_NBT_FIXTURE = Path(__file__).parent / "fixtures" / "create_blueprint_sample.nbt"


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
async def test_parse_litematic_returns_translated_preview(client):
    _, bearer = await _make_player("parser")
    data = _FIXTURE.read_bytes()
    resp = await client.post(
        "/parsing/litematic",
        files={"file": ("机械动力仓库_1.litematic", data, "application/octet-stream")},
        headers=_auth(bearer),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["meta"]["schematic_name"] == "机械动力仓库_1"
    assert body["meta"]["total_blocks"] == 592
    assert body["meta"]["region_count"] == 1

    assert len(body["blocks"]) == 13
    vault = next(b for b in body["blocks"] if b["item_id"] == "create:item_vault")
    assert vault["item_name"] == "物品保险库"  # Create 官方中文
    assert vault["count"] == 486

    assert body["container_items"] == []
    assert body["untranslated"] == []


@pytest.mark.asyncio
async def test_parse_litematic_requires_jwt(client):
    resp = await client.post(
        "/parsing/litematic",
        files={"file": ("x.litematic", b"junk")},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_parse_litematic_rejects_wrong_extension(client):
    _, bearer = await _make_player("p2")
    resp = await client.post(
        "/parsing/litematic",
        files={"file": ("not-a-litematic.zip", b"pk\x03\x04")},
        headers=_auth(bearer),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_parse_litematic_rejects_garbage(client):
    _, bearer = await _make_player("p3")
    resp = await client.post(
        "/parsing/litematic",
        files={"file": ("bad.litematic", b"not nbt at all")},
        headers=_auth(bearer),
    )
    assert resp.status_code == 422


# ---------- POST /parsing/nbt ----------
@pytest.mark.asyncio
async def test_parse_nbt_returns_translated_preview(client):
    """上传 Create 蓝图 .nbt → 200 + 翻译预览（复用 litematic 链路）。"""
    _, bearer = await _make_player("parser_nbt")
    data = _NBT_FIXTURE.read_bytes()
    resp = await client.post(
        "/parsing/nbt",
        files={"file": ("create_blueprint_sample.nbt", data, "application/octet-stream")},
        headers=_auth(bearer),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["meta"]["schematic_name"] == "create_blueprint_sample"
    assert body["meta"]["total_blocks"] == 132
    assert body["meta"]["region_count"] == 1

    assert len(body["blocks"]) == 15
    casing = next(b for b in body["blocks"] if b["item_id"] == "create:andesite_casing")
    assert casing["item_name"] == "安山机壳"  # Create 6.0.8 官方中文（block.create.andesite_casing）
    assert casing["count"] == 38

    assert body["container_items"] == []
    assert body["untranslated"] == []


@pytest.mark.asyncio
async def test_parse_nbt_requires_jwt(client):
    """无 JWT → 401（复用 litematic 鉴权逻辑）。"""
    resp = await client.post(
        "/parsing/nbt",
        files={"file": ("x.nbt", b"junk")},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_parse_nbt_rejects_wrong_extension(client):
    """上传 .litematic 扩展名 → 400（扩展名守卫）。"""
    _, bearer = await _make_player("p_nbt_ext")
    resp = await client.post(
        "/parsing/nbt",
        files={"file": ("not-a-nbt.litematic", b"junk")},
        headers=_auth(bearer),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_parse_nbt_rejects_garbage(client):
    """上传垃圾字节 → 422（NbtParseError → api 翻译）。"""
    _, bearer = await _make_player("p_nbt_garbage")
    resp = await client.post(
        "/parsing/nbt",
        files={"file": ("bad.nbt", b"not nbt at all")},
        headers=_auth(bearer),
    )
    assert resp.status_code == 422
