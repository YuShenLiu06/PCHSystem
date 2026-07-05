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
    format_submit_footer,
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
            "payload": {"actor_name": "玩家B", "item_name": "铁锭", "sheet_title": "202工程"},
        }
        s = str(format_notification(n))
        self.assertIn("玩家B", s)
        self.assertIn("[202工程] 的 [铁锭]", s)  # [清单名] 的 [物品名]
        self.assertTrue(s.startswith("§a"), s)

    def test_sheet_delivered(self):
        n = {
            "category": "sheet_delivered",
            "payload": {
                "actor_name": "玩家B", "item_name": "铁锭",
                "sheet_title": "202工程", "delivered": 32, "need": 64,
            },
        }
        s = str(format_notification(n))
        self.assertIn("32/64", s)
        self.assertIn("[202工程] 的 [铁锭]", s)
        self.assertTrue(s.startswith("§e"), s)

    def test_sheet_done_uses_green(self):
        n = {"category": "sheet_done", "payload": {"actor_name": "A", "item_name": "X", "sheet_title": "清单S"}}
        s = str(format_notification(n))
        self.assertIn("[清单S] 的 [X]", s)
        self.assertTrue(s.startswith("§a"), s)

    def test_sheet_rejected_uses_red(self):
        n = {"category": "sheet_rejected", "payload": {"item_name": "X", "sheet_title": "清单R"}}
        s = str(format_notification(n))
        self.assertTrue(s.startswith("§c"), s)
        self.assertIn("打回", s)
        self.assertIn("[清单R] 的 [X]", s)

    def test_sheet_qty_changed(self):
        n = {
            "category": "sheet_qty_changed",
            "payload": {"item_name": "铁锭", "sheet_title": "清单Q", "old": 64, "new": 32},
        }
        s = str(format_notification(n))
        self.assertIn("32", s)
        self.assertIn("64", s)
        self.assertIn("[清单Q] 的 [铁锭]", s)

    def test_sheet_row_deleted(self):
        n = {"category": "sheet_row_deleted", "payload": {"item_name": "铁锭", "sheet_title": "清单D"}}
        s = str(format_notification(n))
        self.assertIn("删除", s)
        self.assertIn("[清单D] 的 [铁锭]", s)
        self.assertTrue(s.startswith("§c"), s)

    def test_unknown_category_falls_back_to_title(self):
        n = {"category": "some_future_event", "title": "某未来事件", "body": "详情"}
        s = str(format_notification(n))
        self.assertIn("某未来事件", s)

    def test_missing_payload_fields_dont_crash(self):
        n = {"category": "sheet_claimed", "payload": {}}
        # 不应抛异常；缺 sheet_title / item_name / actor_name 时降级为占位符
        s = str(format_notification(n))
        self.assertIsInstance(s, str)
        self.assertIn("[?] 的 [?]", s)


class FormatRowLineTest(unittest.TestCase):
    def test_open_row_gray(self):
        row = {"id": 3, "item_name": "铁锭", "mode": 0, "status": "open", "need_qty": 64, "delivered_qty": 0, "claimant_name": None}
        s = format_row_line(row)
        self.assertIn("铁锭", s)
        self.assertIn("lock", s)
        self.assertIn("未认领", s)
        self.assertEqual(_status_color("open"), "§7")

    def test_claimed_row_yellow(self):
        # progress 行：认领者列显示贡献者名单（非 claimant_name）
        row = {"id": 3, "item_name": "i", "mode": 1, "status": "claimed", "need_qty": 64, "delivered_qty": 32, "claimant_name": None, "contributors": [{"player_uuid": "x", "player_name": "B"}]}
        s = format_row_line(row)
        self.assertIn("progress", s)
        self.assertIn("B", s)  # 贡献者 B 渲染
        self.assertEqual(_status_color("claimed"), "§e")

    def test_progress_row_shows_top_two_contributors_with_ellipsis(self):
        # 3 位贡献者（后端已按 contributed_qty desc 排序）：至多显 2 位 + 省略号
        row = {"id": 3, "item_name": "i", "mode": 1, "status": "claimed", "need_qty": 100, "delivered_qty": 90, "claimant_name": None, "contributors": [{"player_uuid": "a", "player_name": "甲"}, {"player_uuid": "b", "player_name": "乙"}, {"player_uuid": "c", "player_name": "丙"}]}
        s = format_row_line(row)
        self.assertIn("甲", s)
        self.assertIn("乙", s)
        self.assertNotIn("丙", s)  # 第三位被省略
        self.assertIn("…", s)

    def test_progress_row_no_contributors_shows_unclaimed(self):
        row = {"id": 3, "item_name": "i", "mode": 1, "status": "open", "need_qty": 10, "delivered_qty": 0, "claimant_name": None, "contributors": []}
        s = format_row_line(row)
        self.assertIn("未认领", s)

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
        self.assertIn("[改ID]", s)     # 拥有者追加（setreg 改 registry_id）
        cmds = _click_values(rtl)
        self.assertIn("!!PCH sheet claim 3 5", cmds)
        self.assertIn("!!PCH sheet delrow 3 5", cmds)
        # setreg 末尾留空格：回车=手持物品 / 空格后续输 registry_id
        self.assertIn("!!PCH sheet setreg 3 5 ", cmds)

    def test_open_row_non_owner_no_delrow(self):
        rtl = format_row_clickable(self._row(), 3, is_owner=False)
        self.assertIn("[认领]", str(rtl))
        self.assertNotIn("[删行]", str(rtl))
        self.assertNotIn("[改ID]", str(rtl))  # setreg owner 专用

    def test_claimed_lock_non_claimant_no_priv_buttons(self):
        # 非认领人非拥有者看 lock claimed 行：不显示任何特权按钮（仅行文本）
        row = self._row(status="claimed", mode=0, delivered_qty=10, claimant_name="B")
        rtl = format_row_clickable(row, 3, is_owner=False)
        s = str(rtl)
        self.assertNotIn("[标备齐]", s)  # lock 标备齐仅认领人（delivery 端点 owner 不豁免）
        self.assertNotIn("[解除]", s)    # 解除仅认领人/拥有者
        self.assertNotIn("[交付]", s)    # lock 模式无交付按钮
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

    def test_done_row_non_claimant_no_reject(self):
        # 非认领人非拥有者看 lock done 行：不显示 [打回]（仅行文本）
        row = self._row(status="done", mode=0, delivered_qty=64, claimant_name="B")
        rtl = format_row_clickable(row, 3, is_owner=False)
        s = str(rtl)
        self.assertNotIn("[打回]", s)
        self.assertNotIn("[认领]", s)
        self.assertNotIn("[标备齐]", s)
        self.assertNotIn("[交付]", s)

    def test_open_progress_has_deliver_no_claim(self):
        # progress·open：任意玩家直接上交，不显示认领（progress 无认领概念）
        row = self._row(status="open", mode=1, delivered_qty=0)
        rtl = format_row_clickable(row, 3, is_owner=False)
        s = str(rtl)
        self.assertIn("[交付]", s)
        self.assertNotIn("[认领]", s)
        self.assertIn("!!PCH sheet deliver 3 5 ", _click_values(rtl))

    def test_done_progress_owner_release_no_reject(self):
        # progress·done：无打回（reject 对 progress 返 409），owner 显 [解除] 重置进度
        row = self._row(status="done", mode=1, delivered_qty=64)
        rtl = format_row_clickable(row, 3, is_owner=True)
        s = str(rtl)
        self.assertIn("[解除]", s)
        self.assertNotIn("[打回]", s)
        self.assertIn("!!PCH sheet release 3 5", _click_values(rtl))

    def test_done_progress_non_owner_no_release(self):
        # progress·done 非拥有者：无认领人 → 解除仅 owner 可，故隐藏 [解除]
        row = self._row(status="done", mode=1, delivered_qty=64)
        rtl = format_row_clickable(row, 3, is_owner=False)
        self.assertNotIn("[解除]", str(rtl))
        self.assertNotIn("[打回]", str(rtl))

    def test_claimant_claimed_lock_sees_done_and_release(self):
        # 认领人看自己 claimed lock 行：可见 [标备齐]+[解除]，无 [删行]
        row = self._row(status="claimed", mode=0, delivered_qty=10, claimant_name="玩家A")
        rtl = format_row_clickable(row, 3, is_owner=False, player_name="玩家A")
        s = str(rtl)
        self.assertIn("[标备齐]", s)
        self.assertIn("[解除]", s)
        self.assertNotIn("[交付]", s)
        self.assertNotIn("[删行]", s)

    def test_claimant_by_uuid_sees_done(self):
        # UUID 路径（生产路径）：claimant_uuid 匹配 player_uuid 即认领人，名字不同也认
        row = self._row(status="claimed", mode=0, delivered_qty=10,
                        claimant_name="别的名字", claimant_uuid="abc-123")
        rtl = format_row_clickable(row, 3, is_owner=False, player_uuid="abc-123")
        s = str(rtl)
        self.assertIn("[标备齐]", s)
        self.assertIn("[解除]", s)

    def test_owner_non_claimant_claimed_lock_sees_release_not_done(self):
        # 拥有者非认领人看 claimed lock 行：可见 [解除]+[删行]，但不见 [标备齐]
        # （lock 标备齐经 delivery 端点，owner 不豁免 → 后端 403，故隐藏）
        row = self._row(status="claimed", mode=0, delivered_qty=10, claimant_name="B")
        rtl = format_row_clickable(row, 3, is_owner=True, player_name="玩家A")
        s = str(rtl)
        self.assertNotIn("[标备齐]", s)
        self.assertIn("[解除]", s)
        self.assertIn("[删行]", s)

    def test_claimant_done_sees_reject(self):
        # 认领人看自己 done 行：可见 [打回]（自取消备齐）
        row = self._row(status="done", mode=0, delivered_qty=64, claimant_name="玩家A")
        rtl = format_row_clickable(row, 3, is_owner=False, player_name="玩家A")
        self.assertIn("[打回]", str(rtl))

    def test_owner_non_claimant_done_sees_reject(self):
        # 拥有者非认领人看 done 行：可见 [打回]+[删行]（owner 可打回他人行）
        row = self._row(status="done", mode=0, delivered_qty=64, claimant_name="B")
        rtl = format_row_clickable(row, 3, is_owner=True, player_name="玩家A")
        s = str(rtl)
        self.assertIn("[打回]", s)
        self.assertIn("[删行]", s)


class OwnerFooterTest(unittest.TestCase):
    def test_footer_has_all_management_buttons(self):
        rtl = format_owner_footer(3)
        s = str(rtl)
        self.assertIn("[新增物品]", s)
        self.assertIn("[改标题]", s)
        self.assertIn("[删表]", s)
        cmds = _click_values(rtl)
        self.assertIn("!!PCH sheet addhand 3 ", cmds)  # 默认走 addhand，留空格续输
        self.assertIn("!!PCH sheet rename 3 ", cmds)
        self.assertIn("!!PCH sheet delete 3", cmds)


class SubmitFooterTest(unittest.TestCase):
    def test_submit_footer_has_button(self):
        # 公开底栏（所有人可见）：单按钮 [一键提交] → submit <id>
        rtl = format_submit_footer(3)
        s = str(rtl)
        self.assertIn("[一键提交]", s)
        cmds = _click_values(rtl)
        # submit 单参命令已完整，无尾随空格（回车即执行）
        self.assertIn("!!PCH sheet submit 3", cmds)


if __name__ == "__main__":
    unittest.main()
