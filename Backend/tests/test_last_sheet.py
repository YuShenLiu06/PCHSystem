"""last_sheet_id 功能测试。

覆盖：
- 连看 sheet 1、2 后 GET /me/last_sheet 返最新
- csv 分支不更新
- 404 不更新
"""
import uuid

import pytest

import app.api.deps as deps
from app.core.config import get_settings
from tests.conftest import seed_player_with_account


async def _make_player(name: str = "alice", role: str = "user") -> tuple[uuid.UUID, str]:
    """seed player + 临时 WebAccount 并签 JWT，返回 (uuid, bearer)。"""
    return await seed_player_with_account(name=name, role=role)


def _auth(bearer: str) -> dict[str, str]:
    return {"Authorization": bearer}


# ---------- GET /me/last_sheet ----------
@pytest.mark.asyncio
async def test_get_last_sheet_initially_null(client):
    """新玩家首次查看 last_sheet 返回 null。"""
    _, bearer = await _make_player("alice")
    resp = await client.get("/me/last_sheet", headers=_auth(bearer))
    assert resp.status_code == 200
    assert resp.json() == {"sheet_id": None}


@pytest.mark.asyncio
async def test_get_last_sheet_records_latest_view(client):
    """查看 sheet 1 后再查看 sheet 2，last_sheet 应为 2。"""
    u, bearer = await _make_player("alice")
    # 创建两张表
    resp1 = await client.post("/sheets", json={"title": "表1"}, headers=_auth(bearer))
    assert resp1.status_code == 201
    sid1 = resp1.json()["id"]

    resp2 = await client.post("/sheets", json={"title": "表2"}, headers=_auth(bearer))
    assert resp2.status_code == 201
    sid2 = resp2.json()["id"]

    # 查看 sheet 1
    await client.get(f"/sheets/{sid1}", headers=_auth(bearer))
    # last_sheet 应为 1
    resp = await client.get("/me/last_sheet", headers=_auth(bearer))
    assert resp.status_code == 200
    assert resp.json() == {"sheet_id": sid1}

    # 查看 sheet 2
    await client.get(f"/sheets/{sid2}", headers=_auth(bearer))
    # last_sheet 应为 2
    resp = await client.get("/me/last_sheet", headers=_auth(bearer))
    assert resp.status_code == 200
    assert resp.json() == {"sheet_id": sid2}


@pytest.mark.asyncio
async def test_csv_export_does_not_record_last_sheet(client):
    """CSV 导出不应记录 last_sheet_id。"""
    u, bearer = await _make_player("alice")
    # 先建一张表，初始 last_sheet = None
    resp = await client.post("/sheets", json={"title": "表1"}, headers=_auth(bearer))
    sid1 = resp.json()["id"]

    # 先正常查看一次，last_sheet 应为 1
    await client.get(f"/sheets/{sid1}", headers=_auth(bearer))
    resp = await client.get("/me/last_sheet", headers=_auth(bearer))
    assert resp.json() == {"sheet_id": sid1}

    # 创建 sheet 2
    resp2 = await client.post("/sheets", json={"title": "表2"}, headers=_auth(bearer))
    sid2 = resp2.json()["id"]

    # CSV 导出 sheet 2（不应记录）
    resp_csv = await client.get(f"/sheets/{sid2}?format=csv", headers=_auth(bearer))
    assert resp_csv.status_code == 200

    # last_sheet 仍应为 1（未被 CSV 导出覆盖）
    resp = await client.get("/me/last_sheet", headers=_auth(bearer))
    assert resp.json() == {"sheet_id": sid1}


@pytest.mark.asyncio
async def test_404_does_not_record_last_sheet(client):
    """404 查看不应记录 last_sheet_id。"""
    u, bearer = await _make_player("alice")
    # 先建一张表，初始 last_sheet = None
    resp = await client.post("/sheets", json={"title": "表1"}, headers=_auth(bearer))
    sid1 = resp.json()["id"]

    # 先正常查看一次，last_sheet 应为 1
    await client.get(f"/sheets/{sid1}", headers=_auth(bearer))
    resp = await client.get("/me/last_sheet", headers=_auth(bearer))
    assert resp.json() == {"sheet_id": sid1}

    # 查看 999（404，不应记录）
    resp_404 = await client.get("/sheets/999", headers=_auth(bearer))
    assert resp_404.status_code == 404

    # last_sheet 仍应为 1（未被 404 覆盖）
    resp = await client.get("/me/last_sheet", headers=_auth(bearer))
    assert resp.json() == {"sheet_id": sid1}


@pytest.mark.asyncio
async def test_last_sheet_per_player_independent(client):
    """不同玩家的 last_sheet_id 独立。"""
    _, bearer_a = await _make_player("alice")
    _, bearer_b = await _make_player("bob")

    # alice 建表并查看
    resp_a = await client.post("/sheets", json={"title": "Alice表"}, headers=_auth(bearer_a))
    sid_a = resp_a.json()["id"]
    await client.get(f"/sheets/{sid_a}", headers=_auth(bearer_a))

    # bob 建表并查看
    resp_b = await client.post("/sheets", json={"title": "Bob表"}, headers=_auth(bearer_b))
    sid_b = resp_b.json()["id"]
    await client.get(f"/sheets/{sid_b}", headers=_auth(bearer_b))

    # alice 的 last_sheet 应为她自己的表
    resp = await client.get("/me/last_sheet", headers=_auth(bearer_a))
    assert resp.json() == {"sheet_id": sid_a}

    # bob 的 last_sheet 应为他自己的表
    resp = await client.get("/me/last_sheet", headers=_auth(bearer_b))
    assert resp.json() == {"sheet_id": sid_b}


@pytest.mark.asyncio
async def test_get_last_sheet_via_service_token_channel(client, monkeypatch):
    """MCDR service-token + X-Player-UUID 代玩家通道也应能读 last_sheet（RS-8 双通道）。

    回归保护：防未来收紧 header 校验时 MCDR 代玩家通道（!!sheet 的实际链路）静默断裂。
    """
    # 注入 service token（函数级，monkeypatch 自动还原，不污染同模块 JWT 用例）
    monkeypatch.setattr(deps, "_settings", get_settings())
    deps._settings.mcdr_service_token = "svc"

    u, bearer = await _make_player("alice")
    # JWT 通道建表 + 查看，记录 last_sheet_id
    resp = await client.post("/sheets", json={"title": "表1"}, headers=_auth(bearer))
    sid = resp.json()["id"]
    await client.get(f"/sheets/{sid}", headers=_auth(bearer))

    # 改走 service-token + X-Player-UUID（无 Authorization）读 last_sheet
    svc_headers = {"X-Service-Token": "svc", "X-Player-UUID": str(u)}
    resp = await client.get("/me/last_sheet", headers=svc_headers)
    assert resp.status_code == 200
    assert resp.json() == {"sheet_id": sid}
