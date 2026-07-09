"""_sheet_addsub / _sheet_setsub / _sheet_delsub 回调单测（issue #19 子物品）。

覆盖（M1+M2+M3+M4）：
- addsub/setsub 的 mode 由命令树分支闭包烘入（MCDR Literal 不入 ctx，S-1：
  https://docs.mcdreforged.com/en/latest/code_references/command.html §Literal）：
  mode=None（裸 addsub=继承父行 / 裸 setsub=不改 mode）/ 1=progress / 0=lock。
- qty_per_unit <= 0 被回调 guard 拦截（M3，不调 upsert_row，回执 §c 倍数必须 > 0）。
- delsub 无 mode 参数，不受 Literal 不入 ctx bug 影响，确认正常。

命令树路由接线（Literal 分支→闭包 mode）属 MCDR 框架解析行为，_stubs 不实现
解析器、无法在单测层覆盖；其正确性由 S-1 联网核实 + 游戏内热重载实测保证（见
McdrPlugin/CLAUDE.md §7）。本文件直接以不同 mode 调回调，验证 handler 语义。
"""
import os
import sys
import unittest
from unittest import mock

# 安装替身 + 路径（必须在导入被测模块前）
sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401,E402  触发 stubs 安装与 sys.path 配置

import htcmc_auth.sheet_commands as sc  # noqa: E402


class _FakeServer:
    """最小 server 替身：捕获 tell 回执 + 提供 get_plugin_instance（addsub 扫手持用）。"""

    def __init__(self):
        self.told = []
        self._api = mock.Mock()

    def tell(self, name, msg):
        self.told.append((name, str(msg)))

    def get_plugin_instance(self, name):
        return self._api


class _FakeSrc:
    def __init__(self, player="tester"):
        self.player = player
        self.is_player = True
        self._server = _FakeServer()

    def get_server(self):
        return self._server

    def reply(self, msg):
        pass


# === _sheet_addsub ===

class SheetAddsubCallbackTest(unittest.TestCase):
    """mode 由闭包烘入；qty_per_unit <= 0 被 guard 拦截。"""

    def _run_addsub(self, ctx, *, mode, held=("minecraft:iron_ingot",)):
        src = _FakeSrc()
        captured = {}

        def _capture(*args, **kwargs):
            captured.update(
                sheet_id=args[2] if len(args) > 2 else kwargs.get("sheet_id"),
                mode=kwargs.get("mode"),
                qty=kwargs.get("qty_per_unit"),
                parent=kwargs.get("parent_row_id"),
            )
            # 后端继承父行时返回实际 mode（0=lock），供 _show 回显
            return {
                "id": 99,
                "registry_id": held[0],
                "qty_per_unit": kwargs.get("qty_per_unit"),
                "mode": kwargs.get("mode") if kwargs.get("mode") is not None else 0,
            }

        with mock.patch.object(sc.sheet_client, "upsert_row", side_effect=_capture), \
                mock.patch.object(sc.scanner, "read_held_item", return_value=held):
            sc._sheet_addsub(src, ctx, mode=mode)
        return src, captured

    def test_bare_mode_none_inherits_parent(self):
        # 裸 addsub → mode=None 下发，后端建子行时继承父行（不下发 body.mode）
        _, captured = self._run_addsub(
            {"sheet_id": 1, "parent_row_id": 5, "qty_per_unit": 2.0}, mode=None,
        )
        self.assertIsNone(captured["mode"], "裸 addsub 应 mode=None（继承父行）")
        self.assertEqual(captured["qty"], 2.0)
        self.assertEqual(captured["parent"], 5)

    def test_progress_branch_mode_1(self):
        _, captured = self._run_addsub(
            {"sheet_id": 1, "parent_row_id": 5, "qty_per_unit": 1.5}, mode=1,
        )
        self.assertEqual(captured["mode"], 1)

    def test_lock_branch_mode_0(self):
        _, captured = self._run_addsub(
            {"sheet_id": 1, "parent_row_id": 5, "qty_per_unit": 3.0}, mode=0,
        )
        self.assertEqual(captured["mode"], 0)

    def test_qty_zero_blocked_by_guard(self):
        # qty_per_unit=0 → guard 拦截，不调 upsert_row，回执 §c
        src = _FakeSrc()
        upsert = mock.Mock()
        with mock.patch.object(sc.sheet_client, "upsert_row", side_effect=upsert):
            sc._sheet_addsub(src, {"sheet_id": 1, "parent_row_id": 5, "qty_per_unit": 0}, mode=None)
        upsert.assert_not_called()
        self.assertTrue(src._server.told, "应有 guard 回执")
        self.assertIn("倍数必须 > 0", src._server.told[0][1])

    def test_qty_negative_blocked_by_guard(self):
        src = _FakeSrc()
        upsert = mock.Mock()
        with mock.patch.object(sc.sheet_client, "upsert_row", side_effect=upsert):
            sc._sheet_addsub(src, {"sheet_id": 1, "parent_row_id": 5, "qty_per_unit": -1.5}, mode=None)
        upsert.assert_not_called()


# === _sheet_setsub ===

class SheetSetsubCallbackTest(unittest.TestCase):
    """mode 由闭包烘入；裸 setsub mode=None=不改 mode（避免误翻 contributors）。"""

    def _run_setsub(self, ctx, *, mode):
        src = _FakeSrc()
        captured = {}

        def _capture(*args, **kwargs):
            captured.update(
                mode=kwargs.get("mode"),
                qty=kwargs.get("qty_per_unit"),
                row_id=kwargs.get("row_id"),
            )
            return {
                "id": ctx["row_id"],
                "registry_id": "minecraft:iron_ingot",
                "qty_per_unit": kwargs.get("qty_per_unit"),
                "mode": kwargs.get("mode") if kwargs.get("mode") is not None else 0,
            }

        with mock.patch.object(sc.sheet_client, "upsert_row", side_effect=_capture):
            sc._sheet_setsub(src, ctx, mode=mode)
        return src, captured

    def test_bare_mode_none_no_mode_change(self):
        # 裸 setsub → mode=None 下发，后端不改 mode（不误清 contributors）
        _, captured = self._run_setsub(
            {"sheet_id": 1, "row_id": 9, "qty_per_unit": 2.0}, mode=None,
        )
        self.assertIsNone(captured["mode"])
        self.assertEqual(captured["qty"], 2.0)

    def test_progress_branch_mode_1(self):
        _, captured = self._run_setsub(
            {"sheet_id": 1, "row_id": 9, "qty_per_unit": 2.0}, mode=1,
        )
        self.assertEqual(captured["mode"], 1)

    def test_lock_branch_mode_0(self):
        _, captured = self._run_setsub(
            {"sheet_id": 1, "row_id": 9, "qty_per_unit": 2.0}, mode=0,
        )
        self.assertEqual(captured["mode"], 0)

    def test_qty_zero_blocked_by_guard(self):
        src = _FakeSrc()
        upsert = mock.Mock()
        with mock.patch.object(sc.sheet_client, "upsert_row", side_effect=upsert):
            sc._sheet_setsub(src, {"sheet_id": 1, "row_id": 9, "qty_per_unit": 0}, mode=None)
        upsert.assert_not_called()
        self.assertTrue(src._server.told)
        self.assertIn("倍数必须 > 0", src._server.told[0][1])


# === _sheet_delsub（无 mode 参数，确认不受 bug 影响）===

class SheetDelsubCallbackTest(unittest.TestCase):
    def test_delsub_calls_delete_row(self):
        src = _FakeSrc()
        captured = {}

        def _capture(cfg, uuid, sheet_id, row_id):
            captured.update(sheet_id=sheet_id, row_id=row_id)
            return {}

        with mock.patch.object(sc.sheet_client, "delete_row", side_effect=_capture):
            sc._sheet_delsub(src, {"sheet_id": 3, "row_id": 12})
        self.assertEqual(captured["row_id"], 12)
        self.assertTrue(src._server.told)
        self.assertIn("已删子行", src._server.told[0][1])


if __name__ == "__main__":
    unittest.main()
