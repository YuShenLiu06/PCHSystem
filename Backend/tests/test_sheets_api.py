"""sheets API 端点测试。

覆盖 API 契约表全路径 + 权限分支（spec §5.3）：
- 读：JWT 已登录可读所有表
- 写表/行 upsert/删：owner_uuid 或 admin/owner 角色
- claim：任意登录玩家；delivery：认领人；release：认领人/拥有者；reject：认领人/拥有者
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


async def _upsert_row(client, bearer: str, sid: int, item: str = "iron", need: int = 64, mode: int = 0) -> dict:
    resp = await client.put(
        f"/sheets/{sid}/rows",
        json={"item_name": item, "need_qty": need, "mode": mode, "sort_order": 0},
        headers=_auth(bearer),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------- POST /sheets ----------
@pytest.mark.asyncio
async def test_create_sheet_returns_201_with_owner_name(client):
    u, bearer = await _make_player("alice")
    resp = await client.post("/sheets", json={"title": "建材表"}, headers=_auth(bearer))
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "建材表"
    assert body["owner_uuid"] == str(u)
    assert body["owner_name"] == "alice"
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

    # alice 登录能看到两张，且 owner_name 等于对应游戏名
    resp = await client.get("/sheets", headers=_auth(bearer_a))
    assert resp.status_code == 200
    by_title = {s["title"]: s for s in resp.json()}
    assert set(by_title) == {"A", "B"}
    assert by_title["A"]["owner_name"] == "alice"
    assert by_title["B"]["owner_name"] == "bob"


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
    _, bearer = await _make_player("alice")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    await _upsert_row(client, bearer, sid)
    resp = await client.get(f"/sheets/{sid}", headers=_auth(bearer))
    assert resp.status_code == 200
    body = resp.json()
    assert body["owner_name"] == "alice"
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["item_name"] == "iron"
    # RowDetail 含新字段，无 done_flag
    assert row["mode"] == 0
    assert row["status"] == "open"
    assert row["claimant_uuid"] is None
    assert row["claimant_name"] is None
    assert row["delivered_qty"] == 0
    assert "done_flag" not in row


@pytest.mark.asyncio
async def test_get_sheet_missing_returns_404(client):
    _, bearer = await _make_player()
    assert (await client.get("/sheets/999999", headers=_auth(bearer))).status_code == 404


@pytest.mark.asyncio
async def test_get_sheet_csv_format(client):
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    await _upsert_row(client, bearer, sid, "iron", 64, 0)
    resp = await client.get(f"/sheets/{sid}?format=csv", headers=_auth(bearer))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.strip().splitlines()
    assert lines[0] == (
        "sheet_id,item_name,need_qty,mode,status,claimant_uuid,delivered_qty,sort_order"
    )
    assert lines[1] == f"{sid},iron,64,0,open,,0,0"


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
    await _upsert_row(client, bearer, sid, "iron", 1, 0)
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

    first = await _upsert_row(client, bearer, sid, "iron", 64, 0)
    assert first["item_name"] == "iron"
    assert first["need_qty"] == 64
    assert first["mode"] == 0

    # 同名再次 PUT → 更新而非 409
    update_resp = await client.put(
        f"/sheets/{sid}/rows",
        json={"item_name": "iron", "need_qty": 192, "mode": 1, "sort_order": 5},
        headers=_auth(bearer),
    )
    assert update_resp.status_code == 200
    second = update_resp.json()
    assert second["id"] == first["id"]
    assert second["need_qty"] == 192
    assert second["mode"] == 1

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
        json={"item_name": "iron", "need_qty": 1, "mode": 0, "sort_order": 0},
        headers=_auth(bearer_b),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_upsert_row_to_missing_sheet_404(client):
    _, bearer = await _make_player()
    resp = await client.put(
        "/sheets/999999/rows",
        json={"item_name": "iron", "need_qty": 1, "mode": 0, "sort_order": 0},
        headers=_auth(bearer),
    )
    assert resp.status_code == 404


# ---------- DELETE /sheets/{id}/rows/{row_id} ----------
@pytest.mark.asyncio
async def test_delete_row(client):
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    rid = (await _upsert_row(client, bearer, sid, "iron", 1, 0))["id"]
    resp = await client.delete(f"/sheets/{sid}/rows/{rid}", headers=_auth(bearer))
    assert resp.status_code == 204
    detail = (await client.get(f"/sheets/{sid}", headers=_auth(bearer))).json()
    assert detail["rows"] == []


@pytest.mark.asyncio
async def test_delete_row_non_owner_forbidden(client):
    _, bearer_a = await _make_player("alice")
    _, bearer_b = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_a))).json()["id"]
    rid = (await _upsert_row(client, bearer_a, sid, "iron", 1, 0))["id"]
    assert (
        await client.delete(f"/sheets/{sid}/rows/{rid}", headers=_auth(bearer_b))
    ).status_code == 403


# ---------- 行认领协作（spec §5.4） ----------
@pytest.mark.asyncio
async def test_claim_row_any_player_succeeds(client):
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, 0))["id"]

    resp = await client.post(
        f"/sheets/{sid}/rows/{rid}/claim", headers=_auth(bearer_bob)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "claimed"
    assert body["claimant_name"] == "bob"
    assert body["delivered_qty"] == 0


@pytest.mark.asyncio
async def test_claim_done_row_conflict(client):
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, 0))["id"]
    # bob 认领 + 标备齐 → done
    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_auth(bearer_bob))
    await client.patch(
        f"/sheets/{sid}/rows/{rid}/delivery",
        json={"delivered_qty": 10},
        headers=_auth(bearer_bob),
    )
    # carol 再 claim done 行 → 409
    _, bearer_carol = await _make_player("carol")
    resp = await client.post(
        f"/sheets/{sid}/rows/{rid}/claim", headers=_auth(bearer_carol)
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_delivery_by_non_claimant_forbidden(client):
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    _, bearer_carol = await _make_player("carol")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, 0))["id"]
    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_auth(bearer_bob))
    # carol（非认领人）上报交付 → 403
    resp = await client.patch(
        f"/sheets/{sid}/rows/{rid}/delivery",
        json={"delivered_qty": 5},
        headers=_auth(bearer_carol),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_delivery_meets_need_sets_done(client):
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, 0))["id"]
    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_auth(bearer_bob))
    resp = await client.patch(
        f"/sheets/{sid}/rows/{rid}/delivery",
        json={"delivered_qty": 10},
        headers=_auth(bearer_bob),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"
    assert resp.json()["delivered_qty"] == 10


@pytest.mark.asyncio
async def test_release_by_claimant_succeeds(client):
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, 0))["id"]
    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_auth(bearer_bob))
    resp = await client.post(
        f"/sheets/{sid}/rows/{rid}/release", headers=_auth(bearer_bob)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "open"
    assert body["claimant_uuid"] is None
    assert body["delivered_qty"] == 0


@pytest.mark.asyncio
async def test_release_by_owner_succeeds(client):
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, 0))["id"]
    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_auth(bearer_bob))
    # owner 直接 release 认领中的行
    resp = await client.post(
        f"/sheets/{sid}/rows/{rid}/release", headers=_auth(bearer_owner)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "open"


@pytest.mark.asyncio
async def test_release_by_other_forbidden(client):
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    _, bearer_carol = await _make_player("carol")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, 0))["id"]
    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_auth(bearer_bob))
    # carol 非认领人非拥有者 → 403
    resp = await client.post(
        f"/sheets/{sid}/rows/{rid}/release", headers=_auth(bearer_carol)
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_reject_by_owner_done_to_claimed(client):
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, 0))["id"]
    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_auth(bearer_bob))
    await client.patch(
        f"/sheets/{sid}/rows/{rid}/delivery",
        json={"delivered_qty": 10},
        headers=_auth(bearer_bob),
    )
    # owner 打回 → claimed，认领人保留 delivered 归零
    resp = await client.post(
        f"/sheets/{sid}/rows/{rid}/reject", headers=_auth(bearer_owner)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "claimed"
    assert body["claimant_name"] == "bob"
    assert body["delivered_qty"] == 0


@pytest.mark.asyncio
async def test_reject_by_claimant_done_to_claimed(client):
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, 0))["id"]
    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_auth(bearer_bob))
    await client.patch(
        f"/sheets/{sid}/rows/{rid}/delivery",
        json={"delivered_qty": 10},
        headers=_auth(bearer_bob),
    )
    # 认领人（非拥有者）打回自己已备齐的行 → claimed，认领人保留 delivered 归零
    resp = await client.post(
        f"/sheets/{sid}/rows/{rid}/reject", headers=_auth(bearer_bob)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "claimed"
    assert body["claimant_name"] == "bob"
    assert body["delivered_qty"] == 0


@pytest.mark.asyncio
async def test_reject_by_third_party_forbidden(client):
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    _, bearer_carol = await _make_player("carol")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, 0))["id"]
    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_auth(bearer_bob))
    await client.patch(
        f"/sheets/{sid}/rows/{rid}/delivery",
        json={"delivered_qty": 10},
        headers=_auth(bearer_bob),
    )
    # carol（既非拥有者也非认领人）打回 → 403
    resp = await client.post(
        f"/sheets/{sid}/rows/{rid}/reject", headers=_auth(bearer_carol)
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_reject_non_done_conflict(client):
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, 0))["id"]
    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_auth(bearer_bob))
    # claimed 行打回（只有 done 可 reject）→ 409
    resp = await client.post(
        f"/sheets/{sid}/rows/{rid}/reject", headers=_auth(bearer_owner)
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_transition_missing_row_404(client):
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    assert (
        await client.post(
            f"/sheets/{sid}/rows/999999/claim", headers=_auth(bearer)
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_transition_missing_sheet_404(client):
    _, bearer = await _make_player()
    assert (
        await client.post(
            "/sheets/999999/rows/1/claim", headers=_auth(bearer)
        )
    ).status_code == 404


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
    await _upsert_row(client, bearer, sid, "iron", 64, 0)

    resp = await client.get("/sheets/export", headers={"X-Service-Token": "svc"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.strip().splitlines()
    assert lines[0] == (
        "sheet_id,item_name,need_qty,mode,status,claimant_uuid,delivered_qty,sort_order"
    )
    assert f"{sid},iron,64,0,open,,0,0" in lines[1:]
