"""_sheet_set handler 回归测试（按 row_id 更新行的 need/sort；issue #20）。

背景：`!!PCH sheet set <表id> <行号> <数量> [排序]` 经
`Integer("need").runs(_sheet_set)`（可选 `Integer("sort")`）触发本回调。
mode 字面量节点不存入 ctx（MCDR 已知限制），故 ``set`` 只改 need(+sort)，mode 恒不传
（后端部分更新保留原 mode；改 mode 请用 Web 编辑器）。

本文件锁定 handler 的 ctx 抽取 → sheet_client.upsert_row 关键参映射：
- ``row_id``/``need`` 必传；``sort`` 缺省 → None（不下发）；``item``/``mode`` 恒 None。
- 成功回执走 ``SHEET_OK_ROW_UPDATED``（「已更新行」，不再与 add 共用「已 upsert」文案）。

说明：命令树路由接线属 MCDR 框架解析行为，_stubs 不实现解析器、无法在单测层覆盖；
其正确性由 S-1 联网核实 + 游戏内热重载实测保证（见 McdrPlugin/CLAUDE.md §7）。
本文件只守 handler 的抽取/回执语义不被回归。
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
    """最小 server 替身：捕获 server.tell 回执。"""

    def __init__(self):
        self.told = []

    def tell(self, name, msg):
        self.told.append((name, str(msg)))


class _FakeSrc:
    """最小命令源替身：is_player=True，player 给定，get_server 返回捕获用 server。"""

    def __init__(self, player="tester"):
        self.player = player
        self.is_player = True
        self._server = _FakeServer()

    def get_server(self):
        return self._server

    def reply(self, msg):
        # _require_player 仅在非玩家时 reply；本替身恒为玩家，不会走到此分支
        pass


class SheetSetDefaultsTest(unittest.TestCase):
    """ctx 抽取 → upsert_row 关键参（row_id 更新路径）。"""

    def _run_set(self, ctx, *, server_mode=0):
        """跑一次 _sheet_set，捕获传给 sheet_client.upsert_row 的参数 + 回执。"""
        src = _FakeSrc()
        captured = {}

        def _capture(*args, **kwargs):
            # _sheet_set 调用形如 upsert_row(CONFIG, uuid, sheet_id, item=, need=, mode=, sort=, row_id=)
            captured.update(
                sheet_id=args[2] if len(args) > 2 else kwargs.get("sheet_id"),
                item=kwargs.get("item"),
                need=kwargs.get("need"),
                mode=kwargs.get("mode"),
                sort=kwargs.get("sort"),
                row_id=kwargs.get("row_id"),
            )
            # 返回成功 dict，走 _resolve 的 on_success 分支
            return {
                "id": ctx["row_id"],
                "item_name": "铁锭",
                "need_qty": kwargs.get("need") or 0,
                "mode": server_mode,
            }

        with mock.patch.object(sc.sheet_client, "upsert_row", side_effect=_capture):
            sc._sheet_set(src, ctx)
        return src, captured

    def test_row_id_and_need_passed_sort_omitted(self):
        # ctx 仅 row_id + need —— 等价于命令树在 need 节点终止时回调入参
        src, captured = self._run_set({"sheet_id": 1, "row_id": 7, "need": 99})
        self.assertEqual(captured["row_id"], 7, "row_id 必须透传给更新路径")
        self.assertEqual(captured["need"], 99, "need 必须透传")
        self.assertIsNone(captured["sort"], "sort 缺省应为 None（不下发 → 后端不改）")
        self.assertIsNone(captured["item"], "set 不靠 item_name 定位，item 恒 None")
        self.assertIsNone(captured["mode"], "mode 恒 None（字面量不进 ctx；改 mode 走 Web）")
        self.assertEqual(captured["sheet_id"], 1)

    def test_explicit_sort_passed(self):
        _, captured = self._run_set({"sheet_id": 1, "row_id": 7, "need": 99, "sort": 3})
        self.assertEqual(captured["sort"], 3, "显式 sort 应原样透传")

    def test_success_uses_updated_message(self):
        # 回执应为「已更新行」（SHEET_OK_ROW_UPDATED），而非与 add 共用的旧「已 upsert」文案
        src, _ = self._run_set({"sheet_id": 1, "row_id": 7, "need": 99})
        self.assertTrue(src._server.told, "应有一条成功回执")
        msg = src._server.told[0][1]
        self.assertIn("已更新行", msg, "set 回执文案应为「已更新行」")
        self.assertNotIn("upsert", msg, "不应残留旧 upsert 文案")


if __name__ == "__main__":
    unittest.main()
