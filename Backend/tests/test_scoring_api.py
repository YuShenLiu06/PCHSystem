"""积分层 API 集成测试（/v1/scoring 四端点：credit / debit / ledger / admin adjust）。

覆盖（用例清单 §鉴权 §schema §批量 §链路 §透支 §通知 §幂等 §ledger §分页 §admin adjust）：
- 鉴权：credit/debit 仅 service-token（无/错 token 401）；admin/adjust 与
  admin/players **仅特权 JWT**（admin/owner，普通玩家 403、service-token 401——admin ≠
  service-token）；ledger 无凭证 401。
- schema 422：reason 方向收紧、amount 正数两位小数（含 18 位上限）、批大小 1..100、
  limit ≤200 / page ≥1。
- 批量逐条独立：混合批次 skip 原因逐条正确，互不影响。
- 成功链路：delta / balance_after 链式累计；sheet 存在性校验 + 回显；account_id 锚。
- 透支：debit 默认拒绝；allow_overdraft=True 放行到负余额。
- 通知：默认落 scoring_credit / scoring_debit；notify=False 与 skip 条目不发。
- 幂等：同 key 同 payload 重放（entry 同 id、库 1 行）；同 key 不同 amount 冲突 skip。
- ledger 权限矩阵：普通玩家限自身 / 他人 403；admin 与 service-token 全局 + 定向；
  未知 uuid 404；since（>=）/ until（<）边界语义。
- ledger account_id 过滤（余额下钻）：特权 JWT / service-token 按账号收敛；未知账号
  404；普通玩家 403（显式拒绝）；与 player_uuid 互斥 422。
- 分页：id DESC、total、page 语义。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

import app.api.deps as deps
from app.core.db import async_session_factory
from app.models.notification import Notification
from app.models.scoring import ScoreLedger
from app.models.sheet import Sheet
from app.models.user import Player
from tests.conftest import seed_player_with_account

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _svc_token(monkeypatch):
    """patch deps._settings，让 require_service_token / require_ledger_access 都认 "svc"。"""
    # monkeypatch 登记：改原对象属性而非替换 deps._settings 指针（裸赋值 teardown 无法还原，
    # 污染后续测试文件的 service-token 校验——全量批跑顺序性 401 根因）
    monkeypatch.setattr(deps._settings, "mcdr_service_token", "svc")


def _svc() -> dict[str, str]:
    return {"X-Service-Token": "svc"}


def _item(puuid: uuid.UUID, amount, reason: str, **extra) -> dict:
    """构造单条批量 item（amount 故意不做类型收窄，422 用例要传非法值）。"""
    payload = {"player_uuid": str(puuid), "amount": amount, "reason": reason}
    payload.update(extra)
    return payload


def _batch(items: list[dict], **extra) -> dict:
    body = {"items": items}
    body.update(extra)
    return body


async def _seed_unbound(name: str = "npc") -> uuid.UUID:
    """未绑定 WebAccount 的玩家（web_account_id=None）。"""
    puuid = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=puuid, current_name=name, role="user", web_account_id=None))
        await s.commit()
    return puuid


async def _seed_sheet(owner_uuid: uuid.UUID, title: str = "积分关联项目") -> int:
    """直插 Sheet（参照 tests/test_construction_participants.py::_seed_sheet）。"""
    async with async_session_factory() as s:
        sheet = Sheet(owner_uuid=owner_uuid, title=title, status="collecting")
        s.add(sheet)
        await s.flush()
        sid = sheet.id
        await s.commit()
    return sid


async def _credit(client, puuid: uuid.UUID, amount="1", reason="collect", **extra) -> dict:
    """便捷单条 credit（notify 默认开），断言 200 后返回响应体。"""
    resp = await client.post(
        "/v1/scoring/credit", json=_batch([_item(puuid, amount, reason, **extra)]),
        headers=_svc(),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _count_ledger() -> int:
    async with async_session_factory() as s:
        return (
            await s.execute(select(func.count()).select_from(ScoreLedger))
        ).scalar_one()


async def _notifications_for(puuid: uuid.UUID) -> list[Notification]:
    async with async_session_factory() as s:
        rows = (
            await s.execute(
                select(Notification).where(Notification.recipient_uuid == puuid)
            )
        ).scalars().all()
        return list(rows)


# ---------------------------------------------------------------------------
# 鉴权 ①②③
# ---------------------------------------------------------------------------


async def test_credit_without_or_wrong_service_token_401(client):
    """① 无 X-Service-Token / 错 token → 401 invalid service token。"""
    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    body = _batch([_item(puuid, "1", "collect")])
    # Act + Assert（无头）
    resp = await client.post("/v1/scoring/credit", json=body)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid service token"
    # Act + Assert（错 token）
    resp = await client.post(
        "/v1/scoring/credit", json=body, headers={"X-Service-Token": "nope"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid service token"


async def test_write_endpoints_reject_jwt_401(client):
    """② 合法 Bearer JWT 也不放行写端点（仅 service-token，不对 JWT 开放）。"""
    # Arrange
    puuid, bearer = await seed_player_with_account("alice")
    # Act + Assert（credit）
    resp = await client.post(
        "/v1/scoring/credit",
        json=_batch([_item(puuid, "1", "collect")]),
        headers={"Authorization": bearer},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid service token"
    # Act + Assert（debit）
    resp = await client.post(
        "/v1/scoring/debit",
        json=_batch([_item(puuid, "1", "manual_adj")]),
        headers={"Authorization": bearer},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid service token"


async def test_ledger_no_credentials_401(client):
    """③ ledger 无任何凭证 → 401。"""
    resp = await client.get("/v1/scoring/ledger")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid service token"


# ---------------------------------------------------------------------------
# schema 422 ④⑤⑥⑦
# ---------------------------------------------------------------------------


async def test_reason_direction_and_unknown_422(client):
    """④ credit 不收 debit 方向 reason；debit 不收 credit 方向；未知 reason 全拒。"""
    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    # Act + Assert（credit + manual_adj）
    resp = await client.post(
        "/v1/scoring/credit", json=_batch([_item(puuid, "1", "manual_adj")]),
        headers=_svc(),
    )
    assert resp.status_code == 422
    # Act + Assert（credit + 未知 reason）
    resp = await client.post(
        "/v1/scoring/credit", json=_batch([_item(puuid, "1", "foo")]), headers=_svc()
    )
    assert resp.status_code == 422
    # Act + Assert（debit + collect）
    resp = await client.post(
        "/v1/scoring/debit", json=_batch([_item(puuid, "1", "collect")]),
        headers=_svc(),
    )
    assert resp.status_code == 422
    assert await _count_ledger() == 0


async def test_amount_validation_422(client):
    """⑤ amount 必须 > 0、至多两位小数、≤18 位有效数字。"""
    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    for bad in [0, -5, "1.234", "1234567890123456789"]:
        # Act
        resp = await client.post(
            "/v1/scoring/credit",
            json=_batch([_item(puuid, bad, "collect")]),
            headers=_svc(),
        )
        # Assert
        assert resp.status_code == 422, f"amount={bad!r} 应 422，实际 {resp.status_code}"


async def test_batch_size_validation_422(client):
    """⑥ items 空数组 / 101 条 → 422。"""
    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    # Act + Assert（空数组）
    resp = await client.post(
        "/v1/scoring/credit", json=_batch([]), headers=_svc()
    )
    assert resp.status_code == 422
    # Act + Assert（101 条）
    resp = await client.post(
        "/v1/scoring/credit",
        json=_batch([_item(puuid, "1", "collect") for _ in range(101)]),
        headers=_svc(),
    )
    assert resp.status_code == 422


async def test_ledger_query_validation_422(client):
    """⑦ limit=201 / page=0 → 422。"""
    # Act + Assert
    resp = await client.get(
        "/v1/scoring/ledger", params={"limit": 201}, headers=_svc()
    )
    assert resp.status_code == 422
    resp = await client.get("/v1/scoring/ledger", params={"page": 0}, headers=_svc())
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 批量逐条独立 ⑧
# ---------------------------------------------------------------------------


async def test_batch_items_independent(client):
    """⑧ 混合 [合法, 未知 uuid, 未绑定] → 200，1 收 2 跳，skip 原因逐条正确。"""
    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    unknown = uuid.uuid4()
    unbound = await _seed_unbound()
    # Act
    resp = await client.post(
        "/v1/scoring/credit",
        json=_batch([
            _item(puuid, "5", "collect"),
            _item(unknown, "5", "collect"),
            _item(unbound, "5", "collect"),
        ]),
        headers=_svc(),
    )
    # Assert
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted_count"] == 1
    assert data["skipped_count"] == 2
    assert data["results"][0]["accepted"] is True
    assert data["results"][0]["skip_reason"] is None
    assert data["results"][1]["skip_reason"] == "player not found"
    assert data["results"][2]["skip_reason"] == "player not bound to a web account"
    assert await _count_ledger() == 1


# ---------------------------------------------------------------------------
# 成功链路 ⑨⑩⑪
# ---------------------------------------------------------------------------


async def test_credit_first_entry_amounts_and_account_anchor(client):
    """⑨ 首条 credit 12.50 → delta / balance_after 均为 "12.50"；account_id 锚到 WebAccount。"""
    # Act
    puuid, _ = await seed_player_with_account("alice")
    data = await _credit(client, puuid, "12.50")
    # Assert
    entry = data["results"][0]["entry"]
    assert entry["delta"] == "12.50"
    assert entry["balance_after"] == "12.50"
    assert entry["reason"] == "collect"
    async with async_session_factory() as s:
        account_id = (
            await s.execute(
                select(Player.web_account_id).where(Player.uuid == puuid)
            )
        ).scalar_one()
    assert entry["account_id"] == account_id


async def test_chained_balance(client):
    """⑩ 链式 10 → 15.5 → 12.5：每步 balance_after 累计正确。"""
    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    # Act
    first = await _credit(client, puuid, "10")
    second = await _credit(client, puuid, "5.5", reason="build_a")
    resp = await client.post(
        "/v1/scoring/debit", json=_batch([_item(puuid, "3", "manual_adj")]),
        headers=_svc(),
    )
    # Assert
    assert resp.status_code == 200, resp.text
    third = resp.json()
    assert first["results"][0]["entry"]["balance_after"] == "10.00"
    assert second["results"][0]["entry"]["balance_after"] == "15.50"
    assert third["results"][0]["entry"]["delta"] == "-3.00"
    assert third["results"][0]["entry"]["balance_after"] == "12.50"
    assert await _count_ledger() == 3


async def test_sheet_validation_and_echo(client):
    """⑪ sheet_id 不存在 → skip "sheet not found"；存在 → entry.sheet_id 回显。"""
    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    # Act（不存在）
    resp = await client.post(
        "/v1/scoring/credit",
        json=_batch([_item(puuid, "1", "collect", sheet_id=999999)]),
        headers=_svc(),
    )
    # Assert（不存在）
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["accepted"] is False
    assert result["skip_reason"] == "sheet not found"
    # Act（存在）
    sid = await _seed_sheet(puuid)
    data = await _credit(client, puuid, "1", sheet_id=sid)
    # Assert（存在）
    assert data["results"][0]["accepted"] is True
    assert data["results"][0]["entry"]["sheet_id"] == sid


# ---------------------------------------------------------------------------
# 透支 ⑫⑬
# ---------------------------------------------------------------------------


async def test_debit_insufficient_balance_skip(client):
    """⑫ 余额 10，debit 25（默认 allow_overdraft=False）→ skip，库里只 1 条。"""
    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    await _credit(client, puuid, "10")
    # Act
    resp = await client.post(
        "/v1/scoring/debit", json=_batch([_item(puuid, "25", "manual_adj")]),
        headers=_svc(),
    )
    # Assert
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["accepted"] is False
    assert result["skip_reason"] == "insufficient balance"
    assert await _count_ledger() == 1


async def test_debit_allow_overdraft_negative_balance(client):
    """⑬ allow_overdraft=True：10 − 25 → balance_after="-15.00"。"""
    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    await _credit(client, puuid, "10")
    # Act
    resp = await client.post(
        "/v1/scoring/debit",
        json=_batch([_item(puuid, "25", "manual_adj")], allow_overdraft=True),
        headers=_svc(),
    )
    # Assert
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["entry"]["balance_after"] == "-15.00"


# ---------------------------------------------------------------------------
# 通知 ⑭⑮
# ---------------------------------------------------------------------------


async def test_notify_default_creates_notification(client):
    """⑭ 默认 notify=True：credit + debit 各落 1 行，category / title / payload 正确。"""
    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    # Act
    await _credit(client, puuid, "12.50")
    resp = await client.post(
        "/v1/scoring/debit", json=_batch([_item(puuid, "2", "manual_adj")]),
        headers=_svc(),
    )
    assert resp.status_code == 200, resp.text
    # Assert
    notes = await _notifications_for(puuid)
    assert len(notes) == 2
    by_category = {n.category: n for n in notes}
    credit_note = by_category["scoring_credit"]
    assert credit_note.title == "积分入账"
    assert "12.50" in credit_note.body
    assert "(collect)" in credit_note.body  # 无 note → 裸 reason，不拼冒号（ASCII 标点过 _clean_text 白名单）
    assert credit_note.payload["amount"] == "12.50"
    assert credit_note.payload["reason"] == "collect"
    assert credit_note.payload["balance_after"] == "12.50"
    assert by_category["scoring_debit"].title == "积分扣除"


async def test_notify_disabled_and_skip_no_notification(client):
    """⑮ notify=False → 0 行；skip 条目（未绑定玩家）即便 notify=True 也不发。"""
    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    unbound = await _seed_unbound()
    # Act（notify=False 的成功条目）
    resp = await client.post(
        "/v1/scoring/credit",
        json=_batch([_item(puuid, "1", "collect")], notify=False),
        headers=_svc(),
    )
    assert resp.status_code == 200
    # Act（notify=True 但 skip 的条目）
    resp = await client.post(
        "/v1/scoring/credit", json=_batch([_item(unbound, "1", "collect")]),
        headers=_svc(),
    )
    assert resp.status_code == 200
    assert resp.json()["skipped_count"] == 1
    # Assert
    assert await _notifications_for(puuid) == []
    assert await _notifications_for(unbound) == []


# ---------------------------------------------------------------------------
# 幂等 ⑯⑰
# ---------------------------------------------------------------------------


async def test_idempotent_replay_same_payload(client):
    """⑯ 同 key 同 payload 两次：第二次 replay=True、entry.id 相同、库里仅 1 行；
    重放抑制通知（MCDR 重试语义下副作用不重复）。"""
    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    item = _item(puuid, "5", "collect", idempotency_key="idem-1")
    # Act
    r1 = (await client.post(
        "/v1/scoring/credit", json=_batch([item]), headers=_svc()
    )).json()
    r2 = (await client.post(
        "/v1/scoring/credit", json=_batch([item]), headers=_svc()
    )).json()
    # Assert
    assert r1["results"][0]["accepted"] is True
    assert r1["results"][0]["idempotent_replay"] is False
    assert r2["results"][0]["accepted"] is True
    assert r2["results"][0]["idempotent_replay"] is True
    assert r2["results"][0]["entry"]["id"] == r1["results"][0]["entry"]["id"]
    assert await _count_ledger() == 1
    assert len(await _notifications_for(puuid)) == 1


async def test_idempotency_conflict_different_amount(client):
    """⑰ 同 key 不同 amount → 第二次 skip "idempotency key conflict"，库 1 行。"""
    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    first = _item(puuid, "5", "collect", idempotency_key="idem-2")
    second = _item(puuid, "7", "collect", idempotency_key="idem-2")
    # Act
    r1 = (await client.post(
        "/v1/scoring/credit", json=_batch([first]), headers=_svc()
    )).json()
    r2 = (await client.post(
        "/v1/scoring/credit", json=_batch([second]), headers=_svc()
    )).json()
    # Assert
    assert r1["results"][0]["accepted"] is True
    assert r2["results"][0]["accepted"] is False
    assert r2["results"][0]["skip_reason"] == "idempotency key conflict"
    assert await _count_ledger() == 1


# ---------------------------------------------------------------------------
# ledger 权限矩阵 ⑱⑲⑳㉑㉒
# ---------------------------------------------------------------------------


async def _seed_two_players_one_entry_each(client):
    """两名普通玩家各 1 条流水；返回 (p1, b1, acc1, p2, b2, acc2)。"""
    p1, b1 = await seed_player_with_account("alice")
    p2, b2 = await seed_player_with_account("bob")
    e1 = (await _credit(client, p1, "3"))["results"][0]["entry"]
    e2 = (await _credit(client, p2, "4"))["results"][0]["entry"]
    return p1, b1, e1["account_id"], p2, b2, e2["account_id"]


async def test_ledger_regular_player_sees_only_self(client):
    """⑱ 普通玩家 JWT 无 uuid → 只见自己（total=1，account_id=自身）。"""
    # Arrange
    p1, b1, acc1, p2, b2, acc2 = await _seed_two_players_one_entry_each(client)
    # Act
    resp = await client.get("/v1/scoring/ledger", headers={"Authorization": b1})
    # Assert
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["account_id"] == acc1


async def test_ledger_regular_player_other_uuid_403(client):
    """⑲ 普通玩家 JWT + 他人 uuid → 403 forbidden。"""
    # Arrange
    p1, b1, acc1, p2, b2, acc2 = await _seed_two_players_one_entry_each(client)
    # Act
    resp = await client.get(
        "/v1/scoring/ledger",
        params={"player_uuid": str(p2)},
        headers={"Authorization": b1},
    )
    # Assert
    assert resp.status_code == 403
    assert resp.json()["detail"] == "forbidden"


async def test_ledger_admin_global_and_targeted(client):
    """⑳ admin JWT：无 uuid 全局（total=2）；+ 他人 uuid → 仅该玩家流水。"""
    # Arrange
    p1, b1, acc1, p2, b2, acc2 = await _seed_two_players_one_entry_each(client)
    _, admin_bearer = await seed_player_with_account("boss", role="admin")
    # Act + Assert（全局）
    resp = await client.get(
        "/v1/scoring/ledger", headers={"Authorization": admin_bearer}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    # Act + Assert（定向 p1）
    resp = await client.get(
        "/v1/scoring/ledger",
        params={"player_uuid": str(p1)},
        headers={"Authorization": admin_bearer},
    )
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["account_id"] == acc1


async def test_ledger_service_token_global_and_unknown_404(client):
    """㉑ service-token：无 uuid 全局；+ 未知 uuid → 404 player not found。"""
    # Arrange
    p1, b1, acc1, p2, b2, acc2 = await _seed_two_players_one_entry_each(client)
    # Act + Assert（全局）
    resp = await client.get("/v1/scoring/ledger", headers=_svc())
    assert resp.status_code == 200
    assert resp.json()["total"] == 2
    # Act + Assert（未知 uuid）
    resp = await client.get(
        "/v1/scoring/ledger",
        params={"player_uuid": str(uuid.uuid4())},
        headers=_svc(),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "player not found"


async def test_ledger_since_until_filter(client):
    """㉒ since（>=，含边界）/ until（<，开区间）过滤语义。"""
    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    t_before = datetime.now(timezone.utc) - timedelta(seconds=1)
    await _credit(client, puuid, "1")
    await _credit(client, puuid, "2")
    await _credit(client, puuid, "3")
    t_after = datetime.now(timezone.utc) + timedelta(seconds=1)
    # Act + Assert（since 在前 → 全命中）
    resp = await client.get(
        "/v1/scoring/ledger", params={"since": t_before.isoformat()}, headers=_svc()
    )
    assert resp.json()["total"] == 3
    # Act + Assert（since 在后 → 0）
    resp = await client.get(
        "/v1/scoring/ledger", params={"since": t_after.isoformat()}, headers=_svc()
    )
    assert resp.json()["total"] == 0
    # Act + Assert（until 在前 → 0：严格小于）
    resp = await client.get(
        "/v1/scoring/ledger", params={"until": t_before.isoformat()}, headers=_svc()
    )
    assert resp.json()["total"] == 0
    # Act + Assert（until 在后 → 全命中）
    resp = await client.get(
        "/v1/scoring/ledger", params={"until": t_after.isoformat()}, headers=_svc()
    )
    assert resp.json()["total"] == 3
    # 边界精确性：since=最早条目 created_at → 3（>= 含边界；若实现为 > 则只 2）；
    # until=最早条目 created_at → 0（严格 < 连最早条自身都排除；若实现为 <= 则 1）
    page = (
        await client.get("/v1/scoring/ledger", headers=_svc())
    ).json()
    earliest = datetime.fromisoformat(page["items"][-1]["created_at"])
    resp = await client.get(
        "/v1/scoring/ledger", params={"since": earliest.isoformat()}, headers=_svc()
    )
    assert resp.json()["total"] == 3
    resp = await client.get(
        "/v1/scoring/ledger", params={"until": earliest.isoformat()}, headers=_svc()
    )
    assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# ledger account_id 过滤（余额下钻入口，特权专用）
# ---------------------------------------------------------------------------


async def test_ledger_admin_account_id_targeted(client):
    """特权 JWT + account_id → 仅该账号流水（余额行下钻）。"""
    # Arrange
    p1, b1, acc1, p2, b2, acc2 = await _seed_two_players_one_entry_each(client)
    _, admin_bearer = await seed_player_with_account("boss", role="admin")
    # Act
    resp = await client.get(
        "/v1/scoring/ledger",
        params={"account_id": acc1},
        headers={"Authorization": admin_bearer},
    )
    # Assert
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["account_id"] == acc1


async def test_ledger_account_id_unknown_404(client):
    """特权 + 不存在账号 → 404 account not found。"""
    # Arrange
    _, admin_bearer = await seed_player_with_account("boss", role="admin")
    # Act
    resp = await client.get(
        "/v1/scoring/ledger",
        params={"account_id": 999999},
        headers={"Authorization": admin_bearer},
    )
    # Assert
    assert resp.status_code == 404
    assert resp.json()["detail"] == "account not found"


async def test_ledger_account_id_regular_player_403(client):
    """普通玩家 JWT + account_id（即便 = 自身）→ 403（显式拒绝优于静默忽略；
    自账号放行为将来预留语义，暂不实现）。"""
    # Arrange
    p1, b1, acc1, *_ = await _seed_two_players_one_entry_each(client)
    # Act
    resp = await client.get(
        "/v1/scoring/ledger",
        params={"account_id": acc1},
        headers={"Authorization": b1},
    )
    # Assert
    assert resp.status_code == 403
    assert resp.json()["detail"] == "forbidden"


async def test_ledger_account_id_with_player_uuid_422(client):
    """account_id 与 player_uuid 互斥（语义重叠）→ 422。"""
    # Arrange
    p1, b1, acc1, *_ = await _seed_two_players_one_entry_each(client)
    # Act
    resp = await client.get(
        "/v1/scoring/ledger",
        params={"account_id": acc1, "player_uuid": str(p1)},
        headers=_svc(),
    )
    # Assert
    assert resp.status_code == 422


async def test_ledger_service_token_account_id(client):
    """service-token + account_id → 200（MCDR 也可按账号直拉流水）。"""
    # Arrange
    p1, b1, acc1, p2, b2, acc2 = await _seed_two_players_one_entry_each(client)
    # Act
    resp = await client.get(
        "/v1/scoring/ledger", params={"account_id": acc2}, headers=_svc()
    )
    # Assert
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["account_id"] == acc2


# ---------------------------------------------------------------------------
# admin adjust ㉔㉕㉗㉘（管理员调控：**仅特权 JWT**——admin ≠ service-token，
# 系统组件记账走 credit/debit；方向由 reason 定）
# ---------------------------------------------------------------------------


def _adjust_body(items: list[dict], **extra) -> dict:
    """构造 admin/adjust 请求体。"""
    body = {"items": items}
    body.update(extra)
    return body


async def _adjust(client, bearer: str, items: list[dict], **extra) -> dict:
    """便捷单批 adjust（bearer = 特权 JWT；断言 200 后返回响应体）。"""
    resp = await client.post(
        "/v1/scoring/admin/adjust", json=_adjust_body(items, **extra),
        headers={"Authorization": bearer},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_adjust_auth_channels(client):
    """㉔ 无 Authorization → 401；service-token（即便正确）→ 401（admin ≠ service-token）；
    普通玩家 JWT → 403；非法 Authorization → 401（H-2 不降级）。"""
    # Arrange
    puuid, normal_bearer = await seed_player_with_account("alice")
    body = _adjust_body([_item(puuid, "1", "manual_adj")])
    # Act + Assert（无头）
    resp = await client.post("/v1/scoring/admin/adjust", json=body)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "missing authorization"
    # Act + Assert（仅 service-token（含正确值）→ 401：admin 端点不认 service-token）
    resp = await client.post(
        "/v1/scoring/admin/adjust", json=body, headers=_svc()
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "missing authorization"
    # Act + Assert（合法普通玩家 JWT → 403：身份有效但非特权）
    resp = await client.post(
        "/v1/scoring/admin/adjust", json=body, headers={"Authorization": normal_bearer}
    )
    assert resp.status_code == 403
    # Act + Assert（带 Authorization 但 token 非法 → 401 走 JWT 通道，绝不降级 service-token）
    resp = await client.post(
        "/v1/scoring/admin/adjust",
        json=body,
        headers={"Authorization": "Bearer garbage", "X-Service-Token": "svc"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid token"


async def test_adjust_accepts_admin_jwt_with_operator_audit(client, caplog):
    """admin JWT 通道放行；审计日志记 operator=jwt-account:<account_id>。"""
    import logging

    from app.core.jwt import decode_token

    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    _, admin_bearer = await seed_player_with_account("boss", role="admin")
    # seed 账号无 username，account_id 从 JWT sub 解出（即实际调用者）
    admin_account_id = decode_token(admin_bearer.removeprefix("Bearer "))["sub"]
    body = _adjust_body([_item(puuid, "2", "collect")])

    # Act
    with caplog.at_level(logging.INFO, logger="app.api.scoring"):
        resp = await client.post(
            "/v1/scoring/admin/adjust", json=body, headers={"Authorization": admin_bearer}
        )

    # Assert — 200 + 入账方向 + 审计 operator 标签
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["accepted_count"] == 1
    assert data["results"][0]["entry"]["delta"] == "2.00"
    assert f"operator=jwt-account:{admin_account_id}" in caplog.text


async def test_adjust_accepts_owner_jwt(client):
    """owner JWT 通道同样放行（env 同步托管账号即 role=owner）。"""
    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    _, owner_bearer = await seed_player_with_account("root", role="owner")
    body = _adjust_body([_item(puuid, "1", "collect")])

    # Act
    resp = await client.post(
        "/v1/scoring/admin/adjust", json=body, headers={"Authorization": owner_bearer}
    )

    # Assert
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["entry"]["delta"] == "1.00"


async def test_admin_players_search_privileged_only(client):
    """GET /v1/scoring/admin/players：仅特权 JWT 可联想；service-token 401、普通玩家 403。"""
    # Arrange
    _, normal_bearer = await seed_player_with_account("alice")
    _, admin_bearer = await seed_player_with_account("boss", role="admin")

    # Act + Assert（service-token → 401：admin 端点不认 service-token）
    resp = await client.get(
        "/v1/scoring/admin/players", params={"q": "ali"}, headers=_svc()
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "missing authorization"

    # Act + Assert（admin JWT）
    resp = await client.get(
        "/v1/scoring/admin/players", params={"q": "ali"}, headers={"Authorization": admin_bearer}
    )
    assert resp.status_code == 200, resp.text
    assert "alice" in [p["player_name"] for p in resp.json()]

    # Act + Assert（普通玩家 JWT → 403）
    resp = await client.get(
        "/v1/scoring/admin/players", headers={"Authorization": normal_bearer}
    )
    assert resp.status_code == 403

    # Act + Assert（无凭证 → 401）
    resp = await client.get("/v1/scoring/admin/players")
    assert resp.status_code == 401


async def test_adjust_direction_by_reason_full_set(client):
    """㉕ 方向由 reason 定：全集 6 种均 200；入账 reason 正 delta、出账 reason 负。"""
    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    _, admin_bearer = await seed_player_with_account("panel_op", role="owner")
    for reason in ["collect", "build_a", "leader_bonus", "settle"]:
        # Act
        data = await _adjust(client, admin_bearer, [_item(puuid, "1", reason)])
        # Assert
        assert data["results"][0]["accepted"] is True, reason
        assert data["results"][0]["entry"]["delta"] == "1.00"
    # Act（出账两 reason，先补足余额再扣）
    await _adjust(client, admin_bearer, [_item(puuid, "10", "settle")])
    for reason in ["manual_adj", "season_reset"]:
        data = await _adjust(client, admin_bearer, [_item(puuid, "1", reason)])
        assert data["results"][0]["accepted"] is True, reason
        assert data["results"][0]["entry"]["delta"] == "-1.00"


async def test_adjust_overdraft_default_reject_and_flag(client):
    """㉗ allow_overdraft 默认 False 余额不足 skip；True 放行到负余额。"""
    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    _, admin_bearer = await seed_player_with_account("panel_op", role="owner")
    # Act（默认拒绝）
    data = await _adjust(client, admin_bearer, [_item(puuid, "5", "manual_adj")])
    # Assert
    assert data["results"][0]["skip_reason"] == "insufficient balance"
    assert await _count_ledger() == 0
    # Act（显式开透支）
    data = await _adjust(
        client, admin_bearer, [_item(puuid, "5", "manual_adj")], allow_overdraft=True
    )
    # Assert
    assert data["results"][0]["entry"]["balance_after"] == "-5.00"


async def test_credit_into_negative_balance_succeeds(client):
    """透支守卫仅限出账：负余额账号的部分额度 credit 入账不被误伤（CR HIGH 回归）。

    场景：经 allow_overdraft 出账到 -15 后，MCDR credit 5（收集入账）应成功
    落 -10——守卫只拦 delta<0 的扣减，入账方向不应检查余额正负。
    """
    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    _, admin_bearer = await seed_player_with_account("panel_op", role="owner")
    await _adjust(
        client, admin_bearer, [_item(puuid, "15", "manual_adj")], allow_overdraft=True
    )
    # Act
    data = await _credit(client, puuid, "5", "collect")
    # Assert
    r = data["results"][0]
    assert r["accepted"] is True, r
    assert r["entry"]["balance_after"] == "-10.00"
    assert r["entry"]["delta"] == "5.00"


async def test_adjust_notify_and_operator_uuid_echo(client):
    """㉘ 默认发通知（方向对应 category）；operator_uuid / note 审计字段回显。"""
    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    _, admin_bearer = await seed_player_with_account("panel_op", role="owner")
    boss_uuid = uuid.uuid4()
    await _adjust(client, admin_bearer, [_item(puuid, "5", "collect")], notify=False)  # 先补足余额
    # Act
    data = await _adjust(
        client, admin_bearer,
        [_item(puuid, "3", "manual_adj", operator_uuid=str(boss_uuid),
               note="误发回收")],
    )
    # Assert
    entry = data["results"][0]["entry"]
    assert entry["operator_uuid"] == str(boss_uuid)
    assert entry["note"] == "误发回收"
    notes = await _notifications_for(puuid)
    assert len(notes) == 1
    assert notes[0].category == "scoring_debit"
    assert "(manual_adj: 误发回收)" in notes[0].body  # note 拼进通知文案（reason: note）
    assert notes[0].payload["reason"] == "manual_adj"  # payload 保持裸枚举，不被 note 污染


# ---------------------------------------------------------------------------
# 分页 ㉓
# ---------------------------------------------------------------------------


async def test_ledger_pagination(client):
    """㉓ 3 条 limit=2：page1 total=3 回 2 条；page2 回 id 最小的第 3 条（id DESC）。"""
    # Arrange
    puuid, _ = await seed_player_with_account("alice")
    for _i in range(3):
        await _credit(client, puuid, "1")
    # Act
    page1 = (
        await client.get(
            "/v1/scoring/ledger", params={"limit": 2, "page": 1}, headers=_svc()
        )
    ).json()
    page2 = (
        await client.get(
            "/v1/scoring/ledger", params={"limit": 2, "page": 2}, headers=_svc()
        )
    ).json()
    # Assert
    assert page1["total"] == 3
    assert page1["limit"] == 2
    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 1
    all_ids = [it["id"] for it in page1["items"]] + [
        it["id"] for it in page2["items"]
    ]
    assert page2["items"][0]["id"] == min(all_ids)
    assert page1["items"][0]["id"] == max(all_ids)  # id DESC：首页首条 = 最新
