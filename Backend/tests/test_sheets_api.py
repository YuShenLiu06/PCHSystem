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
        "sheet_id,item_name,registry_id,need_qty,mode,status,claimant_uuid,delivered_qty,sort_order,parent_row_id,qty_per_unit"
    )
    assert lines[1] == f"{sid},iron,,64,0,open,,0,0,,"


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
async def test_upsert_row_create_duplicate_name_409_then_update_by_id(client):
    """新建路径严格化（issue #20）：同名重复 PUT（无 row_id）→ 409 不再覆盖；
    改字段须带 row_id 走更新路径。"""
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]

    first = await _upsert_row(client, bearer, sid, "iron", 64, 0)
    assert first["item_name"] == "iron"

    # 同名再次 PUT（无 row_id）→ 409（不再静默覆盖）
    dup_resp = await client.put(
        f"/sheets/{sid}/rows",
        json={"item_name": "iron", "need_qty": 192, "mode": 1, "sort_order": 5},
        headers=_auth(bearer),
    )
    assert dup_resp.status_code == 409

    # 改 need/mode 须带 row_id 走更新路径 → 200
    update_resp = await client.put(
        f"/sheets/{sid}/rows",
        json={"row_id": first["id"], "need_qty": 192, "mode": 1, "sort_order": 5},
        headers=_auth(bearer),
    )
    assert update_resp.status_code == 200
    second = update_resp.json()
    assert second["id"] == first["id"]
    assert second["need_qty"] == 192
    assert second["mode"] == 1

    # 仍是单行（未新建第二行）
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


# ---------- PUT /sheets/{id}/rows 带 row_id（按主键更新；issue #20 改名重复修复）----------
@pytest.mark.asyncio
async def test_upsert_row_with_row_id_rename_no_duplicate(client):
    """带 row_id 改名：行 id 不变、名变、不新增行（issue #20 核心，API 级复现）。"""
    # Arrange：建一行「石英柱」
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    first = await _upsert_row(client, bearer, sid, "石英柱", 64, 0)
    # Act：带 row_id 改名（item_name 换新值）
    resp = await client.put(
        f"/sheets/{sid}/rows",
        json={
            "row_id": first["id"],
            "item_name": "石英柱1",
            "need_qty": 64,
            "mode": 0,
            "sort_order": 0,
        },
        headers=_auth(bearer),
    )
    # Assert：id 不变、名变、仍单行（旧 bug 在此变 2 行）
    assert resp.status_code == 200, resp.text
    second = resp.json()
    assert second["id"] == first["id"]
    assert second["item_name"] == "石英柱1"
    detail = (await client.get(f"/sheets/{sid}", headers=_auth(bearer))).json()
    assert len(detail["rows"]) == 1
    assert detail["rows"][0]["item_name"] == "石英柱1"


@pytest.mark.asyncio
async def test_upsert_row_with_row_id_partial_need_keeps_name(client):
    """带 row_id 只改 need（sparse body）：need 变，item_name 不变。"""
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    first = await _upsert_row(client, bearer, sid, "铁锭", 64, 0)
    resp = await client.put(
        f"/sheets/{sid}/rows",
        json={"row_id": first["id"], "need_qty": 200},
        headers=_auth(bearer),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["need_qty"] == 200
    assert body["item_name"] == "铁锭"  # 名不变


@pytest.mark.asyncio
async def test_upsert_row_with_row_id_missing_404(client):
    """row_id 不存在 → 404。"""
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    resp = await client.put(
        f"/sheets/{sid}/rows",
        json={"row_id": 999999, "item_name": "x"},
        headers=_auth(bearer),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_upsert_row_with_row_id_non_owner_forbidden(client):
    """非 owner 带 row_id 改行 → 403。"""
    _, bearer_a = await _make_player("alice")
    _, bearer_b = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_a))).json()["id"]
    rid = (await _upsert_row(client, bearer_a, sid, "iron", 1, 0))["id"]
    resp = await client.put(
        f"/sheets/{sid}/rows",
        json={"row_id": rid, "item_name": "x"},
        headers=_auth(bearer_b),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_upsert_row_with_row_id_rename_collision_409(client):
    """带 row_id 改名撞同表已存在名 → 409（UNIQUE(sheet_id,item_name) 防重名）。"""
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    rid_a = (await _upsert_row(client, bearer, sid, "A", 1, 0))["id"]
    await _upsert_row(client, bearer, sid, "B", 1, 0)  # 同表已有 B
    resp = await client.put(
        f"/sheets/{sid}/rows",
        json={"row_id": rid_a, "item_name": "B"},  # A 改名撞 B
        headers=_auth(bearer),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_upsert_row_with_row_id_on_archived_409(client, tmp_path, monkeypatch):
    """archived 终态只读：带 row_id PUT → 409。"""
    _patch_archive_root(monkeypatch, tmp_path)
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    rid = (await _upsert_row(client, bearer, sid, "iron", 1, 0))["id"]
    adv = await client.post(f"/sheets/{sid}/advance?to=archived", headers=_auth(bearer))
    assert adv.status_code == 200
    resp = await client.put(
        f"/sheets/{sid}/rows",
        json={"row_id": rid, "item_name": "改名"},
        headers=_auth(bearer),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_upsert_row_without_row_id_still_creates(client):
    """不带 row_id（回归）：走原 by-item_name 新建语义，不破。"""
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    resp = await client.put(
        f"/sheets/{sid}/rows",
        json={"item_name": "iron", "need_qty": 1, "mode": 0, "sort_order": 0},
        headers=_auth(bearer),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["item_name"] == "iron"
    detail = (await client.get(f"/sheets/{sid}", headers=_auth(bearer))).json()
    assert len(detail["rows"]) == 1


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
        "sheet_id,item_name,registry_id,need_qty,mode,status,claimant_uuid,delivered_qty,sort_order,parent_row_id,qty_per_unit"
    )
    assert f"{sid},iron,,64,0,open,,0,0,," in lines[1:]


# ---------- progress 行：多人贡献者（contribute） ----------
async def _contribute(client, bearer: str, sid: int, rid: int, qty: int) -> dict:
    resp = await client.post(
        f"/sheets/{sid}/rows/{rid}/contribute",
        json={"qty": qty},
        headers=_auth(bearer),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_progress_row_claim_returns_409(client):
    """progress 行无 claim 概念：POST /claim → 409（repo 守卫）。"""
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, mode=1))["id"]
    resp = await client.post(
        f"/sheets/{sid}/rows/{rid}/claim", headers=_auth(bearer_bob)
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_progress_row_delivery_returns_409(client):
    """progress 行用 contribute 不走 delivery：PATCH /delivery → 409（repo mode 守卫）。"""
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, mode=1))["id"]
    resp = await client.patch(
        f"/sheets/{sid}/rows/{rid}/delivery",
        json={"delivered_qty": 5},
        headers=_auth(bearer_bob),
    )
    assert resp.status_code == 409


# ---------- progress 行：owner 调整进度（PATCH /progress，绝对值） ----------
@pytest.mark.asyncio
async def test_set_row_progress_by_owner_overrides_and_keeps_contributors(client):
    """owner PATCH /progress 设绝对值：重算 status，contributors 保留（上交历史）。"""
    _, bearer_owner = await _make_player("alice")
    bob_uuid, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, mode=1))["id"]
    # bob 先上交 4（contributors=[bob], status=claimed）
    await _contribute(client, bearer_bob, sid, rid, 4)

    resp = await client.patch(
        f"/sheets/{sid}/rows/{rid}/progress",
        json={"delivered_qty": 8},
        headers=_auth(bearer_owner),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["delivered_qty"] == 8
    assert body["status"] == "claimed"
    assert body["claimant_uuid"] is None
    # contributors 保留（owner 调整不动贡献者名单）
    assert len(body["contributors"]) == 1
    assert body["contributors"][0]["player_uuid"] == str(bob_uuid)


@pytest.mark.asyncio
async def test_set_row_progress_to_zero_reopens(client):
    """owner 设 0 → status=open。"""
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, mode=1))["id"]
    await _contribute(client, bearer_bob, sid, rid, 5)  # claimed
    resp = await client.patch(
        f"/sheets/{sid}/rows/{rid}/progress",
        json={"delivered_qty": 0},
        headers=_auth(bearer_owner),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "open"


@pytest.mark.asyncio
async def test_set_row_progress_by_non_owner_returns_403(client):
    """非 owner PATCH /progress → 403（仅拥有者/admin）。"""
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, mode=1))["id"]
    resp = await client.patch(
        f"/sheets/{sid}/rows/{rid}/progress",
        json={"delivered_qty": 5},
        headers=_auth(bearer_bob),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_set_row_progress_on_lock_row_returns_409(client):
    """lock 行 PATCH /progress → 409（lock 用 /delivery）。"""
    _, bearer_owner = await _make_player("alice")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, mode=0))["id"]
    resp = await client.patch(
        f"/sheets/{sid}/rows/{rid}/progress",
        json={"delivered_qty": 5},
        headers=_auth(bearer_owner),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_progress_row_reject_by_owner_returns_409(client):
    """progress 行无 reject：owner POST /reject → 409（owner 过 RBAC 后 repo 守卫触发）。"""
    _, bearer_owner = await _make_player("alice")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, mode=1))["id"]
    resp = await client.post(
        f"/sheets/{sid}/rows/{rid}/reject", headers=_auth(bearer_owner)
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_contribute_partial_accumulates_and_lists_contributor(client):
    """单次贡献：delivered += qty；contributors 含上交者；部分 → status=claimed。"""
    _, bearer_owner = await _make_player("alice")
    bob_uuid, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, mode=1))["id"]

    body = await _contribute(client, bearer_bob, sid, rid, 3)
    assert body["delivered_qty"] == 3
    assert body["status"] == "claimed"
    assert body["claimant_uuid"] is None
    contribs = body["contributors"]
    assert len(contribs) == 1
    assert contribs[0]["player_uuid"] == str(bob_uuid)
    assert contribs[0]["player_name"] == "bob"


@pytest.mark.asyncio
async def test_contribute_meets_need_sets_done(client):
    """贡献累计 >= need → status=done。"""
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, mode=1))["id"]

    body = await _contribute(client, bearer_bob, sid, rid, 10)
    assert body["delivered_qty"] == 10
    assert body["status"] == "done"


@pytest.mark.asyncio
async def test_contribute_multiple_players_accumulate(client):
    """多人 contribute：delivered 累加；contributors 含多人。"""
    _, bearer_owner = await _make_player("alice")
    bob_uuid, bearer_bob = await _make_player("bob")
    carol_uuid, bearer_carol = await _make_player("carol")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, mode=1))["id"]

    b1 = await _contribute(client, bearer_bob, sid, rid, 3)
    assert b1["delivered_qty"] == 3
    assert {c["player_name"] for c in b1["contributors"]} == {"bob"}

    b2 = await _contribute(client, bearer_carol, sid, rid, 4)
    assert b2["delivered_qty"] == 7
    names = {c["player_name"] for c in b2["contributors"]}
    assert names == {"bob", "carol"}
    uuids = {c["player_uuid"] for c in b2["contributors"]}
    assert str(bob_uuid) in uuids and str(carol_uuid) in uuids


@pytest.mark.asyncio
async def test_contribute_same_player_idempotent_contributor(client):
    """同一玩家多次 contribute：contributors 仅一条（幂等 UNIQUE）。"""
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 100, mode=1))["id"]

    await _contribute(client, bearer_bob, sid, rid, 3)
    body = await _contribute(client, bearer_bob, sid, rid, 4)
    assert body["delivered_qty"] == 7
    assert len(body["contributors"]) == 1
    assert body["contributors"][0]["player_name"] == "bob"


@pytest.mark.asyncio
async def test_contribute_on_done_row_returns_409(client):
    """done 后再 contribute → 409。"""
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, mode=1))["id"]
    await _contribute(client, bearer_bob, sid, rid, 10)

    resp = await client.post(
        f"/sheets/{sid}/rows/{rid}/contribute",
        json={"qty": 1},
        headers=_auth(bearer_bob),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_contribute_on_lock_row_returns_409(client):
    """lock 行 contribute → 409（contribute 仅 progress）。"""
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, mode=0))["id"]
    resp = await client.post(
        f"/sheets/{sid}/rows/{rid}/contribute",
        json={"qty": 1},
        headers=_auth(bearer_bob),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_contribute_qty_must_be_positive(client):
    """qty<1 → 422。"""
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, mode=1))["id"]
    resp = await client.post(
        f"/sheets/{sid}/rows/{rid}/contribute",
        json={"qty": 0},
        headers=_auth(bearer_bob),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_progress_release_by_owner_resets(client):
    """owner release progress 行 → delivered=0 / contributors=[] / status=open。"""
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, mode=1))["id"]
    await _contribute(client, bearer_bob, sid, rid, 5)

    resp = await client.post(
        f"/sheets/{sid}/rows/{rid}/release", headers=_auth(bearer_owner)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "open"
    assert body["delivered_qty"] == 0
    assert body["claimant_uuid"] is None
    assert body["contributors"] == []


@pytest.mark.asyncio
async def test_progress_release_by_non_owner_forbidden(client):
    """progress 无 claimant：非 owner release → 403。"""
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    _, bearer_carol = await _make_player("carol")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid = (await _upsert_row(client, bearer_owner, sid, "iron", 10, mode=1))["id"]
    await _contribute(client, bearer_bob, sid, rid, 5)
    # carol 既非 owner 也非 claimant（progress 行 claimant 永空）
    resp = await client.post(
        f"/sheets/{sid}/rows/{rid}/release", headers=_auth(bearer_carol)
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_progress_detail_includes_contributors_field(client):
    """RowDetail 始终含 contributors 字段（progress 非空 / lock 空数组）。"""
    _, bearer_owner = await _make_player("alice")
    _, bearer_bob = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_owner))).json()["id"]
    rid_p = (await _upsert_row(client, bearer_owner, sid, "iron", 10, mode=1))["id"]
    rid_l = (await _upsert_row(client, bearer_owner, sid, "gold", 5, mode=0))["id"]
    await _contribute(client, bearer_bob, sid, rid_p, 3)

    detail = (await client.get(f"/sheets/{sid}", headers=_auth(bearer_owner))).json()
    rows = {r["id"]: r for r in detail["rows"]}
    # progress 行 contributors 非空
    assert len(rows[rid_p]["contributors"]) == 1
    assert rows[rid_p]["contributors"][0]["player_name"] == "bob"
    # lock 行 contributors 为空数组
    assert rows[rid_l]["contributors"] == []



# ---------- 项目阶段生命周期：advance / archive / status 过滤 ----------


def _patch_archive_root(monkeypatch, tmp_path):
    """注入 archive_root=tmp_path 给 api 层的 get_settings()。"""
    import app.api.sheets.lifecycle as lifecycle_mod
    from app.core.config import Settings

    real = Settings()
    real.archive_root = str(tmp_path)
    monkeypatch.setattr(lifecycle_mod, "get_settings", lambda: real)


@pytest.mark.asyncio
async def test_advance_sheet_owner_to_constructing(client):
    # Arrange
    _, bearer = await _make_player("alice")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    # Act
    resp = await client.post(
        f"/sheets/{sid}/advance?to=constructing", headers=_auth(bearer)
    )
    # Assert
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "constructing"
    assert body["archived_path"] is None
    assert body["archived_at"] is None


@pytest.mark.asyncio
async def test_advance_sheet_owner_to_archived_writes_file(client, tmp_path, monkeypatch):
    # Arrange
    _patch_archive_root(monkeypatch, tmp_path)
    _, bearer = await _make_player("alice")
    sid = (await client.post("/sheets", json={"title": "归档测试"}, headers=_auth(bearer))).json()["id"]
    # Act
    resp = await client.post(
        f"/sheets/{sid}/advance?to=archived", headers=_auth(bearer)
    )
    # Assert
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "archived"
    assert body["archived_path"] == f"projects/{sid}/index.md"
    assert body["archived_at"] is not None
    # 文件落盘
    final = tmp_path / "projects" / str(sid) / "index.md"
    assert final.is_file()
    assert "项目归档：归档测试" in final.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_advance_sheet_default_infers_next_phase(client):
    # Arrange：collecting 缺省 → constructing
    _, bearer = await _make_player("alice")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    resp1 = await client.post(f"/sheets/{sid}/advance", headers=_auth(bearer))
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "constructing"


@pytest.mark.asyncio
async def test_advance_sheet_non_owner_403(client):
    # Arrange
    _, bearer_a = await _make_player("alice")
    _, bearer_b = await _make_player("bob")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer_a))).json()["id"]
    # Act
    resp = await client.post(
        f"/sheets/{sid}/advance?to=constructing", headers=_auth(bearer_b)
    )
    # Assert
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_advance_sheet_already_archived_409(client, tmp_path, monkeypatch):
    # Arrange：先归档，再 advance
    _patch_archive_root(monkeypatch, tmp_path)
    _, bearer = await _make_player("alice")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    r1 = await client.post(f"/sheets/{sid}/advance?to=archived", headers=_auth(bearer))
    assert r1.status_code == 200
    # Act
    resp = await client.post(f"/sheets/{sid}/advance?to=archived", headers=_auth(bearer))
    # Assert
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_advance_sheet_archived_triggers_notification(client, tmp_path, monkeypatch):
    # Arrange
    _patch_archive_root(monkeypatch, tmp_path)
    u, bearer = await _make_player("alice")
    sid = (await client.post("/sheets", json={"title": "通知"}, headers=_auth(bearer))).json()["id"]
    # Act
    resp = await client.post(f"/sheets/{sid}/advance?to=archived", headers=_auth(bearer))
    assert resp.status_code == 200
    # Assert：notifications 表有一条 sheet_archived（owner 自归档 → actor==recipient，
    # notification_service.notify 仍写入；ack 时由 MCDR 拉）
    from app.core.db import async_session_factory
    from app.models.notification import Notification
    from sqlalchemy import select
    async with async_session_factory() as s:
        notifs = (
            await s.execute(select(Notification).where(Notification.recipient_uuid == u))
        ).scalars().all()
    assert any(n.category == "sheet_archived" for n in notifs)


@pytest.mark.asyncio
async def test_get_archive_markdown_returns_content(client, tmp_path, monkeypatch):
    # Arrange：先归档
    _patch_archive_root(monkeypatch, tmp_path)
    _, bearer = await _make_player("alice")
    sid = (await client.post("/sheets", json={"title": "读归档"}, headers=_auth(bearer))).json()["id"]
    await client.post(f"/sheets/{sid}/advance?to=archived", headers=_auth(bearer))
    # Act
    resp = await client.get(f"/sheets/{sid}/archive", headers=_auth(bearer))
    # Assert
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert "项目归档：读归档" in resp.text


@pytest.mark.asyncio
async def test_get_archive_markdown_not_archived_404(client):
    # Arrange：collecting 态
    _, bearer = await _make_player("alice")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    # Act / Assert
    resp = await client.get(f"/sheets/{sid}/archive", headers=_auth(bearer))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_archive_markdown_file_missing_404(client, tmp_path, monkeypatch):
    # Arrange：归档后删文件模拟丢失
    _patch_archive_root(monkeypatch, tmp_path)
    _, bearer = await _make_player("alice")
    sid = (await client.post("/sheets", json={"title": "丢文件"}, headers=_auth(bearer))).json()["id"]
    await client.post(f"/sheets/{sid}/advance?to=archived", headers=_auth(bearer))
    (tmp_path / "projects" / str(sid) / "index.md").unlink()
    # Act
    resp = await client.get(f"/sheets/{sid}/archive", headers=_auth(bearer))
    # Assert
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_archive_asset_returns_png(client, tmp_path, monkeypatch):
    # Arrange：归档一个有贡献者的项目（progress 行 → contributions.png 落盘）
    _patch_archive_root(monkeypatch, tmp_path)
    owner_uuid, bearer = await _make_player("alice")
    sid = (await client.post("/sheets", json={"title": "带图"}, headers=_auth(bearer))).json()["id"]
    row = await _upsert_row(client, bearer, sid, item="圆石", need=999, mode=1)
    # 任意玩家上交（progress 任意登录玩家可 contribute）
    contrib_uuid, contrib_bearer = await _make_player("bob")
    resp = await client.post(
        f"/sheets/{sid}/rows/{row['id']}/contribute",
        json={"qty": 30},
        headers=_auth(contrib_bearer),
    )
    assert resp.status_code == 200, resp.text
    await client.post(f"/sheets/{sid}/advance?to=archived", headers=_auth(bearer))
    # Act
    resp = await client.get(f"/sheets/{sid}/archive/assets/contributions.png", headers=_auth(bearer))
    # Assert：200 + image/png + PNG 头
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("image/png")
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_get_archive_asset_rejects_unknown_filename(client, tmp_path, monkeypatch):
    _patch_archive_root(monkeypatch, tmp_path)
    _, bearer = await _make_player("alice")
    sid = (await client.post("/sheets", json={"title": "白名单"}, headers=_auth(bearer))).json()["id"]
    row = await _upsert_row(client, bearer, sid, item="圆石", need=999, mode=1)
    await client.post(
        f"/sheets/{sid}/rows/{row['id']}/contribute",
        json={"qty": 5},
        headers=_auth((await _make_player("bob"))[1]),
    )
    await client.post(f"/sheets/{sid}/advance?to=archived", headers=_auth(bearer))
    # 非白名单名 → 404（无论文件是否存在，纵深防御）
    resp = await client.get(f"/sheets/{sid}/archive/assets/secret.md", headers=_auth(bearer))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_archive_asset_missing_when_no_contributors(client, tmp_path, monkeypatch):
    # 无贡献者 → 不生 contributions.png → asset 404
    _patch_archive_root(monkeypatch, tmp_path)
    _, bearer = await _make_player("alice")
    sid = (await client.post("/sheets", json={"title": "无图"}, headers=_auth(bearer))).json()["id"]
    await client.post(f"/sheets/{sid}/advance?to=archived", headers=_auth(bearer))
    resp = await client.get(f"/sheets/{sid}/archive/assets/contributions.png", headers=_auth(bearer))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_archive_asset_not_archived_404(client, tmp_path, monkeypatch):
    _patch_archive_root(monkeypatch, tmp_path)
    _, bearer = await _make_player("alice")
    sid = (await client.post("/sheets", json={"title": "未归档"}, headers=_auth(bearer))).json()["id"]
    resp = await client.get(f"/sheets/{sid}/archive/assets/contributions.png", headers=_auth(bearer))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_sheets_status_filter_active(client):
    # Arrange：3 张表各在不同阶段（collecting / constructing / archived）
    _, bearer_a = await _make_player("alice")
    s1 = (await client.post("/sheets", json={"title": "收集中"}, headers=_auth(bearer_a))).json()["id"]
    s2 = (await client.post("/sheets", json={"title": "施工中"}, headers=_auth(bearer_a))).json()["id"]
    s3 = (await client.post("/sheets", json={"title": "另一收集中"}, headers=_auth(bearer_a))).json()["id"]
    await client.post(f"/sheets/{s2}/advance?to=constructing", headers=_auth(bearer_a))
    # Act
    resp = await client.get("/sheets?status=active", headers=_auth(bearer_a))
    # Assert：active = collecting + constructing（不含 archived；这里无 archived）
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert set(ids) == {s1, s2, s3}
@pytest.mark.asyncio
async def test_upsert_row_with_registry_id_stores_and_echoes(client):
    """PUT 行带 item_name + registry_id → 行落库 + 回显 registry_id。"""
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    resp = await client.put(
        f"/sheets/{sid}/rows",
        json={
            "item_name": "石头",
            "need_qty": 64,
            "mode": 0,
            "sort_order": 0,
            "registry_id": "minecraft:stone",
        },
        headers=_auth(bearer),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["registry_id"] == "minecraft:stone"
    assert body["item_name"] == "石头"


@pytest.mark.asyncio
async def test_upsert_row_registry_id_only_auto_translates_name(client):
    """仅传 registry_id（无 item_name）→ 后端翻译表补默认中文名（命中或回退 id，均非空）。"""
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    resp = await client.put(
        f"/sheets/{sid}/rows",
        json={"need_qty": 32, "mode": 0, "sort_order": 0, "registry_id": "minecraft:stone"},
        headers=_auth(bearer),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["registry_id"] == "minecraft:stone"
    assert body["item_name"]  # 非空（翻译命中或回退 registry_id）


@pytest.mark.asyncio
async def test_upsert_row_requires_name_or_registry(client):
    """item_name 与 registry_id 都缺 → 422。"""
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    resp = await client.put(
        f"/sheets/{sid}/rows",
        json={"need_qty": 1, "mode": 0, "sort_order": 0},  # 无 name / registry_id
        headers=_auth(bearer),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_upsert_row_registry_id_not_overwritten_when_omitted(client):
    """已存在行的 registry_id：按 row_id 更新不传 registry_id → 保留原值（None 不覆盖）。"""
    _, bearer = await _make_player()
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    first = (
        await client.put(
            f"/sheets/{sid}/rows",
            json={
                "item_name": "石头",
                "need_qty": 64,
                "mode": 0,
                "sort_order": 0,
                "registry_id": "minecraft:stone",
            },
            headers=_auth(bearer),
        )
    ).json()
    assert first["registry_id"] == "minecraft:stone"
    # 按 row_id 更新：不传 registry_id（只改 need）→ registry_id 保留
    second = (
        await client.put(
            f"/sheets/{sid}/rows",
            json={"row_id": first["id"], "need_qty": 128},
            headers=_auth(bearer),
        )
    ).json()
    assert second["id"] == first["id"]
    assert second["need_qty"] == 128
    assert second["registry_id"] == "minecraft:stone"  # 未被擦


@pytest.mark.asyncio
async def test_list_sheets_status_filter_archived(client, tmp_path, monkeypatch):
    # Arrange
    _patch_archive_root(monkeypatch, tmp_path)
    _, bearer_a = await _make_player("alice")
    s1 = (await client.post("/sheets", json={"title": "活跃"}, headers=_auth(bearer_a))).json()["id"]
    s2 = (await client.post("/sheets", json={"title": "归档"}, headers=_auth(bearer_a))).json()["id"]
    await client.post(f"/sheets/{s2}/advance?to=archived", headers=_auth(bearer_a))
    # Act
    resp = await client.get("/sheets?status=archived", headers=_auth(bearer_a))
    # Assert
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert ids == [s2]


@pytest.mark.asyncio
async def test_sheet_summary_has_status_fields(client):
    # Arrange / Act
    _, bearer = await _make_player("alice")
    sid = (await client.post("/sheets", json={"title": "S"}, headers=_auth(bearer))).json()["id"]
    detail = (await client.get(f"/sheets/{sid}", headers=_auth(bearer))).json()
    listing = (await client.get("/sheets", headers=_auth(bearer))).json()
    # Assert：详情与列表均含新字段
    assert detail["status"] == "collecting"
    assert detail["archived_path"] is None
    assert detail["archived_at"] is None
    assert listing[0]["status"] == "collecting"
    assert "archived_path" in listing[0]
    assert "archived_at" in listing[0]


@pytest.mark.asyncio
async def test_advance_sheet_archive_root_unconfigured_503(client, monkeypatch):
    # Arrange：archive_root 空（未配置）→ 归档端点 503
    import app.api.sheets.lifecycle as lifecycle_mod
    from app.core.config import Settings
    real = Settings()
    real.archive_root = ""
    monkeypatch.setattr(lifecycle_mod, "get_settings", lambda: real)
    _, bearer = await _make_player("alice")
    sid = (await client.post("/sheets", json={"title": "无根"}, headers=_auth(bearer))).json()["id"]
    # Act
    resp = await client.post(f"/sheets/{sid}/advance?to=archived", headers=_auth(bearer))
    # Assert
    assert resp.status_code == 503
    # DB 未变（仍 collecting）
    detail = (await client.get(f"/sheets/{sid}", headers=_auth(bearer))).json()
    assert detail["status"] == "collecting"


# ---------- registry_id（隐式字段）----------


@pytest.mark.asyncio
async def test_list_sheets_involved_first_ordering(client):
    """端到端：参与的表排在前面（owner/claimant/contributor 三种角色）。"""
    # Arrange：创建三个玩家
    _, bearer_a = await _make_player("alice")
    _, bearer_b = await _make_player("bob")
    _, bearer_c = await _make_player("carol")

    # alice 创建 2 张表
    s1 = (await client.post("/sheets", json={"title": "Alice表1"}, headers=_auth(bearer_a))).json()["id"]
    s2 = (await client.post("/sheets", json={"title": "Alice表2"}, headers=_auth(bearer_a))).json()["id"]
    # bob 创建 2 张表
    s3 = (await client.post("/sheets", json={"title": "Bob表1"}, headers=_auth(bearer_b))).json()["id"]
    s4 = (await client.post("/sheets", json={"title": "Bob表2"}, headers=_auth(bearer_b))).json()["id"]
    # carol 创建 1 张表
    s5 = (await client.post("/sheets", json={"title": "Carol表1"}, headers=_auth(bearer_c))).json()["id"]

    # alice 认领 s3 的一行（作为 claimant 参与）
    await client.put(
        f"/sheets/{s3}/rows",
        json={"item_name": "stone", "need_qty": 64, "mode": 0, "sort_order": 0},
        headers=_auth(bearer_a),
    )
    await client.post(f"/sheets/{s3}/rows/1/claim", headers=_auth(bearer_a))

    # alice 上交 s4 的一行（作为 contributor 参与）
    await client.put(
        f"/sheets/{s4}/rows",
        json={"item_name": "dirt", "need_qty": 64, "mode": 1, "sort_order": 0},
        headers=_auth(bearer_a),
    )
    await client.post(
        f"/sheets/{s4}/rows/2/contribute",
        json={"contributed_qty": 10},
        headers=_auth(bearer_a),
    )

    # Act：alice 查询列表
    resp = await client.get("/sheets", headers=_auth(bearer_a))
    assert resp.status_code == 200
    sheets = resp.json()

    # Assert：alice 参与的表（s1, s2, s3, s4）应在前，未参与的（s5）在后
    sheet_ids = [s["id"] for s in sheets]
    involved = {s1, s2, s3, s4}
    not_involved = {s5}

    # 前 4 个应该是参与的表
    assert set(sheet_ids[:4]) == involved
    # 最后一个应该是未参与的表
    assert sheet_ids[4] in not_involved
    # 参与的表内部按 id 升序
    involved_ids = [sid for sid in sheet_ids if sid in involved]
    assert involved_ids == sorted(involved_ids)
