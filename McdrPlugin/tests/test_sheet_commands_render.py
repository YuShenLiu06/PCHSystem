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

import pch_system.sheet_commands as sheet_commands  # noqa: E402


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
        # 非认领人非拥有者查看含 done lock 行的表：不应出现 [退]/reject 命令
        src, told = _make_src_server(player="玩家A")
        detail = self._detail_with_done_row(
            "done", 0,
            claimant_uuid="00000000-0000-0000-0000-000000000000",
            claimant_name="认领人X",
        )
        with mock.patch.object(sheet_commands.sheet_client, "view_sheet", return_value=detail):
            sheet_commands._sheet_view(src, {"sheet_id": 7})
        msg = str(told[0])
        self.assertNotIn("[退]", msg)
        cmds = _all_click_values(told[0])
        self.assertFalse(any("reject" in c for c in cmds), cmds)

    def test_claimant_done_row_sees_reject_button(self):
        # 认领人查看自己 done lock 行：应出现 [退]/reject（UUID 路径命中）
        src, told = _make_src_server(player="玩家A")
        viewer_uuid = sheet_commands.uuid_api_remake.get_uuid("玩家A")
        detail = self._detail_with_done_row(
            "done", 0, claimant_uuid=viewer_uuid, claimant_name="玩家A",
        )
        with mock.patch.object(sheet_commands.sheet_client, "view_sheet", return_value=detail):
            sheet_commands._sheet_view(src, {"sheet_id": 7})
        msg = str(told[0])
        self.assertIn("[退]", msg)
        cmds = _all_click_values(told[0])
        self.assertTrue(any("reject" in c for c in cmds), cmds)

    def test_owner_progress_row_sees_adjust_button(self):
        # owner 查看 progress 行：应出现 [调]/progress 命令（绝对值覆写，owner 专用）
        src, told = _make_src_server(player="玩家A")
        detail = self._detail_with_done_row(
            "claimed", 1, claimant_uuid=None, claimant_name=None, owner_name="玩家A",
        )
        with mock.patch.object(sheet_commands.sheet_client, "view_sheet", return_value=detail):
            sheet_commands._sheet_view(src, {"sheet_id": 7})
        msg = str(told[0])
        self.assertIn("[调]", msg)
        cmds = _all_click_values(told[0])
        self.assertTrue(any("progress" in c for c in cmds), cmds)

    def test_non_owner_progress_row_no_adjust_button(self):
        # 非 owner 查看 progress 行：无 [调]（真实权限以后端 403 为准，R-9）
        src, told = _make_src_server(player="玩家A")
        detail = self._detail_with_done_row(
            "claimed", 1, claimant_uuid=None, claimant_name=None, owner_name="别人",
        )
        with mock.patch.object(sheet_commands.sheet_client, "view_sheet", return_value=detail):
            sheet_commands._sheet_view(src, {"sheet_id": 7})
        msg = str(told[0])
        self.assertNotIn("[调]", msg)
        cmds = _all_click_values(told[0])
        self.assertFalse(any("progress" in c for c in cmds), cmds)

    def test_owner_lock_row_no_adjust_button(self):
        # owner 查看 lock 行：无 [调]（progress 专用，lock 用 delivery）
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


class SubmitBatchReceiptTest(unittest.TestCase):
    """!!submit 薄壳化（P3）：扫背包 → POST /submit-batch → 渲染回执。

    决策权威在后端 ``sheet_repo.batch_submit``；本端只扫背包 + 编排 items + 渲染 outcomes。
    覆盖：全 delivered / contribute 进度行 / skip 三桶（ready 折叠 · noise 折叠 · 逐行展示含
    行状态变化）/ 空 outcomes / 空背包 / 无 data_api / 归档 409。
    """

    def _make_src(self, player="玩家A", has_api=True):
        told = []
        server = mock.Mock()
        server.tell.side_effect = lambda name, msg: told.append(msg)
        # minecraft_data_api 插件实例替身（has_api=False 模拟未安装 → None）
        server.get_plugin_instance.return_value = mock.Mock() if has_api else None
        src = mock.Mock()
        src.is_player = True
        src.player = player
        src.get_server.return_value = server
        return src, told

    def _mk_outcome(self, *, row_id, action, item_name="x", registry_id="minecraft:x",
                 mode=0, qty=0, reason="", is_claimant=False, delivered_qty=0, need_qty=0):
        return {
            "row_id": row_id, "action": action, "item_name": item_name,
            "registry_id": registry_id, "mode": mode, "qty": qty, "reason": reason,
            "is_claimant": is_claimant, "delivered_qty": delivered_qty, "need_qty": need_qty,
        }

    def _run(self, src, *, inventory, result):
        """mock 扫背包 + submit_batch，跑 _sheet_submit_oneclick。"""
        with mock.patch.object(sheet_commands.scanner, "scan_inventory",
                               return_value=inventory), \
             mock.patch.object(sheet_commands.sheet_client, "submit_batch",
                               return_value=result) as submit_mock:
            sheet_commands._sheet_submit_oneclick(src, {"sheet_id": 7})
        return submit_mock

    def test_all_delivered_renders_done_line(self):
        # lock 行认领人交付完成 → 绿色「完成」行
        src, told = self._make_src()
        result = {
            "sheet_id": 7, "actor_uuid": "u",
            "totals": {"delivered": 1, "contributed": 0, "skipped": 0},
            "outcomes": [self._mk_outcome(row_id=1, action="delivered", item_name="铁锭",
                                       mode=0, qty=10, is_claimant=True,
                                       delivered_qty=10, need_qty=10)],
        }
        self._run(src, inventory={"minecraft:iron_ingot": 10}, result=result)
        msg = " ".join(str(m) for m in told)
        self.assertIn("铁锭", msg)
        self.assertIn("完成", msg)
        self.assertIn("已标记 1 行", msg)

    def test_contributed_renders_progress_line(self):
        # progress 行增量上交 → 累计 delivered/need（format_qty_safe 换算为组：64=1组, 128=2组）
        src, told = self._make_src()
        result = {
            "sheet_id": 7, "actor_uuid": "u",
            "totals": {"delivered": 0, "contributed": 1, "skipped": 0},
            "outcomes": [self._mk_outcome(row_id=2, action="contributed", item_name="圆石",
                                       mode=1, qty=60, delivered_qty=64, need_qty=128)],
        }
        self._run(src, inventory={"minecraft:cobblestone": 64}, result=result)
        msg = " ".join(str(m) for m in told)
        self.assertIn("圆石", msg)
        self.assertIn("累计", msg)
        self.assertIn("1组/2组", msg)

    def test_skip_ready_folded(self):
        # 已备齐 skip 行 → 折叠计数（不逐行展示物品名）
        src, told = self._make_src()
        result = {
            "sheet_id": 7, "actor_uuid": "u",
            "totals": {"delivered": 0, "contributed": 0, "skipped": 1},
            "outcomes": [self._mk_outcome(row_id=3, action="skipped", item_name="泥土",
                                       mode=1, reason="已备齐", delivered_qty=10, need_qty=10)],
        }
        self._run(src, inventory={"minecraft:dirt": 99}, result=result)
        msg = " ".join(str(m) for m in told)
        self.assertIn("已备齐/进度已满，已折叠", msg)
        # 物品名不应逐行出现（折叠了）
        self.assertNotIn("泥土", msg)

    def test_skip_noise_folded(self):
        # 与本人无关的 skip 行（他人认领 lock / progress 未携带）→ 折叠计数
        src, told = self._make_src()
        result = {
            "sheet_id": 7, "actor_uuid": "u",
            "totals": {"delivered": 0, "contributed": 0, "skipped": 2},
            "outcomes": [
                self._mk_outcome(row_id=4, action="skipped", item_name="金锭", mode=0,
                              reason="已被他人认领", is_claimant=False),
                self._mk_outcome(row_id=5, action="skipped", item_name="木板", mode=1,
                              reason="背包没有此物", is_claimant=False),
            ],
        }
        self._run(src, inventory={"minecraft:stone": 1}, result=result)
        msg = " ".join(str(m) for m in told)
        self.assertIn("与您无关已折叠", msg)
        self.assertNotIn("金锭", msg)
        self.assertNotIn("木板", msg)

    def test_skip_shown_includes_row_state_change(self):
        # 本人认领的 lock 未完成 / 后端「行状态变化」→ 逐行展示（含 reason）
        src, told = self._make_src()
        result = {
            "sheet_id": 7, "actor_uuid": "u",
            "totals": {"delivered": 0, "contributed": 0, "skipped": 2},
            "outcomes": [
                self._mk_outcome(row_id=6, action="skipped", item_name="橡木板", mode=0,
                              reason="数量不足（0/128）", is_claimant=True, need_qty=128),
                self._mk_outcome(row_id=7, action="skipped", item_name="铁锭", mode=1,
                              reason="行状态变化"),
            ],
        }
        self._run(src, inventory={"minecraft:iron_ingot": 1}, result=result)
        msg = " ".join(str(m) for m in told)
        self.assertIn("跳过 2 行", msg)
        self.assertIn("橡木板", msg)
        self.assertIn("数量不足", msg)
        self.assertIn("行状态变化", msg)

    def test_empty_outcomes_shows_no_rows(self):
        # 后端返空 outcomes（表无配 registry_id 的行）→ 无可匹配的行
        src, told = self._make_src()
        result = {"sheet_id": 7, "actor_uuid": "u",
                  "totals": {"delivered": 0, "contributed": 0, "skipped": 0},
                  "outcomes": []}
        self._run(src, inventory={"minecraft:stone": 64}, result=result)
        msg = " ".join(str(m) for m in told)
        self.assertIn("无可匹配的行", msg)

    def test_empty_inventory_short_circuits(self):
        # 空背包 → 直接回 SHEET_SUBMIT_EMPTY_INV，不调 submit_batch（避 422）
        src, told = self._make_src()
        submit_mock = self._run(src, inventory={}, result={"outcomes": []})
        msg = " ".join(str(m) for m in told)
        self.assertIn("背包为空", msg)
        submit_mock.assert_not_called()

    def test_no_data_api_shows_hint(self):
        # minecraft_data_api 未安装 → 回 SHEET_SUBMIT_NO_API，不扫背包 / 不调端点
        src, told = self._make_src(has_api=False)
        with mock.patch.object(sheet_commands.scanner, "scan_inventory") as scan_mock, \
             mock.patch.object(sheet_commands.sheet_client, "submit_batch") as submit_mock:
            sheet_commands._sheet_submit_oneclick(src, {"sheet_id": 7})
        msg = " ".join(str(m) for m in told)
        self.assertIn("minecraft_data_api", msg)
        scan_mock.assert_not_called()
        submit_mock.assert_not_called()

    def test_unknown_action_shown_explicitly_not_folded(self):
        # 后端未来新增 action（非 delivered/contributed/skipped）→ 显式逐行展示带动作名，
        # 不静默折叠（MEDIUM①：else 兜底显式化，违 coding-style「禁静默吞」）
        src, told = self._make_src()
        result = {
            "sheet_id": 7, "actor_uuid": "u",
            "totals": {"delivered": 0, "contributed": 0, "skipped": 0},
            "outcomes": [self._mk_outcome(row_id=8, action="rolled_back",
                                         item_name="铁锭", mode=0, is_claimant=True,
                                         reason="")],
        }
        self._run(src, inventory={"minecraft:iron_ingot": 1}, result=result)
        msg = " ".join(str(m) for m in told)
        self.assertIn("铁锭", msg)
        self.assertIn("未知动作", msg)
        self.assertIn("rolled_back", msg)

    def test_archived_409_shows_readonly(self):
        # 后端返 409 归档 → _resolve 译 SHEET_ARCHIVED_READONLY
        src, told = self._make_src()
        err = sheet_commands.sheet_client.HttpError(status=409, detail="项目已归档，只读")
        with mock.patch.object(sheet_commands.scanner, "scan_inventory",
                               return_value={"minecraft:stone": 1}), \
             mock.patch.object(sheet_commands.sheet_client, "submit_batch",
                               return_value=err):
            sheet_commands._sheet_submit_oneclick(src, {"sheet_id": 7})
        msg = " ".join(str(m) for m in told)
        self.assertIn("项目已归档，只读", msg)

    def test_submit_batch_receives_inventory_as_items(self):
        # submit_batch 收到的 items 即 scan_inventory 产出的 {registry_id: qty}
        src, told = self._make_src()
        result = {"sheet_id": 7, "actor_uuid": "u",
                  "totals": {"delivered": 0, "contributed": 0, "skipped": 0},
                  "outcomes": []}
        inventory = {"minecraft:stone": 64, "minecraft:dirt": 10}
        submit_mock = self._run(src, inventory=inventory, result=result)
        # 签名：submit_batch(cfg, player_uuid, sheet_id, items)
        args, _ = submit_mock.call_args
        self.assertEqual(args[2], 7)                 # sheet_id
        self.assertEqual(args[3], inventory)         # items dict 原样透传

    def test_build_submit_receipt_mixed(self):
        # 纯函数：混合 outcomes → done 头 + skip 头 + 两类折叠尾齐备
        result = {
            "sheet_id": 7, "actor_uuid": "u",
            "totals": {"delivered": 1, "contributed": 1, "skipped": 4},
            "outcomes": [
                self._mk_outcome(row_id=1, action="delivered", item_name="铁锭",
                              mode=0, qty=10, is_claimant=True, delivered_qty=10, need_qty=10),
                self._mk_outcome(row_id=2, action="contributed", item_name="圆石",
                              mode=1, qty=60, delivered_qty=100, need_qty=160),
                self._mk_outcome(row_id=3, action="skipped", item_name="泥土", mode=1, reason="已备齐"),
                self._mk_outcome(row_id=4, action="skipped", item_name="金锭", mode=0,
                              reason="已被他人认领", is_claimant=False),
                self._mk_outcome(row_id=5, action="skipped", item_name="橡木板", mode=0,
                              reason="数量不足（0/128）", is_claimant=True, need_qty=128),
                self._mk_outcome(row_id=6, action="skipped", item_name="木板", mode=1,
                              reason="背包没有此物"),
            ],
        }
        msg = str(sheet_commands._build_submit_receipt(result))
        # done 区
        self.assertIn("已标记 2 行", msg)   # 1 delivered + 1 contributed
        self.assertIn("铁锭", msg)
        self.assertIn("圆石", msg)
        # skip 区（仅逐行展示 1 行：橡木板数量不足）
        self.assertIn("跳过 1 行", msg)
        self.assertIn("橡木板", msg)
        self.assertIn("数量不足", msg)
        # 折叠尾：ready 1 + noise 2（金锭 + 木板）
        self.assertIn("另有 1 行已备齐/进度已满，已折叠", msg)
        self.assertIn("另有 2 行与您无关已折叠", msg)


class SubmitchestWiringTest(unittest.TestCase):
    """!!submitc 剥离为外部库后的接线验证（v0.10.0）。

    扫描实现不再内嵌，由 chest_scanner_lib（YuShenLiu06/mcdr-chest-scanner）提供；
    本端经 ``server.get_plugin_instance("chest_scanner_lib")`` 取库实例编排提交。
    覆盖：准星 / 坐标两模式接线、库缺失防御回执、错误码 → 中文回执映射。
    """

    def _make_src(self, player="玩家A", lib=None):
        told = []
        server = mock.Mock()
        server.tell.side_effect = lambda name, msg: told.append(msg)
        server.get_plugin_instance.return_value = lib
        src = mock.Mock()
        src.is_player = True
        src.player = player
        src.get_server.return_value = server
        return src, told

    def _lib(self, *, items=None, err=None):
        """chest_scanner_lib 插件实例替身：两个高级 API 返回同一 (items, err)。"""
        lib = mock.Mock()
        lib.find_targeted_chest.return_value = (items, err)
        lib.scan_chest_rcon.return_value = (items, err)
        return lib

    def _mk_outcome(self, *, row_id, action, item_name="x", registry_id="minecraft:x",
                    mode=0, qty=0, reason="", is_claimant=False, delivered_qty=0, need_qty=0):
        return {
            "row_id": row_id, "action": action, "item_name": item_name,
            "registry_id": registry_id, "mode": mode, "qty": qty, "reason": reason,
            "is_claimant": is_claimant, "delivered_qty": delivered_qty, "need_qty": need_qty,
        }

    def _result(self):
        return {
            "sheet_id": 7, "actor_uuid": "u",
            "totals": {"delivered": 1, "contributed": 0, "skipped": 0},
            "outcomes": [self._mk_outcome(row_id=1, action="delivered", item_name="铁锭",
                                          mode=0, qty=10, is_claimant=True,
                                          delivered_qty=10, need_qty=10)],
        }

    def test_crosshair_mode_calls_lib_and_submits(self):
        # 准星模式：lib.find_targeted_chest(server, 玩家) → items → submit_batch → 箱子回执头
        lib = self._lib(items={"minecraft:iron_ingot": 10})
        src, told = self._make_src(lib=lib)
        with mock.patch.object(sheet_commands.sheet_client, "submit_batch",
                               return_value=self._result()) as submit_mock:
            sheet_commands._submitc_oneclick(src, {"sheet_id": 7})
        lib.find_targeted_chest.assert_called_once_with(src.get_server.return_value, "玩家A")
        submit_mock.assert_called_once()
        self.assertEqual(submit_mock.call_args[0][3], {"minecraft:iron_ingot": 10})
        msg = " ".join(str(m) for m in told)
        self.assertIn("箱子提交 #7", msg)
        self.assertIn("完成", msg)

    def test_coords_mode_calls_scan_chest_rcon(self):
        # 坐标模式：走 lib.scan_chest_rcon(server, x, y, z)，不触准星 API
        lib = self._lib(items={"minecraft:stone": 64})
        src, told = self._make_src(lib=lib)
        with mock.patch.object(sheet_commands.sheet_client, "submit_batch",
                               return_value=self._result()) as submit_mock:
            sheet_commands._submitc_coords(src, {"sheet_id": 7, "x": 10, "y": 64, "z": -5})
        lib.scan_chest_rcon.assert_called_once_with(src.get_server.return_value, 10, 64, -5)
        lib.find_targeted_chest.assert_not_called()
        submit_mock.assert_called_once()

    def test_lib_missing_defensive_receipt(self):
        # 库未加载（被禁用等）→ 防御回执（不鼓励重试）+ 服务端 warning 日志，不触 submit_batch
        src, told = self._make_src(lib=None)
        with mock.patch.object(sheet_commands.sheet_client, "submit_batch") as submit_mock:
            sheet_commands._submitc_oneclick(src, {"sheet_id": 7})
        submit_mock.assert_not_called()
        msg = " ".join(str(m) for m in told)
        self.assertIn("chest_scanner_lib", msg)
        self.assertIn("联系管理员", msg)
        server = src.get_server.return_value
        server.logger.warning.assert_called_once()

    def test_error_code_mapped_to_receipt(self):
        # 库错误码 no_rcon → SHEET_SUBMIT_NO_RCON 中文回执，不触 submit_batch
        lib = self._lib(err="no_rcon")
        src, told = self._make_src(lib=lib)
        with mock.patch.object(sheet_commands.sheet_client, "submit_batch") as submit_mock:
            sheet_commands._submitc_oneclick(src, {"sheet_id": 7})
        submit_mock.assert_not_called()
        msg = " ".join(str(m) for m in told)
        self.assertIn("RCON 未运行", msg)

    def test_all_known_error_codes_hit_dedicated_receipts(self):
        # 契约护栏：_CHEST_ERR_MSG 全部 key 必须命中各自专属文案——
        # 外部库侧 key 打错字会静默落到通用 FAIL，此表是唯一仓内防线
        expected_substring = {
            "no_rcon": "RCON 未运行",
            "not_container": "不是容器方块",
            "parse_error": "箱子提交处理失败：parse_error",
            "unknown_format": "箱子提交处理失败：unknown_format",
            "empty": "箱子为空",
            "not_found": "准星 6 格内未检测到容器",
            "no_api": "minecraft_data_api 插件未加载",
            "no_pos": "无法获取玩家位置数据",
        }
        self.assertEqual(set(expected_substring), set(sheet_commands._CHEST_ERR_MSG))
        for code, fragment in expected_substring.items():
            with self.subTest(code=code):
                lib = self._lib(err=code)
                src, told = self._make_src(lib=lib)
                with mock.patch.object(sheet_commands.sheet_client, "submit_batch") as submit_mock:
                    sheet_commands._submitc_oneclick(src, {"sheet_id": 7})
                submit_mock.assert_not_called()
                msg = " ".join(str(m) for m in told)
                self.assertIn(fragment, msg)

    def test_unknown_error_code_falls_back_with_code_echo_and_log(self):
        # 未知错误码 → 通用 FAIL 回显码原文 + 服务端 warning（外部库新增码的唯一可观测点）
        lib = self._lib(err="new_code")
        src, told = self._make_src(lib=lib)
        with mock.patch.object(sheet_commands.sheet_client, "submit_batch") as submit_mock:
            sheet_commands._submitc_oneclick(src, {"sheet_id": 7})
        submit_mock.assert_not_called()
        msg = " ".join(str(m) for m in told)
        self.assertIn("箱子提交处理失败：new_code", msg)
        src.get_server.return_value.logger.warning.assert_called_once()

    def test_empty_items_receipt(self):
        # 扫描成功但箱子空（items 空字典、err=None）→ 空箱回执，不触 submit_batch
        lib = self._lib(items={})
        src, told = self._make_src(lib=lib)
        with mock.patch.object(sheet_commands.sheet_client, "submit_batch") as submit_mock:
            sheet_commands._submitc_oneclick(src, {"sheet_id": 7})
        submit_mock.assert_not_called()
        msg = " ".join(str(m) for m in told)
        self.assertIn("箱子为空", msg)


if __name__ == "__main__":
    unittest.main()
