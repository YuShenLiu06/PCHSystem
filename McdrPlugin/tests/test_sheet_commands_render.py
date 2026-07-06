"""sheet_commands 渲染层单测：空列表场景应给出可点击的「新增」快捷指令。

回归用例：旧版空行 / 空表单分支只回显灰色提示、不带任何按钮，玩家无法一键新增。
依赖 tests/_stubs.py 让 @new_thread passthrough（同步执行回调），便于直接断言 server.tell。
"""
import os
import re
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
        # 拥有者看自己的空表：物品列表分隔符 + (无行) 提示 + [新增物品] 等管理按钮
        # 空表隐藏 [一键提交]（无可匹配行，按钮无效）；[新增物品] 走 addhand（手持建行）
        src, told = _make_src_server(player="玩家A")
        detail = {"id": 3, "title": "清单T", "owner_name": "玩家A", "rows": []}
        with mock.patch.object(sheet_commands.sheet_client, "view_sheet", return_value=detail):
            sheet_commands._sheet_view(src, {"sheet_id": 3})
        msg = str(told[0])
        self.assertIn("物品列表", msg)  # 空表也渲染主分隔符（修复核心点）
        self.assertIn("（无行）", msg)
        self.assertNotIn("[一键提交]", msg)  # 空表隐藏 submit（无可匹配行）
        self.assertIn("[新增物品]", msg)
        # suggest 命令末尾留空格续输：数量 [lock|progress] [排序]
        self.assertIn("!!PCH sheet addhand 3 ", _all_click_values(told[0]))

    def test_non_owner_no_management_buttons_on_empty_rows(self):
        # 非拥有者看别人的空表：物品列表分隔符 + (无行) 提示；空表隐藏 [一键提交]，无管理栏
        src, told = _make_src_server(player="玩家A")
        detail = {"id": 3, "title": "清单T", "owner_name": "别人", "rows": []}
        with mock.patch.object(sheet_commands.sheet_client, "view_sheet", return_value=detail):
            sheet_commands._sheet_view(src, {"sheet_id": 3})
        msg = str(told[0])
        self.assertIn("物品列表", msg)  # 空表也渲染主分隔符
        self.assertIn("（无行）", msg)
        self.assertNotIn("[一键提交]", msg)  # 空表隐藏 submit
        self.assertNotIn("[新增物品]", msg)
        self.assertNotIn("[删表]", msg)
        # 空表无 submit + 非 owner 无管理按钮 → 无任何 click 值
        self.assertEqual(_all_click_values(told[0]), [])

    def test_empty_rows_shows_item_list_separator_before_placeholder(self):
        # 回归：空表必须渲染 ════ 物品列表 ════ 主分隔符，且位于（无行）之前
        # （曾因分隔符放在 else 分支内被跳过，导致空表无标题锚、与「列表管理」不对称）
        src, told = _make_src_server(player="玩家A")
        detail = {"id": 3, "title": "清单T", "owner_name": "别人", "rows": []}
        with mock.patch.object(sheet_commands.sheet_client, "view_sheet", return_value=detail):
            sheet_commands._sheet_view(src, {"sheet_id": 3})
        msg = str(told[0])
        self.assertIn("物品列表", msg)
        self.assertIn("（无行）", msg)
        # 分隔符必须在（无行）之前（锚定物品列表区块标题）
        self.assertLess(msg.index("物品列表"), msg.index("（无行）"))

    def test_empty_rows_placeholder_is_centered(self):
        # 空表（无行）提示应居中显示（前置 center_leading 像素填充），而非顶格左对齐
        src, told = _make_src_server(player="玩家A")
        detail = {"id": 3, "title": "清单T", "owner_name": "别人", "rows": []}
        with mock.patch.object(sheet_commands.sheet_client, "view_sheet", return_value=detail):
            sheet_commands._sheet_view(src, {"sheet_id": 3})
        msg = str(told[0])
        # 找（无行）所在行；stub 原样保留 § 码，剥除后应以前置空格开头（居中填充）
        line = next(l for l in msg.split("\n") if "（无行）" in l)
        plain = re.sub(r"§.", "", line)
        self.assertTrue(plain.startswith(" "), "（无行）应居中，实际行：%r" % line)


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
        # --mine 无表：同样给 [建表]（新 flags 解析器）
        src, told = _make_src_server(player="玩家A")
        with mock.patch.object(sheet_commands.sheet_client, "list_sheets", return_value=[]):
            sheet_commands._sheet_list_flags(src, {"flags": "--mine"})
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

    def test_owner_progress_row_sees_adjust_button(self):
        # owner 查看 progress 行：应出现 [调整进度]/progress 命令（绝对值覆写，owner 专用）
        src, told = _make_src_server(player="玩家A")
        detail = self._detail_with_done_row(
            "claimed", 1, claimant_uuid=None, claimant_name=None, owner_name="玩家A",
        )
        with mock.patch.object(sheet_commands.sheet_client, "view_sheet", return_value=detail):
            sheet_commands._sheet_view(src, {"sheet_id": 7})
        msg = str(told[0])
        self.assertIn("[调整进度]", msg)
        cmds = _all_click_values(told[0])
        self.assertTrue(any("progress" in c for c in cmds), cmds)

    def test_non_owner_progress_row_no_adjust_button(self):
        # 非 owner 查看 progress 行：无 [调整进度]（真实权限以后端 403 为准，R-9）
        src, told = _make_src_server(player="玩家A")
        detail = self._detail_with_done_row(
            "claimed", 1, claimant_uuid=None, claimant_name=None, owner_name="别人",
        )
        with mock.patch.object(sheet_commands.sheet_client, "view_sheet", return_value=detail):
            sheet_commands._sheet_view(src, {"sheet_id": 7})
        msg = str(told[0])
        self.assertNotIn("[调整进度]", msg)
        cmds = _all_click_values(told[0])
        self.assertFalse(any("progress" in c for c in cmds), cmds)

    def test_owner_lock_row_no_adjust_button(self):
        # owner 查看 lock 行：无 [调整进度]（progress 专用，lock 用 delivery）
        src, told = _make_src_server(player="玩家A")
        detail = self._detail_with_done_row(
            "claimed", 0, claimant_uuid=None, claimant_name=None, owner_name="玩家A",
        )
        with mock.patch.object(sheet_commands.sheet_client, "view_sheet", return_value=detail):
            sheet_commands._sheet_view(src, {"sheet_id": 7})
        cmds = _all_click_values(told[0])
        self.assertFalse(any("progress" in c for c in cmds), cmds)


class ViewSubmitButtonTest(unittest.TestCase):
    """_sheet_view 公开「一键提交」底栏：所有查看者可见（submit 无权限要求）。"""

    def test_non_owner_sees_submit_button(self):
        # 非拥有者查看含行表：底部见 [一键提交]（公开）；行尾无 [改ID]（owner 专用）
        src, told = _make_src_server(player="玩家A")
        detail = {
            "id": 7, "title": "清单S", "owner_name": "别人",
            "rows": [{"id": 1, "item_name": "铁锭", "mode": 0, "status": "open",
                      "need_qty": 64, "delivered_qty": 0, "claimant_name": None}],
        }
        with mock.patch.object(sheet_commands.sheet_client, "view_sheet", return_value=detail):
            sheet_commands._sheet_view(src, {"sheet_id": 7})
        msg = str(told[0])
        self.assertIn("[一键提交]", msg)
        self.assertNotIn("[改ID]", msg)  # setreg owner 专用，非 owner 行尾隐藏
        cmds = _all_click_values(told[0])
        self.assertIn("!!PCH sheet submit 7", cmds)
        self.assertFalse(any("setreg" in c for c in cmds), cmds)


class SetregHandTest(unittest.TestCase):
    """_sheet_setreg 的 registry_id 缺省时读手持物品兜底。"""

    def _make_src_server(self, player="tester"):
        told = []
        server = mock.Mock()
        server.tell.side_effect = lambda name, msg: told.append(msg)
        # minecraft_data_api 插件实例替身（非 None 即视为已安装）
        api = mock.Mock()
        server.get_plugin_instance.return_value = api
        src = mock.Mock()
        src.is_player = True
        src.player = player
        src.get_server.return_value = server
        return src, told, server, api

    def test_缺省registry_id_读手持物品(self):
        # ctx 不含 registry_id → 读手持物品的 registry_id 传给 upsert_row
        src, told, server, api = self._make_src_server()
        row = {"id": 1, "item_name": "石头", "need_qty": 64, "mode": 0, "sort_order": 0}
        with mock.patch.object(sheet_commands.sheet_client, "view_sheet",
                               return_value={"id": 7, "rows": [row]}), \
             mock.patch.object(sheet_commands.scanner, "read_held_item",
                               return_value=("minecraft:stone", 32)), \
             mock.patch.object(sheet_commands.sheet_client, "upsert_row",
                               return_value={"id": 1, "registry_id": "minecraft:stone"}) as upsert_mock:
            sheet_commands._sheet_setreg(src, {"sheet_id": 7, "row_id": 1})
        # upsert_row 应收到 registry_id=手持物品 id（关键字参数）
        _, kwargs = upsert_mock.call_args
        self.assertEqual(kwargs.get("registry_id"), "minecraft:stone")

    def test_缺省registry_id_空手回显提示(self):
        # ctx 不含 registry_id + 空手 → 回显 SHEET_SETREG_NEED_HAND，不调 upsert_row
        src, told, server, api = self._make_src_server()
        with mock.patch.object(sheet_commands.scanner, "read_held_item",
                               return_value=None), \
             mock.patch.object(sheet_commands.sheet_client, "upsert_row") as upsert_mock:
            sheet_commands._sheet_setreg(src, {"sheet_id": 7, "row_id": 1})
        told_str = " ".join(str(m) for m in told)
        self.assertIn("手持物品", told_str)
        upsert_mock.assert_not_called()

    def test_显式registry_id_不读手持(self):
        # ctx 含 registry_id → 不调用 read_held_item，直接用参数
        src, told, server, api = self._make_src_server()
        row = {"id": 1, "item_name": "石头", "need_qty": 64, "mode": 0, "sort_order": 0}
        with mock.patch.object(sheet_commands.sheet_client, "view_sheet",
                               return_value={"id": 7, "rows": [row]}), \
             mock.patch.object(sheet_commands.scanner, "read_held_item",
                               return_value=None) as held_mock, \
             mock.patch.object(sheet_commands.sheet_client, "upsert_row",
                               return_value={"id": 1, "registry_id": "minecraft:cobblestone"}) as upsert_mock:
            sheet_commands._sheet_setreg(src, {"sheet_id": 7, "row_id": 1, "registry_id": "minecraft:cobblestone"})
        held_mock.assert_not_called()
        _, kwargs = upsert_mock.call_args
        self.assertEqual(kwargs.get("registry_id"), "minecraft:cobblestone")


class SheetQuickTest(unittest.TestCase):
    """_sheet_quick 快速重开上次查看的表（!!sheet / !!PCH sheet last）。"""

    def test_last_sheet_present_renders_detail(self):
        # get_last_sheet 返回 {"sheet_id": 5} → 渲染 #5 详情（调用 view_sheet）
        src, told = _make_src_server(player="玩家A")
        detail = {"id": 5, "title": "上次表", "owner_name": "玩家A", "status": "collecting", "rows": []}
        with mock.patch.object(sheet_commands.sheet_client, "get_last_sheet",
                               return_value={"sheet_id": 5}), \
             mock.patch.object(sheet_commands.sheet_client, "view_sheet",
                               return_value=detail) as view_mock:
            sheet_commands._sheet_quick(src, {})
        # view_sheet 应被调用
        view_mock.assert_called_once()
        msg = str(told[0])
        self.assertIn("上次表", msg)
        self.assertIn("收集中", msg)

    def test_last_sheet_none_shows_empty_message(self):
        # get_last_sheet 返回 {"sheet_id": None} → 回显 SHEET_LAST_EMPTY，不调 view_sheet
        src, told = _make_src_server(player="玩家A")
        with mock.patch.object(sheet_commands.sheet_client, "get_last_sheet",
                               return_value={"sheet_id": None}), \
             mock.patch.object(sheet_commands.sheet_client, "view_sheet") as view_mock:
            sheet_commands._sheet_quick(src, {})
        # view_sheet 不应被调用
        view_mock.assert_not_called()
        msg = str(told[0])
        self.assertIn("暂无最近打开的表格", msg)


class ListStatusTest(unittest.TestCase):
    """list 命令状态过滤与渲染：默认进行中 + 状态标签 + flags 解析。"""

    def test_list_default_renders_status_label(self):
        # _sheet_list_default（无 flags）→ 渲染每行的阶段标签（如「收集中」）
        src, told = _make_src_server(player="玩家A")
        sheets = [
            {"id": 1, "owner_name": "玩家A", "title": "表A", "status": "collecting"},
            {"id": 2, "owner_name": "玩家B", "title": "表B", "status": "constructing"},
        ]
        with mock.patch.object(sheet_commands.sheet_client, "list_sheets",
                               return_value=sheets) as list_mock:
            sheet_commands._sheet_list_default(src, {})
        # list_sheets 应收到 status="active"（进行中 = collecting + constructing）
        _, kwargs = list_mock.call_args
        self.assertEqual(kwargs.get("status"), "active")
        # 渲染含状态标签
        msg = str(told[0])
        self.assertIn("收集中", msg)
        self.assertIn("施工中", msg)

    def test_list_flags_parses_mine_archived(self):
        # _sheet_list_flags 解析 "--mine --archived" → mine=True, status="archived"
        src, told = _make_src_server(player="玩家A")
        with mock.patch.object(sheet_commands.sheet_client, "list_sheets",
                               return_value=[]) as list_mock:
            sheet_commands._sheet_list_flags(src, {"flags": "--mine --archived"})
        # list_sheets 应收到 mine=True, status="archived"
        _, kwargs = list_mock.call_args
        self.assertTrue(kwargs.get("mine"))
        self.assertEqual(kwargs.get("status"), "archived")

    def test_list_flags_unknown_token_errors(self):
        # _sheet_list_flags 遇未知 token → 回显错误提示
        src, told = _make_src_server(player="玩家A")
        sheet_commands._sheet_list_flags(src, {"flags": "--unknown"})
        msg = str(told[0])
        self.assertIn("未知旗标", msg)

    def test_list_flags_all_sets_status_none(self):
        # _sheet_list_flags 解析 "--all" → status=None（后端返回全部）
        src, told = _make_src_server(player="玩家A")
        with mock.patch.object(sheet_commands.sheet_client, "list_sheets",
                               return_value=[]) as list_mock:
            sheet_commands._sheet_list_flags(src, {"flags": "--all"})
        # list_sheets 应收到 status=None
        _, kwargs = list_mock.call_args
        self.assertIsNone(kwargs.get("status"))

    def test_list_default_sends_active_status(self):
        # _sheet_list_default（无 flags）→ 传 status="active"
        src, told = _make_src_server(player="玩家A")
        with mock.patch.object(sheet_commands.sheet_client, "list_sheets",
                               return_value=[]) as list_mock:
            sheet_commands._sheet_list_default(src, {})
        # list_sheets 应收到 status="active"
        _, kwargs = list_mock.call_args
        self.assertEqual(kwargs.get("status"), "active")


class ListFlagShortTest(unittest.TestCase):
    """_parse_list_flag_tokens 单字母简写 + 组合 + 完整向后兼容。"""

    def test_short_m_mine(self):
        mine, status, unknown = sheet_commands._parse_list_flag_tokens(["-m"])
        self.assertTrue(mine)
        self.assertEqual(status, "active")
        self.assertIsNone(unknown)

    def test_short_c_collecting(self):
        mine, status, unknown = sheet_commands._parse_list_flag_tokens(["-c"])
        self.assertFalse(mine)
        self.assertEqual(status, "collecting")
        self.assertIsNone(unknown)

    def test_short_t_constructing(self):
        # constructing 取 t（避开 collecting 的 c）
        _, status, unknown = sheet_commands._parse_list_flag_tokens(["-t"])
        self.assertEqual(status, "constructing")
        self.assertIsNone(unknown)

    def test_short_a_archived(self):
        _, status, unknown = sheet_commands._parse_list_flag_tokens(["-a"])
        self.assertEqual(status, "archived")
        self.assertIsNone(unknown)

    def test_short_l_all(self):
        # -l = all → status=None（不过滤）
        _, status, unknown = sheet_commands._parse_list_flag_tokens(["-l"])
        self.assertIsNone(status)
        self.assertIsNone(unknown)

    def test_combo_ma(self):
        # -ma = mine + archived（组合简写）
        mine, status, unknown = sheet_commands._parse_list_flag_tokens(["-ma"])
        self.assertTrue(mine)
        self.assertEqual(status, "archived")
        self.assertIsNone(unknown)

    def test_combo_separate_shorts(self):
        # -m -a 分开写等价 -ma
        mine, status, unknown = sheet_commands._parse_list_flag_tokens(["-m", "-a"])
        self.assertTrue(mine)
        self.assertEqual(status, "archived")
        self.assertIsNone(unknown)

    def test_unknown_short_char_returns_token(self):
        # -x 非法字母 → unknown 回填原 token
        _, _, unknown = sheet_commands._parse_list_flag_tokens(["-x"])
        self.assertEqual(unknown, "-x")

    def test_bare_token_returns_unknown(self):
        # 裸 token（无 -- 前缀）→ unknown
        _, _, unknown = sheet_commands._parse_list_flag_tokens(["foo"])
        self.assertEqual(unknown, "foo")

    def test_long_forms_backward_compatible(self):
        # 完整 --mine --archived 仍生效（向后兼容）
        mine, status, unknown = sheet_commands._parse_list_flag_tokens(["--mine", "--archived"])
        self.assertTrue(mine)
        self.assertEqual(status, "archived")
        self.assertIsNone(unknown)

    def test_long_form_typo_returns_unknown(self):
        # 完整形式拼写错（--mining）→ unknown
        _, _, unknown = sheet_commands._parse_list_flag_tokens(["--mining"])
        self.assertEqual(unknown, "--mining")

    def test_empty_tokens_defaults_active(self):
        # 无旗标 → 默认 active
        mine, status, unknown = sheet_commands._parse_list_flag_tokens([])
        self.assertFalse(mine)
        self.assertEqual(status, "active")
        self.assertIsNone(unknown)


class ListShortIntegrationTest(unittest.TestCase):
    """_sheet_list_flags 端到端：简写透传到 list_sheets，未知简写回显提示。"""

    def test_flags_short_ma_passes_mine_archived(self):
        src, told = _make_src_server(player="玩家A")
        with mock.patch.object(sheet_commands.sheet_client, "list_sheets",
                               return_value=[]) as list_mock:
            sheet_commands._sheet_list_flags(src, {"flags": "-ma"})
        _, kwargs = list_mock.call_args
        self.assertTrue(kwargs.get("mine"))
        self.assertEqual(kwargs.get("status"), "archived")

    def test_flags_unknown_short_shows_hint(self):
        # -x 非法 → 回显「未知旗标」+ 助记提示，且不调 list_sheets
        src, told = _make_src_server(player="玩家A")
        with mock.patch.object(sheet_commands.sheet_client, "list_sheets") as list_mock:
            sheet_commands._sheet_list_flags(src, {"flags": "-x"})
        msg = str(told[0])
        self.assertIn("未知旗标", msg)
        self.assertIn("-x", msg)
        self.assertIn("-m", msg)  # 助记里列了可用简写
        list_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
