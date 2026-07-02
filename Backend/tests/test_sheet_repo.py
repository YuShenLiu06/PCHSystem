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

from app.core.db import async_session_factory
from app.models.sheet import Sheet, SheetRow
from app.models.user import Player
from app.repositories import sheet_repo
from app.repositories.sheet_repo import SheetRowConflict


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
        "sheet_id,item_name,need_qty,mode,status,claimant_uuid,delivered_qty,sort_order"
    )
    assert lines[1] == f"{sheet.id},iron,64,0,open,,0,0"
    assert lines[2] == f"{sheet.id},gold,128,1,open,,0,1"


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
        "sheet_id,item_name,need_qty,mode,status,claimant_uuid,delivered_qty,sort_order"
    )
    body = lines[1:]
    assert f"{s1.id},a,1,0,open,,0,0" in body
    assert f"{s2.id},b,2,1,open,,0,0" in body
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
        "sheet_id,item_name,need_qty,mode,status,claimant_uuid,delivered_qty,sort_order"
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
