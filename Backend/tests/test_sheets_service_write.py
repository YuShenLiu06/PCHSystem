"""sheets 写通道：service-token + X-Player-UUID 代理鉴权 + 通知触发集成测试。

覆盖（plan Teammate-Backend §3）：
1. 身份注入：双通道（JWT / service-token+UUID）对同一玩家等价
2. RBAC：认领人/owner/他人 403
3. 状态机 409
4. 每个写操作触发正确 category + 接收者 + payload 的通知（查 notifications 表断言）
5. actor==recipient 时不发通知（如 owner 是 owner 的自操作）
"""
import uuid

import pytest

import app.api.deps as deps
from app.core.config import get_settings
from app.core.db import async_session_factory
from app.models.notification import Notification
from app.models.user import Player, WebAccount
from sqlalchemy import select
from tests.conftest import seed_player_with_account


@pytest.fixture(autouse=True)
def _svc_token(monkeypatch):
    deps._settings = get_settings()
    deps._settings.mcdr_service_token = "svc"


_BEARER_CACHE: dict[uuid.UUID, str] = {}


async def _seed(name: str, role: str = "user") -> uuid.UUID:
    """seed player + 临时 WebAccount，返回 player UUID。

    JWT bearer 缓存到 _BEARER_CACHE，供 _jwt_headers(uuid) 同步取用。
    """
    player_uuid, bearer = await seed_player_with_account(name=name, role=role)
    _BEARER_CACHE[player_uuid] = bearer
    return player_uuid


def _jwt_headers(u: uuid.UUID, role: str = "user") -> dict[str, str]:
    """从缓存取 bearer（_seed 已写入）。"""
    return {"Authorization": _BEARER_CACHE[u]}


def _svc_headers(u: uuid.UUID) -> dict[str, str]:
    return {"X-Service-Token": "svc", "X-Player-UUID": str(u)}


async def _create_sheet(client, headers, title: str = "S") -> int:
    resp = await client.post("/sheets", json={"title": title}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _upsert(client, headers, sid, item="iron", need=10, mode=0) -> dict:
    resp = await client.put(
        f"/sheets/{sid}/rows",
        json={"item_name": item, "need_qty": need, "mode": mode, "sort_order": 0},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _fetch_notifications(recipient: uuid.UUID) -> list[Notification]:
    async with async_session_factory() as s:
        rows = (
            await s.execute(
                select(Notification).where(Notification.recipient_uuid == recipient)
            )
        ).scalars().all()
        return list(rows)


# ---------- 1. 双通道等价 ----------
@pytest.mark.asyncio
async def test_jwt_and_service_token_channels_equivalent(client):
    owner = await _seed("alice")
    sid = await _create_sheet(client, _jwt_headers(owner))
    # JWT 通道 GET
    r1 = await client.get(f"/sheets/{sid}", headers=_jwt_headers(owner))
    # service-token+UUID 通道 GET
    r2 = await client.get(f"/sheets/{sid}", headers=_svc_headers(owner))
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["title"] == r2.json()["title"]


@pytest.mark.asyncio
async def test_service_token_channel_can_write_claim(client):
    owner = await _seed("alice")
    claimer = await _seed("bob")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid))["id"]
    # claimer 用 service-token+UUID 头认领（不经 JWT）
    resp = await client.post(
        f"/sheets/{sid}/rows/{rid}/claim", headers=_svc_headers(claimer)
    )
    assert resp.status_code == 200
    assert resp.json()["claimant_name"] == "bob"


@pytest.mark.asyncio
async def test_service_token_invalid_returns_401(client):
    owner = await _seed("alice")
    resp = await client.get(
        "/sheets",
        headers={"X-Service-Token": "bad", "X-Player-UUID": str(owner)},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_service_token_unknown_player_uuid_returns_401(client):
    random_uuid = uuid.uuid4()
    resp = await client.get(
        "/sheets", headers={"X-Service-Token": "svc", "X-Player-UUID": str(random_uuid)}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_no_credentials_returns_401(client):
    assert (await client.get("/sheets")).status_code == 401


# ---------- 2. RBAC（双通道同样生效） ----------
@pytest.mark.asyncio
async def test_service_token_non_owner_upsert_forbidden(client):
    owner = await _seed("alice")
    other = await _seed("bob")
    sid = await _create_sheet(client, _jwt_headers(owner))
    resp = await client.put(
        f"/sheets/{sid}/rows",
        json={"item_name": "iron", "need_qty": 1, "mode": 0, "sort_order": 0},
        headers=_svc_headers(other),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_service_token_non_claimant_delivery_forbidden(client):
    owner = await _seed("alice")
    claimer = await _seed("bob")
    third = await _seed("carol")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid))["id"]
    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_svc_headers(claimer))
    resp = await client.patch(
        f"/sheets/{sid}/rows/{rid}/delivery",
        json={"delivered_qty": 5},
        headers=_svc_headers(third),
    )
    assert resp.status_code == 403


# ---------- 3. 状态机 409 ----------
@pytest.mark.asyncio
async def test_claim_done_row_conflict_409(client):
    owner = await _seed("alice")
    bob = await _seed("bob")
    carol = await _seed("carol")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid))["id"]
    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_svc_headers(bob))
    await client.patch(
        f"/sheets/{sid}/rows/{rid}/delivery",
        json={"delivered_qty": 10},
        headers=_svc_headers(bob),
    )
    resp = await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_svc_headers(carol))
    assert resp.status_code == 409


# ---------- 4. 通知触发规则（核心） ----------
@pytest.mark.asyncio
async def test_claim_notifies_owner(client):
    owner = await _seed("alice")
    bob = await _seed("bob")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid, "iron", 10))["id"]

    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_svc_headers(bob))

    notes = await _fetch_notifications(owner)
    assert len(notes) == 1
    n = notes[0]
    assert n.category == "sheet_claimed"
    assert n.recipient_uuid == owner
    assert "bob" in n.body and "iron" in n.body
    assert n.payload["actor_name"] == "bob"
    assert n.payload["item_name"] == "iron"
    assert n.payload["sheet_id"] == sid


@pytest.mark.asyncio
async def test_delivery_partial_notifies_owner_delivered(client):
    owner = await _seed("alice")
    bob = await _seed("bob")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid, "iron", 10))["id"]
    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_svc_headers(bob))

    await client.patch(
        f"/sheets/{sid}/rows/{rid}/delivery",
        json={"delivered_qty": 5},
        headers=_svc_headers(bob),
    )

    notes = await _fetch_notifications(owner)
    cats = [n.category for n in notes]
    assert "sheet_delivered" in cats
    delivered = [n for n in notes if n.category == "sheet_delivered"][0]
    assert delivered.payload["delivered"] == 5
    assert delivered.payload["need"] == 10


@pytest.mark.asyncio
async def test_delivery_meets_need_notifies_owner_done(client):
    owner = await _seed("alice")
    bob = await _seed("bob")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid, "iron", 10))["id"]
    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_svc_headers(bob))

    await client.patch(
        f"/sheets/{sid}/rows/{rid}/delivery",
        json={"delivered_qty": 10},
        headers=_svc_headers(bob),
    )

    notes = await _fetch_notifications(owner)
    assert any(n.category == "sheet_done" for n in notes)


@pytest.mark.asyncio
async def test_release_by_claimant_notifies_owner(client):
    owner = await _seed("alice")
    bob = await _seed("bob")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid, "iron", 10))["id"]
    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_svc_headers(bob))

    await client.post(f"/sheets/{sid}/rows/{rid}/release", headers=_svc_headers(bob))

    notes = await _fetch_notifications(owner)
    released = [n for n in notes if n.category == "sheet_released"]
    assert len(released) == 1
    assert "取消" in released[0].body


@pytest.mark.asyncio
async def test_release_by_owner_notifies_claimant(client):
    owner = await _seed("alice")
    bob = await _seed("bob")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid, "iron", 10))["id"]
    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_svc_headers(bob))

    await client.post(f"/sheets/{sid}/rows/{rid}/release", headers=_svc_headers(owner))

    notes = await _fetch_notifications(bob)
    assert len(notes) == 1
    assert notes[0].category == "sheet_released"
    assert "解除" in notes[0].body


@pytest.mark.asyncio
async def test_reject_by_owner_notifies_claimant(client):
    owner = await _seed("alice")
    bob = await _seed("bob")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid, "iron", 10))["id"]
    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_svc_headers(bob))
    await client.patch(
        f"/sheets/{sid}/rows/{rid}/delivery",
        json={"delivered_qty": 10},
        headers=_svc_headers(bob),
    )

    await client.post(f"/sheets/{sid}/rows/{rid}/reject", headers=_svc_headers(owner))

    notes = await _fetch_notifications(bob)
    rejected = [n for n in notes if n.category == "sheet_rejected"]
    assert len(rejected) == 1
    assert "打回" in rejected[0].body


@pytest.mark.asyncio
async def test_upsert_change_need_notifies_claimant_with_old_new(client):
    owner = await _seed("alice")
    bob = await _seed("bob")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid, "iron", 10))["id"]
    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_svc_headers(bob))

    await client.put(
        f"/sheets/{sid}/rows",
        json={"row_id": rid, "need_qty": 20},
        headers=_svc_headers(owner),
    )

    notes = await _fetch_notifications(bob)
    assert len(notes) == 1
    n = notes[0]
    assert n.category == "sheet_qty_changed"
    assert n.payload["old"] == 10
    assert n.payload["new"] == 20


@pytest.mark.asyncio
async def test_upsert_no_change_does_not_notify(client):
    owner = await _seed("alice")
    bob = await _seed("bob")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid, "iron", 10))["id"]
    # bob 没认领，按 row_id 更新且 need_qty 不变 → 不应通知 bob（也无 claimant）
    await client.put(
        f"/sheets/{sid}/rows",
        json={"row_id": rid, "need_qty": 10},
        headers=_svc_headers(owner),
    )
    notes = await _fetch_notifications(bob)
    assert notes == []


@pytest.mark.asyncio
async def test_delete_row_notifies_claimant(client):
    owner = await _seed("alice")
    bob = await _seed("bob")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid, "iron", 10))["id"]
    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_svc_headers(bob))

    await client.delete(f"/sheets/{sid}/rows/{rid}", headers=_svc_headers(owner))

    notes = await _fetch_notifications(bob)
    assert len(notes) == 1
    assert notes[0].category == "sheet_row_deleted"


@pytest.mark.asyncio
async def test_delete_sheet_notifies_all_claimants(client):
    owner = await _seed("alice")
    bob = await _seed("bob")
    carol = await _seed("carol")
    sid = await _create_sheet(client, _jwt_headers(owner))
    r1 = (await _upsert(client, _jwt_headers(owner), sid, "iron", 10))["id"]
    r2 = (await _upsert(client, _jwt_headers(owner), sid, "gold", 5))["id"]
    await client.post(f"/sheets/{sid}/rows/{r1}/claim", headers=_svc_headers(bob))
    await client.post(f"/sheets/{sid}/rows/{r2}/claim", headers=_svc_headers(carol))

    await client.delete(f"/sheets/{sid}", headers=_svc_headers(owner))

    bob_notes = await _fetch_notifications(bob)
    carol_notes = await _fetch_notifications(carol)
    assert len(bob_notes) == 1 and bob_notes[0].category == "sheet_row_deleted"
    assert len(carol_notes) == 1 and carol_notes[0].category == "sheet_row_deleted"


# ---------- 5. actor==recipient 跳过 ----------
@pytest.mark.asyncio
async def test_owner_claiming_own_row_no_self_notify(client):
    """owner 认领自己的行：actor==recipient(owner) → 不给自己发通知。"""
    owner = await _seed("alice")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid, "iron", 10))["id"]

    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_svc_headers(owner))

    notes = await _fetch_notifications(owner)
    assert notes == []


@pytest.mark.asyncio
async def test_owner_reject_own_claimed_no_self_notify(client):
    """owner 既是拥有者又是认领人时打回自己：recipient=claimant=owner → 跳过。"""
    owner = await _seed("alice")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid, "iron", 10))["id"]
    await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=_svc_headers(owner))
    await client.patch(
        f"/sheets/{sid}/rows/{rid}/delivery",
        json={"delivered_qty": 10},
        headers=_svc_headers(owner),
    )
    await client.post(f"/sheets/{sid}/rows/{rid}/reject", headers=_svc_headers(owner))

    notes = await _fetch_notifications(owner)
    assert notes == []


# ---------- H-2: Authorization 非 Bearer 不静默降级 ----------
@pytest.mark.asyncio
async def test_non_bearer_authorization_does_not_fallback_to_service_token(client):
    """Authorization 头存在但非 Bearer → 只走 JWT 通道报 401，绝不降级 service-token。"""
    owner = await _seed("alice")
    # 带 X-Service-Token+UUID 本应能过，但 Authorization 非 Bearer 必须拒绝
    resp = await client.get(
        "/sheets",
        headers={
            "Authorization": "Basic abc123",
            "X-Service-Token": "svc",
            "X-Player-UUID": str(owner),
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_expired_bearer_jwt_does_not_fallback_to_service_token(client):
    """过期/非法 Bearer JWT → JWT 通道报 401，不降级。"""
    owner = await _seed("alice")
    resp = await client.get(
        "/sheets",
        headers={
            "Authorization": "Bearer not.a.valid.jwt",
            "X-Service-Token": "svc",
            "X-Player-UUID": str(owner),
        },
    )
    assert resp.status_code == 401


# ---------- H-1': config 启动校验 mcdr_service_token 非空 ----------
def test_config_rejects_empty_service_token():
    """空 MCDR_SERVICE_TOKEN 启动即 fail-fast（H-1'）。"""
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(mcdr_service_token="")


def test_config_accepts_non_empty_service_token():
    from app.core.config import Settings

    s = Settings(mcdr_service_token="non_empty_secret")
    assert s.mcdr_service_token == "non_empty_secret"


# ---------- 6. progress 行：contribute（service-token 通道等价） ----------
async def _svc_contribute(client, headers, sid, rid, qty):
    resp = await client.post(
        f"/sheets/{sid}/rows/{rid}/contribute",
        json={"qty": qty},
        headers=headers,
    )
    return resp


@pytest.mark.asyncio
async def test_service_token_channel_contribute_equivalent(client):
    """service-token + X-Player-UUID 代玩家 contribute，与 JWT 通道等价。"""
    owner = await _seed("alice")
    bob = await _seed("bob")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid, "iron", 10, mode=1))["id"]

    resp = await _svc_contribute(client, _svc_headers(bob), sid, rid, 4)
    assert resp.status_code == 200
    body = resp.json()
    assert body["delivered_qty"] == 4
    assert body["status"] == "claimed"
    assert len(body["contributors"]) == 1
    c = body["contributors"][0]
    assert str(bob) in c["member_uuids"]
    assert c["display_name"] == "bob"
    assert c["contributed_qty"] == 4


@pytest.mark.asyncio
async def test_service_token_contribute_lock_row_conflict(client):
    """lock 行经 service-token contribute → 409。"""
    owner = await _seed("alice")
    bob = await _seed("bob")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid, "iron", 10, mode=0))["id"]
    resp = await _svc_contribute(client, _svc_headers(bob), sid, rid, 1)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_service_token_contribute_done_row_conflict(client):
    """progress 行 done 后 service-token contribute → 409。"""
    owner = await _seed("alice")
    bob = await _seed("bob")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid, "iron", 10, mode=1))["id"]
    await _svc_contribute(client, _svc_headers(bob), sid, rid, 10)
    resp = await _svc_contribute(client, _svc_headers(bob), sid, rid, 1)
    assert resp.status_code == 409


# ---------- 7. contribute 通知 owner（sheet_delivered / sheet_done） ----------
@pytest.mark.asyncio
async def test_contribute_partial_notifies_owner_delivered_with_delta(client):
    """部分上交 → owner 收 sheet_delivered，payload 含 delta/delivered/need。"""
    owner = await _seed("alice")
    bob = await _seed("bob")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid, "iron", 10, mode=1))["id"]

    resp = await _svc_contribute(client, _svc_headers(bob), sid, rid, 3)
    assert resp.status_code == 200

    notes = await _fetch_notifications(owner)
    delivered = [n for n in notes if n.category == "sheet_delivered"]
    assert len(delivered) == 1
    n = delivered[0]
    assert n.recipient_uuid == owner
    assert n.payload["delta"] == 3
    assert n.payload["delivered"] == 3
    assert n.payload["need"] == 10
    assert n.payload["item_name"] == "iron"
    assert n.payload["sheet_id"] == sid
    assert n.payload["actor_name"] == "bob"


@pytest.mark.asyncio
async def test_contribute_meets_need_notifies_owner_done(client):
    """累计 >= need → owner 收 sheet_done（非 sheet_delivered），payload 含 delta。"""
    owner = await _seed("alice")
    bob = await _seed("bob")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid, "iron", 10, mode=1))["id"]

    await _svc_contribute(client, _svc_headers(bob), sid, rid, 10)

    notes = await _fetch_notifications(owner)
    assert any(n.category == "sheet_done" for n in notes)
    done = [n for n in notes if n.category == "sheet_done"][0]
    assert done.payload["delta"] == 10
    assert done.payload["delivered"] == 10
    assert done.payload["need"] == 10


@pytest.mark.asyncio
async def test_contribute_does_not_notify_contributor_self(client):
    """actor==贡献者（bob），bob 不应收到自己的上交通知。"""
    owner = await _seed("alice")
    bob = await _seed("bob")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid, "iron", 10, mode=1))["id"]

    await _svc_contribute(client, _svc_headers(bob), sid, rid, 3)

    assert (await _fetch_notifications(bob)) == []


# ---------- 8. 删 progress 行 / 删表 → 通知贡献者 sheet_row_deleted ----------
@pytest.mark.asyncio
async def test_delete_progress_row_notifies_contributors(client):
    """删 progress 行：每位贡献者收 sheet_row_deleted。"""
    owner = await _seed("alice")
    bob = await _seed("bob")
    carol = await _seed("carol")
    sid = await _create_sheet(client, _jwt_headers(owner))
    rid = (await _upsert(client, _jwt_headers(owner), sid, "iron", 10, mode=1))["id"]
    await _svc_contribute(client, _svc_headers(bob), sid, rid, 3)
    await _svc_contribute(client, _svc_headers(carol), sid, rid, 4)

    resp = await client.delete(
        f"/sheets/{sid}/rows/{rid}", headers=_svc_headers(owner)
    )
    assert resp.status_code == 204

    bob_notes = await _fetch_notifications(bob)
    carol_notes = await _fetch_notifications(carol)
    assert len(bob_notes) == 1
    assert bob_notes[0].category == "sheet_row_deleted"
    assert "贡献" in bob_notes[0].body
    assert len(carol_notes) == 1
    assert carol_notes[0].category == "sheet_row_deleted"


@pytest.mark.asyncio
async def test_delete_sheet_with_progress_rows_notifies_contributors(client):
    """删整张表：progress 行的贡献者各收一条 sheet_row_deleted。"""
    owner = await _seed("alice")
    bob = await _seed("bob")
    carol = await _seed("carol")
    sid = await _create_sheet(client, _jwt_headers(owner))
    r1 = (await _upsert(client, _jwt_headers(owner), sid, "iron", 10, mode=1))["id"]
    r2 = (await _upsert(client, _jwt_headers(owner), sid, "gold", 5, mode=1))["id"]
    await _svc_contribute(client, _svc_headers(bob), sid, r1, 3)
    await _svc_contribute(client, _svc_headers(carol), sid, r2, 2)

    resp = await client.delete(f"/sheets/{sid}", headers=_svc_headers(owner))
    assert resp.status_code == 204

    bob_notes = await _fetch_notifications(bob)
    carol_notes = await _fetch_notifications(carol)
    assert len(bob_notes) == 1 and bob_notes[0].category == "sheet_row_deleted"
    assert len(carol_notes) == 1 and carol_notes[0].category == "sheet_row_deleted"
