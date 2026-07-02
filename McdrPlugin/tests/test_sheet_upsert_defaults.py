"""_sheet_upsert 默认值回归测试。

背景：`!!PCH sheet add/set <表id> <物品> <数量>` 省略 mode/sort 时，命令树经
`Integer("need").runs(_sheet_upsert)` 触发本回调，ctx 内不含 mode/sort 键。
此处锁定回调的默认映射：mode→0(lock)、sort→0，并覆盖 progress / 显式 sort 非默认路径。

说明：命令树的路由接线（need 节点是否挂 .runs）属 MCDR 框架解析行为，_stubs 不实现
解析器、无法在单测层覆盖；其正确性由 S-1 联网核实 + 游戏内热重载实测保证（见
McdrPlugin/CLAUDE.md §7）。本文件只守 handler 的默认值语义不被回归。
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


class SheetUpsertDefaultsTest(unittest.TestCase):
    """ctx 省略 / 显式 mode/sort → upsert_row 收到的 mode/sort 参数。"""

    def _run_upsert(self, ctx):
        """跑一次 _sheet_upsert，捕获传给 sheet_client.upsert_row 的参数。"""
        src = _FakeSrc()
        captured = {}

        def _capture(cfg, uuid, sheet_id, item, need, mode, sort):
            captured.update(
                uuid=uuid, sheet_id=sheet_id, item=item, need=need, mode=mode, sort=sort
            )
            # 返回成功 dict，走 _resolve 的 on_success 分支
            return {"id": 1, "item_name": item, "need_qty": need}

        with mock.patch.object(sc.sheet_client, "upsert_row", side_effect=_capture):
            sc._sheet_upsert(src, ctx)
        return src, captured

    def test_omitted_mode_and_sort_default_to_lock_zero(self):
        # ctx 无 mode/sort —— 等价于命令树在 need 节点终止时回调入参
        _, captured = self._run_upsert({"sheet_id": 1, "item": "dirt", "need": 5})
        self.assertEqual(captured["mode"], 0, "省略 mode 应默认 lock(0)")
        self.assertEqual(captured["sort"], 0, "省略 sort 应默认 0")
        # 其余位置参数正常透传
        self.assertEqual(captured["sheet_id"], 1)
        self.assertEqual(captured["item"], "dirt")
        self.assertEqual(captured["need"], 5)

    def test_progress_literal_maps_to_mode_1(self):
        _, captured = self._run_upsert(
            {"sheet_id": 1, "item": "dirt", "need": 5, "mode": "progress"}
        )
        self.assertEqual(captured["mode"], 1, "progress 字面量应映射 mode=1")
        self.assertEqual(captured["sort"], 0)

    def test_lock_literal_and_explicit_sort(self):
        _, captured = self._run_upsert(
            {"sheet_id": 1, "item": "dirt", "need": 5, "mode": "lock", "sort": 7}
        )
        self.assertEqual(captured["mode"], 0, "lock 字面量应映射 mode=0")
        self.assertEqual(captured["sort"], 7, "显式 sort 应原样透传")


if __name__ == "__main__":
    unittest.main()
