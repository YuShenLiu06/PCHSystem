"""项目级协管员（manager）端点测试（迁移 0014）。

覆盖三层权限边界：
- tier A（owner/超管专属）：删项目 / 改名 / 授予撤销协管员 / 归档
- tier B（owner/超管/manager）：增删改行 / advance→constructing / progress / release / reject
- 公开：GET managers 任意登录玩家可读

复用 test_sheets_api.py 的 _svc_token / _make_player / _auth 模式。
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


async def _create_sheet(client, bearer, title: str = "S") -> int:
    resp = await client.post("/sheets", json={"title": title}, headers=_auth(bearer))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _grant(client, owner_bearer: str, sheet_id: int, player_uuid: uuid.UUID):
    """owner 授予协管员，返回响应。"""
    return await client.post(
        f"/sheets/{sheet_id}/managers",
        json={"player_uuid": str(player_uuid)},
        headers=_auth(owner_bearer),
    )


async def _upsert_row(client, bearer, sid, item="iron", need=64, mode=0):
    resp = await client.put(
        f"/sheets/{sid}/rows",
        json={"item_name": item, "need_qty": need, "mode": mode, "sort_order": 0},
        headers=_auth(bearer),
    )
    return resp


# ---------- GET /sheets/{id}/managers ----------
@pytest.mark.asyncio
async def test_list_managers_empty_for_new_sheet(client):
    _, owner_bearer = await _make_player("alice")
    sid = await _create_sheet(client, owner_bearer)
    resp = await client.get(f"/sheets/{sid}/managers", headers=_auth(owner_bearer))
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_managers_public_any_logged_player(client):
    _, owner_bearer = await _make_player("alice")
    other_u, other_bearer = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    grant = await _grant(client, owner_bearer, sid, other_u)
    assert grant.status_code == 201
    # bob 自己（被授予者）也能读
    resp = await client.get(f"/sheets/{sid}/managers", headers=_auth(other_bearer))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["player_uuid"] == str(other_u)
    assert body[0]["player_name"] == "bob"
    assert "granted_at" in body[0]


# ---------- POST /sheets/{id}/managers ----------
@pytest.mark.asyncio
async def test_grant_manager_requires_owner(client):
    owner_u, owner_bearer = await _make_player("alice")
    other_u, other_bearer = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    # bob 不是 owner，无权授予
    resp = await _grant(client, other_bearer, sid, owner_u)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_grant_manager_idempotent(client):
    _, owner_bearer = await _make_player("alice")
    other_u, _ = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    first = await _grant(client, owner_bearer, sid, other_u)
    assert first.status_code == 201
    assert len(first.json()) == 1
    # 重复授予：幂等，列表仍只 1 条
    second = await _grant(client, owner_bearer, sid, other_u)
    assert second.status_code == 201
    assert len(second.json()) == 1


@pytest.mark.asyncio
async def test_grant_owner_as_manager_rejected(client):
    owner_u, owner_bearer = await _make_player("alice")
    sid = await _create_sheet(client, owner_bearer)
    resp = await _grant(client, owner_bearer, sid, owner_u)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_grant_nonexistent_player_rejected(client):
    _, owner_bearer = await _make_player("alice")
    sid = await _create_sheet(client, owner_bearer)
    resp = await _grant(client, owner_bearer, sid, uuid.uuid4())
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_grant_manager_unlocks_tier_b(client):
    """被授予 manager 后，对 tier B 写操作获得权限。"""
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)

    # tier B：manager 可 upsert 行
    resp = await _upsert_row(client, mgr_bearer, sid)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_manager_cannot_do_tier_a_rename(client):
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)

    resp = await client.patch(
        f"/sheets/{sid}", json={"title": "hacked"}, headers=_auth(mgr_bearer)
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_manager_cannot_do_tier_a_delete_sheet(client):
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)

    resp = await client.delete(f"/sheets/{sid}", headers=_auth(mgr_bearer))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_manager_cannot_archive(client):
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)

    # 显式 to=archived → tier A → 403
    resp = await client.post(
        f"/sheets/{sid}/advance?to=archived", headers=_auth(mgr_bearer)
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_manager_can_advance_to_constructing(client):
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)

    resp = await client.post(
        f"/sheets/{sid}/advance?to=constructing", headers=_auth(mgr_bearer)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "constructing"


@pytest.mark.asyncio
async def test_manager_cannot_grant_other_manager(client):
    """manager 没有 tier A 授权权——不能授予别人 manager。"""
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    third_u, _ = await _make_player("carol")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)

    resp = await _grant(client, mgr_bearer, sid, third_u)
    assert resp.status_code == 403


# ---------- DELETE /sheets/{id}/managers/{player_uuid} ----------
@pytest.mark.asyncio
async def test_revoke_manager_requires_owner(client):
    _, owner_bearer = await _make_player("alice")
    mgr_u, _ = await _make_player("bob")
    third_u, third_bearer = await _make_player("carol")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)

    # carol 无权撤销 bob
    resp = await client.delete(
        f"/sheets/{sid}/managers/{mgr_u}", headers=_auth(third_bearer)
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_revoke_nonexistent_manager_404(client):
    _, owner_bearer = await _make_player("alice")
    other_u, _ = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    resp = await client.delete(
        f"/sheets/{sid}/managers/{other_u}", headers=_auth(owner_bearer)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_self_revoke_allowed(client):
    """manager 可主动卸任（self-revoke），无需 owner。"""
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)

    resp = await client.delete(
        f"/sheets/{sid}/managers/{mgr_u}", headers=_auth(mgr_bearer)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == []
    # 卸任后 tier B 权限回收
    assert (
        await _upsert_row(client, mgr_bearer, sid)
    ).status_code == 403


@pytest.mark.asyncio
async def test_owner_revoke_manager(client):
    _, owner_bearer = await _make_player("alice")
    mgr_u, _ = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)

    resp = await client.delete(
        f"/sheets/{sid}/managers/{mgr_u}", headers=_auth(owner_bearer)
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ---------- archived 守卫 ----------
@pytest.mark.asyncio
async def test_archived_rejects_manager_mutation(client):
    _, owner_bearer = await _make_player("alice")
    other_u, _ = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    # owner 先归档
    archived = await client.post(
        f"/sheets/{sid}/advance?to=archived", headers=_auth(owner_bearer)
    )
    assert archived.status_code == 200

    grant = await _grant(client, owner_bearer, sid, other_u)
    assert grant.status_code == 409  # 归档只读


# ---------- 响应扩展 ----------
@pytest.mark.asyncio
async def test_detail_includes_managers(client):
    _, owner_bearer = await _make_player("alice")
    mgr_u, _ = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)

    resp = await client.get(f"/sheets/{sid}", headers=_auth(owner_bearer))
    assert resp.status_code == 200
    body = resp.json()
    assert "managers" in body
    assert len(body["managers"]) == 1
    assert body["managers"][0]["player_uuid"] == str(mgr_u)


# ---------- list_sheets 参与排序 UNION 第 4 源 ----------
@pytest.mark.asyncio
async def test_list_sheets_includes_managed_in_involved(client):
    """manager 参与过的项目应在 list_sheets 参与优先排序中置顶。"""
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    # alice 建两张，bob 都不是 owner
    sid_a = await _create_sheet(client, owner_bearer, "A")
    sid_b = await _create_sheet(client, owner_bearer, "B")
    # bob 被授予为 A 的 manager
    await _grant(client, owner_bearer, sid_a, mgr_u)

    # bob 查列表（带 player_uuid 参与排序，由 api 透传）
    resp = await client.get("/sheets", headers=_auth(mgr_bearer))
    assert resp.status_code == 200
    titles = [s["title"] for s in resp.json()]
    assert "A" in titles and "B" in titles
    # A（bob 是 manager）应在 B（无参与）之前
    assert titles.index("A") < titles.index("B")
