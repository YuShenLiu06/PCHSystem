"""messages.format_notification / format_row_line 单测：各 category 中文文案 + §码映射。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401,E402

from htcmc_auth.messages import (  # noqa: E402
    format_notification,
    format_row_line,
    rtext_button,
    format_row_clickable,
    format_owner_footer,
    _status_color,
)


def _click_values(rtext_list):
    """从 RTextList 提取所有按钮的 suggest_command 值（测试辅助）。"""
    return [p._click_value for p in rtext_list.parts if hasattr(p, "_click_value")]


class FormatNotificationTest(unittest.TestCase):
    def test_sheet_claimed(self):
        n = {
            "id": 1,
            "category": "sheet_claimed",
            "payload": {"actor_name": "玩家B", "item_name": "铁锭"},
        }
        s = str(format_notification(n))
        self.assertIn("玩家B", s)
        self.assertIn("铁锭", s)
        self.assertTrue(s.startswith("§a"), s)

    def test_sheet_delivered(self):
        n = {
            "category": "sheet_delivered",
            "payload": {"actor_name": "玩家B", "item_name": "铁锭", "delivered": 32, "need": 64},
        }
        s = str(format_notification(n))
        self.assertIn("32/64", s)
        self.assertTrue(s.startswith("§e"), s)

    def test_sheet_done_uses_green(self):
        n = {"category": "sheet_done", "payload": {"actor_name": "A", "item_name": "X"}}
        s = str(format_notification(n))
        self.assertTrue(s.startswith("§a"), s)

    def test_sheet_rejected_uses_red(self):
        n = {"category": "sheet_rejected", "payload": {"item_name": "X"}}
        s = str(format_notification(n))
        self.assertTrue(s.startswith("§c"), s)
        self.assertIn("打回", s)

    def test_sheet_qty_changed(self):
        n = {
            "category": "sheet_qty_changed",
            "payload": {"item_name": "铁锭", "old": 64, "new": 32},
        }
        s = str(format_notification(n))
        self.assertIn("32", s)
        self.assertIn("64", s)

    def test_sheet_row_deleted(self):
        n = {"category": "sheet_row_deleted", "payload": {"item_name": "铁锭"}}
        s = str(format_notification(n))
        self.assertIn("删除", s)
        self.assertTrue(s.startswith("§c"), s)

    def test_unknown_category_falls_back_to_title(self):
        n = {"category": "some_future_event", "title": "某未来事件", "body": "详情"}
        s = str(format_notification(n))
        self.assertIn("某未来事件", s)

    def test_missing_payload_fields_dont_crash(self):
        n = {"category": "sheet_claimed", "payload": {}}
        # 不应抛异常
        s = str(format_notification(n))
        self.assertIsInstance(s, str)


class FormatRowLineTest(unittest.TestCase):
    def test_open_row_gray(self):
        row = {"id": 3, "item_name": "铁锭", "mode": 0, "status": "open", "need_qty": 64, "delivered_qty": 0, "claimant_name": None}
        s = format_row_line(row)
        self.assertIn("铁锭", s)
        self.assertIn("lock", s)
        self.assertIn("未认领", s)
        self.assertEqual(_status_color("open"), "§7")

    def test_claimed_row_yellow(self):
        row = {"id": 3, "item_name": "i", "mode": 1, "status": "claimed", "need_qty": 64, "delivered_qty": 32, "claimant_name": "B"}
        s = format_row_line(row)
        self.assertIn("progress", s)
        self.assertIn("B", s)
        self.assertEqual(_status_color("claimed"), "§e")

    def test_done_row_green(self):
        row = {"id": 3, "item_name": "i", "mode": 0, "status": "done", "need_qty": 64, "delivered_qty": 64, "claimant_name": "B"}
        format_row_line(row)
        self.assertEqual(_status_color("done"), "§a")


class RTextButtonTest(unittest.TestCase):
    def test_button_text_and_click_value(self):
        btn = rtext_button("[认领]", "!!PCH sheet claim 3 5", color="green", hover="认领此行")
        self.assertEqual(str(btn), "[认领]")
        self.assertEqual(btn._click_value, "!!PCH sheet claim 3 5")

    def test_button_without_hover(self):
        btn = rtext_button("[X]", "!!PCH sheet view 1")
        self.assertEqual(str(btn), "[X]")
        self.assertEqual(btn._click_value, "!!PCH sheet view 1")


class FormatRowClickableTest(unittest.TestCase):
    def _row(self, **over):
        base = {
            "id": 5, "item_name": "铁锭", "mode": 0, "status": "open",
            "need_qty": 64, "delivered_qty": 0, "claimant_name": None,
        }
        base.update(over)
        return base

    def test_open_row_owner_has_claim_and_delrow(self):
        rtl = format_row_clickable(self._row(), 3, is_owner=True)
        s = str(rtl)
        self.assertIn("铁锭", s)       # 行文本仍保留
        self.assertIn("[认领]", s)
        self.assertIn("[删行]", s)     # 拥有者追加
        cmds = _click_values(rtl)
        self.assertIn("!!PCH sheet claim 3 5", cmds)
        self.assertIn("!!PCH sheet delrow 3 5", cmds)

    def test_open_row_non_owner_no_delrow(self):
        rtl = format_row_clickable(self._row(), 3, is_owner=False)
        self.assertIn("[认领]", str(rtl))
        self.assertNotIn("[删行]", str(rtl))

    def test_claimed_lock_no_deliver(self):
        row = self._row(status="claimed", mode=0, delivered_qty=10, claimant_name="B")
        rtl = format_row_clickable(row, 3, is_owner=False)
        s = str(rtl)
        self.assertIn("[标备齐]", s)
        self.assertIn("[解除]", s)
        self.assertNotIn("[交付]", s)  # lock 模式无交付按钮
        self.assertNotIn("[删行]", s)

    def test_claimed_progress_has_deliver_with_trailing_space(self):
        row = self._row(status="claimed", mode=1, delivered_qty=32, claimant_name="B")
        rtl = format_row_clickable(row, 3, is_owner=True)
        s = str(rtl)
        self.assertIn("[交付]", s)
        self.assertIn("[标备齐]", s)
        self.assertIn("[解除]", s)
        self.assertIn("[删行]", s)
        # deliver 末尾留空格，玩家续输数量
        self.assertIn("!!PCH sheet deliver 3 5 ", _click_values(rtl))

    def test_done_row_only_reject(self):
        row = self._row(status="done", mode=0, delivered_qty=64, claimant_name="B")
        rtl = format_row_clickable(row, 3, is_owner=False)
        s = str(rtl)
        self.assertIn("[打回]", s)
        self.assertNotIn("[认领]", s)
        self.assertNotIn("[标备齐]", s)
        self.assertNotIn("[交付]", s)


class OwnerFooterTest(unittest.TestCase):
    def test_footer_has_all_management_buttons(self):
        rtl = format_owner_footer(3)
        s = str(rtl)
        self.assertIn("[新增物品]", s)
        self.assertIn("[改标题]", s)
        self.assertIn("[删表]", s)
        cmds = _click_values(rtl)
        self.assertIn("!!PCH sheet add 3 ", cmds)      # 默认 lock，留空格续输
        self.assertIn("!!PCH sheet rename 3 ", cmds)
        self.assertIn("!!PCH sheet delete 3", cmds)


if __name__ == "__main__":
    unittest.main()
