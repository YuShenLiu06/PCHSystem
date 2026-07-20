"""SheetRepository 测试。

连真实 PG（conftest autouse truncate 每测清库）。seed users.players 行作 owner
（FK 要求），覆盖 CRUD/列表/upsert/级联删除/CSV + 协作状态机（claim/delivery/
release/reject/upsert 封顶）。

注意：repo 只 flush 不 commit，测试用独立 session 包裹 + 末尾 commit 验证持久化。
truncate SQL 已覆盖 sheets.sheet_rows / sheets.sheets（在 conftest 追加后）。
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.db import async_session_factory
from app.models.sheet import Sheet, SheetRow
from app.models.user import Player
from app.repositories import sheet_repo
from app.repositories.sheet_repo import SheetArchived, SheetRowConflict


async def _seed_player(name: str = "alice") -> uuid.UUID:
    """seed 一个 users.players 行作 owner，返回其 uuid。"""
    u = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=u, current_name=name))
        await s.commit()
    return u


@pytest.mark.asyncio
async def test_create_and_get_sheet():
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "建材表")
        await s.commit()
        assert sheet.id is not None
        assert sheet.owner_uuid == owner
        assert sheet.title == "建材表"

    async with async_session_factory() as s:
        result = await sheet_repo.get_sheet(s, sheet.id)
        assert result is not None
        fetched, owner_name = result
        assert fetched.title == "建材表"
        assert owner_name == "alice"


@pytest.mark.asyncio
async def test_get_sheet_returns_none_for_missing():
    async with async_session_factory() as s:
        assert await sheet_repo.get_sheet(s, 999999) is None


@pytest.mark.asyncio
async def test_list_sheets_all_and_owner_filter():
    owner_a = await _seed_player("alice")
    owner_b = await _seed_player("bob")
    async with async_session_factory() as s:
        await sheet_repo.create_sheet(s, owner_a, "A1")
        await sheet_repo.create_sheet(s, owner_a, "A2")
        await sheet_repo.create_sheet(s, owner_b, "B1")
        await s.commit()

    async with async_session_factory() as s:
        all_sheets = await sheet_repo.list_sheets(s)
        assert len(all_sheets) == 3
        only_a = await sheet_repo.list_sheets(s, owner_uuid=owner_a)
        assert {sh.title for sh, _ in only_a} == {"A1", "A2"}
        only_b = await sheet_repo.list_sheets(s, owner_uuid=owner_b)
        assert [sh.title for sh, _ in only_b] == ["B1"]
        # owner_name 来自 join
        assert all_sheets[0][1] in {"alice", "bob"}


@pytest.mark.asyncio
async def test_list_rows_orders_by_sort_then_id():
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        await sheet_repo.upsert_row(s, sheet.id, "iron", 64, 0, 2)
        await sheet_repo.upsert_row(s, sheet.id, "gold", 32, 0, 1)
        await sheet_repo.upsert_row(s, sheet.id, "dirt", 10, 0, 1)
        await s.commit()

    async with async_session_factory() as s:
        rows = await sheet_repo.list_rows(s, sheet.id)
        # sort_order 升序，同 sort_order 按 id 升序；无认领人 → claimant_name=None
        assert [r.item_name for r, _ in rows] == ["gold", "dirt", "iron"]
        assert all(name is None for _, name in rows)


@pytest.mark.asyncio
async def test_upsert_row_creates_then_updates_same_name():
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        await s.commit()

    async with async_session_factory() as s:
        created = await sheet_repo.upsert_row(s, sheet.id, "iron_ingot", 64, 1, 0)
        await s.commit()
        assert created.id is not None
        assert created.need_qty == 64
        assert created.mode == 1
        assert created.status == "open"
        assert created.claimant_uuid is None
        assert created.delivered_qty == 0
        first_id = created.id

    # 同名再次 upsert → 更新而非报错
    async with async_session_factory() as s:
        updated = await sheet_repo.upsert_row(s, sheet.id, "iron_ingot", 192, 0, 5)
        await s.commit()
        assert updated.id == first_id
        assert updated.need_qty == 192
        assert updated.mode == 0
        assert updated.sort_order == 5
        # 协作字段保留默认
        assert updated.status == "open"
        assert updated.claimant_uuid is None

    async with async_session_factory() as s:
        rows = await sheet_repo.list_rows(s, sheet.id)
        assert len(rows) == 1  # 仍是单行（未新建第二行）
        assert rows[0][0].need_qty == 192


@pytest.mark.asyncio
async def test_delete_row():
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        row = await sheet_repo.upsert_row(s, sheet.id, "x", 1, 0, 0)
        await s.commit()
        rid = row.id

    async with async_session_factory() as s:
        count = await sheet_repo.delete_row(s, sheet.id, rid)
        await s.commit()
        assert count == 1
        assert await sheet_repo.list_rows(s, sheet.id) == []

    # 再删已不存在的行 → rowcount 0
    async with async_session_factory() as s:
        assert await sheet_repo.delete_row(s, sheet.id, rid) == 0


@pytest.mark.asyncio
async def test_delete_sheet_cascades_rows():
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        await sheet_repo.upsert_row(s, sheet.id, "a", 1, 0, 0)
        await sheet_repo.upsert_row(s, sheet.id, "b", 2, 0, 0)
        await s.commit()
        sheet_id = sheet.id

    async with async_session_factory() as s:
        count = await sheet_repo.delete_sheet(s, sheet_id)
        await s.commit()
        assert count == 1
        # sheet 已删
        assert await sheet_repo.get_sheet(s, sheet_id) is None
        # rows 级联消失（DDL ON DELETE CASCADE）
        rows_left = (
            await s.execute(select(SheetRow).where(SheetRow.sheet_id == sheet_id))
        ).scalars().all()
        assert list(rows_left) == []


@pytest.mark.asyncio
async def test_delete_sheet_missing_returns_zero():
    async with async_session_factory() as s:
        assert await sheet_repo.delete_sheet(s, 999999) == 0


@pytest.mark.asyncio
async def test_export_csv_single_sheet():
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        await sheet_repo.upsert_row(s, sheet.id, "iron", 64, 0, 0)
        await sheet_repo.upsert_row(s, sheet.id, "gold", 128, 1, 1)
        await s.commit()
        rows = [r for r, _ in await sheet_repo.list_rows(s, sheet.id)]

    csv_str = sheet_repo.export_csv(sheet.id, rows)
    lines = csv_str.strip().splitlines()
    assert lines[0] == (
        "sheet_id,item_name,registry_id,need_qty,mode,status,claimant_uuid,delivered_qty,sort_order,parent_row_id,qty_per_unit"
    )
    assert lines[1] == f"{sheet.id},iron,,64,0,open,,0,0,,"
    assert lines[2] == f"{sheet.id},gold,,128,1,open,,0,1,,"


@pytest.mark.asyncio
async def test_export_all_csv_multiple_sheets():
    owner = await _seed_player()
    async with async_session_factory() as s:
        s1 = await sheet_repo.create_sheet(s, owner, "S1")
        s2 = await sheet_repo.create_sheet(s, owner, "S2")
        await sheet_repo.upsert_row(s, s1.id, "a", 1, 0, 0)
        await sheet_repo.upsert_row(s, s2.id, "b", 2, 1, 0)
        await s.commit()

    async with async_session_factory() as s:
        bundled = await sheet_repo.list_all_sheets_with_rows(s)

    csv_str = sheet_repo.export_all_csv(bundled)
    lines = csv_str.strip().splitlines()
    assert lines[0] == (
        "sheet_id,item_name,registry_id,need_qty,mode,status,claimant_uuid,delivered_qty,sort_order,parent_row_id,qty_per_unit"
    )
    body = lines[1:]
    assert f"{s1.id},a,,1,0,open,,0,0,," in body
    assert f"{s2.id},b,,2,1,open,,0,0,," in body
    assert len(body) == 2


@pytest.mark.asyncio
async def test_export_csv_empty_sheet_has_header_only():
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        await s.commit()

    csv_str = sheet_repo.export_csv(sheet.id, [])
    lines = csv_str.strip().splitlines()
    assert lines == [
        "sheet_id,item_name,registry_id,need_qty,mode,status,claimant_uuid,delivered_qty,sort_order,parent_row_id,qty_per_unit"
    ]


# ---------- 协作状态机 ----------
async def _make_row(need_qty: int = 10, mode: int = 0) -> tuple[int, int]:
    """seed 一张表 + 一行（open），返回 (sheet_id, row_id)。"""
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        row = await sheet_repo.upsert_row(s, sheet.id, "x", need_qty, mode, 0)
        await s.commit()
        return sheet.id, row.id


@pytest.mark.asyncio
async def test_claim_row_open_to_claimed():
    owner = await _seed_player()
    sid, rid = await _make_row()
    claimant = await _seed_player("bob")
    async with async_session_factory() as s:
        row = await sheet_repo.claim_row(s, sid, rid, claimant)
        await s.commit()
        assert row is not None
        assert row.status == "claimed"
        assert row.claimant_uuid == claimant
        assert row.delivered_qty == 0


@pytest.mark.asyncio
async def test_claim_row_on_claimed_raises_conflict():
    sid, rid = await _make_row()
    a = await _seed_player("alice")
    b = await _seed_player("bob")
    async with async_session_factory() as s:
        await sheet_repo.claim_row(s, sid, rid, a)
        await s.commit()
    async with async_session_factory() as s:
        with pytest.raises(SheetRowConflict):
            await sheet_repo.claim_row(s, sid, rid, b)


@pytest.mark.asyncio
async def test_set_row_delivery_below_need_stays_claimed():
    sid, rid = await _make_row(need_qty=10)
    claimant = await _seed_player("bob")
    async with async_session_factory() as s:
        await sheet_repo.claim_row(s, sid, rid, claimant)
        await s.commit()
    async with async_session_factory() as s:
        row = await sheet_repo.set_row_delivery(s, sid, rid, 5)
        await s.commit()
        assert row.status == "claimed"
        assert row.delivered_qty == 5


@pytest.mark.asyncio
async def test_set_row_delivery_meets_need_goes_done():
    sid, rid = await _make_row(need_qty=10)
    claimant = await _seed_player("bob")
    async with async_session_factory() as s:
        await sheet_repo.claim_row(s, sid, rid, claimant)
        await s.commit()
    async with async_session_factory() as s:
        row = await sheet_repo.set_row_delivery(s, sid, rid, 10)
        await s.commit()
        assert row.status == "done"
        assert row.delivered_qty == 10


@pytest.mark.asyncio
async def test_release_row_clears_claimant_and_delivered():
    sid, rid = await _make_row(need_qty=10)
    claimant = await _seed_player("bob")
    async with async_session_factory() as s:
        await sheet_repo.claim_row(s, sid, rid, claimant)
        await s.commit()
    async with async_session_factory() as s:
        await sheet_repo.set_row_delivery(s, sid, rid, 10)
        await s.commit()
    # done → release → open，claimant + delivered 清零
    async with async_session_factory() as s:
        row = await sheet_repo.release_row(s, sid, rid)
        await s.commit()
        assert row.status == "open"
        assert row.claimant_uuid is None
        assert row.delivered_qty == 0


@pytest.mark.asyncio
async def test_reject_done_keeps_claimant_zeroes_delivered():
    sid, rid = await _make_row(need_qty=10)
    claimant = await _seed_player("bob")
    async with async_session_factory() as s:
        await sheet_repo.claim_row(s, sid, rid, claimant)
        await s.commit()
    async with async_session_factory() as s:
        await sheet_repo.set_row_delivery(s, sid, rid, 10)
        await s.commit()
    async with async_session_factory() as s:
        row = await sheet_repo.reject_row(s, sid, rid)
        await s.commit()
        assert row.status == "claimed"
        assert row.claimant_uuid == claimant  # 认领人保留重做
        assert row.delivered_qty == 0


@pytest.mark.asyncio
async def test_reject_non_done_raises_conflict():
    sid, rid = await _make_row()
    claimant = await _seed_player("bob")
    async with async_session_factory() as s:
        await sheet_repo.claim_row(s, sid, rid, claimant)
        await s.commit()
    # claimed 行不能 reject（只有 done 可 reject）
    async with async_session_factory() as s:
        with pytest.raises(SheetRowConflict):
            await sheet_repo.reject_row(s, sid, rid)


@pytest.mark.asyncio
async def test_upsert_caps_delivered_and_status_on_need_change():
    """已认领 done 行：拥有者下调 need 使 delivered 等于新 need 仍保持 done；
    若 delivered 超过新 need 则封顶到新 need。"""
    sid, rid = await _make_row(need_qty=10)
    claimant = await _seed_player("bob")
    async with async_session_factory() as s:
        await sheet_repo.claim_row(s, sid, rid, claimant)
        await sheet_repo.set_row_delivery(s, sid, rid, 10)  # done
        await s.commit()
    # 拥有者把 need 改成 5（< delivered 10）→ delivered 封顶到 5，status 仍 done
    async with async_session_factory() as s:
        row = await sheet_repo.upsert_row(s, sid, "x", 5, 0, 0)
        await s.commit()
        assert row.need_qty == 5
        assert row.delivered_qty == 5
        assert row.status == "done"
    # 再上调 need 到 20（delivered 5 < 20）且之前是 done → 回落 claimed
    async with async_session_factory() as s:
        row = await sheet_repo.upsert_row(s, sid, "x", 20, 0, 0)
        await s.commit()
        assert row.status == "claimed"
        assert row.delivered_qty == 5


@pytest.mark.asyncio
async def test_get_row_returns_none_for_missing():
    sid, _ = await _make_row()
    async with async_session_factory() as s:
        assert await sheet_repo.get_row(s, sid, 999999) is None


# ---------- progress / 多人贡献者 ----------


async def _make_progress_row(need_qty: int = 10) -> tuple[int, int]:
    """seed 一张表 + 一行（mode=progress, open），返回 (sheet_id, row_id)。"""
    return await _make_row(need_qty=need_qty, mode=1)


@pytest.mark.asyncio
async def test_contribute_accumulates_and_transitions_to_done():
    """progress 行：增量累加 delivered；到 need 转 done；可超额；幂等加贡献者。"""
    sid, rid = await _make_progress_row(need_qty=10)
    alice = await _seed_player("alice")
    bob = await _seed_player("bob")

    # alice 上交 4 → claimed（0 < 4 < 10）
    async with async_session_factory() as s:
        row = await sheet_repo.contribute_row(s, sid, rid, alice, 4)
        await s.commit()
        assert row.delivered_qty == 4
        assert row.status == "claimed"
        assert row.claimant_uuid is None  # progress 不变量

    # alice 再次上交 8 → delivered 12（超额），转 done（>=need）
    async with async_session_factory() as s:
        row = await sheet_repo.contribute_row(s, sid, rid, alice, 8)
        await s.commit()
        assert row.delivered_qty == 12  # 不封顶
        assert row.status == "done"

    # bob 也上交一次（行已 done 不影响幂等加贡献者记录——见 test_contribute_on_done_raises）
    # 这里在 done 之前先验证幂等：alice 两次 contribute 应只贡献一条记录
    async with async_session_factory() as s:
        contribs = await sheet_repo.list_contributors(s, [rid])
        alice_entries = [
            _aid for _aid, _dn, mids, _qty in contribs.get(rid, [])
            if alice in mids
        ]
        assert len(alice_entries) == 1  # 幂等：同玩家多次只一条（account 聚合后仍一条）


@pytest.mark.asyncio
async def test_contribute_on_lock_row_raises_conflict():
    """contribute 仅适用于 progress 行；lock 行（mode=0）raise SheetRowConflict。"""
    sid, rid = await _make_row(need_qty=10, mode=0)  # lock 行
    alice = await _seed_player("alice")
    async with async_session_factory() as s:
        with pytest.raises(SheetRowConflict):
            await sheet_repo.contribute_row(s, sid, rid, alice, 1)


@pytest.mark.asyncio
async def test_contribute_on_done_row_raises_conflict():
    """progress 行一旦 done 不再收上交（防超 need 后还能灌）。"""
    sid, rid = await _make_progress_row(need_qty=5)
    alice = await _seed_player("alice")
    async with async_session_factory() as s:
        await sheet_repo.contribute_row(s, sid, rid, alice, 5)  # → done
        await s.commit()
    async with async_session_factory() as s:
        with pytest.raises(SheetRowConflict):
            await sheet_repo.contribute_row(s, sid, rid, alice, 1)


@pytest.mark.asyncio
async def test_claim_on_progress_row_raises_conflict_but_lock_works():
    """progress 行禁止 claim；lock 行 open→claimed 仍正常。"""
    # progress 行 → raise
    sid_p, rid_p = await _make_progress_row(need_qty=10)
    claimant = await _seed_player("bob")
    async with async_session_factory() as s:
        with pytest.raises(SheetRowConflict):
            await sheet_repo.claim_row(s, sid_p, rid_p, claimant)

    # lock 行 → open → claimed
    sid_l, rid_l = await _make_row(need_qty=10, mode=0)
    async with async_session_factory() as s:
        row = await sheet_repo.claim_row(s, sid_l, rid_l, claimant)
        await s.commit()
        assert row.status == "claimed"
        assert row.claimant_uuid == claimant


@pytest.mark.asyncio
async def test_set_row_delivery_on_progress_row_raises_conflict():
    """progress 行用 contribute 管 delivered，set_row_delivery 禁用。"""
    sid, rid = await _make_progress_row(need_qty=10)
    async with async_session_factory() as s:
        with pytest.raises(SheetRowConflict):
            await sheet_repo.set_row_delivery(s, sid, rid, 5)


@pytest.mark.asyncio
async def test_set_row_progress_overrides_delivered_and_keeps_contributors():
    """progress 行 owner 直接设绝对值：按新值重算 status，**不动 contributors**（保留下交历史）。"""
    sid, rid = await _make_progress_row(need_qty=10)
    alice = await _seed_player("alice")
    # 先 alice 上交 4（claimed + contributors=[alice]）
    async with async_session_factory() as s:
        await sheet_repo.contribute_row(s, sid, rid, alice, 4)
        await s.commit()

    # owner 调整为 8（绝对值）→ 仍 claimed
    async with async_session_factory() as s:
        row = await sheet_repo.set_row_progress(s, sid, rid, 8)
        await s.commit()
        assert row.delivered_qty == 8
        assert row.status == "claimed"
        assert row.claimant_uuid is None  # progress 不变量保持

    # 调到 10 → done
    async with async_session_factory() as s:
        row = await sheet_repo.set_row_progress(s, sid, rid, 10)
        await s.commit()
        assert row.status == "done"

    # 回退到 0 → open，但 contributors 保留（上交历史不因 owner 调整而清）
    async with async_session_factory() as s:
        row = await sheet_repo.set_row_progress(s, sid, rid, 0)
        await s.commit()
        assert row.delivered_qty == 0
        assert row.status == "open"
        contribs = await sheet_repo.list_contributors(s, [rid])
        assert len(contribs.get(rid, [])) == 1
        assert alice in contribs[rid][0][2]  # member_uuids 含 alice（4 元组第 3 位）


@pytest.mark.asyncio
async def test_set_row_progress_on_lock_row_raises_conflict():
    """lock 行用 set_row_delivery；set_row_progress 仅 progress。"""
    sid, rid = await _make_row(need_qty=10, mode=0)
    async with async_session_factory() as s:
        with pytest.raises(SheetRowConflict):
            await sheet_repo.set_row_progress(s, sid, rid, 5)


@pytest.mark.asyncio
async def test_reject_on_progress_row_raises_conflict():
    """progress 行无 reject（用 release 重置）；调用 raise SheetRowConflict。"""
    sid, rid = await _make_progress_row(need_qty=10)
    alice = await _seed_player("alice")
    # 先 contribute 让行有进度（status=claimed），再尝试 reject
    async with async_session_factory() as s:
        await sheet_repo.contribute_row(s, sid, rid, alice, 3)
        await s.commit()
    async with async_session_factory() as s:
        with pytest.raises(SheetRowConflict):
            await sheet_repo.reject_row(s, sid, rid)


@pytest.mark.asyncio
async def test_release_progress_clears_delivered_and_contributors():
    """progress 行 release：delivered=0 / contributors 空 / status=open。"""
    sid, rid = await _make_progress_row(need_qty=10)
    alice = await _seed_player("alice")
    bob = await _seed_player("bob")
    # 建进度 + 两个贡献者
    async with async_session_factory() as s:
        await sheet_repo.contribute_row(s, sid, rid, alice, 4)
        await sheet_repo.contribute_row(s, sid, rid, bob, 3)
        await s.commit()
    async with async_session_factory() as s:
        contribs = await sheet_repo.list_contributors(s, [rid])
        assert len(contribs.get(rid, [])) == 2
    # release
    async with async_session_factory() as s:
        row = await sheet_repo.release_row(s, sid, rid)
        await s.commit()
        assert row.status == "open"
        assert row.delivered_qty == 0
        assert row.claimant_uuid is None
    async with async_session_factory() as s:
        contribs = await sheet_repo.list_contributors(s, [rid])
        assert contribs.get(rid, []) == []


@pytest.mark.asyncio
async def test_upsert_progress_mode_change_resets_and_same_mode_preserves():
    """progress 行：mode 变化（progress→lock）重置协作；mode 不变保留进度按新 need 封顶。

    用两行独立验证两条分支，避免一条行先后经历 mode 切换后无法再 contribute。
    """
    sid, rid_reset = await _make_progress_row(need_qty=10)
    alice = await _seed_player("alice")
    bob = await _seed_player("bob")

    # ===== 分支 1：mode 变化 progress(1)→lock(0) → 重置 =====
    async with async_session_factory() as s:
        await sheet_repo.contribute_row(s, sid, rid_reset, alice, 4)
        await sheet_repo.contribute_row(s, sid, rid_reset, bob, 3)
        await s.commit()
    async with async_session_factory() as s:
        row = await sheet_repo.upsert_row(s, sid, "x", 100, 0, 0)
        await s.commit()
        assert row.mode == 0
        assert row.status == "open"
        assert row.claimant_uuid is None
        assert row.delivered_qty == 0
    async with async_session_factory() as s:
        contribs = await sheet_repo.list_contributors(s, [rid_reset])
        assert contribs.get(rid_reset, []) == []

    # ===== 分支 2：mode 不变（仍 progress），改 need 封顶 delivered =====
    sid2, rid_keep = await _make_progress_row(need_qty=10)
    async with async_session_factory() as s:
        await sheet_repo.contribute_row(s, sid2, rid_keep, alice, 6)
        await s.commit()
    # need 从 10 改到 5（< delivered 6），mode 仍 progress → 封顶到 5，status=done
    async with async_session_factory() as s:
        row = await sheet_repo.upsert_row(s, sid2, "x", 5, 1, 0)
        await s.commit()
        assert row.mode == 1
        assert row.delivered_qty == 5  # 封顶
        assert row.status == "done"
    # contributors 保留（mode 未变，未触发 clear）
    async with async_session_factory() as s:
        contribs = await sheet_repo.list_contributors(s, [rid_keep])
        assert len(contribs.get(rid_keep, [])) == 1


@pytest.mark.asyncio
async def test_list_contributors_aggregates_multiple_rows_ordered_by_qty_then_name():
    """多行 × 多贡献者聚合，每行内部按 contributed_qty desc、display_name 升序（account 聚合）。"""
    sid, rid_a = await _make_progress_row(need_qty=100)
    # 同表再加一个 progress 行
    async with async_session_factory() as s:
        row_b = await sheet_repo.upsert_row(s, sid, "y", 100, 1, 1)
        await s.commit()
        rid_b = row_b.id

    alice = await _seed_player("alice")
    bob = await _seed_player("bob")
    carol = await _seed_player("carol")

    # rid_a: alice → bob → carol（按 contribute 顺序，joined_at 升序）
    async with async_session_factory() as s:
        await sheet_repo.contribute_row(s, sid, rid_a, alice, 1)
        await s.commit()
    async with async_session_factory() as s:
        await sheet_repo.contribute_row(s, sid, rid_a, bob, 1)
        await s.commit()
    async with async_session_factory() as s:
        await sheet_repo.contribute_row(s, sid, rid_a, carol, 1)
        await s.commit()

    # rid_b: carol → alice（不同顺序，验证每行独立排序）
    async with async_session_factory() as s:
        await sheet_repo.contribute_row(s, sid, rid_b, carol, 1)
        await s.commit()
    async with async_session_factory() as s:
        await sheet_repo.contribute_row(s, sid, rid_b, alice, 1)
        await s.commit()

    # 批量查两行
    async with async_session_factory() as s:
        contribs = await sheet_repo.list_contributors(s, [rid_a, rid_b])
        # 两行都有结果
        assert set(contribs.keys()) == {rid_a, rid_b}
        # rid_a：alice/bob/carol 各上交 1（qty 相同）→ display_name 升序
        names_a = [dn for _aid, dn, _mids, _qty in contribs[rid_a]]
        assert names_a == ["alice", "bob", "carol"]
        # rid_b：carol/alice 各上交 1（qty 相同）→ display_name 升序（account 聚合排序）
        names_b = [dn for _aid, dn, _mids, _qty in contribs[rid_b]]
        assert names_b == ["alice", "carol"]
        # member_uuids 元素为 UUID（account 聚合后每条至少含一个 member uuid）
        for entries in contribs.values():
            for _aid, _dn, member_uuids, _qty in entries:
                assert member_uuids and isinstance(member_uuids[0], uuid.UUID)


@pytest.mark.asyncio
async def test_list_contributors_empty_input_returns_empty_dict():
    """空 row_ids 入参 → 空 dict（不查 DB）。"""
    async with async_session_factory() as s:
        assert await sheet_repo.list_contributors(s, []) == {}


@pytest.mark.asyncio
async def test_list_contributors_missing_row_returns_no_entry():
    """查询不存在的 row_id → 该 key 不出现在结果中。"""
    alice = await _seed_player("alice")
    sid, rid = await _make_progress_row(need_qty=10)
    async with async_session_factory() as s:
        await sheet_repo.contribute_row(s, sid, rid, alice, 1)
        await s.commit()
    async with async_session_factory() as s:
        contribs = await sheet_repo.list_contributors(s, [rid, 999999])
        assert set(contribs.keys()) == {rid}
        assert contribs[rid]  # 已有贡献者


# ---------- registry_id ----------
@pytest.mark.asyncio
async def test_upsert_row_sets_registry_id_on_create():
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        row = await sheet_repo.upsert_row(
            s, sheet.id, "石头", 64, 0, 0, registry_id="minecraft:stone"
        )
        await s.commit()
        assert row.registry_id == "minecraft:stone"


@pytest.mark.asyncio
async def test_upsert_row_registry_id_defaults_none():
    """不传 registry_id 建行 → None（兼容旧行 / 纯文本行）。"""
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        row = await sheet_repo.upsert_row(s, sheet.id, "纯文本行", 1, 0, 0)
        await s.commit()
        assert row.registry_id is None


@pytest.mark.asyncio
async def test_upsert_row_preserves_registry_id_when_omitted():
    """更新行不传 registry_id（默认 None）→ 不覆盖已有值（避免误擦匹配键）。"""
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        await sheet_repo.upsert_row(
            s, sheet.id, "石头", 64, 0, 0, registry_id="minecraft:stone"
        )
        await s.commit()
    async with async_session_factory() as s:
        row = await sheet_repo.upsert_row(s, sheet.id, "石头", 128, 0, 0)
        await s.commit()
        assert row.need_qty == 128
        assert row.registry_id == "minecraft:stone"  # 保留


@pytest.mark.asyncio
async def test_upsert_row_overwrites_registry_id_when_provided():
    """更新行传新 registry_id → 覆盖旧值。"""
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        await sheet_repo.upsert_row(
            s, sheet.id, "石头", 64, 0, 0, registry_id="minecraft:stone"
        )
        await s.commit()
    async with async_session_factory() as s:
        row = await sheet_repo.upsert_row(
            s, sheet.id, "石头", 64, 0, 0, registry_id="minecraft:cobblestone"
        )
        await s.commit()
        assert row.registry_id == "minecraft:cobblestone"


# ---------- update_row（按主键部分更新；issue #20 改名重复修复）----------
# "修改行"以 row_id（主键）为定位主轴，而非可变的 item_name。
# 旧 upsert by item_name 路径下改名会用新名查不到旧行 → 新建 → 重复；
# update_row 按主键定位绕开 item_name 匹配，逐字段部分更新（None=不改动）。


@pytest.mark.asyncio
async def test_update_row_rename_keeps_id_and_single_row():
    """改名（只传 item_name）：id 不变、名变、其余字段保持原值、不新增行（issue #20 核心）。

    Arrange / Act / Assert：直接证伪旧 bug —— 改名后仍单行、id 不变。
    """
    # Arrange：建一行「石英柱」need=64 mode=lock sort=2 registry_id 已设
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        row = await sheet_repo.upsert_row(
            s,
            sheet.id,
            "石英柱",
            64,
            sheet_repo.MODE_LOCK,
            2,
            registry_id="minecraft:quartz_block",
        )
        await s.commit()
        sid, old_id = sheet.id, row.id

    # Act：按 id 改名（只传 item_name，其余不传）
    async with async_session_factory() as s:
        updated = await sheet_repo.update_row(s, sid, old_id, item_name="石英柱1")
        await s.commit()

    # Assert：id 不变、名变、其余字段保持原值
    assert updated is not None
    assert updated.id == old_id
    assert updated.item_name == "石英柱1"
    assert updated.need_qty == 64
    assert updated.mode == sheet_repo.MODE_LOCK
    assert updated.sort_order == 2
    assert updated.registry_id == "minecraft:quartz_block"
    # 仍只有一行（未新建第二行）—— 旧 bug 在此会变 2 行
    async with async_session_factory() as s:
        rows = await sheet_repo.list_rows(s, sid)
    assert len(rows) == 1
    assert rows[0][0].item_name == "石英柱1"


@pytest.mark.asyncio
async def test_update_row_partial_need_only_isolates_other_fields():
    """只传 need_qty：need 变，item_name/mode/sort_order 不动。"""
    # Arrange
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        row = await sheet_repo.upsert_row(s, sheet.id, "铁锭", 64, sheet_repo.MODE_LOCK, 3)
        await s.commit()
        sid, rid = sheet.id, row.id
    # Act：只改 need
    async with async_session_factory() as s:
        updated = await sheet_repo.update_row(s, sid, rid, need_qty=200)
        await s.commit()
    # Assert：仅 need 变
    assert updated.need_qty == 200
    assert updated.item_name == "铁锭"
    assert updated.mode == sheet_repo.MODE_LOCK
    assert updated.sort_order == 3


@pytest.mark.asyncio
async def test_update_row_rename_preserves_claim_state_without_mode():
    """认领中的 lock 行改名（不传 mode）：status/claimant/delivered 全保留（mode 未变不重置）。"""
    # Arrange：建 + 认领 + 部分交付（claimed，delivered=4）
    sid, rid = await _make_row(need_qty=10, mode=sheet_repo.MODE_LOCK)
    claimant = await _seed_player("bob")
    async with async_session_factory() as s:
        await sheet_repo.claim_row(s, sid, rid, claimant)
        await sheet_repo.set_row_delivery(s, sid, rid, 4)
        await s.commit()
    # Act：改名（不传 mode）
    async with async_session_factory() as s:
        row = await sheet_repo.update_row(s, sid, rid, item_name="改名后")
        await s.commit()
    # Assert：协作状态全保留
    assert row.item_name == "改名后"
    assert row.status == "claimed"
    assert row.claimant_uuid == claimant
    assert row.delivered_qty == 4


@pytest.mark.asyncio
async def test_update_row_mode_change_resets_collaboration():
    """progress 行有贡献者：update_row 显式传 mode=lock → 重置协作（open/delivered 0/清贡献者）。"""
    # Arrange：progress 行 + alice 上交 5（claimed + contributors=[alice]）
    sid, rid = await _make_progress_row(need_qty=10)
    alice = await _seed_player("alice")
    async with async_session_factory() as s:
        await sheet_repo.contribute_row(s, sid, rid, alice, 5)
        await s.commit()
    # Act：mode progress→lock
    async with async_session_factory() as s:
        row = await sheet_repo.update_row(s, sid, rid, mode=sheet_repo.MODE_LOCK)
        await s.commit()
    # Assert：重置
    assert row.mode == sheet_repo.MODE_LOCK
    assert row.status == "open"
    assert row.delivered_qty == 0
    assert row.claimant_uuid is None
    async with async_session_factory() as s:
        contribs = await sheet_repo.list_contributors(s, [rid])
    assert contribs.get(rid, []) == []


@pytest.mark.asyncio
async def test_update_row_need_change_caps_and_recomputes_status():
    """已交付 done 的 lock 行：update_row 下调 need（不传 mode）→ delivered 封顶 + status 重算。"""
    # Arrange：claim + 交付满（done，need=10 delivered=10）
    sid, rid = await _make_row(need_qty=10, mode=sheet_repo.MODE_LOCK)
    claimant = await _seed_player("bob")
    async with async_session_factory() as s:
        await sheet_repo.claim_row(s, sid, rid, claimant)
        await sheet_repo.set_row_delivery(s, sid, rid, 10)
        await s.commit()
    # Act：need 10→5 → delivered 封顶 5，status 仍 done
    async with async_session_factory() as s:
        row = await sheet_repo.update_row(s, sid, rid, need_qty=5)
        await s.commit()
    assert row.need_qty == 5
    assert row.delivered_qty == 5
    assert row.status == "done"
    # Act：need 5→20 → delivered 5 < 20，done→claimed
    async with async_session_factory() as s:
        row = await sheet_repo.update_row(s, sid, rid, need_qty=20)
        await s.commit()
    assert row.status == "claimed"
    assert row.delivered_qty == 5


@pytest.mark.asyncio
async def test_update_row_registry_id_overwrites_and_preserves():
    """update_row 传 registry_id → 覆盖；不传（只改其它字段）→ 保留（None 不擦）。"""
    # Arrange
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        row = await sheet_repo.upsert_row(
            s,
            sheet.id,
            "石头",
            64,
            sheet_repo.MODE_LOCK,
            0,
            registry_id="minecraft:stone",
        )
        await s.commit()
        sid, rid = sheet.id, row.id
    # Act：传 registry_id 覆盖
    async with async_session_factory() as s:
        row = await sheet_repo.update_row(
            s, sid, rid, registry_id="minecraft:cobblestone"
        )
        await s.commit()
    assert row.registry_id == "minecraft:cobblestone"
    # Act：只改名（不传 registry_id）→ registry_id 保留
    async with async_session_factory() as s:
        row = await sheet_repo.update_row(s, sid, rid, item_name="圆石")
        await s.commit()
    assert row.item_name == "圆石"
    assert row.registry_id == "minecraft:cobblestone"


@pytest.mark.asyncio
async def test_update_row_missing_returns_none():
    """row_id 不存在 → None（api 层翻译 404）。"""
    sid, _ = await _make_row()
    async with async_session_factory() as s:
        assert await sheet_repo.update_row(s, sid, 999999, item_name="x") is None


@pytest.mark.asyncio
async def test_update_row_on_archived_raises_sheet_archived():
    """archived 终态只读：update_row 入口经 _assert_writable → raise SheetArchived。"""
    # Arrange：建表 + 行，然后归档
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        row = await sheet_repo.upsert_row(s, sheet.id, "x", 10, sheet_repo.MODE_LOCK, 0)
        await s.commit()
        sid, rid = sheet.id, row.id
    async with async_session_factory() as s:
        await sheet_repo.advance_sheet(
            s,
            sid,
            sheet_repo.SHEET_PHASE_ARCHIVED,
            archived_path=f"projects/{sid}/index.md",
        )
        await s.commit()
    # Act / Assert
    async with async_session_factory() as s:
        with pytest.raises(SheetArchived):
            await sheet_repo.update_row(s, sid, rid, item_name="y")


# ---------- 项目阶段生命周期（迁移 0009）----------
# 三阶段：collecting（默认）→ constructing → archived（只读终态）；collecting 可直跳 archived。
# 所有写操作入口经 _assert_writable 守卫：archived → SheetArchived；不存在 → 交给后续逻辑返 None→404。


async def _make_sheet(title: str = "S") -> int:
    """seed 一张 collecting 态表，返回 sheet_id。"""
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, title)
        await s.commit()
        return sheet.id


@pytest.mark.asyncio
async def test_advance_sheet_collecting_to_constructing():
    # Arrange
    sid = await _make_sheet()
    # Act
    async with async_session_factory() as s:
        sheet = await sheet_repo.advance_sheet(
            s, sid, sheet_repo.SHEET_PHASE_CONSTRUCTING
        )
        await s.commit()
    # Assert
    assert sheet is not None
    assert sheet.status == sheet_repo.SHEET_PHASE_CONSTRUCTING
    assert sheet.archived_path is None
    assert sheet.archived_at is None


@pytest.mark.asyncio
async def test_advance_sheet_constructing_to_archived_with_path():
    # Arrange：先到 constructing
    sid = await _make_sheet()
    async with async_session_factory() as s:
        await sheet_repo.advance_sheet(
            s, sid, sheet_repo.SHEET_PHASE_CONSTRUCTING
        )
        await s.commit()
    # Act：constructing → archived（带 archived_path）
    async with async_session_factory() as s:
        sheet = await sheet_repo.advance_sheet(
            s,
            sid,
            sheet_repo.SHEET_PHASE_ARCHIVED,
            archived_path=f"projects/{sid}/index.md",
        )
        await s.commit()
    # Assert
    assert sheet.status == sheet_repo.SHEET_PHASE_ARCHIVED
    assert sheet.archived_path == f"projects/{sid}/index.md"
    assert sheet.archived_at is not None


@pytest.mark.asyncio
async def test_advance_sheet_collecting_directly_to_archived():
    # Arrange：collecting 态（跳过施工）
    sid = await _make_sheet()
    # Act
    async with async_session_factory() as s:
        sheet = await sheet_repo.advance_sheet(
            s,
            sid,
            sheet_repo.SHEET_PHASE_ARCHIVED,
            archived_path=f"projects/{sid}/index.md",
        )
        await s.commit()
    # Assert
    assert sheet.status == sheet_repo.SHEET_PHASE_ARCHIVED
    assert sheet.archived_path == f"projects/{sid}/index.md"


@pytest.mark.asyncio
async def test_advance_sheet_archived_raises_sheet_archived():
    # Arrange：先归档
    sid = await _make_sheet()
    async with async_session_factory() as s:
        await sheet_repo.advance_sheet(
            s,
            sid,
            sheet_repo.SHEET_PHASE_ARCHIVED,
            archived_path=f"projects/{sid}/index.md",
        )
        await s.commit()
    # Act / Assert：archived 终态，任何 advance → SheetArchived
    async with async_session_factory() as s:
        with pytest.raises(SheetArchived):
            await sheet_repo.advance_sheet(
                s, sid, sheet_repo.SHEET_PHASE_CONSTRUCTING
            )


@pytest.mark.asyncio
async def test_advance_sheet_idempotent_same_status_raises_conflict():
    # Arrange：collecting 态
    sid = await _make_sheet()
    # Act / Assert：to == 当前 → SheetRowConflict（幂等拒绝）
    async with async_session_factory() as s:
        with pytest.raises(SheetRowConflict):
            await sheet_repo.advance_sheet(
                s, sid, sheet_repo.SHEET_PHASE_COLLECTING
            )


@pytest.mark.asyncio
async def test_advance_sheet_archived_without_path_raises_value_error():
    # Arrange
    sid = await _make_sheet()
    # Act / Assert：to=archived 但缺 archived_path → ValueError（契约违反）
    async with async_session_factory() as s:
        with pytest.raises(ValueError):
            await sheet_repo.advance_sheet(
                s, sid, sheet_repo.SHEET_PHASE_ARCHIVED
            )


@pytest.mark.asyncio
async def test_advance_sheet_invalid_transition_raises_conflict():
    """constructing → collecting 是回退非法转移 → SheetRowConflict。"""
    # Arrange：先到 constructing
    sid = await _make_sheet()
    async with async_session_factory() as s:
        await sheet_repo.advance_sheet(
            s, sid, sheet_repo.SHEET_PHASE_CONSTRUCTING
        )
        await s.commit()
    # Act / Assert
    async with async_session_factory() as s:
        with pytest.raises(SheetRowConflict):
            await sheet_repo.advance_sheet(
                s, sid, sheet_repo.SHEET_PHASE_COLLECTING
            )


@pytest.mark.asyncio
async def test_advance_sheet_missing_returns_none():
    # Arrange / Act
    async with async_session_factory() as s:
        result = await sheet_repo.advance_sheet(
            s, 999999, sheet_repo.SHEET_PHASE_CONSTRUCTING
        )
    # Assert：不存在 → None（api 层翻译 404）
    assert result is None


@pytest.mark.asyncio
async def test_write_on_archived_sheet_raises_sheet_archived():
    """先 advance 到 archived，再调多个写函数都应 raise SheetArchived（守卫代表覆盖）。"""
    # Arrange：建表 + 行，然后归档
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        row = await sheet_repo.upsert_row(s, sheet.id, "x", 10, 0, 0)
        await s.commit()
        sid, rid = sheet.id, row.id
    async with async_session_factory() as s:
        await sheet_repo.advance_sheet(
            s,
            sid,
            sheet_repo.SHEET_PHASE_ARCHIVED,
            archived_path=f"projects/{sid}/index.md",
        )
        await s.commit()

    # Act / Assert：覆盖多个写函数入口守卫
    alice = await _seed_player("alice")
    async with async_session_factory() as s:
        with pytest.raises(SheetArchived):
            await sheet_repo.upsert_row(s, sid, "x", 99, 0, 0)
    async with async_session_factory() as s:
        with pytest.raises(SheetArchived):
            await sheet_repo.claim_row(s, sid, rid, alice)
    async with async_session_factory() as s:
        with pytest.raises(SheetArchived):
            await sheet_repo.delete_row(s, sid, rid)
    async with async_session_factory() as s:
        with pytest.raises(SheetArchived):
            await sheet_repo.delete_sheet(s, sid)


@pytest.mark.asyncio
async def test_write_on_archived_sheet_progress_paths_guarded():
    """progress 路径写函数（contribute/set_row_progress）在 archived 上同样守卫。"""
    # Arrange：建表 + progress 行，然后归档
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        row = await sheet_repo.upsert_row(s, sheet.id, "x", 10, 1, 0)
        await s.commit()
        sid, rid = sheet.id, row.id
    async with async_session_factory() as s:
        await sheet_repo.advance_sheet(
            s,
            sid,
            sheet_repo.SHEET_PHASE_ARCHIVED,
            archived_path=f"projects/{sid}/index.md",
        )
        await s.commit()

    alice = await _seed_player("alice")
    async with async_session_factory() as s:
        with pytest.raises(SheetArchived):
            await sheet_repo.contribute_row(s, sid, rid, alice, 1)
    async with async_session_factory() as s:
        with pytest.raises(SheetArchived):
            await sheet_repo.set_row_progress(s, sid, rid, 5)
    async with async_session_factory() as s:
        with pytest.raises(SheetArchived):
            await sheet_repo.release_row(s, sid, rid)


# ---------- list_sheets status_filter ----------


@pytest.mark.asyncio
async def test_list_sheets_filter_active():
    """status=active 返 collecting+constructing，不返 archived。"""
    sid1 = await _make_sheet("A")  # collecting
    sid2 = await _make_sheet("B")  # collecting → constructing
    sid3 = await _make_sheet("C")  # → archived
    async with async_session_factory() as s:
        await sheet_repo.advance_sheet(
            s, sid2, sheet_repo.SHEET_PHASE_CONSTRUCTING
        )
        await sheet_repo.advance_sheet(
            s,
            sid3,
            sheet_repo.SHEET_PHASE_ARCHIVED,
            archived_path=f"projects/{sid3}/index.md",
        )
        await s.commit()

    async with async_session_factory() as s:
        active = await sheet_repo.list_sheets(s, status_filter="active")
        titles = {sh.title for sh, _ in active}
    assert titles == {"A", "B"}  # C 已 archived 不返


@pytest.mark.asyncio
async def test_list_sheets_filter_archived():
    sid1 = await _make_sheet("A")  # collecting
    sid2 = await _make_sheet("B")  # → archived
    async with async_session_factory() as s:
        await sheet_repo.advance_sheet(
            s,
            sid2,
            sheet_repo.SHEET_PHASE_ARCHIVED,
            archived_path=f"projects/{sid2}/index.md",
        )
        await s.commit()

    async with async_session_factory() as s:
        archived = await sheet_repo.list_sheets(s, status_filter="archived")
        titles = {sh.title for sh, _ in archived}
    assert titles == {"B"}


@pytest.mark.asyncio
async def test_list_sheets_filter_combined_with_owner():
    """status_filter 与 owner_uuid 可组合。"""
    owner_a = await _seed_player("alice")
    owner_b = await _seed_player("bob")
    async with async_session_factory() as s:
        await sheet_repo.create_sheet(s, owner_a, "A1")  # active
        await sheet_repo.create_sheet(s, owner_b, "B1")  # active
        a2 = await sheet_repo.create_sheet(s, owner_a, "A2")  # → archived
        await sheet_repo.advance_sheet(
            s,
            a2.id,
            sheet_repo.SHEET_PHASE_ARCHIVED,
            archived_path=f"projects/{a2.id}/index.md",
        )
        await s.commit()

    # owner_a 的 active 表
    async with async_session_factory() as s:
        active_a = await sheet_repo.list_sheets(
            s, owner_uuid=owner_a, status_filter="active"
        )
        assert {sh.title for sh, _ in active_a} == {"A1"}
    # owner_a 的 archived 表
    async with async_session_factory() as s:
        archived_a = await sheet_repo.list_sheets(
            s, owner_uuid=owner_a, status_filter="archived"
        )
        assert {sh.title for sh, _ in archived_a} == {"A2"}
    # owner_b 的 archived（空）
    async with async_session_factory() as s:
        archived_b = await sheet_repo.list_sheets(
            s, owner_uuid=owner_b, status_filter="archived"
        )
        assert archived_b == []


@pytest.mark.asyncio
async def test_list_sheets_no_filter_returns_all_including_archived():
    """status_filter=None 不过滤，archived 也返（保持历史行为）。"""
    sid1 = await _make_sheet("A")
    sid2 = await _make_sheet("B")
    async with async_session_factory() as s:
        await sheet_repo.advance_sheet(
            s,
            sid2,
            sheet_repo.SHEET_PHASE_ARCHIVED,
            archived_path=f"projects/{sid2}/index.md",
        )
        await s.commit()

    async with async_session_factory() as s:
        all_sheets = await sheet_repo.list_sheets(s)
        assert {sh.title for sh, _ in all_sheets} == {"A", "B"}


# ---------- aggregate_contributor_totals（精确贡献量排行） ----------


async def _seed_named_player(name: str) -> uuid.UUID:
    """seed player 并设 current_name（aggregate 排序兜底用名字）。"""
    u = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=u, current_name=name))
        await s.commit()
    return u


@pytest.mark.asyncio
async def test_aggregate_contributor_totals_sums_per_player():
    # Arrange：两个 progress 行；alice 在两行各上交 → 应合并总量
    sid = await _make_sheet("聚合")
    alice = await _seed_named_player("alice")
    bob = await _seed_named_player("bob")
    async with async_session_factory() as s:
        rid1 = await sheet_repo.upsert_row(s, sid, "圆石", 100, sheet_repo.MODE_PROGRESS, 0)
        rid2 = await sheet_repo.upsert_row(s, sid, "铁锭", 100, sheet_repo.MODE_PROGRESS, 1)
        await sheet_repo.contribute_row(s, sid, rid1.id, alice, 30)
        await sheet_repo.contribute_row(s, sid, rid1.id, bob, 10)
        await sheet_repo.contribute_row(s, sid, rid2.id, alice, 50)  # alice 跨行再 +50
        await s.commit()
    # Act
    async with async_session_factory() as s:
        totals = await sheet_repo.aggregate_contributor_totals(s, sid)
    # Assert：alice=80（30+50），bob=10
    by_name = {name: qty for _u, name, qty in totals}
    assert by_name == {"alice": 80, "bob": 10}


@pytest.mark.asyncio
async def test_aggregate_contributor_totals_orders_by_qty_desc():
    # Arrange：alice 100（最高）> bob 50 > carol 50（同票，名字 bob<carol 升序）
    sid = await _make_sheet("排序")
    alice = await _seed_named_player("alice")
    bob = await _seed_named_player("bob")
    carol = await _seed_named_player("carol")
    async with async_session_factory() as s:
        r1 = await sheet_repo.upsert_row(s, sid, "A", 999, sheet_repo.MODE_PROGRESS, 0)
        await sheet_repo.contribute_row(s, sid, r1.id, alice, 100)
        await sheet_repo.contribute_row(s, sid, r1.id, bob, 50)
        await sheet_repo.contribute_row(s, sid, r1.id, carol, 50)
        await s.commit()
    # Act
    async with async_session_factory() as s:
        totals = await sheet_repo.aggregate_contributor_totals(s, sid)
    # Assert：顺序 alice, bob, carol
    names = [name for _u, name, _q in totals]
    assert names == ["alice", "bob", "carol"]


@pytest.mark.asyncio
async def test_aggregate_contributor_totals_empty():
    # Arrange：表无 progress 行 / 无贡献者
    sid = await _make_sheet("空")
    # Act
    async with async_session_factory() as s:
        totals = await sheet_repo.aggregate_contributor_totals(s, sid)
    # Assert
    assert totals == []


@pytest.mark.asyncio
async def test_aggregate_contributor_totals_lock_claimed_not_delivered_excluded():
    # Arrange：lock 行认领但 delivered=0（仅 claim）→ HAVING>0 剔除；只 progress 上交者入榜
    sid = await _make_sheet("lock 未交付")
    alice = await _seed_named_player("alice")
    claimant = await _seed_named_player("claimant")
    async with async_session_factory() as s:
        lock_row = await sheet_repo.upsert_row(s, sid, "锁", 64, sheet_repo.MODE_LOCK, 0)
        prog_row = await sheet_repo.upsert_row(s, sid, "进", 64, sheet_repo.MODE_PROGRESS, 1)
        # lock 仅认领（claimant 不进 contributors 表，delivered_qty=0 → 不计入）
        await sheet_repo.claim_row(s, sid, lock_row.id, claimant)
        await sheet_repo.contribute_row(s, sid, prog_row.id, alice, 7)
        await s.commit()
    # Act
    async with async_session_factory() as s:
        totals = await sheet_repo.aggregate_contributor_totals(s, sid)
    # Assert：只有 alice（claimant 仅 claim 未交付，delivered=0 被剔除）
    assert totals == [(alice, "alice", 7)]


@pytest.mark.asyncio
async def test_aggregate_contributor_totals_includes_lock_delivered():
    # Arrange：lock 行认领并交付 → claimant 按 delivered_qty 计入；progress 上交者各自计
    sid = await _make_sheet("lock 已交付")
    claimant = await _seed_named_player("claimant")
    alice = await _seed_named_player("alice")
    bob = await _seed_named_player("bob")
    async with async_session_factory() as s:
        lock_row = await sheet_repo.upsert_row(s, sid, "锁", 64, sheet_repo.MODE_LOCK, 0)
        prog_row = await sheet_repo.upsert_row(s, sid, "进", 100, sheet_repo.MODE_PROGRESS, 1)
        await sheet_repo.claim_row(s, sid, lock_row.id, claimant)
        await sheet_repo.set_row_delivery(s, sid, lock_row.id, 64)  # claimant 交付 64
        await sheet_repo.contribute_row(s, sid, prog_row.id, alice, 30)
        await sheet_repo.contribute_row(s, sid, prog_row.id, bob, 10)
        await s.commit()
    # Act
    async with async_session_factory() as s:
        totals = await sheet_repo.aggregate_contributor_totals(s, sid)
    # Assert：claimant=64（lock 交付）最高，alice=30，bob=10
    assert totals == [(claimant, "claimant", 64), (alice, "alice", 30), (bob, "bob", 10)]


@pytest.mark.asyncio
async def test_aggregate_contributor_totals_merges_lock_and_progress_same_player():
    # Arrange：同一玩家既是 lock claimant（交付）又是 progress 贡献者 → 两支合并
    sid = await _make_sheet("合并")
    alice = await _seed_named_player("alice")
    async with async_session_factory() as s:
        lock_row = await sheet_repo.upsert_row(s, sid, "锁", 10, sheet_repo.MODE_LOCK, 0)
        prog_row = await sheet_repo.upsert_row(s, sid, "进", 100, sheet_repo.MODE_PROGRESS, 1)
        await sheet_repo.claim_row(s, sid, lock_row.id, alice)
        await sheet_repo.set_row_delivery(s, sid, lock_row.id, 10)  # lock 交付 10
        await sheet_repo.contribute_row(s, sid, prog_row.id, alice, 25)  # progress 上交 25
        await s.commit()
    # Act
    async with async_session_factory() as s:
        totals = await sheet_repo.aggregate_contributor_totals(s, sid)
    # Assert：alice=35（lock 10 + progress 25 合并）
    assert totals == [(alice, "alice", 35)]


@pytest.mark.asyncio
async def test_list_sheets_involved_first_ordering():
    """参与优先排序：玩家参与的表（owner/claimant/contributor）排在前面，组内按 id 升序。"""
    # Arrange：创建多个玩家和表
    alice = await _seed_player("alice")
    bob = await _seed_player("bob")
    carol = await _seed_player("carol")

    async with async_session_factory() as s:
        # 创建 5 张表：1(alice所有), 2(alice所有), 3(bob所有), 4(bob所有), 5(carol所有)
        s1 = await sheet_repo.create_sheet(s, alice, "Alice表1")
        s2 = await sheet_repo.create_sheet(s, alice, "Alice表2")
        s3 = await sheet_repo.create_sheet(s, bob, "Bob表1")
        s4 = await sheet_repo.create_sheet(s, bob, "Bob表2")
        s5 = await sheet_repo.create_sheet(s, carol, "Carol表1")
        await s.commit()

    # 让 alice 参与 s3（作为 claimant）
    async with async_session_factory() as s:
        row = await sheet_repo.upsert_row(s, s3.id, "stone", 64, sheet_repo.MODE_LOCK, 0)
        await sheet_repo.claim_row(s, s3.id, row.id, alice)
        await s.commit()

    # 让 alice 参与 s4（作为 contributor）
    async with async_session_factory() as s:
        row = await sheet_repo.upsert_row(s, s4.id, "dirt", 64, sheet_repo.MODE_PROGRESS, 0)
        await sheet_repo.contribute_row(s, s4.id, row.id, alice, 10)
        await s.commit()

    # Act：查询 alice 的列表（player_uuids=[alice]；账号级聚合传 UUID 列表）
    async with async_session_factory() as s:
        sheets = await sheet_repo.list_sheets(s, player_uuids=[alice])

    # Assert：alice 参与的表（s1, s2, s3, s4）应在前面，未参与的（s5）在后面
    # 组内按 id 升序
    sheet_ids = [sh.id for sh, _ in sheets]
    # 参与的：1, 2, 3, 4 → 应在前
    # 未参与的：5 → 应在后
    involved = {s1.id, s2.id, s3.id, s4.id}
    not_involved = {s5.id}

    # 前 4 个应该是参与的表
    assert set(sheet_ids[:4]) == involved
    # 最后一个应该是未参与的表
    assert sheet_ids[4] in not_involved
    # 参与的表内部按 id 升序
    involved_ids = [sid for sid in sheet_ids if sid in involved]
    assert involved_ids == sorted(involved_ids)

    # 验证不传 player_uuid 时按 id 升序（向后兼容）
    async with async_session_factory() as s:
        sheets_no_uuid = await sheet_repo.list_sheets(s)
    sheet_ids_no_uuid = [sh.id for sh, _ in sheets_no_uuid]
    assert sheet_ids_no_uuid == sorted(sheet_ids_no_uuid)  # 全按 id 升序


# ---------- 子物品嵌套行（0012） ----------
async def _make_sheet_with_row(need_qty: int = 10, mode: int = 0) -> tuple[int, int]:
    """seed 一张表 + 一行（open），返回 (sheet_id, row_id)。"""
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        row = await sheet_repo.create_row(s, sheet.id, "x", need_qty=need_qty, mode=mode, sort_order=0)
        await s.commit()
        return sheet.id, row.id


@pytest.mark.asyncio
async def test_create_row_sub_item_requires_parent():
    """子物品：parent_row_id 必须指向存在的顶层行（单层校验）。"""
    sid, parent_rid = await _make_sheet_with_row()
    async with async_session_factory() as s:
        # 子行挂在顶层父行上 → 成功
        child = await sheet_repo.create_row(
            s, sid, "child", need_qty=0, mode=0, sort_order=0,
            registry_id="minecraft:stone", parent_row_id=parent_rid, qty_per_unit=2
        )
        await s.commit()
        assert child.parent_row_id == parent_rid
        assert child.qty_per_unit == 2
        assert child.need_qty == 20  # 2 × 10（父 need）


@pytest.mark.asyncio
async def test_create_row_sub_item_parent_must_be_top_level():
    """子物品：父行必须是顶层（parent.parent_row_id IS NULL），否则 IntegrityError。"""
    sid, parent_rid = await _make_sheet_with_row()
    async with async_session_factory() as s:
        # 先建一个子行
        child1 = await sheet_repo.create_row(
            s, sid, "child1", need_qty=0, mode=0, sort_order=0,
            registry_id="minecraft:stone", parent_row_id=parent_rid, qty_per_unit=1
        )
        await s.commit()
    # 尝试挂在子行上（单层违反）
    async with async_session_factory() as s:
        with pytest.raises(ValueError):
            await sheet_repo.create_row(
                s, sid, "grandchild", need_qty=0, mode=0, sort_order=0,
                registry_id="minecraft:dirt", parent_row_id=child1.id, qty_per_unit=1
            )


@pytest.mark.asyncio
async def test_create_row_sub_item_requires_registry_id():
    """子物品：registry_id 必填（否则 IntegrityError）。"""
    sid, parent_rid = await _make_sheet_with_row()
    async with async_session_factory() as s:
        with pytest.raises(ValueError):
            await sheet_repo.create_row(
                s, sid, "child", need_qty=0, mode=0, sort_order=0,
                registry_id=None, parent_row_id=parent_rid, qty_per_unit=1
            )


@pytest.mark.asyncio
async def test_create_row_sub_item_qty_per_unit_must_be_positive():
    """子物品：qty_per_unit 必须 > 0（0/负数 → ValueError）。"""
    sid, parent_rid = await _make_sheet_with_row()
    async with async_session_factory() as s:
        with pytest.raises(ValueError):
            await sheet_repo.create_row(
                s, sid, "child", need_qty=0, mode=0, sort_order=0,
                registry_id="minecraft:stone", parent_row_id=parent_rid, qty_per_unit=0
            )


@pytest.mark.asyncio
async def test_create_row_sub_item_inherits_mode_from_lock_parent():
    """子物品：父 lock（mode=0）→ 强制子 lock。"""
    sid, parent_rid = await _make_sheet_with_row(need_qty=10, mode=0)  # lock
    async with async_session_factory() as s:
        # 尝试验 mode=1（progress），但被强制为 lock
        child = await sheet_repo.create_row(
            s, sid, "child", need_qty=0, mode=1, sort_order=0,  # 传 mode=1，预期被覆盖
            registry_id="minecraft:stone", parent_row_id=parent_rid, qty_per_unit=2
        )
        await s.commit()
        assert child.mode == 0  # 被强制为 lock


@pytest.mark.asyncio
async def test_create_row_sub_item_inherits_mode_from_progress_parent():
    """子物品：父 progress（mode=1）→ 缺省继承父 mode（可显式指定）。"""
    sid, parent_rid = await _make_sheet_with_row(need_qty=10, mode=1)  # progress
    async with async_session_factory() as s:
        # 不传 mode，缺省继承父 progress
        child = await sheet_repo.create_row(
            s, sid, "child", need_qty=0, mode=None, sort_order=0,  # mode=None → 继承父
            registry_id="minecraft:stone", parent_row_id=parent_rid, qty_per_unit=2
        )
        await s.commit()
        assert child.mode == 1  # 继承 progress


@pytest.mark.asyncio
async def test_update_row_parent_need_change_cascades_to_children():
    """级联：父行 need 变 → 子行 need 重算（= qty_per_unit × 新父 need）。"""
    sid, parent_rid = await _make_sheet_with_row(need_qty=10, mode=0)
    child_rid = None
    async with async_session_factory() as s:
        child = await sheet_repo.create_row(
            s, sid, "child", need_qty=0, mode=0, sort_order=0,
            registry_id="minecraft:stone", parent_row_id=parent_rid, qty_per_unit=2
        )
        child_rid = child.id
        await s.commit()
        assert child.need_qty == 20  # 2 × 10
    # 修改父行 need
    async with async_session_factory() as s:
        updated_parent = await sheet_repo.update_row(s, sid, parent_rid, need_qty=5)
        await s.commit()
        assert updated_parent.need_qty == 5
    # 验证子行 need 被重算
    async with async_session_factory() as s:
        rows = await sheet_repo.list_rows(s, sid)
        child_row = next((r for r, _ in rows if r.id == child_rid), None)
        assert child_row is not None
        assert child_row.need_qty == 10  # 2 × 5（新父 need）


@pytest.mark.asyncio
async def test_update_row_parent_mode_to_lock_forces_children_lock():
    """级联：父行 mode 切 lock → 非 lock 子行强制 lock + 重置协作（贡献者清空）。"""
    sid, parent_rid = await _make_sheet_with_row(need_qty=10, mode=1)  # progress
    contributor = await _seed_player("bob")
    child_rid = None
    async with async_session_factory() as s:
        child = await sheet_repo.create_row(
            s, sid, "child", need_qty=0, mode=1, sort_order=0,  # 子行 progress
            registry_id="minecraft:stone", parent_row_id=parent_rid, qty_per_unit=2
        )
        child_rid = child.id
        # 子行贡献（progress 模式用 contribute）
        await sheet_repo.contribute_row(s, sid, child_rid, contributor, 5)
        await s.commit()
        assert child.delivered_qty == 5
    # 父行切 lock
    async with async_session_factory() as s:
        updated_parent = await sheet_repo.update_row(s, sid, parent_rid, mode=0)
        await s.commit()
        assert updated_parent.mode == 0
    # 验证子行被强制 lock + 重置
    async with async_session_factory() as s:
        child_result = await sheet_repo.get_row(s, sid, child_rid)
        assert child_result is not None
        child_row, _ = child_result
        assert child_row.mode == 0  # 强制 lock
        assert child_row.delivered_qty == 0  # 交付被清
    # 验证贡献者被清空
    async with async_session_factory() as s:
        contrib_map = await sheet_repo.list_contributors(s, [child_rid])
        # 没有贡献者时，字典中不包含该键
        assert child_rid not in contrib_map or contrib_map.get(child_rid) == []


@pytest.mark.asyncio
async def test_update_row_child_qty_per_unit_change_recomputes_need():
    """子物品：子行 qty_per_unit 变 → need 重算（= qty_per_unit × 父 need）。"""
    sid, parent_rid = await _make_sheet_with_row(need_qty=10, mode=0)
    child_rid = None
    async with async_session_factory() as s:
        child = await sheet_repo.create_row(
            s, sid, "child", need_qty=0, mode=0, sort_order=0,
            registry_id="minecraft:stone", parent_row_id=parent_rid, qty_per_unit=2
        )
        child_rid = child.id
        await s.commit()
        assert child.need_qty == 20  # 2 × 10
    # 修改子行 qty_per_unit
    async with async_session_factory() as s:
        updated_child = await sheet_repo.update_row(s, sid, child_rid, qty_per_unit=3)
        await s.commit()
        assert updated_child.qty_per_unit == 3
        assert updated_child.need_qty == 30  # 3 × 10


@pytest.mark.asyncio
async def test_update_row_child_qty_change_recomputes_status_when_need_exceeds_delivered():
    """HIGH-1：progress 子行已备齐(done) → 调大 qty_per_unit 派生出新 need 超过已交付量，
    状态须从 done 回退到 claimed。need 变化判定必须基于 row.need_qty 实际值，
    否则派生变化（need_qty 参数为 None）漏触发 _recompute_after_edit → 卡死在 done。
    """
    sid, parent_rid = await _make_sheet_with_row(need_qty=10, mode=1)  # progress 父
    contributor = await _seed_player("bob")
    child_rid = None
    async with async_session_factory() as s:
        child = await sheet_repo.create_row(
            s, sid, "child", need_qty=0, mode=1, sort_order=0,  # progress 子行
            registry_id="minecraft:stone", parent_row_id=parent_rid, qty_per_unit=2
        )
        child_rid = child.id
        # 交满 → done（delivered=20 == need=2×10）
        await sheet_repo.contribute_row(s, sid, child_rid, contributor, 20)
        await s.commit()
        assert child.delivered_qty == 20
        assert child.status == "done"
    # 调大 qty_per_unit → need 派生重算为 30（3×10），已交付 20 < 30 → 应回退 claimed
    async with async_session_factory() as s:
        updated_child = await sheet_repo.update_row(s, sid, child_rid, qty_per_unit=3)
        await s.commit()
        assert updated_child.need_qty == 30  # 3 × 10
        assert updated_child.delivered_qty == 20  # 进度保留（mode 不变只重算状态）
        assert updated_child.status == "claimed"  # done → claimed 回退（修复前卡 done）


@pytest.mark.asyncio
async def test_list_rows_groups_children_after_parent():
    """list_rows 分组排序：父行优先，子行紧跟父行，同组内按 sort_order。"""
    owner = await _seed_player()
    async with async_session_factory() as s:
        sid = (await sheet_repo.create_sheet(s, owner, "S")).id
        # 父行 sort_order=1
        parent = await sheet_repo.create_row(s, sid, "父", need_qty=10, mode=0, sort_order=1)
        await s.commit()
        parent_rid = parent.id
    # 建两个子行（sort_order 不同）
    async with async_session_factory() as s:
        child1 = await sheet_repo.create_row(
            s, sid, "子1", need_qty=0, mode=0, sort_order=2,  # sort_order=2
            registry_id="minecraft:stone", parent_row_id=parent_rid, qty_per_unit=1
        )
        child2 = await sheet_repo.create_row(
            s, sid, "子2", need_qty=0, mode=0, sort_order=1,  # sort_order=1
            registry_id="minecraft:dirt", parent_row_id=parent_rid, qty_per_unit=1
        )
        await s.commit()
    # 验证排序：父、子2(sort=1)、子1(sort=2)；子行 item_name 自动加父名前缀「父-」
    async with async_session_factory() as s:
        rows = await sheet_repo.list_rows(s, sid)
        item_names = [r.item_name for r, _ in rows]
        assert item_names == ["父", "父-子2", "父-子1"]


@pytest.mark.asyncio
async def test_create_row_sub_item_float_qty_per_unit_ceils_need():
    """子物品：浮点倍数 → need = ceil(qty_per_unit × 父 need)，向上取整保够用（0.5×7=3.5→4）。"""
    sid, parent_rid = await _make_sheet_with_row(need_qty=7, mode=0)
    async with async_session_factory() as s:
        child = await sheet_repo.create_row(
            s, sid, "child", need_qty=0, mode=0, sort_order=0,
            registry_id="minecraft:stone", parent_row_id=parent_rid, qty_per_unit=0.5,
        )
        await s.commit()
        assert float(child.qty_per_unit) == 0.5
        assert child.need_qty == 4  # ceil(0.5 × 7) = ceil(3.5) = 4


@pytest.mark.asyncio
async def test_create_row_sub_item_name_gets_parent_prefix():
    """子物品：子行 item_name 自动加父名前缀「父名-本名」（flat 视图 CSV/MCDR 消歧）。"""
    sid, parent_rid = await _make_sheet_with_row()  # 父行 item_name="x"
    async with async_session_factory() as s:
        child = await sheet_repo.create_row(
            s, sid, "木板", need_qty=0, mode=0, sort_order=0,
            registry_id="minecraft:oak_planks", parent_row_id=parent_rid, qty_per_unit=2,
        )
        await s.commit()
        assert child.item_name == "x-木板"  # 父名 "x" + "-" + "木板"


def test_sort_sheet_rows_keeps_children_under_parent():
    """sort_sheet_rows：子行恒紧跟其父——即使子行优先级更高（档1）也不排到父行（档3）上方。

    复现用户 bug「子 #1877 排在父 #1700 上方」：旧 flat 排序按 (priority,...) 忽略
    parent_row_id；新实现仅父行参与主排序、子行紧随其父。
    """
    from types import SimpleNamespace
    from uuid import UUID

    from app.services.sheet_row_order import sort_sheet_rows

    viewer = UUID("00000000-0000-0000-0000-000000000001")

    def mk(rid, name, *, parent=None, mode=0, status="open", claimant=None,
           need=10, delivered=0, sort=0):
        return SimpleNamespace(
            id=rid, item_name=name, parent_row_id=parent, mode=mode, status=status,
            claimant_uuid=claimant, need_qty=need, delivered_qty=delivered, sort_order=sort,
        )

    parent = mk(1, "父", mode=0, status="open", claimant=None)   # other-lock → 档 3
    child = mk(2, "子", parent=1, mode=1, status="open", need=5)  # my-progress → 档 1
    rows = [(parent, None), (child, None)]

    out = sort_sheet_rows(rows, {viewer}, my_row_ids={2})
    assert [r.item_name for r, _ in out] == ["父", "子"]  # 子紧跟父，不排到上方


# ---------- claim_row / release_row 子物品级联（0012） ----------


@pytest.mark.asyncio
async def test_claim_top_lock_parent_cascades_to_children():
    """认领顶层 lock 父行 → 父 + 所有子行均 claimed、同 claimant_uuid、delivered_qty==0。"""
    owner = await _seed_player()
    claimant = await _seed_player("bob")
    async with async_session_factory() as s:
        sid = (await sheet_repo.create_sheet(s, owner, "S")).id
        parent = await sheet_repo.create_row(
            s, sid, "父", need_qty=10, mode=sheet_repo.MODE_LOCK, sort_order=0
        )
        await s.commit()
        parent_rid = parent.id
    # 建两个子行（一个 open，一个 也 open，都会被级联）
    async with async_session_factory() as s:
        child1 = await sheet_repo.create_row(
            s, sid, "子1", need_qty=0, mode=sheet_repo.MODE_LOCK, sort_order=0,
            registry_id="minecraft:stone", parent_row_id=parent_rid, qty_per_unit=1
        )
        child2 = await sheet_repo.create_row(
            s, sid, "子2", need_qty=0, mode=sheet_repo.MODE_LOCK, sort_order=1,
            registry_id="minecraft:dirt", parent_row_id=parent_rid, qty_per_unit=2
        )
        await s.commit()
        child1_rid, child2_rid = child1.id, child2.id
    # 认领父行
    async with async_session_factory() as s:
        row = await sheet_repo.claim_row(s, sid, parent_rid, claimant)
        await s.commit()
    # 验证父行 + 子行均为 claimed，同 claimant
    async with async_session_factory() as s:
        rows = await sheet_repo.list_rows(s, sid)
        rows_by_id = {r.id: r for r, _ in rows}
        assert rows_by_id[parent_rid].status == "claimed"
        assert rows_by_id[parent_rid].claimant_uuid == claimant
        assert rows_by_id[parent_rid].delivered_qty == 0
        # 子行级联 claimed
        assert rows_by_id[child1_rid].status == "claimed"
        assert rows_by_id[child1_rid].claimant_uuid == claimant
        assert rows_by_id[child1_rid].delivered_qty == 0
        assert rows_by_id[child2_rid].status == "claimed"
        assert rows_by_id[child2_rid].claimant_uuid == claimant
        assert rows_by_id[child2_rid].delivered_qty == 0


@pytest.mark.asyncio
async def test_claim_lock_child_raises_conflict():
    """认领「lock 父行」的子行 → 抛 SheetRowConflict（不得单独认领）。"""
    owner = await _seed_player()
    claimant = await _seed_player("bob")
    async with async_session_factory() as s:
        sid = (await sheet_repo.create_sheet(s, owner, "S")).id
        parent = await sheet_repo.create_row(
            s, sid, "父", need_qty=10, mode=sheet_repo.MODE_LOCK, sort_order=0
        )
        child = await sheet_repo.create_row(
            s, sid, "子", need_qty=0, mode=sheet_repo.MODE_LOCK, sort_order=0,
            registry_id="minecraft:stone", parent_row_id=parent.id, qty_per_unit=1
        )
        await s.commit()
        child_rid = child.id
    # 尝试认领子行 → 抛冲突
    async with async_session_factory() as s:
        with pytest.raises(SheetRowConflict, match="子物品随父行认领"):
            await sheet_repo.claim_row(s, sid, child_rid, claimant)


@pytest.mark.asyncio
async def test_claim_lock_child_under_progress_parent_succeeds():
    """认领「progress 父行」下的 lock 子行 → 子行 claimed、父行 status 不变（仍 open）。"""
    owner = await _seed_player()
    claimant = await _seed_player("bob")
    async with async_session_factory() as s:
        sid = (await sheet_repo.create_sheet(s, owner, "S")).id
        parent = await sheet_repo.create_row(
            s, sid, "父", need_qty=10, mode=sheet_repo.MODE_PROGRESS, sort_order=0
        )
        child = await sheet_repo.create_row(
            s, sid, "子", need_qty=0, mode=sheet_repo.MODE_LOCK, sort_order=0,
            registry_id="minecraft:stone", parent_row_id=parent.id, qty_per_unit=1
        )
        await s.commit()
        parent_rid, child_rid = parent.id, child.id
    # 认领子行（父是 progress，放行）
    async with async_session_factory() as s:
        row = await sheet_repo.claim_row(s, sid, child_rid, claimant)
        await s.commit()
        assert row.status == "claimed"
        assert row.claimant_uuid == claimant
    # 验证父行 status 仍 open（未被级联影响）
    async with async_session_factory() as s:
        rows = await sheet_repo.list_rows(s, sid)
        rows_by_id = {r.id: r for r, _ in rows}
        assert rows_by_id[parent_rid].status == "open"
        assert rows_by_id[child_rid].status == "claimed"
        assert rows_by_id[child_rid].claimant_uuid == claimant


@pytest.mark.asyncio
async def test_release_top_lock_parent_cascades_to_children():
    """解除顶层 lock 父行 → 父 + 子行均 open、claimant_uuid is None。"""
    owner = await _seed_player()
    claimant = await _seed_player("bob")
    async with async_session_factory() as s:
        sid = (await sheet_repo.create_sheet(s, owner, "S")).id
        parent = await sheet_repo.create_row(
            s, sid, "父", need_qty=10, mode=sheet_repo.MODE_LOCK, sort_order=0
        )
        await s.commit()
        parent_rid = parent.id
    # 建子行 + 认领父行（级联）
    async with async_session_factory() as s:
        child = await sheet_repo.create_row(
            s, sid, "子", need_qty=0, mode=sheet_repo.MODE_LOCK, sort_order=0,
            registry_id="minecraft:stone", parent_row_id=parent_rid, qty_per_unit=1
        )
        await sheet_repo.claim_row(s, sid, parent_rid, claimant)
        await s.commit()
        child_rid = child.id
    # 解除父行
    async with async_session_factory() as s:
        row = await sheet_repo.release_row(s, sid, parent_rid)
        await s.commit()
    # 验证父行 + 子行均为 open，claimant 清空
    async with async_session_factory() as s:
        rows = await sheet_repo.list_rows(s, sid)
        rows_by_id = {r.id: r for r, _ in rows}
        assert rows_by_id[parent_rid].status == "open"
        assert rows_by_id[parent_rid].claimant_uuid is None
        assert rows_by_id[parent_rid].delivered_qty == 0
        # 子行级联 open
        assert rows_by_id[child_rid].status == "open"
        assert rows_by_id[child_rid].claimant_uuid is None
        assert rows_by_id[child_rid].delivered_qty == 0


@pytest.mark.asyncio
async def test_release_lock_child_raises_conflict():
    """解除「lock 父行」的子行 → 抛 SheetRowConflict（不得单独解除）。"""
    owner = await _seed_player()
    claimant = await _seed_player("bob")
    async with async_session_factory() as s:
        sid = (await sheet_repo.create_sheet(s, owner, "S")).id
        parent = await sheet_repo.create_row(
            s, sid, "父", need_qty=10, mode=sheet_repo.MODE_LOCK, sort_order=0
        )
        child = await sheet_repo.create_row(
            s, sid, "子", need_qty=0, mode=sheet_repo.MODE_LOCK, sort_order=0,
            registry_id="minecraft:stone", parent_row_id=parent.id, qty_per_unit=1
        )
        await s.commit()
        child_rid = child.id
    # 认领父行（子行级联 claimed）
    async with async_session_factory() as s:
        await sheet_repo.claim_row(s, sid, parent.id, claimant)
        await s.commit()
    # 尝试解除子行 → 抛冲突
    async with async_session_factory() as s:
        with pytest.raises(SheetRowConflict, match="子物品随父行解除"):
            await sheet_repo.release_row(s, sid, child_rid)


# ---------- D2：浮点 need_qty Decimal 精确计算 ----------
@pytest.mark.asyncio
async def test_create_row_sub_item_decimal_qty_avoids_float_overcount():
    """D2：浮点倍数用 Decimal 精确计算——0.07 × 100 = 7（旧 float 路径 ceil(7.000...001)=8）。"""
    # Arrange
    sid, parent_rid = await _make_sheet_with_row(
        need_qty=100, mode=sheet_repo.MODE_LOCK
    )
    # Act
    async with async_session_factory() as s:
        child = await sheet_repo.create_row(
            s, sid, "child", need_qty=0, mode=sheet_repo.MODE_LOCK, sort_order=0,
            registry_id="minecraft:stone", parent_row_id=parent_rid, qty_per_unit=0.07,
        )
        await s.commit()
    # Assert：0.07 × 100 精确等于 7，不再因 float 误差多算到 8
    assert child.need_qty == 7


@pytest.mark.asyncio
async def test_update_row_child_qty_decimal_recompute_avoids_float_overcount():
    """D2：update 路径 qty_per_unit 变用 Decimal 重算——0.07 × 100 = 7（非 float 的 8）。"""
    # Arrange：父 need=100，子行初始 qty=1（need=100）
    sid, parent_rid = await _make_sheet_with_row(
        need_qty=100, mode=sheet_repo.MODE_LOCK
    )
    async with async_session_factory() as s:
        child = await sheet_repo.create_row(
            s, sid, "child", need_qty=0, mode=sheet_repo.MODE_LOCK, sort_order=0,
            registry_id="minecraft:stone", parent_row_id=parent_rid, qty_per_unit=1,
        )
        await s.commit()
        child_rid = child.id
    # Act：改 qty_per_unit=0.07 → need 重算
    async with async_session_factory() as s:
        updated = await sheet_repo.update_row(s, sid, child_rid, qty_per_unit=0.07)
        await s.commit()
    # Assert：0.07 × 100 = 7（Decimal 精确，非 float 路径的 8）
    assert updated.need_qty == 7


# ---------- D3：reparent 后重算 need_qty ----------
@pytest.mark.asyncio
async def test_update_row_reparent_top_to_child_recomputes_need():
    """D3：顶层行 reparent 成子行 → need 用新 parent 重算（旧路径读旧 parent=None 跳过，留旧值）。"""
    # Arrange：parent need=10；顶层行 need=100（带 registry_id，将挂为子行）
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        parent = await sheet_repo.create_row(
            s, sheet.id, "父", need_qty=10, mode=sheet_repo.MODE_LOCK, sort_order=0,
        )
        top = await sheet_repo.create_row(
            s, sheet.id, "顶层", need_qty=100, mode=sheet_repo.MODE_LOCK, sort_order=1,
            registry_id="minecraft:stone",
        )
        await s.commit()
        sid, parent_rid, top_rid = sheet.id, parent.id, top.id
    # Act：把顶层行挂到 parent 下，qty_per_unit=2 → need 应为 2×10=20（非旧值 100）
    async with async_session_factory() as s:
        updated = await sheet_repo.update_row(
            s, sid, top_rid, parent_row_id=parent_rid, qty_per_unit=2,
        )
        await s.commit()
    # Assert
    assert updated.parent_row_id == parent_rid
    assert updated.need_qty == 20  # 2 × 新 parent.need_qty(10)


@pytest.mark.asyncio
async def test_update_row_reparent_child_to_new_parent_recomputes_need():
    """D3：子行换父 → need 用新 parent 重算（旧路径 qty_per_unit 未变跳过，留旧 parent 算的值）。"""
    # Arrange：两个顶层父（need=10 / need=4），子行挂父 A（qty=2 → need=20）
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        parent_a = await sheet_repo.create_row(
            s, sheet.id, "父A", need_qty=10, mode=sheet_repo.MODE_LOCK, sort_order=0,
        )
        parent_b = await sheet_repo.create_row(
            s, sheet.id, "父B", need_qty=4, mode=sheet_repo.MODE_LOCK, sort_order=1,
        )
        child = await sheet_repo.create_row(
            s, sheet.id, "子", need_qty=0, mode=sheet_repo.MODE_LOCK, sort_order=2,
            registry_id="minecraft:stone",
            parent_row_id=parent_a.id, qty_per_unit=2,
        )
        await s.commit()
        sid, parent_b_rid, child_rid = sheet.id, parent_b.id, child.id
    # Act：子行换挂父 B（need=4），qty 不变 → need 应为 2×4=8
    async with async_session_factory() as s:
        updated = await sheet_repo.update_row(
            s, sid, child_rid, parent_row_id=parent_b_rid,
        )
        await s.commit()
    # Assert
    assert updated.parent_row_id == parent_b_rid
    assert updated.need_qty == 8  # 2 × 新 parent.need_qty(4)


@pytest.mark.asyncio
async def test_update_row_reparent_with_new_qty_uses_new_parent_and_qty():
    """D3：reparent 同时改 qty_per_unit → 用新 parent × 新 qty（两条触发条件合并）。"""
    # Arrange
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        parent = await sheet_repo.create_row(
            s, sheet.id, "父", need_qty=10, mode=sheet_repo.MODE_LOCK, sort_order=0,
        )
        top = await sheet_repo.create_row(
            s, sheet.id, "顶层", need_qty=100, mode=sheet_repo.MODE_LOCK, sort_order=1,
            registry_id="minecraft:stone",
        )
        await s.commit()
        sid, parent_rid, top_rid = sheet.id, parent.id, top.id
    # Act：顶层挂父，qty=0.5 → 0.5 × 10 = 5
    async with async_session_factory() as s:
        updated = await sheet_repo.update_row(
            s, sid, top_rid, parent_row_id=parent_rid, qty_per_unit=0.5,
        )
        await s.commit()
    assert updated.parent_row_id == parent_rid
    assert updated.need_qty == 5  # 0.5 × 新 parent.need_qty(10)


# ---------- D1：update_row reparent 零校验 ----------
@pytest.mark.asyncio
async def test_update_row_self_reference_parent_raises():
    """D1：update parent_row_id 指向自身 → ValueError（自引用拦截，create 无此校验）。"""
    sid, rid = await _make_sheet_with_row()
    async with async_session_factory() as s:
        with pytest.raises(ValueError):
            await sheet_repo.update_row(
                s, sid, rid, parent_row_id=rid, qty_per_unit=1,
            )


@pytest.mark.asyncio
async def test_update_row_cross_sheet_parent_raises():
    """D1：update parent_row_id 指向另一张表的行 → ValueError（跨表拦截）。"""
    # Arrange：两张表各一个顶层行
    sid_a, rid_a = await _make_sheet_with_row()
    _sid_b, rid_b = await _make_sheet_with_row()
    # Act / Assert：rid_a 挂到 rid_b（不同表）→ raise
    async with async_session_factory() as s:
        with pytest.raises(ValueError):
            await sheet_repo.update_row(
                s, sid_a, rid_a, parent_row_id=rid_b, qty_per_unit=1,
            )


@pytest.mark.asyncio
async def test_update_row_parent_already_child_raises():
    """D1：update parent_row_id 指向已是子行的行 → ValueError（单层不变量）。"""
    # Arrange：父 + 子1（挂父）+ 另一顶层行 victim
    sid, parent_rid = await _make_sheet_with_row()
    async with async_session_factory() as s:
        child1 = await sheet_repo.create_row(
            s, sid, "子1", need_qty=0, mode=sheet_repo.MODE_LOCK, sort_order=0,
            registry_id="minecraft:stone", parent_row_id=parent_rid, qty_per_unit=1,
        )
        victim = await sheet_repo.create_row(
            s, sid, "顶层", need_qty=5, mode=sheet_repo.MODE_LOCK, sort_order=1,
            registry_id="minecraft:dirt",
        )
        await s.commit()
        child1_rid, victim_rid = child1.id, victim.id
    # Act：victim 挂到 child1（child1 已是子行）→ 多层 → raise
    async with async_session_factory() as s:
        with pytest.raises(ValueError):
            await sheet_repo.update_row(
                s, sid, victim_rid, parent_row_id=child1_rid, qty_per_unit=1,
            )


@pytest.mark.asyncio
async def test_update_row_top_to_child_without_registry_id_raises():
    """D1：顶层行（无 registry_id）挂父 → ValueError（子行必须有 registry_id）。"""
    sid, parent_rid = await _make_sheet_with_row()
    # 建一个无 registry_id 的顶层行
    async with async_session_factory() as s:
        top = await sheet_repo.create_row(
            s, sid, "顶层", need_qty=5, mode=sheet_repo.MODE_LOCK, sort_order=1,
        )
        await s.commit()
        top_rid = top.id
    # Act：top（无 registry_id）挂父 → raise（顶层→子行必须有 registry_id）
    async with async_session_factory() as s:
        with pytest.raises(ValueError):
            await sheet_repo.update_row(
                s, sid, top_rid, parent_row_id=parent_rid, qty_per_unit=1,
            )


@pytest.mark.asyncio
async def test_update_row_top_to_child_without_qty_per_unit_raises():
    """D1：顶层行挂父但未给 qty_per_unit → ValueError（子行必须有 qty_per_unit > 0）。"""
    sid, parent_rid = await _make_sheet_with_row()
    async with async_session_factory() as s:
        top = await sheet_repo.create_row(
            s, sid, "顶层", need_qty=5, mode=sheet_repo.MODE_LOCK, sort_order=1,
            registry_id="minecraft:stone",
        )
        await s.commit()
        top_rid = top.id
    # Act：有 registry_id 但没 qty_per_unit → raise
    async with async_session_factory() as s:
        with pytest.raises(ValueError):
            await sheet_repo.update_row(
                s, sid, top_rid, parent_row_id=parent_rid,
            )


@pytest.mark.asyncio
async def test_update_row_reparent_parent_with_existing_child_raises():
    """D1 收尾：把「已有子行 B 的顶层行 A」挂到另一父行 C 下 → ValueError。

    否则 A 变 C 的子行、而 B 仍挂 A → A→C 且 A→B = 两层嵌套，破坏单层不变量。
    """
    # Arrange：C 是 _make_sheet_with_row 建的顶层行，作为新父
    sid, parent_c = await _make_sheet_with_row()
    async with async_session_factory() as s:
        # 顶层行 A（带 registry_id，满足「顶层转子行」前提）
        a = await sheet_repo.create_row(
            s, sid, "A", need_qty=5, mode=sheet_repo.MODE_LOCK, sort_order=1,
            registry_id="minecraft:a",
        )
        await s.commit()
        a_id = a.id
        # A 下挂子行 B（A 现在是父行）
        await sheet_repo.create_row(
            s, sid, "B", need_qty=2, mode=sheet_repo.MODE_LOCK, sort_order=0,
            registry_id="minecraft:b", parent_row_id=a_id, qty_per_unit=2,
        )
        await s.commit()
    # Act：把 A 挂到 C 下 → A 有子 B 不能再当子行 → raise
    async with async_session_factory() as s:
        with pytest.raises(ValueError):
            await sheet_repo.update_row(
                s, sid, a_id, parent_row_id=parent_c, qty_per_unit=1,
            )
