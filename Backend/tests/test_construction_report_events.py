"""施工进度上报层 ``construction.report_events``（玩家可见事件流水，迭代 5）集成测试。

覆盖矩阵（task plan §1-§7）：
1. accepted 上报 → report_events 落一行 action=accepted
2. 不在清单的方块上报 → 落一行 action=skipped reason=`方块不在项目材料清单内`
3. 封顶（迭代4）超量上报 → accepted + skipped 都落（part-accept 场景）
4. 源冲突 skip → 落一行 reason=`玩家当前由其他源上报`
5. 未绑账号 / 玩家不存在 → **不落**事件（无 account_id）
6. ``GET /me/report-events`` 只返本人 account、按 recorded_at desc、含 sheet_title；带 limit
7. 无归因 skip_all → bound 玩家落 sheet_id=null 的事件
"""
import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.jwt import create_access_token
from app.models.construction import ReportEvent
from app.models.sheet import Sheet, SheetRow
from app.models.user import Player, WebAccount
from app.repositories import construction_repo

pytestmark = pytest.mark.asyncio
_settings = get_settings()
SVC = _settings.mcdr_service_token


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

async def _seed_sheet(
    owner_uuid, status="constructing", title="测试项目",
    rows=("minecraft:stone", "minecraft:dirt"),
) -> int:
    async with async_session_factory() as s:
        sheet = Sheet(owner_uuid=owner_uuid, title=title, status=status)
        s.add(sheet)
        await s.flush()
        for rid in rows or ():
            s.add(
                SheetRow(sheet_id=sheet.id, item_name=rid, registry_id=rid, need_qty=10)
            )
        await s.commit()
        return sheet.id


async def _seed_player(name="alice", role="user"):
    """返回 (uuid, account_id, bearer, mod_bearer)。"""
    puuid = uuid.uuid4()
    async with async_session_factory() as s:
        account = WebAccount(role=role)
        s.add(account)
        await s.flush()
        aid = account.id
        s.add(Player(uuid=puuid, current_name=name, role=role, web_account_id=aid))
        await s.commit()
    bearer = f"Bearer {create_access_token(aid, role, active_uuid=puuid)}"
    return puuid, aid, bearer


def _svc():
    return {"X-Service-Token": SVC}


async def _events_for_account(account_id: int) -> list[ReportEvent]:
    """读取该 account 的全部 report_events（按 id 升序，便于断言时序）。"""
    async with async_session_factory() as s:
        rows = (
            await s.execute(
                select(ReportEvent)
                .where(ReportEvent.account_id == account_id)
                .order_by(ReportEvent.id)
            )
        ).scalars().all()
    return list(rows)


# ===========================================================================
# 1. accepted 上报 → 落一行 action=accepted
# ===========================================================================

async def test_accepted_writes_event(client):
    owner_uuid, _, _ = await _seed_player("owner")
    p1, p1_aid, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid)

    r = await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 7, "broken_qty": 2},
        ]},
        headers=_svc(),
    )
    assert r.status_code == 200, r.text

    events = await _events_for_account(p1_aid)
    assert len(events) == 1
    ev = events[0]
    assert ev.action == "accepted"
    assert ev.reason == ""
    assert ev.net_delta == 5  # 7 - 2
    assert ev.sheet_id == sid
    assert ev.registry_id == "minecraft:stone"
    assert ev.player_uuid == p1


# ===========================================================================
# 2. 不在清单 → 落 skipped 行 reason=`方块不在项目材料清单内`
# ===========================================================================

async def test_skip_not_in_manifest_writes_event(client):
    owner_uuid, _, _ = await _seed_player("owner")
    p1, p1_aid, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid, rows=None)

    r = await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
        ]},
        headers=_svc(),
    )
    assert r.status_code == 200

    events = await _events_for_account(p1_aid)
    assert len(events) == 1
    ev = events[0]
    assert ev.action == "skipped"
    assert ev.reason == "方块不在项目材料清单内"
    assert ev.net_delta == 0


# ===========================================================================
# 3. 封顶超量 → accepted + skipped 都落（part-accept 场景）
# ===========================================================================

async def test_partial_accept_at_cap_writes_both_events(client):
    owner_uuid, _, _ = await _seed_player("owner")
    p1, p1_aid, _ = await _seed_player("p1")
    # 单行 need=10 → 容易测封顶
    async with async_session_factory() as s:
        sheet = Sheet(owner_uuid=owner_uuid, title="封顶项目", status="constructing")
        s.add(sheet)
        await s.flush()
        s.add(SheetRow(sheet_id=sheet.id, item_name="stone",
                       registry_id="minecraft:stone", need_qty=10))
        await s.commit()
        sid = sheet.id

    # 先放到 8
    await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 8, "broken_qty": 0},
        ]},
        headers=_svc(),
    )
    # 再报 +5 → accepted=2, over=3
    await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 5, "broken_qty": 0},
        ]},
        headers=_svc(),
    )

    events = await _events_for_account(p1_aid)
    # 第一次 1 条 accepted（8）；第二次 2 条（accepted=2 + skipped=3）
    assert len(events) == 3
    assert events[0].action == "accepted" and events[0].net_delta == 8
    assert events[1].action == "accepted" and events[1].net_delta == 2
    assert events[2].action == "skipped"
    assert events[2].reason == "已达材料上限"
    assert events[2].net_delta == 3


# ===========================================================================
# 4. 源冲突 skip → 落一行 reason=`玩家当前由其他源上报`
# ===========================================================================

async def test_source_conflict_writes_event(client):
    owner_uuid, _, _ = await _seed_player("owner")
    p1, p1_aid, p1_bearer = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid)
    # p1 切到 local mod → mcdr 上报被 skip
    r = await client.post(
        "/v1/construction/source/switch-self",
        json={"mode": "local", "source_id": "their-mod"},
        headers={"Authorization": p1_bearer},
    )
    assert r.status_code == 200

    await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
        ]},
        headers=_svc(),
    )

    events = await _events_for_account(p1_aid)
    assert len(events) == 1
    assert events[0].action == "skipped"
    assert events[0].reason == "玩家当前由其他源上报"


# ===========================================================================
# 5. 未绑账号 / 玩家不存在 → 不落事件
# ===========================================================================

async def test_unbound_player_no_event(client):
    owner_uuid, _, _ = await _seed_player("owner")
    # 未绑账号玩家
    puuid = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=puuid, current_name="nobind"))
        await s.commit()
    sid = await _seed_sheet(owner_uuid)

    await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(puuid), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
        ]},
        headers=_svc(),
    )
    # 该玩家无 account_id → report_events 无行（按 puuid 查应空）
    async with async_session_factory() as s:
        rows = (await s.execute(
            select(ReportEvent).where(ReportEvent.player_uuid == puuid)
        )).scalars().all()
    assert len(rows) == 0


async def test_unknown_player_no_event(client):
    owner_uuid, _, _ = await _seed_player("owner")
    sid = await _seed_sheet(owner_uuid)
    ghost = uuid.uuid4()
    await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(ghost), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
        ]},
        headers=_svc(),
    )
    async with async_session_factory() as s:
        rows = (await s.execute(
            select(ReportEvent).where(ReportEvent.player_uuid == ghost)
        )).scalars().all()
    assert len(rows) == 0


# ===========================================================================
# 6. GET /me/report-events：本人 account、按 recorded_at desc、含 sheet_title；带 limit
# ===========================================================================

async def test_me_report_events_endpoint(client):
    owner_uuid, _, owner_bearer = await _seed_player("owner")
    p1, p1_aid, p1_bearer = await _seed_player("p1")
    # 另一玩家（隔离断言）
    p2, _, _ = await _seed_player("p2")
    sid = await _seed_sheet(owner_uuid, title="塔楼")

    # p1 上报 1 条 accepted
    await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 3, "broken_qty": 1},
        ]},
        headers=_svc(),
    )
    # p2 上报 1 条 accepted（不应出现在 p1 的列表里）
    await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(p2), "registry_id": "minecraft:dirt", "placed_qty": 2, "broken_qty": 0},
        ]},
        headers=_svc(),
    )

    r = await client.get(
        "/v1/construction/me/report-events",
        headers={"Authorization": p1_bearer},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 1
    item = body[0]
    assert item["action"] == "accepted"
    assert item["sheet_id"] == sid
    assert item["sheet_title"] == "塔楼"
    assert item["registry_id"] == "minecraft:stone"
    assert item["net_delta"] == 2


async def test_me_report_events_limit(client):
    owner_uuid, _, _ = await _seed_player("owner")
    p1, _, p1_bearer = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid)
    # 上报 3 次
    for _ in range(3):
        await client.post(
            "/v1/construction/report",
            json={"sheet_id": sid, "placements": [
                {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
            ]},
            headers=_svc(),
        )
    # limit=2 → 只返 2 条；按 recorded_at desc → 前两条是最近
    r = await client.get(
        "/v1/construction/me/report-events?limit=2",
        headers={"Authorization": p1_bearer},
    )
    body = r.json()
    assert len(body) == 2
    # 倒序：第一条时间 >= 第二条
    assert body[0]["recorded_at"] >= body[1]["recorded_at"]


async def test_me_report_events_unbound_403(client):
    """未绑 Web 账号的玩家 → 403（与 /me/reports 同口径）。"""
    puuid = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=puuid, current_name="nobind"))
        await s.commit()
    token = create_access_token(999999, "user", active_uuid=puuid)  # 不存在的 account
    r = await client.get(
        "/v1/construction/me/report-events",
        headers={"Authorization": f"Bearer {token}"},
    )
    # JWT sub 不存在的 account → _player_from_jwt 会抛 401
    assert r.status_code in (401, 403)


# ===========================================================================
# 7. 无归因 skip_all → bound 玩家落 sheet_id=null 事件
# ===========================================================================

async def test_no_attribution_skip_all_writes_null_sheet_event(client):
    """无施工项目（attribution_source='none'）→ bound 玩家落 sheet_id=null 事件。"""
    p1, p1_aid, _ = await _seed_player("p1")
    # 不创建任何 constructing sheet → attribution=none
    r = await client.post(
        "/v1/construction/report",
        json={"placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
        ]},
        headers=_svc(),
    )
    assert r.status_code == 200
    assert r.json()["attribution_source"] == "none"

    events = await _events_for_account(p1_aid)
    assert len(events) == 1
    ev = events[0]
    assert ev.action == "skipped"
    assert ev.reason == "当前无施工中项目"
    assert ev.sheet_id is None  # 归因失败 → sheet_id 列 null


async def test_client_mod_disabled_skip_all_writes_event(client):
    """客户端 mod 全局关闭（jwt 通道）→ bound 玩家落事件 reason=`客户端模组上报已被服主关闭`。"""
    _, _, admin_bearer = await _seed_player("admin", role="admin")
    owner_uuid, _, _ = await _seed_player("owner")
    p1, p1_aid, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid)
    # 关闭 client_mods
    await client.patch(
        "/v1/construction/settings",
        json={"allow_client_mods": False},
        headers={"Authorization": admin_bearer},
    )
    # 用 JWT[mod_id] 通道上报（active_uuid 被强制覆盖为 p1）
    puuid = p1
    aid = p1_aid
    mod_token = create_access_token(aid, "user", active_uuid=puuid, extra_claims={"mod_id": "test-mod"})
    r = await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
        ]},
        headers={"Authorization": f"Bearer {mod_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["skipped"] == 1
    assert body["outcomes"][0]["reason"] == "客户端模组上报已被服主关闭"

    events = await _events_for_account(p1_aid)
    assert len(events) == 1
    # sheet_id 列照落 resolved id（因为归因成功，只是 client_mod_closed）
    assert events[0].sheet_id == sid
    assert events[0].action == "skipped"
    assert events[0].reason == "客户端模组上报已被服主关闭"


# ===========================================================================
# 8. 不绑 web 账号的 player 走 /me/report-events 路径不命中（JWT sub 不存在）
#    已由 test_me_report_events_unbound_403 覆盖；此处补正向：me 端点只看本人 account
# ===========================================================================

async def test_submit_report_repo_direct_writes_events():
    """直接调 repo 验证 best-effort 写入：bound 玩家落事件，未绑不落。"""
    from app.api.deps import ReporterIdentity
    from app.schemas.construction import PlacementEntry, PlacementReport

    owner_uuid = uuid.uuid4()
    async with async_session_factory() as s:
        owner_account = WebAccount(role="user")
        s.add(owner_account)
        await s.flush()
        s.add(Player(uuid=owner_uuid, current_name="owner", role="user",
                     web_account_id=owner_account.id))
        # 未绑玩家
        unbound_uuid = uuid.uuid4()
        s.add(Player(uuid=unbound_uuid, current_name="unbound"))
        # bound 玩家
        bound_uuid = uuid.uuid4()
        bound_account = WebAccount(role="user")
        s.add(bound_account)
        await s.flush()
        bound_aid = bound_account.id
        s.add(Player(uuid=bound_uuid, current_name="bound", role="user",
                     web_account_id=bound_aid))
        # sheet + 行
        sheet = Sheet(owner_uuid=owner_uuid, title="repo", status="constructing")
        s.add(sheet)
        await s.flush()
        sid = sheet.id
        s.add(SheetRow(sheet_id=sid, item_name="stone",
                       registry_id="minecraft:stone", need_qty=10))
        await s.commit()

    reporter = ReporterIdentity(channel="service_token", source_type="mcdr", source_id="official")
    async with async_session_factory() as s:
        await construction_repo.submit_report(
            s,
            reporter=reporter,
            body=PlacementReport(sheet_id=sid, placements=[
                # bound 玩家 → accepted
                PlacementEntry(player_uuid=bound_uuid, registry_id="minecraft:stone",
                               placed_qty=2, broken_qty=0),
                # 未绑玩家 → skipped「玩家未绑 Web 账号」（不落事件）
                PlacementEntry(player_uuid=unbound_uuid, registry_id="minecraft:stone",
                               placed_qty=1, broken_qty=0),
                # 不存在的玩家 → skipped「玩家不存在」（不落事件）
                PlacementEntry(player_uuid=uuid.uuid4(), registry_id="minecraft:stone",
                               placed_qty=1, broken_qty=0),
            ]),
        )
        await s.commit()

    events = await _events_for_account(bound_aid)
    # 仅 bound 玩家那一条落事件（accepted）
    assert len(events) == 1
    assert events[0].action == "accepted"
    assert events[0].net_delta == 2
