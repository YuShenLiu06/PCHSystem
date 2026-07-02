"""SheetRepository 测试（B2）。

连真实 PG（conftest autouse truncate 每测清库）。seed users.players 行作 owner
（FK 要求），覆盖 8 方法 + delete_sheet 级联 + upsert 新建/同名更新。

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
        fetched = await sheet_repo.get_sheet(s, sheet.id)
        assert fetched is not None
        assert fetched.title == "建材表"


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
        assert {sh.title for sh in only_a} == {"A1", "A2"}
        only_b = await sheet_repo.list_sheets(s, owner_uuid=owner_b)
        assert [sh.title for sh in only_b] == ["B1"]


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
        # sort_order 升序，同 sort_order 按 id 升序
        assert [r.item_name for r in rows] == ["gold", "dirt", "iron"]


@pytest.mark.asyncio
async def test_upsert_row_creates_then_updates_same_name():
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        await s.commit()

    async with async_session_factory() as s:
        created = await sheet_repo.upsert_row(s, sheet.id, "iron_ingot", 64, 0, 0)
        await s.commit()
        assert created.id is not None
        assert created.need_qty == 64
        assert created.done_flag == 0
        first_id = created.id

    # 同名再次 upsert → 更新而非报错
    async with async_session_factory() as s:
        updated = await sheet_repo.upsert_row(s, sheet.id, "iron_ingot", 192, 1, 5)
        await s.commit()
        assert updated.id == first_id
        assert updated.need_qty == 192
        assert updated.done_flag == 1
        assert updated.sort_order == 5

    async with async_session_factory() as s:
        rows = await sheet_repo.list_rows(s, sheet.id)
        assert len(rows) == 1  # 仍是单行（未新建第二行）
        assert rows[0].need_qty == 192


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
        rows = await sheet_repo.list_rows(s, sheet.id)

    csv_str = sheet_repo.export_csv(sheet.id, rows)
    lines = csv_str.strip().splitlines()
    assert lines[0] == "sheet_id,item_name,need_qty,done_flag,sort_order"
    assert lines[1] == f"{sheet.id},iron,64,0,0"
    assert lines[2] == f"{sheet.id},gold,128,1,1"


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
    assert lines[0] == "sheet_id,item_name,need_qty,done_flag,sort_order"
    body = lines[1:]
    assert f"{s1.id},a,1,0,0" in body
    assert f"{s2.id},b,2,1,0" in body
    assert len(body) == 2


@pytest.mark.asyncio
async def test_export_csv_empty_sheet_has_header_only():
    owner = await _seed_player()
    async with async_session_factory() as s:
        sheet = await sheet_repo.create_sheet(s, owner, "S")
        await s.commit()

    csv_str = sheet_repo.export_csv(sheet.id, [])
    lines = csv_str.strip().splitlines()
    assert lines == ["sheet_id,item_name,need_qty,done_flag,sort_order"]
