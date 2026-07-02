"""notifications API + service 单元测试。

覆盖：
- notification_service.notify 落库 + fetch_pending + mark_delivered + mark_read
- 同事务原子（rollback 则不落库，R-10）
- GET /notifications/pending：service-token 鉴权（无 token 401）、player_uuid 校验（404）
- POST /notifications/ack：标投递，返 acked
- POST /notifications/{id}/read：标已读，404 if not found

复用 test_sheets_api.py 的 _svc_token fixture 模式（注入 service token 到 deps._settings）。
"""
import uuid

import pytest

import app.api.deps as deps
from app.core.config import get_settings
from app.core.db import async_session_factory
from app.models.user import Player
from app.services import notification_service


@pytest.fixture(autouse=True)
def _svc_token(monkeypatch):
    deps._settings = get_settings()
    deps._settings.mcdr_service_token = "svc"


async def _seed_player(name: str = "alice") -> uuid.UUID:
    u = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=u, current_name=name))
        await s.commit()
    return u


def _svc_headers() -> dict[str, str]:
    return {"X-Service-Token": "svc"}


# ---------- service: notify 落库 + 拉取 ----------
@pytest.mark.asyncio
async def test_notify_persists_and_fetch_pending_returns_it():
    recipient = await _seed_player("alice")
    async with async_session_factory() as s:
        await notification_service.notify(
            s,
            recipient_uuid=recipient,
            category="sheet_claimed",
            title="物品被认领",
            body="bob 认领了 [铁锭]",
            payload={"sheet_id": 1, "item_name": "铁锭"},
        )
        await s.commit()

    async with async_session_factory() as s:
        pending = await notification_service.fetch_pending(s, recipient)
        assert len(pending) == 1
        rec = pending[0]
        assert rec.category == "sheet_claimed"
        assert rec.title == "物品被认领"
        assert rec.payload["item_name"] == "铁锭"
        assert rec.delivered_at is None
        assert rec.read_at is None


@pytest.mark.asyncio
async def test_notify_atomic_with_caller_session_rollback():
    """notify 在调用方事务内；rollback 则通知不落库（R-10 一致性）。"""
    recipient = await _seed_player("alice")
    async with async_session_factory() as s:
        await notification_service.notify(
            s, recipient, "sheet_claimed", "t", "b", payload={}
        )
        await s.flush()
        # 模拟业务失败 → rollback，通知随之消失
        await s.rollback()

    async with async_session_factory() as s:
        assert await notification_service.fetch_pending(s, recipient) == []


@pytest.mark.asyncio
async def test_fetch_pending_orders_oldest_first_and_respects_limit():
    recipient = await _seed_player("alice")
    async with async_session_factory() as s:
        for i in range(3):
            await notification_service.notify(
                s, recipient, "sheet_claimed", f"t{i}", f"b{i}", payload={}
            )
        await s.commit()
    async with async_session_factory() as s:
        pending = await notification_service.fetch_pending(s, recipient, limit=2)
        assert [r.title for r in pending] == ["t0", "t1"]


@pytest.mark.asyncio
async def test_mark_delivered_sets_delivered_at_and_excludes_them_from_pending():
    recipient = await _seed_player("alice")
    async with async_session_factory() as s:
        r1 = await notification_service.notify(s, recipient, "c", "t1", "b", payload={})
        await notification_service.notify(s, recipient, "c", "t2", "b", payload={})
        await s.commit()
        rid1 = r1.id

    async with async_session_factory() as s:
        n = await notification_service.mark_delivered(s, [rid1], recipient)
        await s.commit()
        assert n == 1

    async with async_session_factory() as s:
        pending = await notification_service.fetch_pending(s, recipient)
        assert [r.title for r in pending] == ["t2"]


@pytest.mark.asyncio
async def test_mark_read_sets_read_at():
    recipient = await _seed_player("alice")
    async with async_session_factory() as s:
        rec = await notification_service.notify(s, recipient, "c", "t", "b", payload={})
        await s.commit()
        rid = rec.id
    async with async_session_factory() as s:
        ok = await notification_service.mark_read(s, rid, recipient)
        await s.commit()
        assert ok
    async with async_session_factory() as s:
        rec = await notification_service.fetch_by_id_or_none(s, rid, recipient)
        assert rec is not None
        assert rec.read_at is not None


# ---------- API: pending 鉴权 ----------
@pytest.mark.asyncio
async def test_pending_requires_service_token(client):
    recipient = await _seed_player()
    # 无 service token → 401
    assert (
        await client.get(f"/notifications/pending?player_uuid={recipient}")
    ).status_code == 401
    # 错 token → 401
    resp = await client.get(
        f"/notifications/pending?player_uuid={recipient}",
        headers={"X-Service-Token": "bad"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_pending_unknown_player_returns_404(client):
    random_uuid = uuid.uuid4()
    resp = await client.get(
        f"/notifications/pending?player_uuid={random_uuid}",
        headers=_svc_headers(),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pending_missing_player_uuid_returns_400(client):
    resp = await client.get("/notifications/pending", headers=_svc_headers())
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_pending_returns_only_target_player_notifications(client):
    alice = await _seed_player("alice")
    bob = await _seed_player("bob")
    async with async_session_factory() as s:
        await notification_service.notify(
            s, alice, "sheet_claimed", "给 alice", "b", payload={"k": 1}
        )
        await notification_service.notify(
            s, bob, "sheet_claimed", "给 bob", "b", payload={"k": 2}
        )
        await s.commit()

    resp = await client.get(
        f"/notifications/pending?player_uuid={alice}", headers=_svc_headers()
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["title"] == "给 alice"
    assert body[0]["recipient_uuid"] == str(alice)
    assert body[0]["payload"]["k"] == 1


# ---------- API: ack ----------
@pytest.mark.asyncio
async def test_ack_marks_delivered_and_returns_count(client):
    recipient = await _seed_player()
    async with async_session_factory() as s:
        r1 = await notification_service.notify(s, recipient, "c", "t1", "b", payload={})
        r2 = await notification_service.notify(s, recipient, "c", "t2", "b", payload={})
        await s.commit()

    resp = await client.post(
        "/notifications/ack",
        json={"player_uuid": str(recipient), "ids": [r1.id, r2.id]},
        headers=_svc_headers(),
    )
    assert resp.status_code == 200
    assert resp.json() == {"acked": 2}

    # 全部已投递 → pending 空
    pending = (
        await client.get(
            f"/notifications/pending?player_uuid={recipient}", headers=_svc_headers()
        )
    ).json()
    assert pending == []


@pytest.mark.asyncio
async def test_ack_cross_player_is_noop(client):
    """C-1：用 bob 的 player_uuid ack alice 的通知 → 命中 0，alice 通知状态不变。"""
    alice = await _seed_player("alice")
    bob = await _seed_player("bob")
    async with async_session_factory() as s:
        rec = await notification_service.notify(s, alice, "c", "t", "b", payload={})
        await s.commit()
    resp = await client.post(
        "/notifications/ack",
        json={"player_uuid": str(bob), "ids": [rec.id]},
        headers=_svc_headers(),
    )
    assert resp.status_code == 200
    assert resp.json() == {"acked": 0}
    # alice 的通知仍未投递
    pending = (
        await client.get(
            f"/notifications/pending?player_uuid={alice}", headers=_svc_headers()
        )
    ).json()
    assert len(pending) == 1


@pytest.mark.asyncio
async def test_ack_requires_service_token(client):
    recipient = await _seed_player()
    assert (
        await client.post(
            "/notifications/ack",
            json={"player_uuid": str(recipient), "ids": []},
        )
    ).status_code == 401


@pytest.mark.asyncio
async def test_ack_unknown_player_returns_404(client):
    resp = await client.post(
        "/notifications/ack",
        json={"player_uuid": str(uuid.uuid4()), "ids": []},
        headers=_svc_headers(),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ack_empty_ids_returns_zero(client):
    recipient = await _seed_player()
    resp = await client.post(
        "/notifications/ack",
        json={"player_uuid": str(recipient), "ids": []},
        headers=_svc_headers(),
    )
    assert resp.status_code == 200
    assert resp.json() == {"acked": 0}


# ---------- API: read ----------
@pytest.mark.asyncio
async def test_read_marks_read_and_returns_record(client):
    recipient = await _seed_player()
    async with async_session_factory() as s:
        rec = await notification_service.notify(s, recipient, "c", "t", "b", payload={})
        await s.commit()
    resp = await client.post(
        f"/notifications/{rec.id}/read?player_uuid={recipient}",
        headers=_svc_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["read_at"] is not None
    # L-2：已读同时置 delivered_at
    assert body["delivered_at"] is not None


@pytest.mark.asyncio
async def test_read_cross_player_returns_404(client):
    """C-1：bob 用自己 uuid read alice 的通知 → 404，且 alice 通知 read_at 仍空。"""
    alice = await _seed_player("alice")
    bob = await _seed_player("bob")
    async with async_session_factory() as s:
        rec = await notification_service.notify(s, alice, "c", "t", "b", payload={})
        await s.commit()
    resp = await client.post(
        f"/notifications/{rec.id}/read?player_uuid={bob}",
        headers=_svc_headers(),
    )
    assert resp.status_code == 404
    async with async_session_factory() as s:
        fresh = await notification_service.fetch_by_id_or_none(s, rec.id, alice)
        assert fresh is not None and fresh.read_at is None


@pytest.mark.asyncio
async def test_read_missing_returns_404(client):
    recipient = await _seed_player()
    resp = await client.post(
        f"/notifications/999999/read?player_uuid={recipient}",
        headers=_svc_headers(),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_read_requires_service_token(client):
    assert (
        await client.post("/notifications/1/read")
    ).status_code == 401


@pytest.mark.asyncio
async def test_read_missing_player_uuid_returns_422(client):
    resp = await client.post("/notifications/1/read", headers=_svc_headers())
    # player_uuid 是必填 query，FastAPI 校验层返 422
    assert resp.status_code == 422


# ---------- M-2/M-3: notify 清洗 ----------
@pytest.mark.asyncio
async def test_notify_truncates_and_strips_control_chars():
    recipient = await _seed_player()
    long_title = "标题" * 200  # 远超 200
    body_with_ctrl = "已打回\x00交付\r\n可重做\tend"
    async with async_session_factory() as s:
        rec = await notification_service.notify(
            s, recipient, "sheet_rejected", long_title, body_with_ctrl, payload={}
        )
        await s.commit()
    async with async_session_factory() as s:
        fresh = await notification_service.fetch_by_id_or_none(s, rec.id, recipient)
        assert fresh is not None
        assert len(fresh.title) == 200
        # 控制字符 \x00 \r \t 被剔除，保留 \n 与可见字符 + 中文
        assert "\x00" not in fresh.body
        assert "\r" not in fresh.body
        assert "\t" not in fresh.body
        assert "\n" in fresh.body


@pytest.mark.asyncio
async def test_notify_truncates_oversized_payload():
    recipient = await _seed_player()
    huge = {"blob": "x" * (8 * 1024 + 100)}
    async with async_session_factory() as s:
        rec = await notification_service.notify(
            s, recipient, "c", "t", "b", payload=huge
        )
        await s.commit()
    async with async_session_factory() as s:
        fresh = await notification_service.fetch_by_id_or_none(s, rec.id, recipient)
        assert fresh is not None
        assert fresh.payload.get("truncated") is True
