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
# issue #8 复现样本（Create 蓝图 .nbt 经投影转出的 .litematic，缺可选 NBT 键）。
_BLUEPRINT_FIXTURE = Path(__file__).parent / "fixtures" / "1103.litematic"


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


@pytest.mark.asyncio
async def test_parse_litematic_garbage_returns_friendly_message(client):
    # 需求 #1：硬解析失败时返回玩家可读中文文案，不泄露内部异常串（如 'PendingBlockTicks'）。
    _, bearer = await _make_player("p4")
    resp = await client.post(
        "/parsing/litematic",
        files={"file": ("bad.litematic", b"not nbt at all")},
        headers=_auth(bearer),
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "无法解析该投影文件" in detail
    assert "PendingBlockTicks" not in detail  # 不泄露内部键名


@pytest.mark.asyncio
async def test_parse_litematic_handles_create_blueprint(client):
    # issue #8 端到端：Create 蓝图转出的 .litematic（缺可选 NBT 键）应正常解析、Create 方块存活。
    _, bearer = await _make_player("p5")
    data = _BLUEPRINT_FIXTURE.read_bytes()
    resp = await client.post(
        "/parsing/litematic",
        files={"file": ("1103.litematic", data, "application/octet-stream")},
        headers=_auth(bearer),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["meta"]["region_count"] == 1
    assert body["meta"]["total_blocks"] == 121
    ids = {b["item_id"] for b in body["blocks"]}
    assert "create:belt" in ids
    assert "create:mechanical_crafter" in ids
