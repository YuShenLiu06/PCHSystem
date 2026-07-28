"""construction_commands 单测：``_render_status`` / ``_format_outcome`` 渲染契约。

覆盖最易错的「outcome 字面量 → 中文配色」映射（字面量漂移会静默退化为 gray 兜底），
以及 ``_render_status`` 对各状态字段的呈现（启用/禁用、多项目黄色提示、从未运行兜底）。
RText 在 _stubs 里 __str__ 返回纯文本，故用 ``str(...)`` 断言关键字。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401,E402  触发 stubs 安装与 sys.path 配置

import pch_system.construction_commands as cc  # noqa: E402


def _full_state():
    return {
        "enabled": True, "stats_dir": "world/stats", "stats_dir_ok": True,
        "online": 3, "active_sheets": 1, "heuristic_eligible": True,
        "flush_interval": 30.0, "last_at": "2026-07-28T00:00:00+00:00",
        "last_outcome": "ok", "last_reported": 2, "last_accepted": 2,
        "last_skipped": 0, "last_error": None, "baselined_players": 3,
    }


class FormatOutcomeTest(unittest.TestCase):
    def test_已知outcome映射中文(self):
        self.assertIn("成功上报", str(cc._format_outcome("ok")))
        self.assertIn("被限频", str(cc._format_outcome("rate_limited")))
        self.assertIn("stats 目录不存在", str(cc._format_outcome("stats_dir_missing")))
        self.assertIn("拉取施工项目失败", str(cc._format_outcome("fetch_failed")))
        self.assertIn("多项目", str(cc._format_outcome("skipped_no_attribution")))

    def test_None映射尚未运行(self):
        self.assertIn("尚未运行", str(cc._format_outcome(None)))

    def test_未知outcome兜底带原值(self):
        # 未来新增字面量若忘登 _OUTCOME_MAP → gray 兜底带原值（不静默错配）
        s = str(cc._format_outcome("something_new"))
        self.assertIn("something_new", s)

    def test_outcome_map覆盖tracker全部字面量(self):
        """契约：_OUTCOME_MAP 键 == construction_tracker._flush_once 产出的全部 outcome。

        tracker 的 outcome 字面量散落在 _record(...) 调用点；此处硬编码全集断言
        _OUTCOME_MAP 不缺键（任一端新增字面量忘了同步另一端 → 本测即红）。
        """
        tracker_outcomes = {
            "disabled", "stats_dir_missing", "no_online", "fetch_failed",
            "no_placements", "skipped_no_attribution", "ok", "report_failed",
            "rate_limited", "removed", "http_error",
        }
        self.assertEqual(tracker_outcomes, set(cc._OUTCOME_MAP.keys()))


class RenderStatusTest(unittest.TestCase):
    def test_完整状态含标题与关键字段(self):
        s = str(cc._render_status(_full_state()))
        self.assertIn("施工进度追踪器", s)
        self.assertIn("已启用", s)
        self.assertIn("world/stats", s)
        self.assertIn("存在", s)
        self.assertIn("可自动归因", s)
        self.assertIn("成功上报", s)
        self.assertIn("30.0 秒", s)

    def test_禁用状态文案(self):
        state = _full_state()
        state["enabled"] = False
        self.assertIn("已禁用", str(cc._render_status(state)))

    def test_stats目录不存在红色(self):
        state = _full_state()
        state["stats_dir_ok"] = False
        self.assertIn("不存在", str(cc._render_status(state)))

    def test_多项目黄色提示(self):
        state = _full_state()
        state["heuristic_eligible"] = False
        state["active_sheets"] = 2
        self.assertIn("无法自动归因", str(cc._render_status(state)))

    def test_从未运行过_空dict容错(self):
        s = str(cc._render_status({}))
        self.assertIn("施工进度追踪器", s)
        self.assertIn("尚未运行过一次", s)
        self.assertIn("已禁用", s)  # enabled 缺省 False → 禁用文案

    def test_last_error附在结果行(self):
        state = _full_state()
        state["last_outcome"] = "http_error"
        state["last_error"] = "HTTP 500: boom"
        s = str(cc._render_status(state))
        self.assertIn("HTTP 错误", s)
        self.assertIn("HTTP 500: boom", s)

    def test_末尾多项目提示行常驻(self):
        self.assertIn("Web 端切客户端模组", str(cc._render_status(_full_state())))


class ConstructionStatusCallbackTest(unittest.TestCase):
    def test_回调包new_thread并reply状态(self):
        """_construction_status 调 get_status → reply（@new_thread stub 同步直跑）。"""
        called = {}

        class FakeSrc:
            def reply(self, msg):
                called["reply"] = msg

        with mock.patch.object(cc.construction_tracker, "get_status", return_value={"enabled": True}):
            cc._construction_status(FakeSrc(), {})
        self.assertIn("reply", called)
        self.assertIn("施工进度追踪器", str(called["reply"]))

    def test_回调get_status异常回执红字(self):
        class FakeSrc:
            def __init__(self):
                self.msg = None

            def reply(self, msg):
                self.msg = msg

        src = FakeSrc()
        with mock.patch.object(cc.construction_tracker, "get_status", side_effect=RuntimeError("boom")):
            cc._construction_status(src, {})
        self.assertIn("状态查询失败", str(src.msg))


if __name__ == "__main__":
    unittest.main()
