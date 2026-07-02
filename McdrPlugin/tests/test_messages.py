"""messages.format_notification / format_row_line 单测：各 category 中文文案 + §码映射。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401,E402

from htcmc_auth.messages import (  # noqa: E402
    format_notification,
    format_row_line,
    _status_color,
)


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


if __name__ == "__main__":
    unittest.main()
