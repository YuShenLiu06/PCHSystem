"""项目级协管员（manager）端点测试（迁移 0014，account 锚，R-5 落地）。

覆盖三层权限边界 + account 锚定：
- tier A（owner/超管专属）：删项目 / 改名 / 授予撤销协管员 / 归档
- tier B（owner/超管/manager）：增删改行 / advance→constructing / progress / release / reject
- account 继承：同账号任一 UUID 都是 manager；self-revoke 按 account 比对
- 公开：GET managers 任意登录玩家可读

JWT sub=account_id：所有玩家经 ``seed_player_with_account`` 建 Player+WebAccount。
"""
import uuid

import pytest
from sqlalchemy import select

import app.api.deps as deps
from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.jwt import create_access_token
from app.models.user import Player
from tests.conftest import seed_player_with_account


@pytest.fixture(autouse=True)
def _svc_token(monkeypatch):
    deps._settings = get_settings()
    deps._settings.mcdr_service_token = "svc"


async def _make_player(
    name: str = "alice", role: str = "user"
) -> tuple[uuid.UUID, str]:
    """seed Player + 临时 WebAccount 并签 JWT，返回 (uuid, bearer)。"""
    return await seed_player_with_account(name=name, role=role)


async def _make_player_with_account_id(
    account_id: int, name: str, role: str = "user"
) -> tuple[uuid.UUID, str]:
    """给已存在的 WebAccount 再绑一个 UUID（验同账号继承），返回 (uuid, bearer)。"""
    u = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(
            Player(
                uuid=u,
                current_name=name,
                role=role,
                web_account_id=account_id,
            )
        )
        await s.commit()
    token = create_access_token(account_id, role, active_uuid=u)
    return u, f"Bearer {token}"


async def _account_id_of(player_uuid: uuid.UUID) -> int:
    async with async_session_factory() as s:
        return (
            await s.execute(
                select(Player.web_account_id).where(Player.uuid == player_uuid)
            )
        ).scalar_one()


def _auth(bearer: str) -> dict[str, str]:
    return {"Authorization": bearer}


async def _create_sheet(client, bearer: str, title: str = "S") -> int:
    resp = await client.post("/sheets", json={"title": title}, headers=_auth(bearer))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _grant(
    client, owner_bearer: str, sheet_id: int, player_uuid: uuid.UUID
):
    """owner 授予协管员，返回响应。"""
    return await client.post(
        f"/sheets/{sheet_id}/managers",
        json={"player_uuid": str(player_uuid)},
        headers=_auth(owner_bearer),
    )


async def _revoke_by_account(
    client, bearer: str, sheet_id: int, web_account_id: int
):
    """按 account_id 撤销协管员（新契约：body-based DELETE）。"""
    return await client.request(
        "DELETE",
        f"/sheets/{sheet_id}/managers",
        json={"web_account_id": web_account_id},
        headers=_auth(bearer),
    )


async def _upsert_row(client, bearer, sid, item="iron", need=64, mode=0):
    return await client.put(
        f"/sheets/{sid}/rows",
        json={"item_name": item, "need_qty": need, "mode": mode, "sort_order": 0},
        headers=_auth(bearer),
    )


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
    # bob（被授予者）也能读
    resp = await client.get(f"/sheets/{sid}/managers", headers=_auth(other_bearer))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    entry = body[0]
    # account 锚契约字段
    assert isinstance(entry["web_account_id"], int)
    assert entry["display_name"] == "bob"
    assert str(other_u) in entry["member_uuids"]
    assert "granted_at" in entry


# ---------- POST /sheets/{id}/managers ----------
@pytest.mark.asyncio
async def test_grant_manager_requires_owner(client):
    _, owner_bearer = await _make_player("alice")
    third_u, third_bearer = await _make_player("carol")
    sid = await _create_sheet(client, owner_bearer)
    # carol 非 owner 无权授予（即使目标是 owner 自身也拒）
    resp = await _grant(client, third_bearer, sid, owner_uuid_dummy())
    assert resp.status_code == 403


def owner_uuid_dummy() -> uuid.UUID:
    """占位 UUID（不存在），测 tier A 403 不依赖 target 校验顺序。"""
    return uuid.uuid4()


@pytest.mark.asyncio
async def test_grant_manager_idempotent_no_duplicate_notification(client):
    _, owner_bearer = await _make_player("alice")
    other_u, _ = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    first = await _grant(client, owner_bearer, sid, other_u)
    assert first.status_code == 201
    assert len(first.json()) == 1
    # 重复授予：幂等，列表仍只 1 条；通知不重发（见 notification 计数测试）
    second = await _grant(client, owner_bearer, sid, other_u)
    assert second.status_code == 201
    assert len(second.json()) == 1

    # 通知仅 1 条（首次授予），幂等重授不重发
    notes = await _notifications_for(other_u)
    granted = [n for n in notes if n[0] == "sheet_manager_granted"]
    assert len(granted) == 1


@pytest.mark.asyncio
async def test_grant_owner_account_as_manager_rejected_409(client):
    """B7：把 owner 同账号另一 UUID 设为 manager → 409（按 account 比对）。"""
    owner_u, owner_bearer = await _make_player("alice")
    sid = await _create_sheet(client, owner_bearer)
    # 给 owner account 再绑一个 UUID，作为授予目标
    owner_account_id = await _account_id_of(owner_u)
    second_u, _ = await _make_player_with_account_id(
        owner_account_id, "alice_alt"
    )
    resp = await _grant(client, owner_bearer, sid, second_u)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_grant_target_unbound_account_rejected_422(client):
    """B7：target Player 未绑 Web 账号 → 422（FK NOT NULL 守卫）。"""
    _, owner_bearer = await _make_player("alice")
    sid = await _create_sheet(client, owner_bearer)
    # 手动建未绑账号的 Player（绕过 seed_player_with_account 的自动挂接）
    unbound_u = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=unbound_u, current_name="unbound"))
        await s.commit()
    resp = await _grant(client, owner_bearer, sid, unbound_u)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_grant_nonexistent_player_rejected_422(client):
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
    """显式 ?to=archived → tier A → 403（manager 无权）。"""
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)
    resp = await client.post(
        f"/sheets/{sid}/advance?to=archived", headers=_auth(mgr_bearer)
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_manager_can_advance_to_constructing(client):
    """tier B 行为变化点：manager 可推进施工。"""
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
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    third_u, _ = await _make_player("carol")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)
    resp = await _grant(client, mgr_bearer, sid, third_u)
    assert resp.status_code == 403


# ---------- DELETE /sheets/{id}/managers ----------
@pytest.mark.asyncio
async def test_revoke_manager_requires_owner(client):
    """非 self-revoke 且非 owner → 403。"""
    _, owner_bearer = await _make_player("alice")
    mgr_u, _ = await _make_player("bob")
    third_u, third_bearer = await _make_player("carol")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)
    mgr_account_id = await _account_id_of(mgr_u)
    # carol（与 manager 账号无关）尝试撤销 bob → 403
    resp = await _revoke_by_account(client, third_bearer, sid, mgr_account_id)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_revoke_nonexistent_manager_404(client):
    _, owner_bearer = await _make_player("alice")
    other_u, _ = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    other_account_id = await _account_id_of(other_u)
    resp = await _revoke_by_account(client, owner_bearer, sid, other_account_id)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_self_revoke_allowed_for_same_account_uuid(client):
    """B6：被授予账号下另一 UUID 也可 self-revoke（account 继承）。"""
    _, owner_bearer = await _make_player("alice")
    mgr_u, _ = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)
    mgr_account_id = await _account_id_of(mgr_u)
    # 同账号另一 UUID 登录后 self-revoke
    _, mgr_alt_bearer = await _make_player_with_account_id(
        mgr_account_id, "bob_alt"
    )
    resp = await _revoke_by_account(client, mgr_alt_bearer, sid, mgr_account_id)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


@pytest.mark.asyncio
async def test_self_revoke_recovers_tier_b(client):
    """self-revoke 后 tier B 权限回收（_can_operate 每次重读 managers）。"""
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)
    mgr_account_id = await _account_id_of(mgr_u)
    resp = await _revoke_by_account(client, mgr_bearer, sid, mgr_account_id)
    assert resp.status_code == 200
    # 卸任后 tier B 写操作 → 403
    assert (
        await _upsert_row(client, mgr_bearer, sid)
    ).status_code == 403


@pytest.mark.asyncio
async def test_owner_revoke_manager(client):
    _, owner_bearer = await _make_player("alice")
    mgr_u, _ = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)
    mgr_account_id = await _account_id_of(mgr_u)
    resp = await _revoke_by_account(client, owner_bearer, sid, mgr_account_id)
    assert resp.status_code == 200
    assert resp.json() == []


async def _notifications_for(player_uuid: uuid.UUID) -> list:
    """直查 notifications 表，返回该玩家名下全部通知。"""
    from app.models.notification import Notification

    async with async_session_factory() as s:
        rows = (
            await s.execute(
                select(Notification).where(
                    Notification.recipient_uuid == player_uuid
                )
            )
        ).scalars().all()
        return [(r.category, r.title, r.body) for r in rows]


@pytest.mark.asyncio
async def test_revoke_sends_notification_to_revoked_player(client):
    _, owner_bearer = await _make_player("alice")
    mgr_u, _ = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer, "MyProj")
    await _grant(client, owner_bearer, sid, mgr_u)
    mgr_account_id = await _account_id_of(mgr_u)
    resp = await _revoke_by_account(client, owner_bearer, sid, mgr_account_id)
    assert resp.status_code == 200

    notes = await _notifications_for(mgr_u)
    categories = [c for c, _, _ in notes]
    assert "sheet_manager_granted" in categories
    assert "sheet_manager_revoked" in categories
    revoked = [(c, t, b) for c, t, b in notes if c == "sheet_manager_revoked"][0]
    assert revoked[1] == "你不再是项目协管员"
    assert "MyProj" in revoked[2] and "alice" in revoked[2]


@pytest.mark.asyncio
async def test_self_revoke_does_not_notify(client):
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)
    mgr_account_id = await _account_id_of(mgr_u)
    resp = await _revoke_by_account(client, mgr_bearer, sid, mgr_account_id)
    assert resp.status_code == 200

    notes = await _notifications_for(mgr_u)
    categories = [c for c, _, _ in notes]
    assert "sheet_manager_granted" in categories
    assert "sheet_manager_revoked" not in categories


# ---------- archived 守卫 ----------
@pytest.mark.asyncio
async def test_archived_rejects_manager_mutation(client, tmp_path, monkeypatch):
    _patch_archive_root(monkeypatch, tmp_path)
    _, owner_bearer = await _make_player("alice")
    other_u, _ = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    archived = await client.post(
        f"/sheets/{sid}/advance?to=archived", headers=_auth(owner_bearer)
    )
    assert archived.status_code == 200
    grant = await _grant(client, owner_bearer, sid, other_u)
    assert grant.status_code == 409


def _patch_archive_root(monkeypatch, tmp_path):
    """注入 archive_root=tmp_path 给 api 层的 get_settings()。"""
    import app.api.sheets.lifecycle as lifecycle_mod
    from app.core.config import Settings

    real = Settings()
    real.archive_root = str(tmp_path)
    monkeypatch.setattr(lifecycle_mod, "get_settings", lambda: real)


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
    entry = body["managers"][0]
    assert entry["web_account_id"] == await _account_id_of(mgr_u)
    assert entry["display_name"] == "bob"
    assert str(mgr_u) in entry["member_uuids"]


@pytest.mark.asyncio
async def test_detail_includes_viewer_uuids(client):
    """SheetDetail 含 viewer_uuids（HEAD account 级可见性）。"""
    owner_u, owner_bearer = await _make_player("alice")
    sid = await _create_sheet(client, owner_bearer)
    resp = await client.get(f"/sheets/{sid}", headers=_auth(owner_bearer))
    body = resp.json()
    assert "viewer_uuids" in body
    assert str(owner_u) in body["viewer_uuids"]


# ---------- list_sheets 参与排序 UNION 第 4 源 ----------
@pytest.mark.asyncio
async def test_list_sheets_includes_managed_in_involved(client):
    """manager 关系表纳入「参与过」UNION（viewer_web_account_id 透传）。"""
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    sid_a = await _create_sheet(client, owner_bearer, "A")
    sid_b = await _create_sheet(client, owner_bearer, "B")
    await _grant(client, owner_bearer, sid_a, mgr_u)

    resp = await client.get("/sheets", headers=_auth(mgr_bearer))
    assert resp.status_code == 200
    titles = [s["title"] for s in resp.json()]
    assert "A" in titles and "B" in titles
    # A（bob 是 manager）应在 B（无参与）之前
    assert titles.index("A") < titles.index("B")


# ---------- SuperAdmin（WebAccount.role='admin'，非 owner）tier A 通过 ----------
@pytest.mark.asyncio
async def test_super_admin_can_grant_manager(client):
    """SuperAdmin（非 owner）可授予 manager（tier A 通过 _resolve_role 权威）。"""
    _, owner_bearer = await _make_player("alice")
    target_u, _ = await _make_player("bob")
    _, admin_bearer = await _make_player("admin", role="admin")
    sid = await _create_sheet(client, owner_bearer)
    resp = await _grant(client, admin_bearer, sid, target_u)
    assert resp.status_code == 201


# ==========================================================================
# §8 权限矩阵 M01–M26 测试子集
# 本文件覆盖 M01–M04 / M06 / M08–M10 / M13–M18 / M21–M22 / M24–M26
# （M05 / M07 / M11 / M12 / M19 / M20 / M23 由集成阶段手写，不在此处）
#
# 身份档位：
# - Owner-S：owner 账号唯一 UUID（基线）
# - Owner-M：owner 账号另一 UUID（验 account 继承）
# - SuperAdmin：``WebAccount.role='admin'`` 非 owner
# - Mgr-Other：独立账号的 manager
# - Unbound：``web_account_id IS NULL``，走 service-token 通道
# - Stranger：任意他人（已绑账号但与 sheet 无关系）
# ==========================================================================


def _svc_auth(player_uuid: uuid.UUID) -> dict[str, str]:
    """service-token + X-Player-UUID 代玩家（Unbound 身份用）。"""
    return {"X-Service-Token": "svc", "X-Player-UUID": str(player_uuid)}


async def _make_unbound(name: str = "unbound") -> tuple[uuid.UUID, dict[str, str]]:
    """未绑 Web 账号的 Player（web_account_id IS NULL），用 service-token 通道。"""
    u = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=u, current_name=name))
        await s.commit()
    return u, _svc_auth(u)


async def _claim(client, auth: dict, sid: int, rid: int):
    return await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=auth)


async def _delivery(client, auth: dict, sid: int, rid: int, qty: int = 5):
    return await client.patch(
        f"/sheets/{sid}/rows/{rid}/delivery",
        json={"delivered_qty": qty},
        headers=auth,
    )


async def _progress(client, auth: dict, sid: int, rid: int, qty: int = 5):
    return await client.patch(
        f"/sheets/{sid}/rows/{rid}/progress",
        json={"delivered_qty": qty},
        headers=auth,
    )


async def _release(client, auth: dict, sid: int, rid: int):
    return await client.post(f"/sheets/{sid}/rows/{rid}/release", headers=auth)


async def _reject(client, auth: dict, sid: int, rid: int):
    return await client.post(f"/sheets/{sid}/rows/{rid}/reject", headers=auth)


async def _delete_row(client, auth: dict, sid: int, rid: int):
    return await client.delete(f"/sheets/{sid}/rows/{rid}", headers=auth)


# ---------- M01 Owner-S tier A 基线 ----------
@pytest.mark.asyncio
async def test_m01_owner_s_tier_a_baseline(client, tmp_path, monkeypatch):
    """M01：Owner-S 跑全套 tier A → 全 2xx（基线）。"""
    _patch_archive_root(monkeypatch, tmp_path)
    _, owner_bearer = await _make_player("alice")
    sid = await _create_sheet(client, owner_bearer)
    auth = _auth(owner_bearer)

    # patch（rename）
    assert (
        await client.patch(
            f"/sheets/{sid}", json={"title": "S2"}, headers=auth
        )
    ).status_code == 200
    # advance→archived
    archived = await client.post(
        f"/sheets/{sid}/advance?to=archived", headers=auth
    )
    assert archived.status_code == 200


# ---------- M02 Owner-M tier A account 继承核心 ----------
@pytest.mark.asyncio
async def test_m02_owner_m_tier_a_account_inheritance(client):
    """M02：Owner-M（同账号另一 UUID）跑 tier A → 全 2xx（account 级继承核心）。"""
    owner_u, owner_bearer = await _make_player("alice")
    sid = await _create_sheet(client, owner_bearer)
    owner_account_id = await _account_id_of(owner_u)
    _, owner_m_bearer = await _make_player_with_account_id(
        owner_account_id, "alice_alt"
    )
    auth_m = _auth(owner_m_bearer)

    # patch
    assert (
        await client.patch(
            f"/sheets/{sid}", json={"title": "by_alt"}, headers=auth_m
        )
    ).status_code == 200
    # delete
    assert (
        await client.delete(f"/sheets/{sid}", headers=auth_m)
    ).status_code == 204


# ---------- M03 Owner-M set_row_delivery（Owner-S 认领的行）----------
@pytest.mark.asyncio
async def test_m03_owner_m_delivers_owner_s_claim(client):
    """M03：Owner-M 给 Owner-S 认领的行上报交付 → 2xx（同 account UUID 共享 claimant）。"""
    owner_u, owner_bearer = await _make_player("alice")
    sid = await _create_sheet(client, owner_bearer)
    rid = (
        await _upsert_row(client, owner_bearer, sid, "iron", 10, 0)
    ).json()["id"]
    # Owner-S 认领
    assert (
        await _claim(client, _auth(owner_bearer), sid, rid)
    ).status_code == 200
    # Owner-M（同账号）上报交付
    owner_account_id = await _account_id_of(owner_u)
    _, owner_m_bearer = await _make_player_with_account_id(
        owner_account_id, "alice_alt"
    )
    resp = await _delivery(client, _auth(owner_m_bearer), sid, rid, 5)
    assert resp.status_code == 200, resp.text


# ---------- M04 SuperAdmin 全部 tier A+B 通过 ----------
@pytest.mark.asyncio
async def test_m04_super_admin_all_pass(client):
    """M04：SuperAdmin（``WebAccount.role='admin'``）跑 tier A+B → 全通过。"""
    _, owner_bearer = await _make_player("alice")
    sid = await _create_sheet(client, owner_bearer)
    _, admin_bearer = await _make_player("admin", role="admin")
    auth_adm = _auth(admin_bearer)

    # tier A：patch
    assert (
        await client.patch(
            f"/sheets/{sid}", json={"title": "admin"}, headers=auth_adm
        )
    ).status_code == 200
    # tier B：upsert_row
    assert (
        await _upsert_row(client, admin_bearer, sid, "by_admin", 5, 0)
    ).status_code == 200


# ---------- M06 Mgr-Other tier A 拒 ----------
@pytest.mark.asyncio
async def test_m06_mgr_other_tier_a_rejected(client):
    """M06：Mgr-Other 跑 tier A（patch/delete/advance→archived/grant/revoke）→ 403。"""
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    third_u, _ = await _make_player("carol")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)
    auth_mgr = _auth(mgr_bearer)

    # patch
    assert (
        await client.patch(
            f"/sheets/{sid}", json={"title": "hacked"}, headers=auth_mgr
        )
    ).status_code == 403
    # delete
    assert (
        await client.delete(f"/sheets/{sid}", headers=auth_mgr)
    ).status_code == 403
    # advance→archived
    assert (
        await client.post(
            f"/sheets/{sid}/advance?to=archived", headers=auth_mgr
        )
    ).status_code == 403
    # grant
    assert (
        await _grant(client, mgr_bearer, sid, third_u)
    ).status_code == 403


# ---------- M08 Mgr-Other tier B 通过 ----------
@pytest.mark.asyncio
async def test_m08_mgr_other_tier_b_pass(client):
    """M08：Mgr-Other upsert/delete/progress/reject 全 2xx。"""
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)
    auth_mgr = _auth(mgr_bearer)

    # upsert_row（progress 行）
    ups = await _upsert_row(client, mgr_bearer, sid, "iron", 10, mode=1)
    assert ups.status_code == 200, ups.text
    rid = ups.json()["id"]
    # progress（tier B）
    assert (
        await _progress(client, auth_mgr, sid, rid, 5)
    ).status_code == 200
    # delete_row
    assert (
        await _delete_row(client, auth_mgr, sid, rid)
    ).status_code == 204


# ---------- M09 Mgr-Other self-revoke ----------
@pytest.mark.asyncio
async def test_m09_mgr_other_self_revoke(client):
    """M09：Mgr-Other self-revoke 自己 web_account_id → 2xx。"""
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)
    mgr_account_id = await _account_id_of(mgr_u)
    resp = await _revoke_by_account(client, mgr_bearer, sid, mgr_account_id)
    assert resp.status_code == 200


# ---------- M10 Mgr-Other release_row（他人 claimant 的行）----------
@pytest.mark.asyncio
async def test_m10_mgr_other_release_other_claim(client):
    """M10：Mgr-Other release 他人认领的行 → 2xx（tier B）。"""
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    claimer_u, claimer_bearer = await _make_player("carol")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)
    rid = (
        await _upsert_row(client, owner_bearer, sid, "iron", 10, 0)
    ).json()["id"]
    # carol 认领
    assert (
        await _claim(client, _auth(claimer_bearer), sid, rid)
    ).status_code == 200
    # manager 解除 → tier B
    resp = await _release(client, _auth(mgr_bearer), sid, rid)
    assert resp.status_code == 200, resp.text


# ---------- M13 Mgr-Other claim/contribute 公开协作 ----------
@pytest.mark.asyncio
async def test_m13_mgr_other_public_collab(client):
    """M13：Mgr-Other claim_row / contribute → 2xx（公开协作，与 manager 身份无关）。"""
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)
    rid_lock = (
        await _upsert_row(client, owner_bearer, sid, "lock", 10, 0)
    ).json()["id"]
    rid_prog = (
        await _upsert_row(client, owner_bearer, sid, "prog", 10, 1)
    ).json()["id"]
    auth_mgr = _auth(mgr_bearer)
    # claim（公开）
    assert (
        await _claim(client, auth_mgr, sid, rid_lock)
    ).status_code == 200
    # contribute（公开）
    assert (
        await client.post(
            f"/sheets/{sid}/rows/{rid_prog}/contribute",
            json={"qty": 3},
            headers=auth_mgr,
        )
    ).status_code == 200


# ---------- M14 Unbound tier A 拒 ----------
@pytest.mark.asyncio
async def test_m14_unbound_tier_a_rejected(client):
    """M14：Unbound（web_account_id IS NULL）tier A → 403（owner 回退 {self.uuid}）。"""
    _, owner_bearer = await _make_player("alice")
    sid = await _create_sheet(client, owner_bearer)
    unbound_u, unbound_auth = await _make_unbound("unbound")

    # patch
    assert (
        await client.patch(
            f"/sheets/{sid}", json={"title": "x"}, headers=unbound_auth
        )
    ).status_code == 403
    # delete
    assert (
        await client.delete(f"/sheets/{sid}", headers=unbound_auth)
    ).status_code == 403
    # advance→archived
    assert (
        await client.post(
            f"/sheets/{sid}/advance?to=archived", headers=unbound_auth
        )
    ).status_code == 403
    # grant（target 随便）
    grant_body = {"player_uuid": str(unbound_u)}
    assert (
        await client.post(
            f"/sheets/{sid}/managers", json=grant_body, headers=unbound_auth
        )
    ).status_code == 403


# ---------- M15 Unbound upsert_row（他人项目）----------
@pytest.mark.asyncio
async def test_m15_unbound_cannot_upsert(client):
    """M15：Unbound 他人项目 upsert_row → 403（manager 列无该 account）。"""
    _, owner_bearer = await _make_player("alice")
    sid = await _create_sheet(client, owner_bearer)
    _, unbound_auth = await _make_unbound("unbound")
    resp = await client.put(
        f"/sheets/{sid}/rows",
        json={"item_name": "x", "need_qty": 1, "mode": 0, "sort_order": 0},
        headers=unbound_auth,
    )
    assert resp.status_code == 403


# ---------- M16 Unbound set_row_delivery（自己 claimant）----------
@pytest.mark.asyncio
async def test_m16_unbound_delivers_self_claim(client):
    """M16：Unbound 自己 claim 的行上报交付 → 2xx（claimant 走 UUID）。"""
    _, owner_bearer = await _make_player("alice")
    sid = await _create_sheet(client, owner_bearer)
    rid = (
        await _upsert_row(client, owner_bearer, sid, "iron", 10, 0)
    ).json()["id"]
    _, unbound_auth = await _make_unbound("unbound")
    # 先认领
    assert (
        await _claim(client, unbound_auth, sid, rid)
    ).status_code == 200
    # 上报交付（claimant_uuid in {self.uuid}）
    resp = await _delivery(client, unbound_auth, sid, rid, 5)
    assert resp.status_code == 200, resp.text


# ---------- M17 Stranger 所有 tier A+B 拒 ----------
@pytest.mark.asyncio
async def test_m17_stranger_all_rejected(client):
    """M17：Stranger（已绑账号但与 sheet 无关系）所有 tier A+B → 403。"""
    _, owner_bearer = await _make_player("alice")
    sid = await _create_sheet(client, owner_bearer)
    _, stranger_bearer = await _make_player("stranger")
    auth_s = _auth(stranger_bearer)
    rid = (
        await _upsert_row(client, owner_bearer, sid, "iron", 10, 0)
    ).json()["id"]

    # tier A
    assert (
        await client.patch(
            f"/sheets/{sid}", json={"title": "x"}, headers=auth_s
        )
    ).status_code == 403
    # tier B：upsert
    assert (
        await client.put(
            f"/sheets/{sid}/rows",
            json={"item_name": "y", "need_qty": 1, "mode": 0, "sort_order": 0},
            headers=auth_s,
        )
    ).status_code == 403
    # tier B：release others' claim
    assert (
        await _release(client, auth_s, sid, rid)
    ).status_code == 403


# ---------- M18 Stranger claim/contribute 公开协作 ----------
@pytest.mark.asyncio
async def test_m18_stranger_public_collab(client):
    """M18：Stranger claim/contribute → 2xx（公开协作）。"""
    _, owner_bearer = await _make_player("alice")
    sid = await _create_sheet(client, owner_bearer)
    rid_lock = (
        await _upsert_row(client, owner_bearer, sid, "lock", 10, 0)
    ).json()["id"]
    rid_prog = (
        await _upsert_row(client, owner_bearer, sid, "prog", 10, 1)
    ).json()["id"]
    _, stranger_bearer = await _make_player("stranger")
    auth_s = _auth(stranger_bearer)
    assert (
        await _claim(client, auth_s, sid, rid_lock)
    ).status_code == 200
    assert (
        await client.post(
            f"/sheets/{sid}/rows/{rid_prog}/contribute",
            json={"qty": 3},
            headers=auth_s,
        )
    ).status_code == 200


# ---------- M21 Owner-S grant 幂等不重发通知 ----------
@pytest.mark.asyncio
async def test_m21_grant_idempotent_no_renotify(client):
    """M21：Owner-S 重复 grant 同一目标 → 201 + 不重发通知（PK 防重复）。"""
    _, owner_bearer = await _make_player("alice")
    target_u, _ = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    first = await _grant(client, owner_bearer, sid, target_u)
    assert first.status_code == 201
    second = await _grant(client, owner_bearer, sid, target_u)
    assert second.status_code == 201
    notes = await _notifications_for(target_u)
    granted = [n for n in notes if n[0] == "sheet_manager_granted"]
    assert len(granted) == 1


# ---------- M22 Mgr-Other revoke_manager（他人 manager）403 ----------
@pytest.mark.asyncio
async def test_m22_mgr_other_revoke_other_forbidden(client):
    """M22：Mgr-Other 撤销另一 manager → 403（self-revoke 不覆盖他人）。"""
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    other_mgr_u, _ = await _make_player("carol")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)
    await _grant(client, owner_bearer, sid, other_mgr_u)
    other_mgr_aid = await _account_id_of(other_mgr_u)
    resp = await _revoke_by_account(client, mgr_bearer, sid, other_mgr_aid)
    assert resp.status_code == 403


# ---------- M24 Mgr-Other self-revoke 已被 owner 先撤 → 404 ----------
@pytest.mark.asyncio
async def test_m24_self_revoke_after_owner_removed_404(client):
    """M24：Mgr-Other self-revoke，但 owner 已先撤 → 404（并发）。"""
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)
    mgr_aid = await _account_id_of(mgr_u)
    # owner 先撤
    assert (
        await _revoke_by_account(client, owner_bearer, sid, mgr_aid)
    ).status_code == 200
    # mgr 再 self-revoke → 404
    resp = await _revoke_by_account(client, mgr_bearer, sid, mgr_aid)
    assert resp.status_code == 404


# ---------- M25/M26 archived 终态守卫优先于权限 ----------
@pytest.mark.asyncio
async def test_m25_advance_constructing_on_archived_409(client, tmp_path, monkeypatch):
    """M25：archived 项目 advance→constructing → 409（终态守卫先于权限）。"""
    _patch_archive_root(monkeypatch, tmp_path)
    _, owner_bearer = await _make_player("alice")
    sid = await _create_sheet(client, owner_bearer)
    # 先归档（collecting → archived 跳过施工）
    archived = await client.post(
        f"/sheets/{sid}/advance?to=archived", headers=_auth(owner_bearer)
    )
    assert archived.status_code == 200
    # owner 自己（tier A+B 全过）尝试 advance→constructing → 409
    resp = await client.post(
        f"/sheets/{sid}/advance?to=constructing", headers=_auth(owner_bearer)
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_m26_upsert_row_on_archived_409(client, tmp_path, monkeypatch):
    """M26：archived 项目 upsert_row → 409（终态守卫先于权限）。"""
    _patch_archive_root(monkeypatch, tmp_path)
    _, owner_bearer = await _make_player("alice")
    sid = await _create_sheet(client, owner_bearer)
    await client.post(f"/sheets/{sid}/advance?to=archived", headers=_auth(owner_bearer))
    resp = await client.put(
        f"/sheets/{sid}/rows",
        json={"item_name": "x", "need_qty": 1, "mode": 0, "sort_order": 0},
        headers=_auth(owner_bearer),
    )
    assert resp.status_code == 409


# ==========================================================================
# 集成阶段手写的高风险跨切用例（M05 / M07 / M11 / M12 / M23）
# 注：M19（grant owner 同账号 → 409）= test_grant_owner_account_as_manager_rejected_409；
#     M20（grant target 未绑 → 422）= test_grant_target_unbound_account_rejected_422，
#     均已由上方覆盖，此处不重复。
# ==========================================================================


@pytest.mark.asyncio
async def test_m05_account_role_overrides_stale_player_role(client):
    """M05（B1 关键）：``WebAccount.role='admin'`` 但 ``Player.role='user'`` → tier A 仍通过。

    ``_is_superuser`` 走 ``_resolve_role``（account 级权威），player.role 旧值不影响
    tier A 判定，与 ``require_role`` dep 同源不撕裂。
    """
    _, owner_bearer = await _make_player("alice")
    target_u, _ = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)

    # 构造 divergence：account.role='admin'、player.role='user'
    from app.models.user import WebAccount

    admin_u = uuid.uuid4()
    async with async_session_factory() as s:
        acct = WebAccount(role="admin")
        s.add(acct)
        await s.flush()
        s.add(
            Player(
                uuid=admin_u, current_name="adm", role="user", web_account_id=acct.id
            )
        )
        await s.commit()
        aid = acct.id
    bearer = f"Bearer {create_access_token(aid, 'admin', active_uuid=admin_u)}"
    # tier A：admin（player.role 仍是 'user'）授予 manager → 201
    resp = await _grant(client, bearer, sid, target_u)
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_m07_manager_advance_constructing_owner_can_still_archive(
    client, tmp_path, monkeypatch
):
    """M07（行为变化点）：Mgr-Other 可 advance→constructing（HEAD 原 tier A，融合后 tier B）；
    owner 之后仍可归档（tier A 链路未受影响）。"""
    _patch_archive_root(monkeypatch, tmp_path)
    _, owner_bearer = await _make_player("alice")
    mgr_u, mgr_bearer = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, mgr_u)

    # manager 推进到施工（tier B）
    resp = await client.post(
        f"/sheets/{sid}/advance?to=constructing", headers=_auth(mgr_bearer)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "constructing"
    # owner 仍可归档（tier A 未受影响）
    arch = await client.post(
        f"/sheets/{sid}/advance?to=archived", headers=_auth(owner_bearer)
    )
    assert arch.status_code == 200


@pytest.mark.asyncio
async def test_m11_mgr_same_account_second_uuid_inherits_tier_b(client):
    """M11：授予 manager 后，同账号下【已存在】的另一 UUID 也继承 tier B（account 锚）。"""
    _, owner_bearer = await _make_player("alice")
    mgr_u, _ = await _make_player("bob")  # account X
    sid = await _create_sheet(client, owner_bearer)
    mgr_aid = await _account_id_of(mgr_u)
    # 同账号再加一个 UUID（在 grant 之前已存在）
    _, mgr_alt_bearer = await _make_player_with_account_id(mgr_aid, "bob_alt")
    # 授予 mgr_u（account X 成为 manager）
    await _grant(client, owner_bearer, sid, mgr_u)
    # mgr_alt（同 account X，非授予目标）做 tier B → 200（account 继承）
    resp = await _upsert_row(client, mgr_alt_bearer, sid, "iron", 5, 0)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_m12_mgr_bind_after_grant_inherits_tier_b(client):
    """M12（account 继承核心）：UUID_A 被授 manager 后，再绑同账号的 UUID_B 也继承 tier B。"""
    _, owner_bearer = await _make_player("alice")
    mgr_u, _ = await _make_player("bob")  # account X
    sid = await _create_sheet(client, owner_bearer)
    mgr_aid = await _account_id_of(mgr_u)
    await _grant(client, owner_bearer, sid, mgr_u)
    # grant 之后再绑 UUID_B 到同 account X
    _, mgr_b_bearer = await _make_player_with_account_id(mgr_aid, "bob_b")
    resp = await _upsert_row(client, mgr_b_bearer, sid, "iron", 5, 0)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_m23_unbound_cannot_self_revoke(client):
    """M23（B6）：未绑账号玩家（``web_account_id IS NULL``）self-revoke → 403。

    ``player.web_account_id is not None and web_account_id == player.web_account_id``
    守卫显式拒 ``None``，防 Python ``None == None`` 误匹配；未绑玩家非 owner → 403。
    """
    _, owner_bearer = await _make_player("alice")
    target_u, _ = await _make_player("bob")
    sid = await _create_sheet(client, owner_bearer)
    await _grant(client, owner_bearer, sid, target_u)
    target_aid = await _account_id_of(target_u)
    # 未绑玩家走 service-token 通道，尝试 revoke target 的 account
    _, unbound_auth = await _make_unbound("unbound")
    resp = await client.request(
        "DELETE",
        f"/sheets/{sid}/managers",
        json={"web_account_id": target_aid},
        headers=unbound_auth,
    )
    assert resp.status_code == 403
