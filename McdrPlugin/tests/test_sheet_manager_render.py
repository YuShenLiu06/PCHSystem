"""协管员（manager，迁移 0014）渲染分流单测。

验证 format_row_clickable / format_owner_footer 的 tier 分流：
- tier B（owner 或 manager 可见）：行级 [改][-][子][调]、底部 [进入施工][新增物品]
- tier A（仅 owner）：底部 [直接归档]/[标记施工完成并归档]/[改标题]/[删表]

依赖 tests/_stubs.py 提供 RText/RTextList 替身。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401,E402

from pch_system.messages import format_owner_footer, format_row_clickable  # noqa: E402


def _click_values(obj):
    """递归提取 RTextList 中所有按钮的 suggest 命令。"""
    out = []
    if hasattr(obj, "_click_value") and obj._click_value:
        out.append(obj._click_value)
    if hasattr(obj, "parts"):
        for p in obj.parts:
            out.extend(_click_values(p))
    return out


def _row(row_id=1, mode=0, status="open", parent_row_id=None):
    return {
        "id": row_id, "item_name": "铁锭", "registry_id": "minecraft:iron_ingot",
        "need_qty": 64, "mode": mode, "status": status, "claimant_uuid": None,
        "claimant_name": None, "delivered_qty": 0, "sort_order": 0,
        "parent_row_id": parent_row_id, "qty_per_unit": None,
    }


class FormatRowClickableManagerTest(unittest.TestCase):
    """manager（is_manager=True）应看到 tier B 行级管理按钮，与 owner 等价。"""

    def test_manager_sees_edit_delete_buttons(self):
        vals_owner = set(_click_values(format_row_clickable(_row(), 1, is_owner=True)))
        vals_mgr = set(_click_values(format_row_clickable(_row(), 1, is_manager=True)))
        # manager 能触发 setreg / delrow（与 owner 一致）
        self.assertIn("!!PCH sheet setreg 1 1 ", vals_mgr)
        self.assertIn("!!PCH sheet delrow 1 1", vals_mgr)
        self.assertEqual(
            {v for v in vals_owner if "setreg" in v or "delrow" in v or "addsub" in v},
            {v for v in vals_mgr if "setreg" in v or "delrow" in v or "addsub" in v},
        )

    def test_manager_sees_progress_override(self):
        vals = _click_values(format_row_clickable(_row(mode=1, status="claimed"), 1, is_manager=True))
        self.assertTrue(any("progress" in v for v in vals))

    def test_plain_player_sees_no_manage_buttons(self):
        vals = set(_click_values(format_row_clickable(_row(), 1)))
        self.assertFalse(any("setreg" in v or "delrow" in v for v in vals))


class FormatOwnerFooterTierTest(unittest.TestCase):
    """底部管理栏 tier 分流：归档/改名/删表仅 owner；进入施工/新增物品 owner+manager。"""

    def test_manager_collecting_sees_advance_construction_not_archive(self):
        vals_mgr = set(_click_values(format_owner_footer(1, "collecting", is_owner=False)))
        self.assertIn("!!PCH sheet advance 1 constructing", vals_mgr)  # tier B
        self.assertIn("!!PCH sheet addhand 1 ", vals_mgr)  # tier B
        self.assertNotIn("!!PCH sheet advance 1 archived", vals_mgr)  # tier A
        self.assertFalse(any("rename" in v for v in vals_mgr))  # tier A
        self.assertFalse(any("delete" in v for v in vals_mgr))  # tier A

    def test_owner_collecting_sees_archive_rename_delete(self):
        vals_owner = set(_click_values(format_owner_footer(1, "collecting", is_owner=True)))
        self.assertIn("!!PCH sheet advance 1 archived", vals_owner)
        self.assertTrue(any("rename" in v for v in vals_owner))
        self.assertTrue(any("delete" in v for v in vals_owner))

    def test_manager_constructing_no_archive_button(self):
        vals_mgr = set(_click_values(format_owner_footer(1, "constructing", is_owner=False)))
        self.assertNotIn("!!PCH sheet advance 1 archived", vals_mgr)
        self.assertTrue(any("addhand" in v for v in vals_mgr))  # tier B 仍可见


if __name__ == "__main__":
    unittest.main()
