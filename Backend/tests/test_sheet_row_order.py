"""sheet_row_order 纯函数单测（五档优先级 + 还需数量降序 + 玩家相关）。

不连 DB：直接构造 SheetRow ORM 对象（仅设排序相关属性）。本模块 override conftest 的
autouse ``_truncate_db`` 为 no-op（纯逻辑测试，无库可清）。
"""
import uuid

import pytest

from app.models.sheet import SheetRow
from app.services.sheet_row_order import row_priority, row_remaining, sort_sheet_rows


@pytest.fixture(autouse=True)
def _truncate_db():
    # override conftest 的同名 autouse fixture：纯函数测试不连库、无表可清
    yield


ME = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER = uuid.UUID("00000000-0000-0000-0000-000000000002")


def _row(
    *,
    id=1,
    mode=0,
    status="open",
    claimant_uuid=None,
    need_qty=10,
    delivered_qty=0,
    sort_order=0,
    item_name="x",
    registry_id=None,
):
    """构造非持久化 SheetRow（仅排序相关字段）。"""
    return SheetRow(
        id=id,
        sheet_id=1,
        item_name=item_name,
        registry_id=registry_id,
        need_qty=need_qty,
        mode=mode,
        status=status,
        claimant_uuid=claimant_uuid,
        delivered_qty=delivered_qty,
        sort_order=sort_order,
    )


# === row_remaining ===


def test_row_remaining_basic_and_clamp():
    assert row_remaining(_row(need_qty=10, delivered_qty=3)) == 7
    assert row_remaining(_row(need_qty=10, delivered_qty=0)) == 10
    assert row_remaining(_row(need_qty=10, delivered_qty=12)) == 0  # 超额交付钳到 0


# === row_priority: lock（玩家相关：我认领 vs 非我认领）===


def test_priority_lock_mine_claimed():
    assert row_priority(_row(mode=0, status="claimed", claimant_uuid=ME), ME, set()) == 0


def test_priority_lock_open_is_other():
    assert row_priority(_row(mode=0, status="open", claimant_uuid=None), ME, set()) == 3


def test_priority_lock_claimed_by_other_is_other():
    assert row_priority(_row(mode=0, status="claimed", claimant_uuid=OTHER), ME, set()) == 3


def test_priority_lock_mine_but_done():
    assert row_priority(_row(mode=0, status="done", claimant_uuid=ME), ME, set()) == 4


# === row_priority: progress（玩家相关：我是否贡献过）===


def test_priority_progress_my_contribution():
    assert row_priority(_row(mode=1, status="claimed", id=42), ME, {42}) == 1


def test_priority_progress_no_my_contribution():
    assert row_priority(_row(mode=1, status="open", id=42), ME, set()) == 2


def test_priority_progress_done_ignores_contributors():
    assert row_priority(_row(mode=1, status="done", id=42), ME, {42}) == 4


def test_priority_progress_need_zero_claimed():
    # need=0 + delivered>0 → status=claimed（永不 done）；贡献判定仍看 my_row_ids
    r = _row(id=1, mode=1, status="claimed", need_qty=0, delivered_qty=5)
    assert row_priority(r, ME, set()) == 2
    assert row_priority(r, ME, {1}) == 1
    assert row_remaining(r) == 0


# === sort_sheet_rows: 五档总体顺序 ===


def test_sort_full_tier_order():
    mine_lock = (_row(id=1, mode=0, status="claimed", claimant_uuid=ME), "me")
    my_prog = (_row(id=2, mode=1, status="claimed"), "me")
    other_prog = (_row(id=3, mode=1, status="open"), None)
    other_lock = (_row(id=4, mode=0, status="open"), None)
    done = (_row(id=5, mode=0, status="done", claimant_uuid=ME), "me")
    out = sort_sheet_rows([done, other_lock, other_prog, my_prog, mine_lock], ME, {2})
    assert [r.id for r, _ in out] == [1, 2, 3, 4, 5]


# === 同档内：还需数量降序 ===


def test_sort_secondary_remaining_desc():
    a = (_row(id=1, mode=1, status="open", need_qty=10, delivered_qty=0), None)  # rem 10
    b = (_row(id=2, mode=1, status="open", need_qty=10, delivered_qty=8), None)  # rem 2
    assert [r.id for r, _ in sort_sheet_rows([a, b], ME, set())] == [1, 2]


# === tiebreak: sort_order 升序，再 id ===


def test_sort_tiebreak_sort_order_then_id():
    a = (_row(id=2, mode=0, status="open", sort_order=1), None)
    b = (_row(id=1, mode=0, status="open", sort_order=1), None)
    c = (_row(id=3, mode=0, status="open", sort_order=0), None)
    out = [r.id for r, _ in sort_sheet_rows([a, b, c], ME, set())]
    assert out == [3, 1, 2]  # c(sort0) 先；sort1 内 id1<id2


# === 玩家相关：换 viewer 改变 progress 行档位与相对顺序 ===


def test_sort_viewer_change_swaps_progress_order():
    prog_a = (_row(id=1, mode=1, status="claimed", need_qty=10, delivered_qty=1), None)  # rem 9
    prog_b = (_row(id=2, mode=1, status="open", need_qty=10, delivered_qty=0), None)  # rem 10
    # ME 视角：a 我贡献过→档1；b 未贡献→档2 → a 先
    assert [r.id for r, _ in sort_sheet_rows([prog_b, prog_a], ME, {1})] == [1, 2]
    # OTHER 视角：a、b 都档2 → 按 remaining 降序：b(rem10) 先于 a(rem9)
    assert [r.id for r, _ in sort_sheet_rows([prog_b, prog_a], OTHER, set())] == [2, 1]


def test_sort_viewer_change_lock_mine_vs_other():
    # lock 被我认领：ME 视角档0；OTHER 视角档3
    my_lock = (_row(id=1, mode=0, status="claimed", claimant_uuid=ME), "me")
    other_prog = (_row(id=2, mode=1, status="open"), None)  # 对两人都档2
    # ME 视角：my_lock 档0 最前
    assert [r.id for r, _ in sort_sheet_rows([other_prog, my_lock], ME, set())] == [1, 2]
    # OTHER 视角：my_lock 档3 > other_prog 档2 → other_prog 先
    assert [r.id for r, _ in sort_sheet_rows([other_prog, my_lock], OTHER, set())] == [2, 1]


# === 不可变：不改入参 ===


def test_sort_does_not_mutate_input():
    src = [(_row(id=2, mode=0, status="open"), None), (_row(id=1, mode=0, status="open"), None)]
    snapshot = [r.id for r, _ in src]
    sort_sheet_rows(src, ME, set())
    assert [r.id for r, _ in src] == snapshot  # 入参顺序不变
