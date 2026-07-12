"""messages.format_notification / format_row_line 单测：各 category 中文文案 + §码映射。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401,E402

from pch_system.messages import (  # noqa: E402
    format_notification,
    format_row_line,
    rtext_button,
    format_row_clickable,
    format_owner_footer,
    format_submit_footer,
    format_section_separator,
    _status_color,
)
from pch_system.text_layout import CHAT_LINE_PX, text_width_px  # noqa: E402


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
        self.assertIn("32个/1组", s)  # delivered=32→32个, need=64→1组（issue #18 换算）
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
        self.assertIn("32个", s)   # new=32 → 32个（issue #18 换算）
        self.assertIn("1组", s)    # old=64 → 1组
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
        self.assertIn("1组", s)        # lock 行数量=需求单值（need=64→1组），不再有 [lock] 标签
        self.assertIn("未认领", s)
        self.assertEqual(_status_color("open"), "§7")

    def test_claimed_row_yellow(self):
        # progress 行：认领者列显示贡献者名单（非 claimant_name）
        row = {"id": 3, "item_name": "i", "mode": 1, "status": "claimed", "need_qty": 64, "delivered_qty": 32, "claimant_name": None, "contributors": [{"player_uuid": "x", "player_name": "B"}]}
        s = format_row_line(row)
        self.assertIn("32个/1组", s)   # progress 数量=当前/需求（delivered=32→32个, need=64→1组）
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

    def test_sub_row_need_converted_to_box_unit(self):
        # 子行 mode=lock need_qty=7217 → 7217/1728=4.18 → 单值「4.18盒」，不得漏出裸 7217
        # （子行格式已与顶层统一：无「每件×」「(需)」段，仅多 └ 缩进前缀）
        row = {"id": 4174, "item_name": "石砖-泥土", "mode": 0, "status": "open",
               "need_qty": 7217, "delivered_qty": 0, "claimant_name": None,
               "parent_row_id": 3999, "qty_per_unit": 0.5}
        s = format_row_line(row)
        self.assertIn("└", s)               # 子行缩进前缀
        self.assertIn("4.18盒", s)          # need=7217→4.18盒（lock 单值）
        self.assertNotIn("7217", s)         # 回归点：裸 int 不得代入
        self.assertNotIn("每件×", s)        # 子行不再渲染倍数
        self.assertNotIn("需", s)           # 子行不再有「(需)」段

    def test_sub_row_need_stack_tier(self):
        # 子行 mode=lock need_qty=64 → 单值「1组」，覆盖「组」档换算路径
        row = {"id": 4174, "item_name": "x", "mode": 0, "status": "open",
               "need_qty": 64, "delivered_qty": 0, "claimant_name": None,
               "parent_row_id": 3999, "qty_per_unit": 2}
        s = format_row_line(row)
        self.assertIn("1组", s)
        self.assertNotIn("需", s)

    def test_sub_row_progress_shows_delivered_over_need(self):
        # 子行 progress：数量段与顶层一致（当前/需求），仅多 └ 缩进前缀，无「每件×」
        row = {"id": 4175, "item_name": "石砖-test", "mode": 1, "status": "open",
               "need_qty": 10368, "delivered_qty": 3456, "claimant_name": None,
               "parent_row_id": 3999, "qty_per_unit": 1.5,
               "contributors": [{"player_uuid": "u", "player_name": "A"}]}
        s = format_row_line(row)
        self.assertIn("└", s)              # 子行缩进前缀
        self.assertIn("2盒/6盒", s)        # delivered=3456→2盒, need=10368→6盒
        self.assertIn("A", s)              # progress 认领者列显贡献者
        self.assertNotIn("每件×", s)       # 子行不再渲染倍数

    def test_lock_row_hides_delivered_qty(self):
        # lock 行只显需求单值，不显当前数量（即便 delivered_qty>0）；数量段无「/」
        row = {"id": 4001, "item_name": "磨制深板岩", "mode": 0, "status": "claimed",
               "need_qty": 7217, "delivered_qty": 5000, "claimant_name": "刘宇辰"}
        s = format_row_line(row)
        self.assertIn("4.18盒", s)         # need=7217→4.18盒（单值）
        self.assertNotIn("2.89盒", s)      # delivered=5000→2.89盒 不得显示
        self.assertNotIn("/", s)           # lock 数量段无「/」（进度模式才有）
        self.assertIn("刘宇辰", s)


class RTextButtonTest(unittest.TestCase):
    def test_button_text_and_click_value(self):
        btn = rtext_button("[认]", "!!PCH sheet claim 3 5", color="green", hover="认领此行")
        self.assertEqual(str(btn), "[认]")
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
        self.assertIn("[认]", s)
        self.assertIn("[-]", s)     # 拥有者追加
        self.assertIn("[改]", s)     # 拥有者追加（setreg 改 registry_id）
        cmds = _click_values(rtl)
        self.assertIn("!!PCH sheet claim 3 5", cmds)
        self.assertIn("!!PCH sheet delrow 3 5", cmds)
        # setreg 末尾留空格：回车=手持物品 / 空格后续输 registry_id
        self.assertIn("!!PCH sheet setreg 3 5 ", cmds)

    def test_open_row_non_owner_no_delrow(self):
        rtl = format_row_clickable(self._row(), 3, is_owner=False)
        self.assertIn("[认]", str(rtl))
        self.assertNotIn("[-]", str(rtl))
        self.assertNotIn("[改]", str(rtl))  # setreg owner 专用

    def test_claimed_lock_non_claimant_no_priv_buttons(self):
        # 非认领人非拥有者看 lock claimed 行：不显示任何特权按钮（仅行文本）
        row = self._row(status="claimed", mode=0, delivered_qty=10, claimant_name="B")
        rtl = format_row_clickable(row, 3, is_owner=False)
        s = str(rtl)
        self.assertNotIn("[完]", s)  # lock 标备齐仅认领人（delivery 端点 owner 不豁免）
        self.assertNotIn("[释]", s)    # 解除仅认领人/拥有者
        self.assertNotIn("[交]", s)    # lock 模式无交付按钮
        self.assertNotIn("[-]", s)

    def test_claimed_progress_has_deliver_with_trailing_space(self):
        row = self._row(status="claimed", mode=1, delivered_qty=32, claimant_name="B")
        rtl = format_row_clickable(row, 3, is_owner=True)
        s = str(rtl)
        self.assertIn("[献]", s)  # progress 模式用「贡献」按钮（非 lock 的「交付」）
        self.assertIn("[完]", s)
        self.assertIn("[释]", s)
        self.assertIn("[-]", s)
        # deliver 末尾留空格，玩家续输数量
        self.assertIn("!!PCH sheet deliver 3 5 ", _click_values(rtl))

    def test_done_row_non_claimant_no_reject(self):
        # 非认领人非拥有者看 lock done 行：不显示 [退]（仅行文本）
        row = self._row(status="done", mode=0, delivered_qty=64, claimant_name="B")
        rtl = format_row_clickable(row, 3, is_owner=False)
        s = str(rtl)
        self.assertNotIn("[退]", s)
        self.assertNotIn("[认]", s)
        self.assertNotIn("[完]", s)
        self.assertNotIn("[交]", s)

    def test_open_progress_has_deliver_no_claim(self):
        # progress·open：任意玩家直接上交，不显示认领（progress 无认领概念）
        row = self._row(status="open", mode=1, delivered_qty=0)
        rtl = format_row_clickable(row, 3, is_owner=False)
        s = str(rtl)
        self.assertIn("[献]", s)  # progress 模式用「贡献」按钮（非 lock 的「交付」）
        self.assertNotIn("[认]", s)
        self.assertIn("!!PCH sheet deliver 3 5 ", _click_values(rtl))

    def test_done_progress_owner_release_no_reject(self):
        # progress·done：无打回（reject 对 progress 返 409），owner 显 [释] 重置进度
        row = self._row(status="done", mode=1, delivered_qty=64)
        rtl = format_row_clickable(row, 3, is_owner=True)
        s = str(rtl)
        self.assertIn("[释]", s)
        self.assertNotIn("[退]", s)
        self.assertIn("!!PCH sheet release 3 5", _click_values(rtl))

    def test_done_progress_non_owner_no_release(self):
        # progress·done 非拥有者：无认领人 → 解除仅 owner 可，故隐藏 [释]
        row = self._row(status="done", mode=1, delivered_qty=64)
        rtl = format_row_clickable(row, 3, is_owner=False)
        self.assertNotIn("[释]", str(rtl))
        self.assertNotIn("[退]", str(rtl))

    def test_claimant_claimed_lock_sees_done_and_release(self):
        # 认领人看自己 claimed lock 行：可见 [完]+[释]，无 [-]
        row = self._row(status="claimed", mode=0, delivered_qty=10, claimant_name="玩家A")
        rtl = format_row_clickable(row, 3, is_owner=False, player_name="玩家A")
        s = str(rtl)
        self.assertIn("[完]", s)
        self.assertIn("[释]", s)
        self.assertNotIn("[交]", s)
        self.assertNotIn("[-]", s)

    def test_claimant_by_uuid_sees_done(self):
        # UUID 路径（生产路径）：claimant_uuid 匹配 player_uuid 即认领人，名字不同也认
        row = self._row(status="claimed", mode=0, delivered_qty=10,
                        claimant_name="别的名字", claimant_uuid="abc-123")
        rtl = format_row_clickable(row, 3, is_owner=False, player_uuid="abc-123")
        s = str(rtl)
        self.assertIn("[完]", s)
        self.assertIn("[释]", s)

    def test_owner_non_claimant_claimed_lock_sees_release_not_done(self):
        # 拥有者非认领人看 claimed lock 行：可见 [释]+[-]，但不见 [完]
        # （lock 标备齐经 delivery 端点，owner 不豁免 → 后端 403，故隐藏）
        row = self._row(status="claimed", mode=0, delivered_qty=10, claimant_name="B")
        rtl = format_row_clickable(row, 3, is_owner=True, player_name="玩家A")
        s = str(rtl)
        self.assertNotIn("[完]", s)
        self.assertIn("[释]", s)
        self.assertIn("[-]", s)

    def test_claimant_done_sees_reject(self):
        # 认领人看自己 done 行：可见 [退]（自取消备齐）
        row = self._row(status="done", mode=0, delivered_qty=64, claimant_name="玩家A")
        rtl = format_row_clickable(row, 3, is_owner=False, player_name="玩家A")
        self.assertIn("[退]", str(rtl))

    def test_owner_non_claimant_done_sees_reject(self):
        # 拥有者非认领人看 done 行：可见 [退]+[-]（owner 可打回他人行）
        row = self._row(status="done", mode=0, delivered_qty=64, claimant_name="B")
        rtl = format_row_clickable(row, 3, is_owner=True, player_name="玩家A")
        s = str(rtl)
        self.assertIn("[退]", s)
        self.assertIn("[-]", s)


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


class FormatSectionSeparatorTest(unittest.TestCase):
    """分节分隔符：gold+bold 双线，title 居中；渲染总宽 ≤ CHAT_LINE_PX（粗体已计入）。"""

    SEPARATOR_TITLES = [
        "物品列表",        # 实际场景：行列表标题
        "列表管理",        # 实际场景：owner 管理栏
        "短",             # 边界：极短 title（bar 最多）
        "一个比较长的分节标题示例",  # 边界：长 title（bar 最少）
        "",               # 边界：空 title
    ]

    def test_rendered_width_fits_chat_line(self):
        # 关键契约：粗体（§l）下每字符 +1px；若公式漏算粗体（仍按非粗体求 bar 数），
        # 渲染会超 320px 换行。本测试以 §l 前缀重建粗体宽度来锁定。
        for title in self.SEPARATOR_TITLES:
            rt = format_section_separator(title)
            rendered_px = text_width_px(f"§l{rt.to_plain_text()}")
            self.assertLessEqual(
                rendered_px, CHAT_LINE_PX,
                f"title={title!r} 分隔符粗体渲染宽 {rendered_px}px > {CHAT_LINE_PX}px（会换行）",
            )

    def test_title_centered_symmetric_bars(self):
        # 两侧 bar 数相等（视觉居中）：形如 "<bar> <title> <bar>"
        for title in ["物品列表", "列表管理", "短", "一个比较长的分节标题示例"]:
            s = format_section_separator(title).to_plain_text()
            left, right = s.split(f" {title} ")
            self.assertEqual(left, right, f"title={title!r} 两侧 ═ 数不等（未居中）")


if __name__ == "__main__":
    unittest.main()
