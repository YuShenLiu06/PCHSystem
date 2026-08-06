"""_sheet_addhand 默认值回归测试（issue #47）。

背景：`!!PCH sheet addhand <表id> <数量> [mode]` 的 mode 由命令树分支闭包烘入
（MCDR Literal 不入 ctx，与 _sheet_upsert 同修）。此处锁定 mode 默认值语义。
"""
import os
import sys
import unittest
from unittest import mock

# 安装替身 + 路径（必须在导入被测模块前）
sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401,E402  触发 stubs 安装与 sys.path 配置

import pch_system.sheet_commands as sc  # noqa: E402


class _FakeServer:
    """最小 server 替身：捕获 tell 回执 + 提供 get_plugin_instance（addhand 扫手持用）。"""

    def __init__(self):
        self.told = []
        self._api = mock.Mock()

    def tell(self, name, msg):
        self.told.append((name, str(msg)))

    def get_plugin_instance(self, name):
        return self._api


class _FakeSrc:
    """最小命令源替身：is_player=True，player 给定，get_server 返回捕获用 server。"""

    def __init__(self, player="tester"):
        self.player = player
        self.is_player = True
        self._server = _FakeServer()

    def get_server(self):
        return self._server

    def reply(self, msg):
        pass


class SheetAddhandDefaultsTest(unittest.TestCase):
    """mode 由 kwarg 烘入 → upsert_row 收到的 mode 参数。"""

    def _run_addhand(self, ctx, *, mode=0, held=("minecraft:iron_ingot",)):
        """跑一次 _sheet_addhand，捕获传给 sheet_client.upsert_row 的参数。"""
        src = _FakeSrc()
        captured = {}

        def _capture(*args, **kwargs):
            captured.update(
                sheet_id=args[2] if len(args) > 2 else kwargs.get("sheet_id"),
                mode=kwargs.get("mode"),
                sort=kwargs.get("sort"),
                registry_id=kwargs.get("registry_id"),
            )
            return {
                "id": 1,
                "item_name": "铁锭",
                "need_qty": kwargs.get("need"),
            }

        with mock.patch.object(sc.sheet_client, "upsert_row", side_effect=_capture), \
                mock.patch.object(sc.scanner, "read_held_item", return_value=held):
            sc._sheet_addhand(src, ctx, mode=mode)
        return src, captured

    def test_bare_mode_defaults_to_lock_zero(self):
        # 裸 addhand（无 Literal 祖先）→ 闭包烘入 mode=0（默认 lock）
        _, captured = self._run_addhand({"sheet_id": 1, "need": 5}, mode=0)
        self.assertEqual(captured["mode"], 0, "默认应 lock(0)")
        self.assertEqual(captured["sort"], 0, "省略 sort 应默认 0")
        self.assertEqual(captured["registry_id"], "minecraft:iron_ingot")

    def test_progress_branch_mode_1(self):
        # progress 字面量分支 → 闭包烘入 mode=1
        _, captured = self._run_addhand({"sheet_id": 1, "need": 10}, mode=1)
        self.assertEqual(captured["mode"], 1, "progress 应映射 mode=1")

    def test_lock_branch_mode_0_with_sort(self):
        # lock 字面量分支 → 闭包烘入 mode=0 + 显式 sort
        _, captured = self._run_addhand(
            {"sheet_id": 1, "need": 3, "sort": 7}, mode=0,
        )
        self.assertEqual(captured["mode"], 0)
        self.assertEqual(captured["sort"], 7)


if __name__ == "__main__":
    unittest.main()
