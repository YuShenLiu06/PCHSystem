"""施工进度上报层「按材料封顶」集成测试（迭代 4）。

封顶粒度 = (sheet_id, registry_id) 跨全部账号合计净放置不得超过
``sum(sheet_rows.need_qty)``（含子物品，与 ``get_material_completion`` 同口径）。

覆盖矩阵（task plan §1-§7）：
1. 未满时报 → accepted 全量，material_totals 更新
2. 恰满：need=10 已 8，报 +5 → accepted=2，skipped reason=「已达材料上限」net_delta=3
3. 已满再报 → 整条 skip「已达材料上限」，DB 不变
4. 拆毁释放：已满 10，报 broken=3 → accepted=-3 合计=7；再报 +5 → accepted=3 over=2
5. 跨账号共享同一材料上限：A 放满后 B 再放 → B 被 skip
6. completion_pct 不再 >100（progress 端点回归）
7. net_qty == placed_qty - broken_qty 不变量在封顶后仍成立
"""
import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import async_session_factory
from app.models.construction import PlacementRecord
from app.models.sheet import Sheet, SheetRow
from app.models.user import Player, WebAccount
from app.repositories import construction_repo

pytestmark = pytest.mark.asyncio
_settings = get_settings()
SVC = _settings.mcdr_service_token


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

async def _seed_player(name="alice", role="user"):
    puuid = uuid.uuid4()
    async with async_session_factory() as s:
        account = WebAccount(role=role)
        s.add(account)
        await s.flush()
        aid = account.id
        s.add(Player(uuid=puuid, current_name=name, role=role, web_account_id=aid))
        await s.commit()
    return puuid, aid


async def _seed_sheet(owner_uuid, rows, title="封顶项目", status="constructing") -> int:
    """rows: dict[registry_id, need_qty] → 顶层行。"""
    async with async_session_factory() as s:
        sheet = Sheet(owner_uuid=owner_uuid, title=title, status=status)
        s.add(sheet)
        await s.flush()
        for rid, need in rows.items():
            s.add(
                SheetRow(
                    sheet_id=sheet.id,
                    item_name=rid,
                    registry_id=rid,
                    need_qty=need,
                )
            )
        await s.commit()
        return sheet.id


def _svc():
    return {"X-Service-Token": SVC}


async def _report(client, sid, placements):
    """便捷上报封装，返回 body。"""
    r = await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": placements},
        headers=_svc(),
    )
    assert r.status_code == 200, r.text
    return r.json()


# ===========================================================================
# 1. 未满时报 → accepted 全量
# ===========================================================================

async def test_under_cap_full_accept(client):
    """未满：报 +5 全部 accepted；DB net=5；material_totals 同步。"""
    owner_uuid, _ = await _seed_player("owner")
    p1, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid, {"minecraft:stone": 10})

    body = await _report(client, sid, [
        {"player_uuid": str(p1), "registry_id": "minecraft:stone",
         "placed_qty": 5, "broken_qty": 0},
    ])
    assert body["totals"] == {"accepted": 1, "skipped": 0}
    assert body["outcomes"][0]["action"] == "accepted"
    assert body["outcomes"][0]["net_delta"] == 5

    async with async_session_factory() as s:
        rows = (await s.execute(
            select(PlacementRecord).where(PlacementRecord.sheet_id == sid)
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].net_qty == 5


# ===========================================================================
# 2. 恰满：need=10 已 8，报 +5 → accepted=2，skipped net_delta=3
# ===========================================================================

async def test_partial_accept_at_cap(client):
    """部分接受：报超过 available，差额 emit skipped「已达材料上限」。"""
    owner_uuid, _ = await _seed_player("owner")
    p1, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid, {"minecraft:stone": 10})

    # 先放到 8
    await _report(client, sid, [
        {"player_uuid": str(p1), "registry_id": "minecraft:stone",
         "placed_qty": 8, "broken_qty": 0},
    ])
    # 再报 +5 → available=2 → accepted=2, over=3
    body = await _report(client, sid, [
        {"player_uuid": str(p1), "registry_id": "minecraft:stone",
         "placed_qty": 5, "broken_qty": 0},
    ])
    assert body["totals"] == {"accepted": 1, "skipped": 1}
    accepted = next(o for o in body["outcomes"] if o["action"] == "accepted")
    skipped = next(o for o in body["outcomes"] if o["action"] == "skipped")
    assert accepted["net_delta"] == 2
    assert skipped["reason"] == "已达材料上限"
    assert skipped["net_delta"] == 3

    # DB 合计 = 10（精确卡到 need）
    async with async_session_factory() as s:
        rows = (await s.execute(
            select(PlacementRecord).where(PlacementRecord.sheet_id == sid)
        )).scalars().all()
    assert sum(r.net_qty for r in rows) == 10


# ===========================================================================
# 3. 已满再报 → 整条 skip「已达材料上限」，DB 不变
# ===========================================================================

async def test_full_cap_skip_entire(client):
    """已达上限：整条 skip，不写库。"""
    owner_uuid, _ = await _seed_player("owner")
    p1, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid, {"minecraft:stone": 10})

    # 放满
    await _report(client, sid, [
        {"player_uuid": str(p1), "registry_id": "minecraft:stone",
         "placed_qty": 10, "broken_qty": 0},
    ])
    # 再报 +3 → accepted_delta=0 → 整条 skip（仅 1 条 outcome）
    body = await _report(client, sid, [
        {"player_uuid": str(p1), "registry_id": "minecraft:stone",
         "placed_qty": 3, "broken_qty": 0},
    ])
    assert body["totals"] == {"accepted": 0, "skipped": 1}
    assert len(body["outcomes"]) == 1
    assert body["outcomes"][0]["action"] == "skipped"
    assert body["outcomes"][0]["reason"] == "已达材料上限"
    assert body["outcomes"][0]["net_delta"] == 3

    # DB 不变（合计仍 = 10）
    async with async_session_factory() as s:
        rows = (await s.execute(
            select(PlacementRecord).where(PlacementRecord.sheet_id == sid)
        )).scalars().all()
    assert sum(r.net_qty for r in rows) == 10


# ===========================================================================
# 4. 拆毁释放：已满 10，报 broken=3 → accepted=-3 合计=7；再报 +5 → accepted=3 over=2
# ===========================================================================

async def test_broken_releases_capacity(client):
    """拆毁减少 material_totals → 释放容量供后续放置。"""
    owner_uuid, _ = await _seed_player("owner")
    p1, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid, {"minecraft:stone": 10})

    # 放满
    await _report(client, sid, [
        {"player_uuid": str(p1), "registry_id": "minecraft:stone",
         "placed_qty": 10, "broken_qty": 0},
    ])
    # 报 broken=3（delta=-3 → accepted_delta=-3，不受 cap 限制）
    body = await _report(client, sid, [
        {"player_uuid": str(p1), "registry_id": "minecraft:stone",
         "placed_qty": 0, "broken_qty": 3},
    ])
    assert body["totals"] == {"accepted": 1, "skipped": 0}
    assert body["outcomes"][0]["net_delta"] == -3

    # 合计 = 7
    async with async_session_factory() as s:
        rows = (await s.execute(
            select(PlacementRecord).where(PlacementRecord.sheet_id == sid)
        )).scalars().all()
    assert sum(r.net_qty for r in rows) == 7

    # 再报 +5 → available = 10 - 7 = 3 → accepted=3, over=2
    body = await _report(client, sid, [
        {"player_uuid": str(p1), "registry_id": "minecraft:stone",
         "placed_qty": 5, "broken_qty": 0},
    ])
    assert body["totals"] == {"accepted": 1, "skipped": 1}
    accepted = next(o for o in body["outcomes"] if o["action"] == "accepted")
    skipped = next(o for o in body["outcomes"] if o["action"] == "skipped")
    assert accepted["net_delta"] == 3
    assert skipped["net_delta"] == 2

    # 合计 = 10
    async with async_session_factory() as s:
        rows = (await s.execute(
            select(PlacementRecord).where(PlacementRecord.sheet_id == sid)
        )).scalars().all()
    assert sum(r.net_qty for r in rows) == 10


# ===========================================================================
# 5. 跨账号共享同一材料上限：A 放满后 B 再放 → B 被 skip
# ===========================================================================

async def test_cross_account_shared_cap(client):
    """A 放满后 B 报同 rid → 整条 skip（跨账号合计已满）。"""
    owner_uuid, _ = await _seed_player("owner")
    p1, _ = await _seed_player("p1")
    p2, _ = await _seed_player("p2")
    sid = await _seed_sheet(owner_uuid, {"minecraft:stone": 10})

    # p1 放满
    await _report(client, sid, [
        {"player_uuid": str(p1), "registry_id": "minecraft:stone",
         "placed_qty": 10, "broken_qty": 0},
    ])
    # p2 再报同 rid → 跨账号合计已满 → 整条 skip
    body = await _report(client, sid, [
        {"player_uuid": str(p2), "registry_id": "minecraft:stone",
         "placed_qty": 4, "broken_qty": 0},
    ])
    assert body["totals"] == {"accepted": 0, "skipped": 1}
    assert body["outcomes"][0]["reason"] == "已达材料上限"

    # 各账号 row 保持：p1=10, p2 无 row
    async with async_session_factory() as s:
        rows = (await s.execute(
            select(PlacementRecord).where(PlacementRecord.sheet_id == sid)
            .order_by(PlacementRecord.account_id)
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].net_qty == 10


# ===========================================================================
# 5b. 跨账号部分接受：A 放 8 后 B 报 +5 → B accepted=2 over=3
# ===========================================================================

async def test_cross_account_partial_accept(client):
    """A 占 8/10，B 再报 +5 → B accepted=2 over=3（跨账号共享）。"""
    owner_uuid, _ = await _seed_player("owner")
    p1, _ = await _seed_player("p1")
    p2, _ = await _seed_player("p2")
    sid = await _seed_sheet(owner_uuid, {"minecraft:stone": 10})

    await _report(client, sid, [
        {"player_uuid": str(p1), "registry_id": "minecraft:stone",
         "placed_qty": 8, "broken_qty": 0},
    ])
    body = await _report(client, sid, [
        {"player_uuid": str(p2), "registry_id": "minecraft:stone",
         "placed_qty": 5, "broken_qty": 0},
    ])
    assert body["totals"] == {"accepted": 1, "skipped": 1}
    accepted = next(o for o in body["outcomes"] if o["action"] == "accepted")
    skipped = next(o for o in body["outcomes"] if o["action"] == "skipped")
    assert accepted["net_delta"] == 2
    assert skipped["net_delta"] == 3

    # 跨账号合计 = 10
    async with async_session_factory() as s:
        rows = (await s.execute(
            select(PlacementRecord).where(PlacementRecord.sheet_id == sid)
        )).scalars().all()
    assert sum(r.net_qty for r in rows) == 10


# ===========================================================================
# 6. progress material_completion 不再 >100
# ===========================================================================

async def test_progress_completion_never_exceeds_100(client):
    """progress.material_completion.net_qty <= need_qty（封顶后不再超量）。"""
    from app.core.jwt import create_access_token

    owner_uuid, owner_aid, = await _seed_player("owner")
    p1, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid, {"minecraft:stone": 10})

    # 试图放到 15（封顶应阻止）
    await _report(client, sid, [
        {"player_uuid": str(p1), "registry_id": "minecraft:stone",
         "placed_qty": 15, "broken_qty": 0},
    ])

    owner_bearer = f"Bearer {create_access_token(owner_aid, 'user', active_uuid=owner_uuid)}"
    r = await client.get(
        f"/v1/construction/{sid}/progress",
        headers={"Authorization": owner_bearer},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    stone = body["material_completion"][0]
    assert stone["need_qty"] == 10
    assert stone["net_qty"] == 10  # 卡到 need
    assert stone["completion_pct"] == 100.0


# ===========================================================================
# 7. 不变量 net_qty == placed_qty - broken_qty（封顶后仍成立）
# ===========================================================================

async def test_invariant_preserved_after_cap(client):
    """部分接受场景：每行 net == placed - broken 不变量仍成立。"""
    owner_uuid, _ = await _seed_player("owner")
    p1, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid, {"minecraft:stone": 10})

    # 先放 8（placed=8, broken=0, net=8）
    await _report(client, sid, [
        {"player_uuid": str(p1), "registry_id": "minecraft:stone",
         "placed_qty": 8, "broken_qty": 0},
    ])
    # 报 +5（available=2 → accepted_delta=2, broken=0）
    # 传 placed_eff = 2 + 0 = 2 → placed_qty += 2 = 10, net_qty += 2 = 10
    await _report(client, sid, [
        {"player_uuid": str(p1), "registry_id": "minecraft:stone",
         "placed_qty": 5, "broken_qty": 0},
    ])

    async with async_session_factory() as s:
        rows = (await s.execute(
            select(PlacementRecord).where(PlacementRecord.sheet_id == sid)
        )).scalars().all()
    for r in rows:
        assert r.net_qty == r.placed_qty - r.broken_qty
        # 合计正好 = need
    assert sum(r.net_qty for r in rows) == 10


# ===========================================================================
# 7b. 不变量在拆毁 + 部分接受混合场景仍成立
# ===========================================================================

async def test_invariant_mixed_broken_and_cap(client):
    """混合场景（拆毁后部分接受）：每行 net == placed - broken。"""
    owner_uuid, _ = await _seed_player("owner")
    p1, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid, {"minecraft:stone": 10})

    # 放满 10
    await _report(client, sid, [
        {"player_uuid": str(p1), "registry_id": "minecraft:stone",
         "placed_qty": 10, "broken_qty": 0},
    ])
    # 拆 3（accepted_delta=-3，placed_eff = -3 + 3 = 0, broken_eff=3）
    # → placed_qty += 0 = 10, broken_qty += 3 = 3, net_qty += -3 = 7
    await _report(client, sid, [
        {"player_uuid": str(p1), "registry_id": "minecraft:stone",
         "placed_qty": 0, "broken_qty": 3},
    ])
    # 再放 5（available=3 → accepted_delta=3, placed_eff = 3 + 0 = 3）
    # → placed_qty += 3 = 13, broken_qty += 0 = 3, net_qty += 3 = 10
    await _report(client, sid, [
        {"player_uuid": str(p1), "registry_id": "minecraft:stone",
         "placed_qty": 5, "broken_qty": 0},
    ])

    async with async_session_factory() as s:
        rows = (await s.execute(
            select(PlacementRecord).where(PlacementRecord.sheet_id == sid)
        )).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.placed_qty == 13
    assert row.broken_qty == 3
    assert row.net_qty == 10
    assert row.net_qty == row.placed_qty - row.broken_qty


# ===========================================================================
# 8. 子物品 need 计入口径（与 get_material_completion 一致）
# ===========================================================================

async def test_cap_with_subrows_need_aggregation(client):
    """父行 need + 子行 need 同时计入 cap（按 registry 聚合 sum）。"""
    owner_uuid, _ = await _seed_player("owner")
    p1, _ = await _seed_player("p1")
    # 同一 registry 出现在多个顶层行（例如不同 item_name 同 registry_id）
    # → need 按 registry 聚合 sum
    async with async_session_factory() as s:
        sheet = Sheet(owner_uuid=owner_uuid, title="多行同 rid", status="constructing")
        s.add(sheet)
        await s.flush()
        # 两行同 registry_id，need_qty 各 6 → 合计 12
        s.add(SheetRow(sheet_id=sheet.id, item_name="A",
                       registry_id="minecraft:stone", need_qty=6))
        s.add(SheetRow(sheet_id=sheet.id, item_name="B",
                       registry_id="minecraft:stone", need_qty=6))
        await s.commit()
        sid = sheet.id

    # 报 +10（available=12，未满）→ 全部 accepted
    body = await _report(client, sid, [
        {"player_uuid": str(p1), "registry_id": "minecraft:stone",
         "placed_qty": 10, "broken_qty": 0},
    ])
    assert body["totals"] == {"accepted": 1, "skipped": 0}

    # 再报 +5（available=2 → accepted=2 over=3）
    body = await _report(client, sid, [
        {"player_uuid": str(p1), "registry_id": "minecraft:stone",
         "placed_qty": 5, "broken_qty": 0},
    ])
    assert body["totals"] == {"accepted": 1, "skipped": 1}
    accepted = next(o for o in body["outcomes"] if o["action"] == "accepted")
    assert accepted["net_delta"] == 2

    # 跨账号合计 = 12（卡到聚合 need）
    async with async_session_factory() as s:
        rows = (await s.execute(
            select(PlacementRecord).where(PlacementRecord.sheet_id == sid)
        )).scalars().all()
    assert sum(r.net_qty for r in rows) == 12


# ===========================================================================
# 9. 多 registry 互不干扰（A registry 满 不影响 B registry 接受）
# ===========================================================================

async def test_multi_registry_independent(client):
    """stone 满 → dirt 仍可接受（封顶粒度 = per registry）。"""
    owner_uuid, _ = await _seed_player("owner")
    p1, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid, {"minecraft:stone": 5, "minecraft:dirt": 10})

    # 同批次：stone 报 10（超 cap=5）+ dirt 报 3（未满 cap=10）
    body = await _report(client, sid, [
        {"player_uuid": str(p1), "registry_id": "minecraft:stone",
         "placed_qty": 10, "broken_qty": 0},
        {"player_uuid": str(p1), "registry_id": "minecraft:dirt",
         "placed_qty": 3, "broken_qty": 0},
    ])
    # stone: accepted=1, skipped=1；dirt: accepted=1
    assert body["totals"]["accepted"] == 2
    assert body["totals"]["skipped"] == 1

    async with async_session_factory() as s:
        rows = (await s.execute(
            select(PlacementRecord).where(PlacementRecord.sheet_id == sid)
        )).scalars().all()
    by_rid = {r.registry_id: r.net_qty for r in rows}
    assert by_rid["minecraft:stone"] == 5  # 卡到 need
    assert by_rid["minecraft:dirt"] == 3   # 未满


# ===========================================================================
# 10. 迁移 0022 SQL：clamp 超量历史数据（直接执行 SQL 验证算法）
# ===========================================================================

async def test_migration_clamps_overcap_history(client):
    """0022 迁移 SQL 算法验证：手工塞超量数据 → 执行 SQL → 合计 = need。

    场景：need=10，3 账号合计 net=15（超额 5）；
    按账号 net 占比分摊 → sum(clamp_delta)=5，合计=10；
    不变量 per row net == placed - broken 保持。
    """
    from sqlalchemy import text as sql_text

    owner_uuid, _ = await _seed_player("owner")
    p1, p1_aid = await _seed_player("p1")
    p2, p2_aid = await _seed_player("p2")
    p3, p3_aid = await _seed_player("p3")
    sid = await _seed_sheet(owner_uuid, {"minecraft:stone": 10})

    # 手工塞超量：合计 net=15（>need=10）
    # 拆分：p1=8, p2=4, p3=3 → 合计 15
    # 同时维护 placed=net, broken=0 不变量
    async with async_session_factory() as s:
        for aid, net in [(p1_aid, 8), (p2_aid, 4), (p3_aid, 3)]:
            await s.execute(
                sql_text(
                    "INSERT INTO construction.placement_records "
                    "(sheet_id, account_id, registry_id, placed_qty, broken_qty, net_qty) "
                    "VALUES (:sid, :aid, :rid, :net, 0, :net)"
                ),
                {"sid": sid, "aid": aid, "rid": "minecraft:stone", "net": net},
            )
        await s.commit()

    # 直接跑 0022 迁移 SQL（取自迁移文件 _CLAMP_SQL，模块名以数字开头无法 import）
    import importlib.util
    from pathlib import Path

    migration_path = (
        Path(__file__).parent.parent / "alembic" / "versions" / "0022_clamp_overcap_net.py"
    )
    spec = importlib.util.spec_from_file_location("_migration_0022", migration_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    async with async_session_factory() as s:
        await s.execute(sql_text(mod._CLAMP_SQL))
        await s.commit()

    # 验证：合计 = need = 10；每行 net == placed - broken
    async with async_session_factory() as s:
        rows = (await s.execute(
            select(PlacementRecord).where(PlacementRecord.sheet_id == sid)
        )).scalars().all()

    assert sum(r.net_qty for r in rows) == 10
    for r in rows:
        assert r.net_qty == r.placed_qty - r.broken_qty
        assert r.net_qty >= 0
