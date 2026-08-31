"""积分余额排名端点集成测试（GET /v1/scoring/admin/balances）。

覆盖（§鉴权 §聚合 §排序 §分页 §schema）：
- 鉴权：仅特权 JWT（admin/owner）；无凭证 401、service-token 401
  （admin ≠ service-token）、普通玩家 JWT 403——鉴权同 admin/adjust。
- 聚合：balance = SUM(delta)（append-only 可审计重建，R-2，与最新
  balance_after 恒一致）；entries_count / last_entry_at；同账号多玩家
  只占一行（余额归属锚 = WebAccount，R-5）；display_name 空回退最新
  玩家名（#41 回退链），设了 display_name 则优先；未绑定玩家不入榜。
- 排序：balance DESC + account_id 稳定序（含负余额垫底、0 余额居中）。
- 分页：total 不受分页截断。
- schema 422：limit > 200 / page < 1。
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

import app.api.deps as deps
from app.core.db import async_session_factory
from app.models.user import Player, WebAccount
from tests.conftest import seed_player_with_account

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _svc_token(monkeypatch):
    """patch deps._settings，让 require_service_token / ledger 通道认 "svc"。"""
    # monkeypatch 登记：改原对象属性而非替换 deps._settings 指针（裸赋值 teardown 无法还原，
    # 污染后续测试文件的 service-token 校验——全量批跑顺序性 401 根因）
    monkeypatch.setattr(deps._settings, "mcdr_service_token", "svc")


def _svc() -> dict[str, str]:
    return {"X-Service-Token": "svc"}


async def _credit(client, puuid: uuid.UUID, amount="1") -> None:
    """便捷单条 credit 入账（service-token 通道）。"""
    resp = await client.post(
        "/v1/scoring/credit",
        json={"items": [{"player_uuid": str(puuid), "amount": amount, "reason": "collect"}]},
        headers=_svc(),
    )
    assert resp.status_code == 200, resp.text


async def _seed_owner_bearer() -> str:
    """特权 owner 账号 bearer（镜像面板托管账号场景）。"""
    _, bearer = await seed_player_with_account("panel_op", role="owner")
    return bearer


async def _bind_extra_player(
    anchor_uuid: uuid.UUID, name: str, *, seen_at: datetime | None = None
) -> uuid.UUID:
    """把第二个 Player 挂到 anchor 所在 WebAccount（同账号多 UUID，R-5）。

    seen_at 显式注入（server_default 精度不足以区分同秒插入），驱动
    player_names 的 last_seen_at DESC 顺序与 display_name 回退链。
    """
    async with async_session_factory() as s:
        account_id = (
            await s.execute(
                select(Player.web_account_id).where(Player.uuid == anchor_uuid)
            )
        ).scalar_one()
        puuid = uuid.uuid4()
        s.add(
            Player(
                uuid=puuid,
                current_name=name,
                role="user",
                web_account_id=account_id,
                last_seen_at=seen_at or datetime.now(timezone.utc),
            )
        )
        await s.commit()
    return puuid


async def _set_display_name(anchor_uuid: uuid.UUID, display_name: str) -> None:
    """给 anchor 所在账号设 display_name（#41 显示名主源）。"""
    async with async_session_factory() as s:
        account_id = (
            await s.execute(
                select(Player.web_account_id).where(Player.uuid == anchor_uuid)
            )
        ).scalar_one()
        account = await s.get(WebAccount, account_id)
        account.display_name = display_name
        await s.commit()


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------


async def test_balances_privileged_only(client):
    """无凭证 401；service-token（含正确值）401（admin ≠ service-token）；
    普通玩家 JWT 403；owner JWT 200。"""
    # Arrange
    _, normal_bearer = await seed_player_with_account("alice")
    owner_bearer = await _seed_owner_bearer()
    # Act + Assert（无凭证）
    resp = await client.get("/v1/scoring/admin/balances")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "missing authorization"
    # Act + Assert（仅 service-token → 401：admin 端点不认 service-token）
    resp = await client.get("/v1/scoring/admin/balances", headers=_svc())
    assert resp.status_code == 401
    assert resp.json()["detail"] == "missing authorization"
    # Act + Assert（普通玩家 JWT → 403）
    resp = await client.get(
        "/v1/scoring/admin/balances", headers={"Authorization": normal_bearer}
    )
    assert resp.status_code == 403
    # Act + Assert（owner JWT → 200）
    resp = await client.get(
        "/v1/scoring/admin/balances", headers={"Authorization": owner_bearer}
    )
    assert resp.status_code == 200, resp.text


async def test_balances_empty_ok(client):
    """除调用者自身账号外无任何流水 → 200，仅一行 0 余额（调用者也在榜）。"""
    # Arrange
    owner_bearer = await _seed_owner_bearer()
    # Act
    resp = await client.get(
        "/v1/scoring/admin/balances", headers={"Authorization": owner_bearer}
    )
    # Assert
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["balance"] == "0.00"
    assert data["items"][0]["entries_count"] == 0
    assert data["page"] == 1
    assert data["limit"] == 50


# ---------------------------------------------------------------------------
# 聚合 + 排序
# ---------------------------------------------------------------------------


async def test_balances_aggregation_and_ranking(client):
    """balance = SUM(delta)（含负余额）；多玩家同账号一行；未绑定玩家不入榜；
    排序 balance DESC + account_id 稳定序。"""
    # Arrange
    # alice 账号：+100 +30 → 130.00，另绑一个旧身份（同账号多 UUID）
    alice, _ = await seed_player_with_account("alice")
    await _bind_extra_player(
        alice, "alice_old", seen_at=datetime(2020, 1, 1, tzinfo=timezone.utc)
    )
    await _credit(client, alice, "100")
    await _credit(client, alice, "30")
    # bob 账号：有绑定无流水 → 0.00
    bob, _ = await seed_player_with_account("bob")
    # carol 账号：+5 −10（透支）→ -5.00 垫底
    carol, _ = await seed_player_with_account("carol")
    await _credit(client, carol, "5")
    resp = await client.post(
        "/v1/scoring/debit",
        json={
            "items": [
                {"player_uuid": str(carol), "amount": "10", "reason": "manual_adj"}
            ],
            "allow_overdraft": True,
        },
        headers=_svc(),
    )
    assert resp.status_code == 200, resp.text
    # 未绑定玩家：不入榜
    async with async_session_factory() as s:
        s.add(Player(uuid=uuid.uuid4(), current_name="npc", role="user"))
        await s.commit()
    owner_bearer = await _seed_owner_bearer()

    # Act
    resp = await client.get(
        "/v1/scoring/admin/balances", headers={"Authorization": owner_bearer}
    )
    # Assert
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # 调用者（panel_op）自身也是一行 0 余额账号，与 bob 平分按 account_id 稳定序
    assert data["total"] == 4
    rows = data["items"]
    # 排序：130 > 0（两行平分）> -5
    assert [r["balance"] for r in rows] == ["130.00", "0.00", "0.00", "-5.00"]
    by_name = {r["player_names"][0]: r for r in rows}
    bob, panel = by_name["bob"], by_name["panel_op"]
    assert (bob["account_id"] < panel["account_id"]) is (
        rows.index(bob) < rows.index(panel)
    )  # 平分 → account_id 升序稳定
    rows = [r for r in rows if r["player_names"][0] in {"alice", "bob", "carol"}]

    alice_row = rows[0]
    assert alice_row["account_id"] == (
        await _account_id_of(alice)
    )
    assert alice_row["entries_count"] == 2
    assert alice_row["last_entry_at"] is not None
    # player_names 按 last_seen_at DESC：alice（新）在前，alice_old（2020）在后；
    # display_name 未设 → 回退最新玩家名（#41 链）
    assert alice_row["player_names"] == ["alice", "alice_old"]
    assert alice_row["display_name"] == "alice"

    bob_row = rows[1]
    assert bob_row["player_names"] == ["bob"]
    assert bob_row["entries_count"] == 0
    assert bob_row["last_entry_at"] is None
    assert bob_row["display_name"] == "bob"

    carol_row = rows[2]
    assert carol_row["entries_count"] == 2
    assert carol_row["player_names"] == ["carol"]

    # 未绑定玩家 npc 不在榜
    assert all("npc" not in r["player_names"] for r in rows)


async def test_balances_display_name_takes_precedence(client):
    """账号显式设 display_name → 优先于玩家名回退（#41 主源）。"""
    # Arrange
    alice, _ = await seed_player_with_account("alice")
    await _set_display_name(alice, "老王")
    await _credit(client, alice, "1")
    owner_bearer = await _seed_owner_bearer()
    # Act
    resp = await client.get(
        "/v1/scoring/admin/balances", headers={"Authorization": owner_bearer}
    )
    # Assert
    assert resp.status_code == 200, resp.text
    row = resp.json()["items"][0]
    assert row["display_name"] == "老王"
    assert row["player_names"] == ["alice"]


# ---------------------------------------------------------------------------
# 分页 + schema
# ---------------------------------------------------------------------------


async def test_balances_pagination(client):
    """3 玩家账号余额 3 / 1 / 2 + 调用者 0，limit=2：page1 = [3, 2]、total=4；
    page2 = [1, 0]。"""
    # Arrange
    for name, amount in [("a3", "3"), ("b1", "1"), ("c2", "2")]:
        puuid, _ = await seed_player_with_account(name)
        await _credit(client, puuid, amount)
    owner_bearer = await _seed_owner_bearer()
    # Act
    page1 = (
        await client.get(
            "/v1/scoring/admin/balances",
            params={"limit": 2, "page": 1},
            headers={"Authorization": owner_bearer},
        )
    ).json()
    page2 = (
        await client.get(
            "/v1/scoring/admin/balances",
            params={"limit": 2, "page": 2},
            headers={"Authorization": owner_bearer},
        )
    ).json()
    # Assert
    assert page1["total"] == 4
    assert page1["limit"] == 2
    assert [r["balance"] for r in page1["items"]] == ["3.00", "2.00"]
    assert [r["balance"] for r in page2["items"]] == ["1.00", "0.00"]


async def test_balances_query_validation_422(client):
    """limit=201 / page=0 → 422。"""
    # Arrange
    owner_bearer = await _seed_owner_bearer()
    # Act + Assert
    resp = await client.get(
        "/v1/scoring/admin/balances",
        params={"limit": 201},
        headers={"Authorization": owner_bearer},
    )
    assert resp.status_code == 422
    resp = await client.get(
        "/v1/scoring/admin/balances",
        params={"page": 0},
        headers={"Authorization": owner_bearer},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _account_id_of(puuid: uuid.UUID) -> int:
    async with async_session_factory() as s:
        return (
            await s.execute(
                select(Player.web_account_id).where(Player.uuid == puuid)
            )
        ).scalar_one()
