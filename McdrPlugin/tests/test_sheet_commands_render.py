"""sheet_commands 渲染层单测：空列表场景应给出可点击的「新增」快捷指令。

回归用例：旧版空行 / 空表单分支只回显灰色提示、不带任何按钮，玩家无法一键新增。
依赖 tests/_stubs.py 让 @new_thread passthrough（同步执行回调），便于直接断言 server.tell。
"""
import os
import sys
import unittest
from unittest import mock

# 安装替身 + 路径（必须在导入被测模块前）
sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401,E402  触发 stubs 安装与 sys.path 配置

import htcmc_auth.sheet_commands as sheet_commands  # noqa: E402


def _make_src_server(player="玩家A"):
    """造 fake src + server，捕获 server.tell(name, msg) 的 msg 列表。"""
    told = []
    server = mock.Mock()
    server.tell.side_effect = lambda name, msg: told.append(msg)
    src = mock.Mock()
    src.is_player = True
    src.player = player
    src.get_server.return_value = server
    return src, told


def _all_click_values(obj):
    """递归提取 RTextList（含嵌套，如 format_owner_footer 返回值）中所有按钮的 suggest 命令。

    stub 的 RText.__str__ 只输出文本、不含 click_value，故命令校验必须走 _click_value。
    """
    out = []
    if hasattr(obj, "_click_value"):
        out.append(obj._click_value)
    if hasattr(obj, "parts"):  # RTextList
        for p in obj.parts:
            out.extend(_all_click_values(p))
    return out


class ViewEmptyRowsTest(unittest.TestCase):
    def test_owner_sees_add_button_on_empty_rows(self):
        # 拥有者看自己的空表：应同时看到 (无行) 提示 + [新增物品] 等管理按钮
        src, told = _make_src_server(player="玩家A")
        detail = {"id": 3, "title": "清单T", "owner_name": "玩家A", "rows": []}
        with mock.patch.object(sheet_commands.sheet_client, "view_sheet", return_value=detail):
            sheet_commands._sheet_view(src, {"sheet_id": 3})
        msg = str(told[0])
        self.assertIn("（无行）", msg)
        self.assertIn("[新增物品]", msg)
        # suggest 命令末尾留空格续输：物品 数量 ...
        self.assertIn("!!PCH sheet add 3 ", _all_click_values(told[0]))

    def test_non_owner_no_management_buttons_on_empty_rows(self):
        # 非拥有者看别人的空表：只有 (无行) 提示，不显示管理栏（RBAC 以后端为准）
        src, told = _make_src_server(player="玩家A")
        detail = {"id": 3, "title": "清单T", "owner_name": "别人", "rows": []}
        with mock.patch.object(sheet_commands.sheet_client, "view_sheet", return_value=detail):
            sheet_commands._sheet_view(src, {"sheet_id": 3})
        msg = str(told[0])
        self.assertIn("（无行）", msg)
        self.assertNotIn("[新增物品]", msg)
        self.assertNotIn("[删表]", msg)
        self.assertEqual(_all_click_values(told[0]), [])  # 无任何可点击按钮


class ListEmptyTest(unittest.TestCase):
    def test_empty_list_shows_create_button(self):
        # 全服无表：应给出 [建表] 快捷指令
        src, told = _make_src_server(player="玩家A")
        with mock.patch.object(sheet_commands.sheet_client, "list_sheets", return_value=[]):
            sheet_commands._sheet_list(src, {})
        msg = str(told[0])
        self.assertIn("（无表格）", msg)
        self.assertIn("[建表]", msg)
        self.assertIn("!!PCH sheet create ", _all_click_values(told[0]))  # 末尾留空格续输标题

    def test_empty_list_mine_shows_create_button(self):
        # --mine 无表：同样给 [建表]
        src, told = _make_src_server(player="玩家A")
        with mock.patch.object(sheet_commands.sheet_client, "list_sheets", return_value=[]):
            sheet_commands._sheet_list_mine(src, {})
        msg = str(told[0])
        self.assertIn("（仅看自己）", msg)
        self.assertIn("[建表]", msg)
        self.assertIn("!!PCH sheet create ", _all_click_values(told[0]))


class ViewPermissionTest(unittest.TestCase):
    """_sheet_view 按查看者身份显隐特权按钮（端到端验证 player_uuid/player_name 接线）。"""

    def _detail_with_done_row(self, status, mode, claimant_uuid, claimant_name, owner_name="别人"):
        return {
            "id": 7, "title": "清单P", "owner_name": owner_name,
            "rows": [{
                "id": 1, "item_name": "铁锭", "mode": mode, "status": status,
                "need_qty": 64, "delivered_qty": 64,
                "claimant_uuid": claimant_uuid, "claimant_name": claimant_name,
            }],
        }

    def test_non_claimant_done_row_no_reject_button(self):
        # 非认领人非拥有者查看含 done lock 行的表：不应出现 [打回]/reject 命令
        src, told = _make_src_server(player="玩家A")
        detail = self._detail_with_done_row(
            "done", 0,
            claimant_uuid="00000000-0000-0000-0000-000000000000",
            claimant_name="认领人X",
        )
        with mock.patch.object(sheet_commands.sheet_client, "view_sheet", return_value=detail):
            sheet_commands._sheet_view(src, {"sheet_id": 7})
        msg = str(told[0])
        self.assertNotIn("[打回]", msg)
        cmds = _all_click_values(told[0])
        self.assertFalse(any("reject" in c for c in cmds), cmds)

    def test_claimant_done_row_sees_reject_button(self):
        # 认领人查看自己 done lock 行：应出现 [打回]/reject（UUID 路径命中）
        src, told = _make_src_server(player="玩家A")
        viewer_uuid = sheet_commands.uuid_api_remake.get_uuid("玩家A")
        detail = self._detail_with_done_row(
            "done", 0, claimant_uuid=viewer_uuid, claimant_name="玩家A",
        )
        with mock.patch.object(sheet_commands.sheet_client, "view_sheet", return_value=detail):
            sheet_commands._sheet_view(src, {"sheet_id": 7})
        msg = str(told[0])
        self.assertIn("[打回]", msg)
        cmds = _all_click_values(told[0])
        self.assertTrue(any("reject" in c for c in cmds), cmds)


if __name__ == "__main__":
    unittest.main()
