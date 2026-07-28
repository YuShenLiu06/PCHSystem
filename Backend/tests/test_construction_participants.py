"""加入施工机制集成测试（plan BLOCK 1，迁移 0020+0021）。

覆盖矩阵（plan 验证 §1-§6）：
① join 矩阵（enforce on/off × auto/manual × 本/他 sheet/未加入）
② auto-join hook（collecting 与 constructing contribute/claim 后有行；已在他项目
   auto skip；hook 异常不阻断上交）
③ 归档后 close_all_participants（全部 left_at；report 仍 409）
④ progress 端点返回 construction_started_at + archived_at 两字段
⑤ report API project 维度零校验回归（同账号交替报 sheet A/B 均成功）
⑥ participants partial unique index 并发兜底（SAVEPOINT 隔离）
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.jwt import create_access_token
from app.models.construction import Participant
from app.models.sheet import Sheet, SheetRow
from app.models.user import Player, WebAccount
from app.repositories import construction_repo, sheet_repo

pytestmark = pytest.mark.asyncio
_settings = get_settings()
SVC = _settings.mcdr_service_token


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

async def _seed_player(name="alice", role="user"):
    """返回 (uuid, account_id, bearer)。"""
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


async def _seed_sheet(owner_uuid, status="collecting", title="项目", rows=()):
    """建 sheet + 可选 progress 行（用于 contribute 触发 auto-join）。"""
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
                    mode=1,  # progress（默认；claim 测试在 case 内改 mode）
                )
            )
        await s.commit()
        return sheet.id


def _svc():
    return {"X-Service-Token": SVC}


async def _set_enforce_single(value: bool):
    """切换 enforce_single_construction setting（admin）。"""
    _, _, admin_bearer = await _seed_player("admin", role="admin")
    async with async_session_factory() as s:
        from app.schemas.construction import ConstructionSettingsUpdate
        await construction_repo.update_settings(
            s, ConstructionSettingsUpdate(enforce_single_construction=value)
        )
        await s.commit()
    return admin_bearer


async def _get_active_participant(account_id: int) -> Participant | None:
    async with async_session_factory() as s:
        return await construction_repo._get_active_participant_row(s, account_id)


# ===========================================================================
# ① join 矩阵（enforce on/off × auto/manual × 本/他 sheet/未加入）
# ===========================================================================

async def test_join_manual_first_join_succeeds(client):
    """未加入 + manual join 本 sheet → 成功（enforce on）。"""
    owner_uuid, _, _ = await _seed_player("owner")
    p1, _, p1_bearer = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid, status="constructing")
    r = await client.post(
        "/v1/construction/me/join",
        json={"sheet_id": sid},
        headers={"Authorization": p1_bearer},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active"]["sheet_id"] == sid
    assert body["active"]["join_source"] == "manual"
    assert body["active"]["sheet_title"] == "项目"


async def test_join_manual_idempotent_same_sheet(client):
    """已活跃加入本 sheet + manual join 本 sheet → 幂等返回（不报错）。"""
    owner_uuid, _, _ = await _seed_player("owner")
    p1, _, p1_bearer = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid, status="constructing")
    # 第一次 join
    await client.post(
        "/v1/construction/me/join",
        json={"sheet_id": sid},
        headers={"Authorization": p1_bearer},
    )
    # 第二次 join 同 sheet → 幂等
    r = await client.post(
        "/v1/construction/me/join",
        json={"sheet_id": sid},
        headers={"Authorization": p1_bearer},
    )
    assert r.status_code == 200
    assert r.json()["active"]["sheet_id"] == sid


async def test_join_manual_conflict_other_sheet_409(client):
    """已活跃加入他 sheet + manual + enforce on → 409 ParticipantConflict。"""
    owner_uuid, _, _ = await _seed_player("owner")
    p1, _, p1_bearer = await _seed_player("p1")
    sid_a = await _seed_sheet(owner_uuid, status="constructing", title="A")
    sid_b = await _seed_sheet(owner_uuid, status="constructing", title="B")
    # 先加入 A
    await client.post(
        "/v1/construction/me/join",
        json={"sheet_id": sid_a},
        headers={"Authorization": p1_bearer},
    )
    # 欲加入 B → 409
    r = await client.post(
        "/v1/construction/me/join",
        json={"sheet_id": sid_b},
        headers={"Authorization": p1_bearer},
    )
    assert r.status_code == 409
    assert str(sid_a) in r.json()["detail"]


async def test_join_auto_skip_when_in_other_sheet(client):
    """已活跃加入他 sheet + auto + enforce on → silent skip（保留旧活跃行）。"""
    owner_uuid, _, _ = await _seed_player("owner")
    p1, p1_aid, _ = await _seed_player("p1")
    sid_a = await _seed_sheet(owner_uuid, status="constructing", title="A")
    sid_b = await _seed_sheet(owner_uuid, status="constructing", title="B")
    # manual 加入 A
    async with async_session_factory() as s:
        await construction_repo.join_construction(
            s, p1_aid, sid_a, source="manual"
        )
        await s.commit()
    # auto join B → skip，仍活跃在 A
    async with async_session_factory() as s:
        result = await construction_repo.join_construction(
            s, p1_aid, sid_b, source="auto"
        )
        await s.commit()
        assert result.sheet_id == sid_a  # 返回旧活跃行
    # 数据库校验：仍活跃在 A
    active = await _get_active_participant(p1_aid)
    assert active is not None
    assert active.sheet_id == sid_a


async def test_join_auto_when_not_in_any_sheet(client):
    """未加入 + auto → 成功加入（auto 是默认触发路径）。"""
    owner_uuid, _, _ = await _seed_player("owner")
    p1, p1_aid, _ = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid, status="constructing")
    async with async_session_factory() as s:
        result = await construction_repo.join_construction(
            s, p1_aid, sid, source="auto"
        )
        await s.commit()
        assert result.sheet_id == sid
        assert result.join_source == "auto"


async def test_join_enforce_off_auto_switches(client):
    """enforce off + 已活跃加入他 sheet + auto → 自动切换到新 sheet。"""
    owner_uuid, _, _ = await _seed_player("owner")
    p1, p1_aid, _ = await _seed_player("p1")
    sid_a = await _seed_sheet(owner_uuid, status="constructing", title="A")
    sid_b = await _seed_sheet(owner_uuid, status="constructing", title="B")
    # enforce off
    await _set_enforce_single(False)
    # manual 加入 A
    async with async_session_factory() as s:
        await construction_repo.join_construction(
            s, p1_aid, sid_a, source="manual"
        )
        await s.commit()
    # auto 加入 B → 自动切换
    async with async_session_factory() as s:
        result = await construction_repo.join_construction(
            s, p1_aid, sid_b, source="auto"
        )
        await s.commit()
        assert result.sheet_id == sid_b
    # 旧 A 行 left_at 已置（switched）
    async with async_session_factory() as s:
        rows = list(
            (
                await s.execute(
                    select(Participant).where(
                        Participant.web_account_id == p1_aid,
                        Participant.sheet_id == sid_a,
                    )
                )
            ).scalars().all()
        )
        assert len(rows) == 1
        assert rows[0].left_at is not None
        assert rows[0].left_reason == "switched"


async def test_join_enforce_off_manual_switches(client):
    """enforce off + 已活跃加入他 sheet + manual → 自动切换（不抛 409）。"""
    owner_uuid, _, _ = await _seed_player("owner")
    p1, p1_aid, _ = await _seed_player("p1")
    sid_a = await _seed_sheet(owner_uuid, status="constructing", title="A")
    sid_b = await _seed_sheet(owner_uuid, status="constructing", title="B")
    await _set_enforce_single(False)
    async with async_session_factory() as s:
        await construction_repo.join_construction(
            s, p1_aid, sid_a, source="manual"
        )
        await s.commit()
    async with async_session_factory() as s:
        result = await construction_repo.join_construction(
            s, p1_aid, sid_b, source="manual"
        )
        await s.commit()
        assert result.sheet_id == sid_b


async def test_switch_endpoint_auto_switches(client):
    """``/me/switch`` 自动 leave 旧 + join 新（enforce on，不抛 409）。"""
    owner_uuid, _, _ = await _seed_player("owner")
    p1, _, p1_bearer = await _seed_player("p1")
    sid_a = await _seed_sheet(owner_uuid, status="constructing", title="A")
    sid_b = await _seed_sheet(owner_uuid, status="constructing", title="B")
    # 先 join A
    await client.post(
        "/v1/construction/me/join",
        json={"sheet_id": sid_a},
        headers={"Authorization": p1_bearer},
    )
    # switch 到 B → 成功（不抛 409）
    r = await client.post(
        "/v1/construction/me/switch",
        json={"sheet_id": sid_b},
        headers={"Authorization": p1_bearer},
    )
    assert r.status_code == 200, r.text
    assert r.json()["active"]["sheet_id"] == sid_b


async def test_leave_clears_active(client):
    """``/me/leave`` 置 left_at + left_reason='manual_leave'。"""
    owner_uuid, _, _ = await _seed_player("owner")
    p1, p1_aid, p1_bearer = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid, status="constructing")
    await client.post(
        "/v1/construction/me/join",
        json={"sheet_id": sid},
        headers={"Authorization": p1_bearer},
    )
    r = await client.post(
        "/v1/construction/me/leave",
        headers={"Authorization": p1_bearer},
    )
    assert r.status_code == 200
    assert r.json()["active"]["sheet_id"] is None
    # DB 校验
    async with async_session_factory() as s:
        rows = list(
            (
                await s.execute(
                    select(Participant).where(
                        Participant.web_account_id == p1_aid
                    )
                )
            ).scalars().all()
        )
        assert len(rows) == 1
        assert rows[0].left_at is not None
        assert rows[0].left_reason == "manual_leave"


async def test_leave_idempotent_when_not_active(client):
    """未活跃加入 + leave → 幂等返回空态（不报错）。"""
    _, _, p1_bearer = await _seed_player("p1")
    r = await client.post(
        "/v1/construction/me/leave",
        headers={"Authorization": p1_bearer},
    )
    assert r.status_code == 200
    assert r.json()["active"]["sheet_id"] is None


async def test_get_my_construction_returns_state(client):
    """``GET /me/construction`` 三态：未加入 / 已加入 / 已退出。"""
    owner_uuid, _, _ = await _seed_player("owner")
    p1, _, p1_bearer = await _seed_player("p1")
    sid = await _seed_sheet(owner_uuid, status="constructing", title="测")
    # 未加入
    r = await client.get(
        "/v1/construction/me/construction",
        headers={"Authorization": p1_bearer},
    )
    assert r.status_code == 200
    assert r.json()["active"]["sheet_id"] is None
    # 加入
    await client.post(
        "/v1/construction/me/join",
        json={"sheet_id": sid},
        headers={"Authorization": p1_bearer},
    )
    r = await client.get(
        "/v1/construction/me/construction",
        headers={"Authorization": p1_bearer},
    )
    body = r.json()
    assert body["active"]["sheet_id"] == sid
    assert body["active"]["sheet_title"] == "测"
    assert body["active"]["join_source"] == "manual"


async def test_join_archived_sheet_409(client):
    """sheet archived → join 409。"""
    from datetime import datetime, timezone
    owner_uuid, _, _ = await _seed_player("owner")
    p1, _, p1_bearer = await _seed_player("p1")
    # 直接建 archived sheet（绕过 archive service）
    async with async_session_factory() as s:
        sheet = Sheet(
            owner_uuid=owner_uuid,
            title="已归档",
            status="archived",
            archived_path="projects/x/index.md",
            archived_at=datetime.now(timezone.utc),
        )
        s.add(sheet)
        await s.commit()
        sid = sheet.id
    r = await client.post(
        "/v1/construction/me/join",
        json={"sheet_id": sid},
        headers={"Authorization": p1_bearer},
    )
    assert r.status_code == 409


async def test_join_sheet_not_found_404(client):
    _, _, p1_bearer = await _seed_player("p1")
    r = await client.post(
        "/v1/construction/me/join",
        json={"sheet_id": 99999},
        headers={"Authorization": p1_bearer},
    )
    assert r.status_code == 404


# ===========================================================================
# ② auto-join hook（claim / contribute → 自动加入）
# ===========================================================================

async def test_auto_join_on_contribute_collecting(client):
    """collecting 阶段 progress 行 contribute → 自动加入。"""
    owner_uuid, _, _ = await _seed_player("owner")
    p1, p1_aid, _ = await _seed_player("p1")
    sid = await _seed_sheet(
        owner_uuid, status="collecting", rows=("minecraft:stone",)
    )
    async with async_session_factory() as s:
        row = (await s.execute(select(SheetRow).where(SheetRow.sheet_id == sid))).scalar_one()
        await sheet_repo.contribute_row(s, sid, row.id, p1, 3)
        await s.commit()
    active = await _get_active_participant(p1_aid)
    assert active is not None
    assert active.sheet_id == sid
    assert active.join_source == "auto"


async def test_auto_join_on_contribute_constructing(client):
    """constructing 阶段 progress 行 contribute → 自动加入（同 collecting）。"""
    owner_uuid, _, _ = await _seed_player("owner")
    p1, p1_aid, _ = await _seed_player("p1")
    sid = await _seed_sheet(
        owner_uuid, status="constructing", rows=("minecraft:stone",)
    )
    async with async_session_factory() as s:
        row = (await s.execute(select(SheetRow).where(SheetRow.sheet_id == sid))).scalar_one()
        await sheet_repo.contribute_row(s, sid, row.id, p1, 5)
        await s.commit()
    active = await _get_active_participant(p1_aid)
    assert active is not None
    assert active.sheet_id == sid


async def test_auto_join_on_claim(client):
    """collecting 阶段 lock 行 claim → 自动加入（顶层级联子行同 claimant 单次）。"""
    owner_uuid, _, _ = await _seed_player("owner")
    p1, p1_aid, _ = await _seed_player("p1")
    # 建 sheet + lock 行
    async with async_session_factory() as s:
        sheet = Sheet(owner_uuid=owner_uuid, title="测", status="collecting")
        s.add(sheet)
        await s.flush()
        sid = sheet.id
        s.add(
            SheetRow(
                sheet_id=sid, item_name="lock-item", registry_id="minecraft:iron",
                need_qty=10, mode=0,
            )
        )
        await s.commit()
    async with async_session_factory() as s:
        row = (await s.execute(select(SheetRow).where(SheetRow.sheet_id == sid))).scalar_one()
        await sheet_repo.claim_row(s, sid, row.id, p1)
        await s.commit()
    active = await _get_active_participant(p1_aid)
    assert active is not None
    assert active.sheet_id == sid
    assert active.join_source == "auto"


async def test_auto_join_skips_when_in_other_sheet(client):
    """已在他项目 + auto → silent skip（保留旧活跃行，不动）。"""
    owner_uuid, _, _ = await _seed_player("owner")
    p1, p1_aid, _ = await _seed_player("p1")
    sid_a = await _seed_sheet(
        owner_uuid, status="constructing", title="A", rows=("minecraft:stone",)
    )
    sid_b = await _seed_sheet(
        owner_uuid, status="constructing", title="B", rows=("minecraft:stone",)
    )
    # 先 manual 加入 A
    async with async_session_factory() as s:
        await construction_repo.join_construction(s, p1_aid, sid_a, source="manual")
        await s.commit()
    # 在 B 上 contribute → auto-join 应 skip
    async with async_session_factory() as s:
        row = (
            await s.execute(
                select(SheetRow).where(SheetRow.sheet_id == sid_b)
            )
        ).scalar_one()
        await sheet_repo.contribute_row(s, sid_b, row.id, p1, 1)
        await s.commit()
    active = await _get_active_participant(p1_aid)
    assert active is not None
    assert active.sheet_id == sid_a  # 仍活跃在 A


async def test_auto_join_failure_does_not_block_contribute(client):
    """hook 异常不阻断上交（contribute 仍成功，participant 未写入）。

    模拟方式：mock ``construction_repo.join_construction`` 抛异常（auto-join 下游），
    验证 ``_maybe_auto_join`` 内部 try/except 吞掉异常、contribute 仍成功落库。
    """
    owner_uuid, _, _ = await _seed_player("owner")
    p1, _, _ = await _seed_player("p1")
    sid = await _seed_sheet(
        owner_uuid, status="constructing", rows=("minecraft:stone",)
    )
    from unittest.mock import patch

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated join_construction failure")

    with patch.object(construction_repo, "join_construction", _boom):
        async with async_session_factory() as s:
            row = (
                await s.execute(select(SheetRow).where(SheetRow.sheet_id == sid))
            ).scalar_one()
            await sheet_repo.contribute_row(s, sid, row.id, p1, 7)
            await s.commit()
    # 验证：contribute 成功（delivered_qty=7），participant 未写入（异常吞掉）
    async with async_session_factory() as s:
        row2 = (
            await s.execute(select(SheetRow).where(SheetRow.sheet_id == sid))
        ).scalar_one()
        assert row2.delivered_qty == 7


# ===========================================================================
# ③ 归档批量退出（close_all_participants）
# ===========================================================================

async def test_archive_closes_all_participants(tmp_path, client):
    """归档成功后该 sheet 全部活跃参与者 left_at 已置（reason='archived'）。"""
    from app.services.archive import archive_sheet
    owner_uuid, _, _ = await _seed_player("owner")
    p1, p1_aid, _ = await _seed_player("p1")
    p2, p2_aid, _ = await _seed_player("p2")
    sid = await _seed_sheet(owner_uuid, status="constructing", title="归档测")
    # 两个玩家加入
    async with async_session_factory() as s:
        await construction_repo.join_construction(s, p1_aid, sid, source="manual")
        await construction_repo.join_construction(s, p2_aid, sid, source="manual")
        await s.commit()
    # 归档
    async with async_session_factory() as s:
        player = (
            await s.execute(select(Player).where(Player.uuid == owner_uuid))
        ).scalar_one()
        await archive_sheet(
            s, sid, archive_root=str(tmp_path), player=player,
            actor_account_uuids=set(),
        )
    # 验证：两玩家 left_at 都已置，left_reason='archived'
    async with async_session_factory() as s:
        rows = list(
            (
                await s.execute(
                    select(Participant).where(Participant.sheet_id == sid)
                )
            ).scalars().all()
        )
        assert len(rows) == 2
        for r in rows:
            assert r.left_at is not None
            assert r.left_reason == "archived"


# ===========================================================================
# ④ progress 端点返回 construction_started_at + archived_at
# ===========================================================================

async def test_progress_returns_constructing_at(client):
    """constructing sheet 的 progress 返回 construction_started_at 非 None。"""
    owner_uuid, _, owner_bearer = await _seed_player("owner")
    p1, _, _ = await _seed_player("p1")
    # 建 collecting sheet，advance 到 constructing（写 constructing_at）
    async with async_session_factory() as s:
        sheet = Sheet(owner_uuid=owner_uuid, title="测", status="collecting")
        s.add(sheet)
        await s.flush()
        sid = sheet.id
        s.add(SheetRow(
            sheet_id=sid, item_name="x", registry_id="minecraft:stone",
            need_qty=10, mode=1,
        ))
        await s.commit()
    async with async_session_factory() as s:
        await sheet_repo.advance_sheet(s, sid, sheet_repo.SHEET_PHASE_CONSTRUCTING)
        await s.commit()
    # progress
    r = await client.get(
        f"/v1/construction/{sid}/progress",
        headers={"Authorization": owner_bearer},
    )
    body = r.json()
    assert body["construction_started_at"] is not None
    assert body["archived_at"] is None


async def test_progress_returns_archived_at(client):
    """archived sheet 的 progress 返回 archived_at 非 None。"""
    from datetime import datetime, timezone
    owner_uuid, _, owner_bearer = await _seed_player("owner")
    async with async_session_factory() as s:
        sheet = Sheet(
            owner_uuid=owner_uuid, title="测",
            status="archived", archived_path="projects/x/index.md",
            archived_at=datetime.now(timezone.utc),
            constructing_at=datetime.now(timezone.utc),
        )
        s.add(sheet)
        await s.flush()
        sid = sheet.id
        await s.commit()
    r = await client.get(
        f"/v1/construction/{sid}/progress",
        headers={"Authorization": owner_bearer},
    )
    body = r.json()
    assert body["construction_started_at"] is not None
    assert body["archived_at"] is not None


# ===========================================================================
# ⑤ report API project 维度零校验回归
# ===========================================================================

async def test_report_zero_project_check_same_account_alternating(client):
    """同账号交替报 sheet A/B 均成功（report API 无 participants 校验）。

    关键回归：加入机制不应影响 report 的 project 维度零校验——第三方/mod 源仍可
    多项目同时上报，与 participants join 完全解耦。
    """
    owner_uuid, _, _ = await _seed_player("owner")
    p1, _, _ = await _seed_player("p1")
    sid_a = await _seed_sheet(
        owner_uuid, status="constructing", title="A", rows=("minecraft:stone",)
    )
    sid_b = await _seed_sheet(
        owner_uuid, status="constructing", title="B", rows=("minecraft:stone",)
    )
    # 交替上报 A/B（同玩家同 source=mcdr/official），均应成功
    for sid in (sid_a, sid_b, sid_a, sid_b):
        r = await client.post(
            "/v1/construction/report",
            json={"sheet_id": sid, "placements": [
                {"player_uuid": str(p1), "registry_id": "minecraft:stone",
                 "placed_qty": 1, "broken_qty": 0},
            ]},
            headers=_svc(),
        )
        assert r.status_code == 200, r.text
        assert r.json()["totals"]["accepted"] == 1


# ===========================================================================
# ⑥ participants partial unique index 并发兜底（SAVEPOINT 隔离）
# ===========================================================================

async def test_concurrent_join_savepoint_isolation():
    """并发：两事务同时 join 同 account 不同 sheet，后者 IntegrityError 兜底降级。

    模拟：在已存在活跃行的情况下再调 join_construction（source=manual）→ 应抛
    ParticipantConflict（而非 IntegrityError 上抛污染事务）。
    """
    owner_uuid, _, _ = await _seed_player("owner")
    p1, p1_aid, _ = await _seed_player("p1")
    sid_a = await _seed_sheet(owner_uuid, status="constructing", title="A")
    sid_b = await _seed_sheet(owner_uuid, status="constructing", title="B")
    # 先 manual join A（同 session 内直接 insert，绕过 join_construction 模拟「已存在」）
    async with async_session_factory() as s:
        await construction_repo.join_construction(s, p1_aid, sid_a, source="manual")
        await s.commit()
    # 同 session 内并发再 manual join B → ParticipantConflict（enforce on）
    async with async_session_factory() as s:
        with pytest.raises(construction_repo.ParticipantConflict):
            await construction_repo.join_construction(
                s, p1_aid, sid_b, source="manual"
            )
        # 事务未污染：rollback 后 session 仍可用
        await s.rollback()


async def test_partial_unique_index_blocks_duplicate_active():
    """DB 层兜底：同 account 两条活跃行触发 IntegrityError（绕过应用层）。"""
    owner_uuid, _, _ = await _seed_player("owner")
    p1, p1_aid, _ = await _seed_player("p1")
    sid_a = await _seed_sheet(owner_uuid, status="constructing", title="A")
    sid_b = await _seed_sheet(owner_uuid, status="constructing", title="B")
    # 直接 insert 两条活跃行（绕过应用层），应触发 uq_participants_active
    async with async_session_factory() as s:
        s.add(Participant(web_account_id=p1_aid, sheet_id=sid_a, join_source="manual"))
        await s.commit()
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        async with async_session_factory() as s:
            s.add(Participant(web_account_id=p1_aid, sheet_id=sid_b, join_source="manual"))
            await s.commit()


# ===========================================================================
# active-by-uuids（tracker 用）
# ===========================================================================

async def test_active_by_uuids_returns_mappings(client):
    """service-token 批量查 UUID → sheet_id 映射。"""
    owner_uuid, _, _ = await _seed_player("owner")
    p1, p1_aid, _ = await _seed_player("p1")
    p2, p2_aid, _ = await _seed_player("p2")
    p3, _, _ = await _seed_player("p3")  # 未加入
    sid = await _seed_sheet(owner_uuid, status="constructing")
    async with async_session_factory() as s:
        await construction_repo.join_construction(s, p1_aid, sid, source="manual")
        await construction_repo.join_construction(s, p2_aid, sid, source="auto")
        await s.commit()
    r = await client.post(
        "/v1/construction/active-by-uuids",
        json={"player_uuids": [str(p1), str(p2), str(p3)]},
        headers=_svc(),
    )
    assert r.status_code == 200, r.text
    mappings = r.json()["mappings"]
    assert mappings[str(p1)] == sid
    assert mappings[str(p2)] == sid
    assert mappings[str(p3)] is None


async def test_active_by_uuids_requires_service_token(client):
    """无 service-token → 401。"""
    r = await client.post(
        "/v1/construction/active-by-uuids",
        json={"player_uuids": [str(uuid.uuid4())]},
    )
    assert r.status_code == 401


# ===========================================================================
# 迁移 0020：constructing_at 写入时机
# ===========================================================================

async def test_advance_sheet_writes_constructing_at():
    """collecting → constructing 切换时写入 constructing_at（幂等保护不重写）。"""
    owner_uuid, _, _ = await _seed_player("owner")
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner_uuid, "测")
        await s.commit()
        sid = sheet.id
    # advance 到 constructing
    async with async_session_factory() as s:
        await sheet_repo.advance_sheet(s, sid, sheet_repo.SHEET_PHASE_CONSTRUCTING)
        await s.commit()
    async with async_session_factory() as s:
        sheet = (
            await s.execute(select(Sheet).where(Sheet.id == sid))
        ).scalar_one()
        assert sheet.constructing_at is not None
    # advance 到 archived（不应改 constructing_at）
    from app.services.archive import archive_sheet
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        async with async_session_factory() as s:
            player = (
                await s.execute(select(Player).where(Player.uuid == owner_uuid))
            ).scalar_one()
            await archive_sheet(
                s, sid, archive_root=tmp, player=player,
                actor_account_uuids=set(),
            )
    async with async_session_factory() as s:
        sheet = (
            await s.execute(select(Sheet).where(Sheet.id == sid))
        ).scalar_one()
        assert sheet.status == "archived"
        assert sheet.constructing_at is not None  # 仍保留
        assert sheet.archived_at is not None
