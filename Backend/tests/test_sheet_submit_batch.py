"""``POST /sheets/{sheet_id}/submit-batch`` 端点测试（TDD 红测）。

覆盖 plan §1.6 全矩阵：
- lock 成功：claimed-by-me + 够数 → delivered(done)；同账号另一 UUID 认领 → delivered（R-5）
- lock 跳过：open→「需先认领」；他人认领→「已被他人认领」；done→「已备齐」；不足→「数量不足」
- progress 成功：提交 → contributed(delivered 增 + 贡献者 upsert)；need=0 无限模式 → 永不 done
- progress 跳过：已满→「已备齐」；未提交→「背包没有此物」
- 边界：重复 registry_id 聚合；未知 registry_id 静默忽略；空 items→422；>2000→422；qty=0 →「背包没有此物」
- 错误：archived→409；sheet 不存在→404
- 鉴权（双通道专项 4 例）：服务端代写 / JWT 自写 / JWT 通道伪造 X-Player-UUID 无效 / H-2 不降级
- 事务：混合批（deliver+contribute+skip）→ 三类 outcome 齐全 + 成功行落库 + skip 不中断

impl 由 T1 并行实现，本文件此刻预期 import 失败或 404（红测）；落地后即转绿。
"""
import uuid

import pytest

import app.api.deps as deps
from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.jwt import create_access_token
from app.models.user import Player
from sqlalchemy import select
from tests.conftest import seed_player_with_account


# ---------- fixtures / helpers ----------

@pytest.fixture(autouse=True)
def _svc_token(monkeypatch):
    """注入 service-token=svc 到 deps._settings（镜像 test_sheets_api.py 范式）。"""
    # monkeypatch 登记：改原对象属性而非替换 deps._settings 指针（裸赋值 teardown 无法还原，
    # 污染后续测试文件的 service-token 校验——全量批跑顺序性 401 根因）
    monkeypatch.setattr(deps._settings, "mcdr_service_token", "svc")


_BEARER_CACHE: dict[uuid.UUID, str] = {}


async def _make_player(name: str = "alice", role: str = "user") -> tuple[uuid.UUID, str]:
    """seed player + 临时 WebAccount + 签 JWT；返回 (uuid, bearer)。

    bearer 缓存到 ``_BEARER_CACHE``，方便后续按 UUID 取（与 test_sheets_service_write 一致）。
    """
    player_uuid, bearer = await seed_player_with_account(name=name, role=role)
    _BEARER_CACHE[player_uuid] = bearer
    return player_uuid, bearer


def _jwt_headers(u: uuid.UUID) -> dict[str, str]:
    """按 UUID 取缓存的 JWT bearer 头。"""
    return {"Authorization": _BEARER_CACHE[u]}


def _svc_headers(u: uuid.UUID) -> dict[str, str]:
    """服务端通道头：service-token + X-Player-UUID（无 Authorization）。"""
    return {"X-Service-Token": "svc", "X-Player-UUID": str(u)}


async def _bind_second_uuid_to_account(
    owner_uuid: uuid.UUID, name: str = "alt"
) -> tuple[uuid.UUID, str]:
    """给 owner_uuid 同 WebAccount 再绑一个 UUID，返回 (new_uuid, bearer)。

    用于 R-5「同 account 多 UUID」场景。镜像 test_sheets_api.py:1346-1367 范式。
    """
    async with async_session_factory() as s:
        account_id = (
            await s.execute(
                select(Player.web_account_id).where(Player.uuid == owner_uuid)
            )
        ).scalar_one()
    alt_uuid = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(
            Player(
                uuid=alt_uuid,
                current_name=name,
                role="user",
                web_account_id=account_id,
            )
        )
        await s.commit()
    alt_bearer = f"Bearer {create_access_token(account_id, 'user', active_uuid=alt_uuid)}"
    _BEARER_CACHE[alt_uuid] = alt_bearer
    return alt_uuid, alt_bearer


async def _create_sheet(client, headers, title: str = "S") -> int:
    resp = await client.post("/sheets", json={"title": title}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _upsert_row(
    client,
    headers,
    sid: int,
    *,
    item_name: str = "铁锭",
    registry_id: str = "minecraft:iron_ingot",
    need: int = 10,
    mode: int = 0,
    sort_order: int = 0,
) -> dict:
    """upsert 行（默认带 registry_id——批量提交按 registry_id 匹配必需）。"""
    body: dict = {
        "item_name": item_name,
        "need_qty": need,
        "mode": mode,
        "sort_order": sort_order,
    }
    if registry_id is not None:
        body["registry_id"] = registry_id
    resp = await client.put(f"/sheets/{sid}/rows", json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _claim(client, headers, sid: int, rid: int) -> dict:
    resp = await client.post(f"/sheets/{sid}/rows/{rid}/claim", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _deliver(client, headers, sid: int, rid: int, qty: int) -> dict:
    resp = await client.patch(
        f"/sheets/{sid}/rows/{rid}/delivery",
        json={"delivered_qty": qty},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _contribute(client, headers, sid: int, rid: int, qty: int) -> dict:
    resp = await client.post(
        f"/sheets/{sid}/rows/{rid}/contribute",
        json={"qty": qty},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _items(*pairs: tuple[str, int]) -> list[dict]:
    """构造请求 items：``("minecraft:iron_ingot", 10)`` → ``{"registry_id":..., "qty":...}``。"""
    return [{"registry_id": rid, "qty": q} for rid, q in pairs]


async def _submit_batch(client, headers, sid: int, items: list[dict]):
    """调批量提交端点；返回 httpx.Response（不做状态断言，让用例自断）。"""
    return await client.post(
        f"/sheets/{sid}/submit-batch", json={"items": items}, headers=headers
    )


def _patch_archive_root(monkeypatch, tmp_path):
    """注入 archive_root=tmp_path（复用 test_sheets_api.py:1148 范式）。"""
    import app.api.sheets.lifecycle as lifecycle_mod
    from app.core.config import Settings

    real = Settings()
    real.archive_root = str(tmp_path)
    monkeypatch.setattr(lifecycle_mod, "get_settings", lambda: real)


async def _advance_to_archived(client, headers, sid: int):
    resp = await client.post(f"/sheets/{sid}/advance?to=archived", headers=headers)
    assert resp.status_code == 200, resp.text


def _outcome_by_row(outcomes: list[dict], row_id: int) -> dict:
    """从 outcomes 找指定 row_id 的条目（每行恰好一条 outcome）。"""
    matches = [o for o in outcomes if o["row_id"] == row_id]
    assert len(matches) == 1, f"row_id={row_id} 应恰有一条 outcome，实得 {len(matches)}"
    return matches[0]


# =====================================================================
# 1. lock 成功
# =====================================================================

@pytest.mark.asyncio
async def test_lock_delivered_when_claimant_self_has_enough(client):
    """lock 行：本人认领 + 携带 ≥ need → delivered(qty=need)，行变 done。"""
    # Arrange：owner 建表 + lock 行（need=10），bob 认领
    owner_uuid, owner_bearer = await _make_player("alice")
    bob_uuid, bob_bearer = await _make_player("bob")
    sid = await _create_sheet(client, _jwt_headers(owner_uuid))
    row = await _upsert_row(client, _jwt_headers(owner_uuid), sid, need=10, mode=0)
    await _claim(client, _jwt_headers(bob_uuid), sid, row["id"])

    # Act：bob 提交 10 个
    resp = await _submit_batch(
        client, _jwt_headers(bob_uuid), sid,
        _items(("minecraft:iron_ingot", 10)),
    )

    # Assert
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sheet_id"] == sid
    assert body["actor_uuid"] == str(bob_uuid)
    assert body["totals"] == {"delivered": 1, "contributed": 0, "skipped": 0}
    o = _outcome_by_row(body["outcomes"], row["id"])
    assert o["action"] == "delivered"
    assert o["qty"] == 10
    assert o["reason"] == ""
    assert o["mode"] == 0
    assert o["registry_id"] == "minecraft:iron_ingot"
    assert o["item_name"] == "铁锭"
    assert o["is_claimant"] is True
    assert o["delivered_qty"] == 10  # 写后值
    assert o["need_qty"] == 10
    # 行确实落库为 done
    detail = (await client.get(f"/sheets/{sid}", headers=_jwt_headers(owner_uuid))).json()
    db_row = next(r for r in detail["rows"] if r["id"] == row["id"])
    assert db_row["status"] == "done"
    assert db_row["delivered_qty"] == 10


@pytest.mark.asyncio
async def test_lock_delivered_when_claimant_is_same_account_other_uuid(client):
    """R-5 account 级：行被同 account 的另一 UUID 认领 → 当前 UUID 也能 deliver。"""
    # Arrange：alice 主 UUID + alice_alt 同 account；alice_alt 认领 lock 行
    alice_uuid, _ = await _make_player("alice")
    alice_alt_uuid, alice_alt_bearer = await _bind_second_uuid_to_account(alice_uuid, "alice_alt")
    sid = await _create_sheet(client, _jwt_headers(alice_uuid))
    row = await _upsert_row(client, _jwt_headers(alice_uuid), sid, need=5, mode=0)
    await _claim(client, _jwt_headers(alice_alt_uuid), sid, row["id"])

    # Act：alice 主 UUID（同 account）提交
    resp = await _submit_batch(
        client, _jwt_headers(alice_uuid), sid,
        _items(("minecraft:iron_ingot", 5)),
    )

    # Assert：deliver 成功，is_claimant=True（同 account）
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["actor_uuid"] == str(alice_uuid)
    o = _outcome_by_row(body["outcomes"], row["id"])
    assert o["action"] == "delivered"
    assert o["qty"] == 5
    assert o["is_claimant"] is True  # R-5：同 account 任一 UUID 认领 → is_claimant=True


# =====================================================================
# 2. lock 跳过
# =====================================================================

@pytest.mark.asyncio
async def test_lock_skip_open_row_requires_claim_first(client):
    """lock 行未认领 → skipped「需先认领」（批量提交不自动认领 open 行）。"""
    # Arrange
    owner_uuid, owner_bearer = await _make_player("alice")
    bob_uuid, bob_bearer = await _make_player("bob")
    sid = await _create_sheet(client, _jwt_headers(owner_uuid))
    row = await _upsert_row(client, _jwt_headers(owner_uuid), sid, need=10, mode=0)
    # bob 未 claim，直接提交

    # Act
    resp = await _submit_batch(
        client, _jwt_headers(bob_uuid), sid,
        _items(("minecraft:iron_ingot", 10)),
    )

    # Assert
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["totals"] == {"delivered": 0, "contributed": 0, "skipped": 1}
    o = _outcome_by_row(body["outcomes"], row["id"])
    assert o["action"] == "skipped"
    assert o["qty"] == 0
    assert o["reason"] == "需先认领"
    assert o["is_claimant"] is False
    assert o["delivered_qty"] == 0  # 未变


@pytest.mark.asyncio
async def test_lock_skip_claimed_by_other_uuid(client):
    """lock 行被他人认领 → skipped「已被他人认领」。"""
    # Arrange：bob 认领，carol 提交
    owner_uuid, owner_bearer = await _make_player("alice")
    bob_uuid, _ = await _make_player("bob")
    carol_uuid, carol_bearer = await _make_player("carol")
    sid = await _create_sheet(client, _jwt_headers(owner_uuid))
    row = await _upsert_row(client, _jwt_headers(owner_uuid), sid, need=10, mode=0)
    await _claim(client, _jwt_headers(bob_uuid), sid, row["id"])

    # Act：carol 提交
    resp = await _submit_batch(
        client, _jwt_headers(carol_uuid), sid,
        _items(("minecraft:iron_ingot", 10)),
    )

    # Assert
    assert resp.status_code == 200, resp.text
    o = _outcome_by_row(resp.json()["outcomes"], row["id"])
    assert o["action"] == "skipped"
    assert o["reason"] == "已被他人认领"
    assert o["is_claimant"] is False


@pytest.mark.asyncio
async def test_lock_skip_done_row_already_ready(client):
    """lock 行已 done → skipped「已备齐」。"""
    # Arrange：bob 认领 + 上报备齐 → done
    owner_uuid, _ = await _make_player("alice")
    bob_uuid, bob_bearer = await _make_player("bob")
    sid = await _create_sheet(client, _jwt_headers(owner_uuid))
    row = await _upsert_row(client, _jwt_headers(owner_uuid), sid, need=10, mode=0)
    await _claim(client, _jwt_headers(bob_uuid), sid, row["id"])
    await _deliver(client, _jwt_headers(bob_uuid), sid, row["id"], 10)

    # Act：bob 再次提交同物
    resp = await _submit_batch(
        client, _jwt_headers(bob_uuid), sid,
        _items(("minecraft:iron_ingot", 5)),
    )

    # Assert
    assert resp.status_code == 200, resp.text
    o = _outcome_by_row(resp.json()["outcomes"], row["id"])
    assert o["action"] == "skipped"
    assert o["reason"] == "已备齐"
    assert o["is_claimant"] is True  # bob 仍是 claimant
    assert o["delivered_qty"] == 10  # 未变


@pytest.mark.asyncio
async def test_lock_skip_insufficient_qty_carries_shortage_reason(client):
    """lock 行：本人认领但携带 < need → skipped「数量不足（{have}/{need}）」。"""
    # Arrange
    owner_uuid, _ = await _make_player("alice")
    bob_uuid, bob_bearer = await _make_player("bob")
    sid = await _create_sheet(client, _jwt_headers(owner_uuid))
    row = await _upsert_row(client, _jwt_headers(owner_uuid), sid, need=10, mode=0)
    await _claim(client, _jwt_headers(bob_uuid), sid, row["id"])

    # Act：bob 只交 3 个（< need=10）
    resp = await _submit_batch(
        client, _jwt_headers(bob_uuid), sid,
        _items(("minecraft:iron_ingot", 3)),
    )

    # Assert
    assert resp.status_code == 200, resp.text
    o = _outcome_by_row(resp.json()["outcomes"], row["id"])
    assert o["action"] == "skipped"
    assert o["reason"] == "数量不足（3/10）"
    assert o["qty"] == 0
    assert o["is_claimant"] is True
    assert o["delivered_qty"] == 0  # 未变


# =====================================================================
# 3. progress 成功
# =====================================================================

@pytest.mark.asyncio
async def test_progress_contribute_increments_delivered_and_upserts_contributor(client):
    """progress 行：首次 contribute → delivered += qty + 贡献者记一条。"""
    # Arrange
    owner_uuid, _ = await _make_player("alice")
    bob_uuid, bob_bearer = await _make_player("bob")
    sid = await _create_sheet(client, _jwt_headers(owner_uuid))
    row = await _upsert_row(
        client, _jwt_headers(owner_uuid), sid, need=10, mode=1,
        item_name="圆石", registry_id="minecraft:cobblestone",
    )

    # Act：bob 提交 4 个圆石
    resp = await _submit_batch(
        client, _jwt_headers(bob_uuid), sid,
        _items(("minecraft:cobblestone", 4)),
    )

    # Assert
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["totals"] == {"delivered": 0, "contributed": 1, "skipped": 0}
    o = _outcome_by_row(body["outcomes"], row["id"])
    assert o["action"] == "contributed"
    assert o["qty"] == 4
    assert o["reason"] == ""
    assert o["mode"] == 1
    assert o["delivered_qty"] == 4  # 写后
    assert o["need_qty"] == 10
    # 行落库 + 贡献者落库
    detail = (await client.get(f"/sheets/{sid}", headers=_jwt_headers(owner_uuid))).json()
    db_row = next(r for r in detail["rows"] if r["id"] == row["id"])
    assert db_row["delivered_qty"] == 4
    assert db_row["status"] == "claimed"  # 未满
    assert len(db_row["contributors"]) == 1
    assert str(bob_uuid) in db_row["contributors"][0]["member_uuids"]


@pytest.mark.asyncio
async def test_progress_need_zero_infinite_mode_never_done(client):
    """progress need=0 → 无限模式，contribute 永不 done（status 始终 claimed）。"""
    # Arrange
    owner_uuid, _ = await _make_player("alice")
    bob_uuid, bob_bearer = await _make_player("bob")
    sid = await _create_sheet(client, _jwt_headers(owner_uuid))
    row = await _upsert_row(
        client, _jwt_headers(owner_uuid), sid, need=0, mode=1,
        item_name="泥土", registry_id="minecraft:dirt",
    )

    # Act：bob 提交 99 个
    resp = await _submit_batch(
        client, _jwt_headers(bob_uuid), sid,
        _items(("minecraft:dirt", 99)),
    )

    # Assert
    assert resp.status_code == 200, resp.text
    body = resp.json()
    o = _outcome_by_row(body["outcomes"], row["id"])
    assert o["action"] == "contributed"
    assert o["qty"] == 99
    assert o["delivered_qty"] == 99
    assert o["need_qty"] == 0
    # 行不会因 delivered>=need 进 done（need=0 = 无限模式）
    detail = (await client.get(f"/sheets/{sid}", headers=_jwt_headers(owner_uuid))).json()
    db_row = next(r for r in detail["rows"] if r["id"] == row["id"])
    assert db_row["status"] != "done"


# =====================================================================
# 4. progress 跳过
# =====================================================================

@pytest.mark.asyncio
async def test_progress_skip_already_full_ready(client):
    """progress 行 delivered>=need → skipped「已备齐」。"""
    # Arrange：bob 先上交满 10，行变 done
    owner_uuid, _ = await _make_player("alice")
    bob_uuid, bob_bearer = await _make_player("bob")
    sid = await _create_sheet(client, _jwt_headers(owner_uuid))
    row = await _upsert_row(
        client, _jwt_headers(owner_uuid), sid, need=10, mode=1,
        item_name="圆石", registry_id="minecraft:cobblestone",
    )
    await _contribute(client, _jwt_headers(bob_uuid), sid, row["id"], 10)

    # Act：再次提交
    resp = await _submit_batch(
        client, _jwt_headers(bob_uuid), sid,
        _items(("minecraft:cobblestone", 5)),
    )

    # Assert
    assert resp.status_code == 200, resp.text
    o = _outcome_by_row(resp.json()["outcomes"], row["id"])
    assert o["action"] == "skipped"
    assert o["reason"] == "已备齐"
    assert o["delivered_qty"] == 10


@pytest.mark.asyncio
async def test_progress_skip_qty_zero_means_no_item_in_inventory(client):
    """progress 行 qty=0 → 视为「背包没有此物」skip。"""
    # Arrange
    owner_uuid, _ = await _make_player("alice")
    bob_uuid, bob_bearer = await _make_player("bob")
    sid = await _create_sheet(client, _jwt_headers(owner_uuid))
    row = await _upsert_row(
        client, _jwt_headers(owner_uuid), sid, need=10, mode=1,
        item_name="圆石", registry_id="minecraft:cobblestone",
    )

    # Act：bob 提交 qty=0（即未携带此物）
    resp = await _submit_batch(
        client, _jwt_headers(bob_uuid), sid,
        _items(("minecraft:cobblestone", 0)),
    )

    # Assert
    assert resp.status_code == 200, resp.text
    o = _outcome_by_row(resp.json()["outcomes"], row["id"])
    assert o["action"] == "skipped"
    assert o["reason"] == "背包没有此物"
    assert o["qty"] == 0


# =====================================================================
# 5. 边界（请求体验证 + 重复聚合 + 未知 registry_id）
# =====================================================================

@pytest.mark.asyncio
async def test_duplicate_registry_id_aggregates_qty_in_request(client):
    """同一 registry_id 多条 entries → 后端聚合求和（[{a,3},{a,7}] → qty=10）。"""
    # Arrange
    owner_uuid, _ = await _make_player("alice")
    bob_uuid, bob_bearer = await _make_player("bob")
    sid = await _create_sheet(client, _jwt_headers(owner_uuid))
    row = await _upsert_row(
        client, _jwt_headers(owner_uuid), sid, need=10, mode=1,
        item_name="圆石", registry_id="minecraft:cobblestone",
    )

    # Act：传两条同 registry_id，qty 分别 3 / 7
    resp = await _submit_batch(
        client, _jwt_headers(bob_uuid), sid,
        _items(("minecraft:cobblestone", 3), ("minecraft:cobblestone", 7)),
    )

    # Assert：聚合后 qty=10 → 满足 need → delivered_qty=10
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["totals"]["contributed"] == 1
    o = _outcome_by_row(body["outcomes"], row["id"])
    assert o["action"] == "contributed"
    assert o["qty"] == 10  # 3+7 聚合


@pytest.mark.asyncio
async def test_unknown_registry_id_silently_ignored(client):
    """请求含未匹配任何行的 registry_id → 该条静默忽略（不产 outcome，不报错）。"""
    # Arrange：只建一行 iron_ingot
    owner_uuid, _ = await _make_player("alice")
    bob_uuid, bob_bearer = await _make_player("bob")
    sid = await _create_sheet(client, _jwt_headers(owner_uuid))
    row = await _upsert_row(
        client, _jwt_headers(owner_uuid), sid, need=10, mode=1,
        item_name="铁锭", registry_id="minecraft:iron_ingot",
    )

    # Act：items 含一行匹配 + 一行未知
    resp = await _submit_batch(
        client, _jwt_headers(bob_uuid), sid,
        _items(
            ("minecraft:iron_ingot", 4),
            ("minecraft:nonexistent_item", 999),
        ),
    )

    # Assert：只产 1 条 outcome（iron_ingot），未知项被忽略
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["outcomes"]) == 1
    assert body["outcomes"][0]["row_id"] == row["id"]
    assert body["outcomes"][0]["action"] == "contributed"


@pytest.mark.asyncio
async def test_empty_items_returns_422(client):
    """items=[] → 422（min_length=1）。"""
    owner_uuid, _ = await _make_player("alice")
    sid = await _create_sheet(client, _jwt_headers(owner_uuid))

    resp = await _submit_batch(client, _jwt_headers(owner_uuid), sid, [])

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_more_than_2000_items_returns_422(client):
    """items 超过 2000 条 → 422（max_length=2000）。"""
    owner_uuid, _ = await _make_player("alice")
    sid = await _create_sheet(client, _jwt_headers(owner_uuid))

    # 构造 2001 条（同 registry_id 不影响——校验在 schema 层先于聚合）
    huge_items = [
        {"registry_id": "minecraft:cobblestone", "qty": 1}
        for _ in range(2001)
    ]
    resp = await _submit_batch(client, _jwt_headers(owner_uuid), sid, huge_items)

    assert resp.status_code == 422


# =====================================================================
# 6. 错误（archived / 404）
# =====================================================================

@pytest.mark.asyncio
async def test_archived_sheet_returns_409(client, tmp_path, monkeypatch):
    """archived 终态只读：submit-batch → 409（含「归档」字样）。"""
    _patch_archive_root(monkeypatch, tmp_path)
    owner_uuid, owner_bearer = await _make_player("alice")
    sid = await _create_sheet(client, _jwt_headers(owner_uuid))
    await _upsert_row(client, _jwt_headers(owner_uuid), sid, need=10, mode=1)
    await _advance_to_archived(client, _jwt_headers(owner_uuid), sid)

    resp = await _submit_batch(
        client, _jwt_headers(owner_uuid), sid,
        _items(("minecraft:iron_ingot", 1)),
    )

    assert resp.status_code == 409
    assert "归档" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_missing_sheet_returns_404(client):
    """sheet 不存在 → 404。"""
    owner_uuid, _ = await _make_player("alice")
    resp = await _submit_batch(
        client, _jwt_headers(owner_uuid), 999999,
        _items(("minecraft:iron_ingot", 1)),
    )
    assert resp.status_code == 404


# =====================================================================
# 7. 鉴权（双通道专项 —— 4 例必全覆盖）
# =====================================================================

@pytest.mark.asyncio
async def test_service_token_channel_proxies_player_write(client):
    """服务端通道：X-Service-Token + X-Player-UUID 代玩家写成功（高权限代写）。"""
    # Arrange：owner 建表 + progress 行
    owner_uuid, _ = await _make_player("alice")
    bob_uuid, _ = await _make_player("bob")
    sid = await _create_sheet(client, _jwt_headers(owner_uuid))
    row = await _upsert_row(
        client, _jwt_headers(owner_uuid), sid, need=10, mode=1,
        item_name="圆石", registry_id="minecraft:cobblestone",
    )

    # Act：用 service-token + X-Player-UUID=bob 代 bob 提交（无 Authorization）
    resp = await _submit_batch(
        client, _svc_headers(bob_uuid), sid,
        _items(("minecraft:cobblestone", 4)),
    )

    # Assert：成功代写，actor_uuid=bob
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["actor_uuid"] == str(bob_uuid)
    o = _outcome_by_row(body["outcomes"], row["id"])
    assert o["action"] == "contributed"
    assert o["qty"] == 4


@pytest.mark.asyncio
async def test_jwt_channel_writes_self_only(client):
    """客户端通道：Bearer JWT 写自己成功（玩家自助提交）。"""
    # Arrange：owner alice + progress 行；bob 持 JWT 自助上交
    owner_uuid, _ = await _make_player("alice")
    bob_uuid, bob_bearer = await _make_player("bob")
    sid = await _create_sheet(client, _jwt_headers(owner_uuid))
    row = await _upsert_row(
        client, _jwt_headers(owner_uuid), sid, need=10, mode=1,
        item_name="圆石", registry_id="minecraft:cobblestone",
    )

    # Act：bob 用自己的 JWT 提交（无 service-token 头）
    resp = await _submit_batch(
        client, _jwt_headers(bob_uuid), sid,
        _items(("minecraft:cobblestone", 4)),
    )

    # Assert：actor_uuid=bob（JWT.sub 解析）
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["actor_uuid"] == str(bob_uuid)
    assert _outcome_by_row(body["outcomes"], row["id"])["qty"] == 4


@pytest.mark.asyncio
async def test_jwt_channel_ignores_forged_player_uuid_header(client):
    """JWT 通道下伪造 X-Player-UUID 头 → 身份仍 = JWT.sub（不影响 actor）。

    场景：alice（JWT）+ bob 认领的 lock 行。alice 伪造 X-Player-UUID=bob 想骗后端
    把自己当 bob；后端因 JWT 存在完全忽略 X-Player-UUID → actor=alice，对 bob
    认领的行只能 skip（is_claimant=False）。
    """
    # Arrange：alice 持 JWT；bob 认领了 lock 行
    alice_uuid, alice_bearer = await _make_player("alice")
    bob_uuid, _ = await _make_player("bob")
    sid = await _create_sheet(client, _jwt_headers(alice_uuid))
    row = await _upsert_row(client, _jwt_headers(alice_uuid), sid, need=10, mode=0)
    await _claim(client, _jwt_headers(bob_uuid), sid, row["id"])

    # Act：alice 用自己的 JWT + 伪造 X-Player-UUID=bob 头
    forged_headers = {
        "Authorization": _BEARER_CACHE[alice_uuid],
        "X-Player-UUID": str(bob_uuid),  # 试图伪装成 bob
    }
    resp = await _submit_batch(
        client, forged_headers, sid,
        _items(("minecraft:iron_ingot", 10)),
    )

    # Assert：身份锚仍是 JWT.sub=alice，伪造头无效
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["actor_uuid"] == str(alice_uuid)  # 不是 bob
    o = _outcome_by_row(body["outcomes"], row["id"])
    # alice 不是该行的认领人（claimant=bob，account_uuids 不含 alice）
    assert o["action"] == "skipped"
    assert o["reason"] == "已被他人认领"
    assert o["is_claimant"] is False


@pytest.mark.asyncio
async def test_non_bearer_authorization_does_not_fallback_to_service_token(client):
    """H-2：Authorization 非 Bearer → 401，绝不降级到 service-token 通道。

    即便同时带正确的 X-Service-Token + X-Player-UUID，只要 Authorization 头存在
    且非 Bearer 格式，就只走 JWT 通道报 401。
    """
    # Arrange
    owner_uuid, _ = await _make_player("alice")
    sid = await _create_sheet(client, _jwt_headers(owner_uuid))

    # Act：Authorization 非 Bearer + 双头齐全
    resp = await _submit_batch(
        client,
        headers={
            "Authorization": "Basic abc123",  # 非 Bearer → H-2 触发
            "X-Service-Token": "svc",
            "X-Player-UUID": str(owner_uuid),
        },
        sid=sid,
        items=_items(("minecraft:iron_ingot", 1)),
    )

    # Assert
    assert resp.status_code == 401


# =====================================================================
# 8. 事务：混合批（deliver + contribute + skip 齐全 + skip 不中断）
# =====================================================================

@pytest.mark.asyncio
async def test_mixed_batch_deliver_contribute_skip_atomic(client):
    """混合批：1 deliver + 1 contribute + 1 skip 同批提交 → 三类 outcome 齐全，
    成功行落库，skip 不中断整批。"""
    # Arrange：一张表三行——lock 行（bob 认领）+ progress 行（无人提交过）+ 他人认领的 lock 行
    owner_uuid, _ = await _make_player("alice")
    bob_uuid, bob_bearer = await _make_player("bob")
    carol_uuid, _ = await _make_player("carol")
    sid = await _create_sheet(client, _jwt_headers(owner_uuid), title="混合批")

    # 行1：lock，bob 认领，need=10 → 应 deliver
    row_lock_mine = await _upsert_row(
        client, _jwt_headers(owner_uuid), sid, need=10, mode=0,
        item_name="铁锭", registry_id="minecraft:iron_ingot", sort_order=0,
    )
    await _claim(client, _jwt_headers(bob_uuid), sid, row_lock_mine["id"])

    # 行2：progress，need=10 → 应 contribute
    row_progress = await _upsert_row(
        client, _jwt_headers(owner_uuid), sid, need=10, mode=1,
        item_name="圆石", registry_id="minecraft:cobblestone", sort_order=1,
    )

    # 行3：lock，carol 认领 → bob 视角应 skip「已被他人认领」
    row_lock_other = await _upsert_row(
        client, _jwt_headers(owner_uuid), sid, need=10, mode=0,
        item_name="金锭", registry_id="minecraft:gold_ingot", sort_order=2,
    )
    await _claim(client, _jwt_headers(carol_uuid), sid, row_lock_other["id"])

    # Act：bob 提交三类材料（铁锭10 + 圆石4 + 金锭10）
    resp = await _submit_batch(
        client, _jwt_headers(bob_uuid), sid,
        _items(
            ("minecraft:iron_ingot", 10),
            ("minecraft:cobblestone", 4),
            ("minecraft:gold_ingot", 10),
        ),
    )

    # Assert：三类 outcome 齐全，skip 不中断整批
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["totals"] == {"delivered": 1, "contributed": 1, "skipped": 1}

    o_deliver = _outcome_by_row(body["outcomes"], row_lock_mine["id"])
    assert o_deliver["action"] == "delivered"
    assert o_deliver["qty"] == 10
    assert o_deliver["is_claimant"] is True

    o_contribute = _outcome_by_row(body["outcomes"], row_progress["id"])
    assert o_contribute["action"] == "contributed"
    assert o_contribute["qty"] == 4

    o_skip = _outcome_by_row(body["outcomes"], row_lock_other["id"])
    assert o_skip["action"] == "skipped"
    assert o_skip["reason"] == "已被他人认领"
    assert o_skip["is_claimant"] is False

    # 落库验证：成功行状态确实改变
    detail = (await client.get(f"/sheets/{sid}", headers=_jwt_headers(owner_uuid))).json()
    rows_by_id = {r["id"]: r for r in detail["rows"]}
    assert rows_by_id[row_lock_mine["id"]]["status"] == "done"
    assert rows_by_id[row_lock_mine["id"]]["delivered_qty"] == 10
    assert rows_by_id[row_progress["id"]]["delivered_qty"] == 4
    assert rows_by_id[row_progress["id"]]["status"] == "claimed"  # 未满
    # carol 的行不受 bob 提交影响（仍是 claimed + delivered=0）
    assert rows_by_id[row_lock_other["id"]]["claimant_uuid"] == str(carol_uuid)
    assert rows_by_id[row_lock_other["id"]]["delivered_qty"] == 0
