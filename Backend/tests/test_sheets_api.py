"""sheets API 端点测试（B3）。

覆盖 API 契约表全路径 + 权限分支（D-3）：
- 读：JWT 已登录可读所有表
- 写：owner_uuid 或 admin/owner 角色
- CSV 全量导出：service token

复用 test_auth_api.py 的 _svc_token fixture 模式（注入 service token 到 deps._settings）。
JWT 用 app.core.jwt.create_access_token 直接签发（不经 /auth/exchange）。
"""
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
    """seed player 并签 JWT，返回 (uuid, bearer)。"""
    u = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=u, current_name=name, role=role))
        await s.commit()
    token = create_access_token(u, role)
    return u, f"Bearer {token}"


def _auth(bearer: str) -> dict[str, str]:
    return {"Authorization": bearer}


# ---------- POST /sheets ----------
@pytest.mark.asyncio
async def test_create_sheet_returns_201_with_owner(client):
    u, bearer = await _make_player()
    resp = await client.post("/sheets", json={"title": "建材表"}, headers=_auth(bearer))
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "建材表"
    assert body["owner_uuid"] == str(u)
    assert body["rows"] == []
    assert body["id"] is not None


@pytest.mark.asyncio
async def test_create_sheet_requires_jwt(client):
    assert (await client.post("/sheets", json={"title": "x"})).status_code == 401


@pytest.mark.asyncio
async def test_create_sheet_validates_title(client):
    _, bearer = await _make_player()
    resp = await client.post("/sheets", json={"title": ""}, headers=_auth(bearer))
    assert resp.status_code == 422


# ---------- GET /sheets ----------
@pytest.mark.asyncio
async def test_list_sheets_shows_all_including_others(client):
    owner_a, bearer_a = await _make_player("alice")
    owner_b, bearer_b = await _make_player("bob")
    # alice 建一张，bob 建一张
    await client.post("/sheets", json={"title": "A"}, headers=_auth(bearer_a))
    await client.post("/sheets", json={"title": "B"}, headers=_auth(bearer_b))

    # alice 登录能看到两张
    resp = await client.get("/sheets", headers=_auth(bearer_a))
    assert resp.status_code == 200
    titles = {s["title"] for s in resp.json()}
    assert titles == {"A", "B"}


@pytest.mark.asyncio
async def test_list_sheets_owner_me_filter(client):
    owner_a, bearer_a = await _make_player("alice")
    _, bearer_b = await _make_player("bob")
    await client.post("/sheets", json={"title": "A"}, headers=_auth(bearer_a))
    await client.post("/sheets", json={"title": "B"}, headers=_auth(bearer_b))

    resp = await client.get("/sheets?owner=me", headers=_auth(bearer_a))
    assert resp.status_code == 200
    assert {s["title"] for s in resp.json()} == {"A"}


# ---------- GET /sheets/{id} ----------
@pytest.mark.asyncio
async def test_get_sheet_detail_with_rows(client):
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    await client.put(
        f"/sheets/{sid}/rows",
        json={"item_name": "iron", "need_qty": 64, "done_flag": 0, "sort_order": 0},
        headers=_auth(bearer),
    )
    resp = await client.get(f"/sheets/{sid}", headers=_auth(bearer))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["item_name"] == "iron"


@pytest.mark.asyncio
async def test_get_sheet_missing_returns_404(client):
    _, bearer = await _make_player()
    assert (await client.get("/sheets/999999", headers=_auth(bearer))).status_code == 404


@pytest.mark.asyncio
async def test_get_sheet_csv_format(client):
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    await client.put(
        f"/sheets/{sid}/rows",
        json={"item_name": "iron", "need_qty": 64, "done_flag": 0, "sort_order": 0},
        headers=_auth(bearer),
    )
    resp = await client.get(f"/sheets/{sid}?format=csv", headers=_auth(bearer))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.strip().splitlines()
    assert lines[0] == "sheet_id,item_name,need_qty,done_flag,sort_order"
    assert lines[1] == f"{sid},iron,64,0,0"


# ---------- PATCH /sheets/{id} ----------
@pytest.mark.asyncio
async def test_patch_sheet_owner_ok(client):
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    resp = await client.patch(
        f"/sheets/{sid}", json={"title": "S2"}, headers=_auth(bearer)
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "S2"


@pytest.mark.asyncio
async def test_patch_sheet_non_owner_forbidden(client):
    _, bearer_a = await _make_player("alice")
    _, bearer_b = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_a))).json()["id"]
    resp = await client.patch(
        f"/sheets/{sid}", json={"title": "X"}, headers=_auth(bearer_b)
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_patch_sheet_admin_ok_even_not_owner(client):
    _, bearer_owner = await _make_player("alice", role="user")
    _, bearer_admin = await _make_player("admin", role="admin")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    resp = await client.patch(
        f"/sheets/{sid}", json={"title": "admin改"}, headers=_auth(bearer_admin)
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "admin改"


@pytest.mark.asyncio
async def test_patch_sheet_owner_role_ok(client):
    _, bearer_owner = await _make_player("alice", role="user")
    _, bearer_super = await _make_player("super", role="owner")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    resp = await client.patch(
        f"/sheets/{sid}", json={"title": "super改"}, headers=_auth(bearer_super)
    )
    assert resp.status_code == 200


# ---------- DELETE /sheets/{id} ----------
@pytest.mark.asyncio
async def test_delete_sheet_cascades_rows(client):
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    await client.put(
        f"/sheets/{sid}/rows",
        json={"item_name": "iron", "need_qty": 1, "done_flag": 0, "sort_order": 0},
        headers=_auth(bearer),
    )
    resp = await client.delete(f"/sheets/{sid}", headers=_auth(bearer))
    assert resp.status_code == 204
    # 再 GET 应 404
    assert (await client.get(f"/sheets/{sid}", headers=_auth(bearer))).status_code == 404


@pytest.mark.asyncio
async def test_delete_sheet_non_owner_forbidden(client):
    _, bearer_a = await _make_player("alice")
    _, bearer_b = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_a))).json()["id"]
    assert (
        await client.delete(f"/sheets/{sid}", headers=_auth(bearer_b))
    ).status_code == 403


# ---------- PUT /sheets/{id}/rows ----------
@pytest.mark.asyncio
async def test_upsert_row_create_then_update(client):
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]

    create_resp = await client.put(
        f"/sheets/{sid}/rows",
        json={"item_name": "iron", "need_qty": 64, "done_flag": 0, "sort_order": 0},
        headers=_auth(bearer),
    )
    assert create_resp.status_code == 200
    first = create_resp.json()
    assert first["item_name"] == "iron"
    assert first["need_qty"] == 64

    # 同名再次 PUT → 更新而非 409
    update_resp = await client.put(
        f"/sheets/{sid}/rows",
        json={"item_name": "iron", "need_qty": 192, "done_flag": 1, "sort_order": 5},
        headers=_auth(bearer),
    )
    assert update_resp.status_code == 200
    second = update_resp.json()
    assert second["id"] == first["id"]
    assert second["need_qty"] == 192
    assert second["done_flag"] == 1

    # 仍是单行
    detail = (await client.get(f"/sheets/{sid}", headers=_auth(bearer))).json()
    assert len(detail["rows"]) == 1


@pytest.mark.asyncio
async def test_upsert_row_non_owner_forbidden(client):
    _, bearer_a = await _make_player("alice")
    _, bearer_b = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_a))).json()["id"]
    resp = await client.put(
        f"/sheets/{sid}/rows",
        json={"item_name": "iron", "need_qty": 1, "done_flag": 0, "sort_order": 0},
        headers=_auth(bearer_b),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_upsert_row_to_missing_sheet_404(client):
    _, bearer = await _make_player()
    resp = await client.put(
        "/sheets/999999/rows",
        json={"item_name": "iron", "need_qty": 1, "done_flag": 0, "sort_order": 0},
        headers=_auth(bearer),
    )
    assert resp.status_code == 404


# ---------- DELETE /sheets/{id}/rows/{row_id} ----------
@pytest.mark.asyncio
async def test_delete_row(client):
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    rid = (
        await client.put(
            f"/sheets/{sid}/rows",
            json={"item_name": "iron", "need_qty": 1, "done_flag": 0, "sort_order": 0},
            headers=_auth(bearer),
        )
    ).json()["id"]
    resp = await client.delete(f"/sheets/{sid}/rows/{rid}", headers=_auth(bearer))
    assert resp.status_code == 204
    detail = (await client.get(f"/sheets/{sid}", headers=_auth(bearer))).json()
    assert detail["rows"] == []


@pytest.mark.asyncio
async def test_delete_row_non_owner_forbidden(client):
    _, bearer_a = await _make_player("alice")
    _, bearer_b = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_a))).json()["id"]
    rid = (
        await client.put(
            f"/sheets/{sid}/rows",
            json={"item_name": "iron", "need_qty": 1, "done_flag": 0, "sort_order": 0},
            headers=_auth(bearer_a),
        )
    ).json()["id"]
    assert (
        await client.delete(f"/sheets/{sid}/rows/{rid}", headers=_auth(bearer_b))
    ).status_code == 403


# ---------- GET /sheets/export (service token) ----------
@pytest.mark.asyncio
async def test_export_all_requires_service_token(client):
    # JWT 不应能访问全量导出
    _, bearer = await _make_player()
    resp = await client.get("/sheets/export", headers=_auth(bearer))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_export_all_returns_csv(client):
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    await client.put(
        f"/sheets/{sid}/rows",
        json={"item_name": "iron", "need_qty": 64, "done_flag": 0, "sort_order": 0},
        headers=_auth(bearer),
    )

    resp = await client.get("/sheets/export", headers={"X-Service-Token": "svc"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.strip().splitlines()
    assert lines[0] == "sheet_id,item_name,need_qty,done_flag,sort_order"
    assert f"{sid},iron,64,0,0" in lines[1:]
