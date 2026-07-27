"""施工进度上报层集成测试（``/v1/construction``）。

覆盖矩阵（plan 验证 #3）：
- report 双通道（service-token 多玩家 / JWT[mod_id] 强制 active_uuid / H-2 不降级）
- 严格单源（默认 mcdr/official / 切换后非活跃源 skip / 无活跃源 skip）
- 归因三分支（显式 / 启发式恰 1 / 0 或 >1 全 skip）
- 切源端点（switch-server admin / switch-self server|local / source/me）
- admin RBAC 403 / archived/非施工 409 / 未绑账号 skip
- D8 aggregate_placement_totals 形状
"""
import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.jwt import create_access_token
from app.models.construction import PlacementSnapshot
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
    owner_uuid,
    status="constructing",
    title="测试项目",
    rows=("minecraft:stone", "minecraft:dirt"),
) -> int:
    """建 sheet + 默认材料行（stone/dirt 在清单内 → 方块清单校验可通过）。

    ``rows=None`` / ``rows=()`` → 空清单（测清单外 skip）；传 tuple 覆盖 registry。
    """
    async with async_session_factory() as s:
        sheet = Sheet(owner_uuid=owner_uuid, title=title, status=status)
        s.add(sheet)
        await s.flush()
        for rid in rows or ():
            s.add(
                SheetRow(
                    sheet_id=sheet.id,
                    item_name=rid,
                    registry_id=rid,
                    need_qty=10,
                )
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
        s.add(
            Player(uuid=puuid, current_name=name, role=role, web_account_id=aid)
        )
        await s.commit()
    bearer = f"Bearer {create_access_token(aid, role, active_uuid=puuid)}"
    mod_bearer = f"Bearer {create_access_token(aid, role, active_uuid=puuid, extra_claims={'mod_id': 'test-mod'})}"
    return puuid, aid, bearer, mod_bearer


def _svc(source_id=None):
    h = {"X-Service-Token": SVC}
    if source_id:
        h["X-Source-Id"] = source_id
    return h


# ===========================================================================
# report · service-token 通道 + 归因
# ===========================================================================

async def test_report_explicit_sheet_accepted(client):
    owner_uuid, _, _, _ = await _seed_player("owner")
    p1, _, _, _ = await _seed_player("p1")
    p2, _, _, _ = await _seed_player("p2")
    sid = await _seed_sheet(owner_uuid)

    r = await client.post(
        "/v1/construction/report",
        json={
            "sheet_id": sid,
            "placements": [
                {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 10, "broken_qty": 2},
                {"player_uuid": str(p2), "registry_id": "minecraft:dirt", "placed_qty": 5, "broken_qty": 0},
            ],
        },
        headers=_svc(),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["attribution_source"] == "explicit"
    assert body["sheet_id"] == sid
    assert body["totals"] == {"accepted": 2, "skipped": 0}
    # net_delta = placed - broken
    nets = {o["player_uuid"]: o["net_delta"] for o in body["outcomes"]}
    assert nets[str(p1)] == 8
    assert nets[str(p2)] == 5


async def test_report_heuristic_single(client):
    owner_uuid, _, _, _ = await _seed_player("owner")
    p1, _, _, _ = await _seed_player("p1")
    await _seed_sheet(owner_uuid)  # 恰 1 个 constructing

    r = await client.post(
        "/v1/construction/report",
        json={"sheet_id": None, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 3, "broken_qty": 1},
        ]},
        headers=_svc(),
    )
    assert r.status_code == 200
    assert r.json()["attribution_source"] == "heuristic"


async def test_report_heuristic_zero_all_skip(client):
    p1, _, _, _ = await _seed_player("p1")
    r = await client.post(
        "/v1/construction/report",
        json={"placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
        ]},
        headers=_svc(),
    )
    body = r.json()
    assert body["attribution_source"] == "none"
    assert body["totals"]["skipped"] == 1
    assert body["outcomes"][0]["reason"] == "当前无施工中项目"


async def test_report_heuristic_multiple_all_skip(client):
    owner_uuid, _, _, _ = await _seed_player("owner")
    p1, _, _, _ = await _seed_player("p1")
    await _seed_sheet(owner_uuid, title="A")
    await _seed_sheet(owner_uuid, title="B")
    r = await client.post(
        "/v1/construction/report",
        json={"placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
        ]},
        headers=_svc(),
    )
    assert r.json()["outcomes"][0]["reason"] == "多个施工项目并发，须显式指定 sheet_id"


async def test_report_explicit_not_constructing_409(client):
    owner_uuid, _, _, _ = await _seed_player("owner")
    p1, _, _, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid, status="collecting")
    r = await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
        ]},
        headers=_svc(),
    )
    assert r.status_code == 409


async def test_report_explicit_not_found_404(client):
    p1, _, _, _ = await _seed_player("p1")
    r = await client.post(
        "/v1/construction/report",
        json={"sheet_id": 99999, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
        ]},
        headers=_svc(),
    )
    assert r.status_code == 404


async def test_report_unbound_player_skipped(client):
    owner_uuid, _, _, _ = await _seed_player("owner")
    # 未绑账号的玩家（web_account_id=None）
    puuid = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=puuid, current_name="nobind"))
        await s.commit()
    sid = await _seed_sheet(owner_uuid)
    r = await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(puuid), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
        ]},
        headers=_svc(),
    )
    body = r.json()
    assert body["totals"]["skipped"] == 1
    assert body["outcomes"][0]["reason"] == "玩家未绑 Web 账号"


# ===========================================================================
# report · 严格单源（D2）
# ===========================================================================

async def test_report_default_source_official_accepted(client):
    """无 player_sources 记录 + official_tracker_enabled → 默认 mcdr/official 活跃。"""
    owner_uuid, _, _, _ = await _seed_player("owner")
    p1, _, _, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid)
    r = await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
        ]},
        headers=_svc(),
    )
    assert r.json()["totals"]["accepted"] == 1


async def test_report_official_disabled_skips(client):
    admin_uuid, _, admin_bearer, _ = await _seed_player("admin", role="admin")
    owner_uuid, _, _, _ = await _seed_player("owner")
    p1, _, _, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid)
    # 关闭官方追踪器
    r = await client.patch(
        "/v1/construction/settings",
        json={"official_tracker_enabled": False},
        headers={"Authorization": admin_bearer},
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
        ]},
        headers=_svc(),
    )
    body = r.json()
    assert body["totals"]["skipped"] == 1
    assert body["outcomes"][0]["reason"] == "玩家当前无活跃上报源"


async def test_report_player_switched_to_local_mcdr_skips(client):
    owner_uuid, _, owner_bearer, _ = await _seed_player("owner")
    p1, _, p1_bearer, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid)
    # p1 切到 local mod
    r = await client.post(
        "/v1/construction/source/switch-self",
        json={"mode": "local", "source_id": "their-mod"},
        headers={"Authorization": p1_bearer},
    )
    assert r.status_code == 200
    # mcdr 上报 p1 → skip
    r = await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
        ]},
        headers=_svc(),
    )
    assert r.json()["outcomes"][0]["reason"] == "玩家当前由其他源上报"


async def test_report_client_mod_accepted_when_active(client):
    owner_uuid, _, _, _ = await _seed_player("owner")
    p1, _, _, p1_mod = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid)
    # p1 切到 local test-mod
    await client.post(
        "/v1/construction/source/switch-self",
        json={"mode": "local", "source_id": "test-mod"},
        headers={"Authorization": p1_mod},
    )
    # JWT[mod_id=test-mod] 上报 → 接受
    r = await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            # payload player_uuid 被 server 强制覆盖为 active_uuid
            {"player_uuid": str(uuid.uuid4()), "registry_id": "minecraft:stone", "placed_qty": 4, "broken_qty": 1},
        ]},
        headers={"Authorization": p1_mod},
    )
    body = r.json()
    assert body["totals"]["accepted"] == 1
    # outcome player_uuid = active_uuid（被覆盖）
    assert body["outcomes"][0]["player_uuid"] == str(p1)
    assert body["outcomes"][0]["net_delta"] == 3


# ===========================================================================
# report · JWT 通道 + 鉴权
# ===========================================================================

async def test_report_jwt_without_mod_id_401(client):
    owner_uuid, _, owner_bearer, _ = await _seed_player("owner")
    sid = await _seed_sheet(owner_uuid)
    r = await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(owner_uuid), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
        ]},
        headers={"Authorization": owner_bearer},  # 普通 JWT，无 mod_id
    )
    assert r.status_code == 401


async def test_report_no_auth_401(client):
    r = await client.post(
        "/v1/construction/report",
        json={"placements": [
            {"player_uuid": str(uuid.uuid4()), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
        ]},
    )
    assert r.status_code == 401


async def test_report_unknown_player_skipped(client):
    owner_uuid, _, _, _ = await _seed_player("owner")
    sid = await _seed_sheet(owner_uuid)
    r = await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(uuid.uuid4()), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
        ]},
        headers=_svc(),
    )
    body = r.json()
    assert body["totals"]["skipped"] == 1
    assert body["outcomes"][0]["reason"] == "玩家不存在"


async def test_report_server_mod_not_whitelisted_403(client):
    owner_uuid, _, _, _ = await _seed_player("owner")
    p1, _, _, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid)
    r = await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
        ]},
        headers=_svc(source_id="ghost-mod"),
    )
    assert r.status_code == 403


async def test_report_server_mod_whitelisted_accepted(client):
    _, admin_uuid, admin_bearer, _ = await _seed_player("admin", role="admin")
    owner_uuid, _, _, _ = await _seed_player("owner")
    p1, _, _, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid)
    # 加白名单
    await client.post(
        "/v1/construction/mod-sources",
        json={"name": "srv-mod"},
        headers={"Authorization": admin_bearer},
    )
    # admin 切 p1 到 server_mod/srv-mod
    await client.post(
        "/v1/construction/source/switch-server",
        json={"player_uuid": str(p1), "source_type": "server_mod", "source_id": "srv-mod"},
        headers={"Authorization": admin_bearer},
    )
    # server_mod 上报 → 接受
    r = await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 2, "broken_qty": 0},
        ]},
        headers=_svc(source_id="srv-mod"),
    )
    assert r.json()["totals"]["accepted"] == 1


# ===========================================================================
# 切源（D9）
# ===========================================================================

async def test_switch_server_admin_mcdr(client):
    _, _, admin_bearer, _ = await _seed_player("admin", role="admin")
    p1, _, p1_bearer, _ = await _seed_player("p1")
    # 先切到 local，再切回 mcdr
    await client.post(
        "/v1/construction/source/switch-self",
        json={"mode": "local", "source_id": "x"},
        headers={"Authorization": p1_bearer},
    )
    r = await client.post(
        "/v1/construction/source/switch-server",
        json={"player_uuid": str(p1), "source_type": "mcdr"},
        headers={"Authorization": admin_bearer},
    )
    assert r.status_code == 200
    assert r.json()["source_type"] == "mcdr"
    assert r.json()["source_id"] == "official"


async def test_switch_server_server_mod_not_whitelisted_422(client):
    _, _, admin_bearer, _ = await _seed_player("admin", role="admin")
    p1, _, _, _ = await _seed_player("p1")
    r = await client.post(
        "/v1/construction/source/switch-server",
        json={"player_uuid": str(p1), "source_type": "server_mod", "source_id": "ghost"},
        headers={"Authorization": admin_bearer},
    )
    assert r.status_code == 422


async def test_switch_server_non_admin_403(client):
    p1, _, p1_bearer, _ = await _seed_player("p1")
    p2, _, _, _ = await _seed_player("p2")
    r = await client.post(
        "/v1/construction/source/switch-server",
        json={"player_uuid": str(p2), "source_type": "mcdr"},
        headers={"Authorization": p1_bearer},
    )
    assert r.status_code == 403


async def test_switch_self_local_no_source_id_422(client):
    _, _, p1_bearer, _ = await _seed_player("p1")
    r = await client.post(
        "/v1/construction/source/switch-self",
        json={"mode": "local"},
        headers={"Authorization": p1_bearer},
    )
    assert r.status_code == 422


async def test_switch_self_local_client_mods_disabled_403(client):
    _, admin_uuid, admin_bearer, _ = await _seed_player("admin", role="admin")
    _, _, p1_bearer, _ = await _seed_player("p1")
    await client.patch(
        "/v1/construction/settings",
        json={"allow_client_mods": False},
        headers={"Authorization": admin_bearer},
    )
    r = await client.post(
        "/v1/construction/source/switch-self",
        json={"mode": "local", "source_id": "m"},
        headers={"Authorization": p1_bearer},
    )
    assert r.status_code == 403


async def test_source_me_returns_history(client):
    _, _, p1_bearer, _ = await _seed_player("p1")
    await client.post(
        "/v1/construction/source/switch-self",
        json={"mode": "local", "source_id": "m1"},
        headers={"Authorization": p1_bearer},
    )
    await client.post(
        "/v1/construction/source/switch-self",
        json={"mode": "server"},
        headers={"Authorization": p1_bearer},
    )
    r = await client.get("/v1/construction/source/me", headers={"Authorization": p1_bearer})
    body = r.json()
    # 切回 server 后，活跃源 = mcdr/official（显式记录，is_default=False）
    assert body["active"]["source_type"] == "mcdr"
    assert body["active"]["is_default"] is False
    # 历史按时间倒序：最近一次 to=mcdr
    assert body["history"][0]["to_type"] == "mcdr"
    assert body["history"][1]["to_type"] == "client_mod"


# ===========================================================================
# admin 设置 + 白名单 + RBAC
# ===========================================================================

async def test_settings_default(client):
    _, _, admin_bearer, _ = await _seed_player("admin", role="admin")
    r = await client.get("/v1/construction/settings", headers={"Authorization": admin_bearer})
    body = r.json()
    assert body["allow_client_mods"] is True
    assert body["official_tracker_enabled"] is True
    assert body["report_interval_seconds"] == 30
    assert body["anti_cheat_threshold"] is None


async def test_settings_patch_and_persist(client):
    _, _, admin_bearer, _ = await _seed_player("admin", role="admin")
    r = await client.patch(
        "/v1/construction/settings",
        json={"report_interval_seconds": 60, "anti_cheat_threshold": 1000},
        headers={"Authorization": admin_bearer},
    )
    assert r.json()["report_interval_seconds"] == 60
    assert r.json()["anti_cheat_threshold"] == 1000
    # 二次读验证落库
    r = await client.get("/v1/construction/settings", headers={"Authorization": admin_bearer})
    assert r.json()["report_interval_seconds"] == 60


async def test_settings_non_admin_403(client):
    _, _, p1_bearer, _ = await _seed_player("p1")
    r = await client.get("/v1/construction/settings", headers={"Authorization": p1_bearer})
    assert r.status_code == 403


async def test_mod_sources_crud(client):
    _, _, admin_bearer, _ = await _seed_player("admin", role="admin")
    # POST
    r = await client.post(
        "/v1/construction/mod-sources",
        json={"name": "mod-a", "notes": "测试"},
        headers={"Authorization": admin_bearer},
    )
    assert r.status_code == 201
    assert r.json()["name"] == "mod-a"
    # GET
    r = await client.get("/v1/construction/mod-sources", headers={"Authorization": admin_bearer})
    assert any(m["name"] == "mod-a" for m in r.json())
    # DELETE
    r = await client.delete("/v1/construction/mod-sources/mod-a", headers={"Authorization": admin_bearer})
    assert r.status_code == 204
    # 再 GET 确认空
    r = await client.get("/v1/construction/mod-sources", headers={"Authorization": admin_bearer})
    assert all(m["name"] != "mod-a" for m in r.json())


async def test_mod_sources_non_admin_403(client):
    _, _, p1_bearer, _ = await _seed_player("p1")
    r = await client.post(
        "/v1/construction/mod-sources",
        json={"name": "x"},
        headers={"Authorization": p1_bearer},
    )
    assert r.status_code == 403


# ===========================================================================
# active-sheets + progress + D8 aggregate
# ===========================================================================

async def test_active_sheets(client):
    _, _, p1_bearer, _ = await _seed_player("p1")
    owner_uuid, _, _, _ = await _seed_player("owner")
    await _seed_sheet(owner_uuid, title="A")
    await _seed_sheet(owner_uuid, status="collecting", title="B")
    r = await client.get("/v1/construction/active-sheets", headers={"Authorization": p1_bearer})
    body = r.json()
    assert body["heuristic_eligible"] is True
    titles = [s["title"] for s in body["sheets"]]
    assert titles == ["A"]  # 仅 constructing


async def test_progress_after_report(client):
    owner_uuid, _, owner_bearer, _ = await _seed_player("owner")
    p1, _, _, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid)
    await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 10, "broken_qty": 3},
        ]},
        headers=_svc(),
    )
    r = await client.get(f"/v1/construction/{sid}/progress", headers={"Authorization": owner_bearer})
    body = r.json()
    assert body["sheet_id"] == sid
    assert len(body["account_totals"]) == 1
    assert body["account_totals"][0]["net_qty"] == 7
    assert body["breakdown"][0]["registry_id"] == "minecraft:stone"


async def test_progress_not_found_404(client):
    _, _, p1_bearer, _ = await _seed_player("p1")
    r = await client.get("/v1/construction/99999/progress", headers={"Authorization": p1_bearer})
    assert r.status_code == 404


async def test_aggregate_placement_totals_shape():
    """D8：scoring 层消费契约（[(account_id, display_name, net_qty)]）。"""
    owner_uuid, _, _, _ = await _seed_player("owner")
    p1, p1_aid, _, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid)
    async with async_session_factory() as s:
        from app.api.deps import ReporterIdentity
        from app.schemas.construction import PlacementEntry, PlacementReport
        reporter = ReporterIdentity(channel="service_token", source_type="mcdr", source_id="official")
        await construction_repo.submit_report(
            s,
            reporter=reporter,
            body=PlacementReport(sheet_id=sid, placements=[
                PlacementEntry(player_uuid=p1, registry_id="minecraft:stone", placed_qty=5, broken_qty=2),
            ]),
        )
        await s.commit()
        totals = await construction_repo.aggregate_placement_totals(s, sid)
    assert len(totals) == 1
    assert totals[0].account_id == p1_aid
    assert totals[0].net_qty == 3
    assert isinstance(totals[0].display_name, str)


# ===========================================================================
# 迭代 2：方块清单校验 + 时序快照 + 休眠源 + progress 新字段
# ===========================================================================

async def test_report_block_not_in_manifest_skipped(client):
    """需求 2：上报方块不在 sheet 收集清单 → skip。"""
    owner_uuid, _, _, _ = await _seed_player("owner")
    p1, _, _, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid, rows=None)  # 空清单
    r = await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
        ]},
        headers=_svc(),
    )
    body = r.json()
    assert body["totals"]["skipped"] == 1
    assert body["outcomes"][0]["reason"] == "方块不在项目材料清单内"


async def test_report_block_in_manifest_accepted(client):
    """需求 2：方块在清单内 → accepted（默认 stone/dirt）。"""
    owner_uuid, _, _, _ = await _seed_player("owner")
    p1, _, _, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid)  # 默认 stone/dirt
    r = await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 1, "broken_qty": 0},
        ]},
        headers=_svc(),
    )
    assert r.json()["totals"]["accepted"] == 1


async def test_report_writes_snapshot(client):
    """需求 4：report 后写时序快照（total_net = sum net）。"""
    owner_uuid, _, _, _ = await _seed_player("owner")
    p1, p1_aid, _, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid)
    await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 10, "broken_qty": 3},
        ]},
        headers=_svc(),
    )
    async with async_session_factory() as s:
        rows = (
            await s.execute(
                select(PlacementSnapshot).where(PlacementSnapshot.sheet_id == sid)
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].account_id == p1_aid
    assert rows[0].total_net == 7  # 10 - 3


async def test_progress_returns_material_and_timeline(client):
    """需求 4：progress 端点返回 material_completion + timeline。"""
    owner_uuid, _, owner_bearer, _ = await _seed_player("owner")
    p1, _, _, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid)
    await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 5, "broken_qty": 0},
        ]},
        headers=_svc(),
    )
    r = await client.get(f"/v1/construction/{sid}/progress", headers={"Authorization": owner_bearer})
    body = r.json()
    stone = next(m for m in body["material_completion"] if m["registry_id"] == "minecraft:stone")
    assert stone["need_qty"] == 10
    assert stone["net_qty"] == 5
    assert stone["completion_pct"] == 50.0
    assert len(body["timeline"]) == 1
    assert body["timeline"][0]["total_net"] == 5


async def test_source_me_returns_dormant_sources(client):
    """需求 1：source/me 返回休眠源（曾用过的 client_mod，按 source_id 去重）。"""
    _, _, p1_bearer, _ = await _seed_player("p1")
    # m1 → m2 → server：m1/m2 变休眠
    await client.post("/v1/construction/source/switch-self", json={"mode": "local", "source_id": "m1"}, headers={"Authorization": p1_bearer})
    await client.post("/v1/construction/source/switch-self", json={"mode": "local", "source_id": "m2"}, headers={"Authorization": p1_bearer})
    await client.post("/v1/construction/source/switch-self", json={"mode": "server"}, headers={"Authorization": p1_bearer})
    r = await client.get("/v1/construction/source/me", headers={"Authorization": p1_bearer})
    body = r.json()
    dormant_ids = {d["source_id"] for d in body["dormant_sources"]}
    assert dormant_ids == {"m1", "m2"}


async def test_progress_multi_account(client):
    """多玩家上报同一 sheet → account_totals 多账号（饼图多扇区数据源）。"""
    owner_uuid, _, owner_bearer, _ = await _seed_player("owner")
    p1, _, _, _ = await _seed_player("p1")
    p2, _, _, _ = await _seed_player("p2")
    sid = await _seed_sheet(owner_uuid)
    await client.post(
        "/v1/construction/report",
        json={"sheet_id": sid, "placements": [
            {"player_uuid": str(p1), "registry_id": "minecraft:stone", "placed_qty": 10, "broken_qty": 0},
            {"player_uuid": str(p2), "registry_id": "minecraft:dirt", "placed_qty": 5, "broken_qty": 0},
        ]},
        headers=_svc(),
    )
    r = await client.get(f"/v1/construction/{sid}/progress", headers={"Authorization": owner_bearer})
    body = r.json()
    assert len(body["account_totals"]) == 2
    assert {t["net_qty"] for t in body["account_totals"]} == {10, 5}
    # timeline 两账号各有 1 条快照
    assert len(body["timeline"]) == 2
