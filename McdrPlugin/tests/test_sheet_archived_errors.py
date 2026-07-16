"""归档终态写操作错误码解析回归（issue #7）。

修复前：归档项目 claim（及 collab 其余写）后端未捕获 SheetArchived → HTTP 500
→ 游戏端 `_resolve` 把 500 译成 SHEET_SERVICE_DOWN「表格服务暂不可用」。
修复后：后端统一返 409「项目已归档，只读」，`_resolve` 按 detail 含「归档」/「archiv」
识别归档态 → SHEET_ARCHIVED_READONLY；submit 在 view_sheet 后主动短路。

覆盖：
- claim 路径经 `_resolve` 识别归档 409（中文 / 英文 detail 两种文案变体）。
- 非归档 409（row conflict）仍走通用 SHEET_CONFLICT，不误判。
- submit 归档短路：view_sheet 返 status=archived → SHEET_ARCHIVED_READONLY，且不触发逐行写。
"""
import os
import sys
import unittest
from unittest import mock

# 安装替身 + 路径（必须在导入被测模块前）
sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401,E402  触发 stubs 安装与 sys.path 配置

import pch_system.sheet_commands as sc  # noqa: E402
import pch_system.sheet_client as sheet_client  # noqa: E402
from pch_system.messages import (  # noqa: E402
    SHEET_ARCHIVED_READONLY,
    SHEET_CONFLICT,
)


# === 公共替身 ===

class _FakeServer:
    def __init__(self):
        self.told = []

    def tell(self, name, msg):
        self.told.append((name, str(msg)))

    def get_plugin_instance(self, name):
        # submit 流程需要 minecraft_data_api 非 None 才继续；测试返回哨兵对象即可
        return object()


class _FakeSrc:
    def __init__(self, player="tester"):
        self.player = player
        self.is_player = True
        self._server = _FakeServer()

    def get_server(self):
        return self._server

    def reply(self, msg):
        pass


class ClaimArchivedResolveTest(unittest.TestCase):
    """claim 路径经 `_resolve` 集中识别归档 409（验证 issue #7 游戏端文案）。"""

    def _run_claim(self, outcome):
        src = _FakeSrc()
        with mock.patch.object(sc.sheet_client, "claim_row", return_value=outcome):
            sc._sheet_claim(src, {"sheet_id": 42, "row_id": 1})
        return src._server.told

    def test_claim_archived_chinese_detail(self):
        # 后端 collab/rows/sheets_crud 归档文案
        told = self._run_claim(sheet_client.HttpError(409, "项目已归档，只读"))
        self.assertEqual(told[0][1], SHEET_ARCHIVED_READONLY)

    def test_claim_archived_english_detail(self):
        # advance 路径英文文案（防后端两种文案漂移导致漏判）
        told = self._run_claim(sheet_client.HttpError(409, "sheet is archived, read-only"))
        self.assertEqual(told[0][1], SHEET_ARCHIVED_READONLY)

    def test_claim_non_archived_conflict_still_conflict(self):
        # 非归档 409（行状态非法，如对已备齐行 claim）→ 通用 SHEET_CONFLICT，不误判归档
        told = self._run_claim(sheet_client.HttpError(409, "row conflict"))
        self.assertEqual(told[0][1], SHEET_CONFLICT)


class SubmitArchivedShortCircuitTest(unittest.TestCase):
    """submit 归档短路：view 带 status=archived → 整体只读回执，零逐行写。"""

    def test_submit_on_archived_short_circuits(self):
        server = _FakeServer()
        view = {"status": "archived", "rows": []}
        with mock.patch.object(sc.scanner, "scan_inventory", return_value={}), \
                mock.patch.object(sc.sheet_client, "view_sheet", return_value=view), \
                mock.patch.object(sc.sheet_client, "deliver_row") as m_deliver, \
                mock.patch.object(sc.sheet_client, "contribute_row") as m_contrib:
            sc._sheet_submit_impl(server, "tester", "uuid-x", 42)
        self.assertTrue(server.told, "submit 归档应短路回执")
        self.assertEqual(server.told[0][1], SHEET_ARCHIVED_READONLY)
        m_deliver.assert_not_called()
        m_contrib.assert_not_called()


if __name__ == "__main__":
    unittest.main()
