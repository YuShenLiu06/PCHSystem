"""construction_commands 单测：``_render_status`` / ``_format_outcome`` 渲染契约 +
v0.10.0 ``join``/``leave``/``current`` 命令回调（含 _resolve_outcome 错误码翻译）。

覆盖最易错的「outcome 字面量 → 中文配色」映射（字面量漂移会静默退化为 gray 兜底），
以及 ``_render_status`` 对各状态字段的呈现（启用/禁用、多项目黄色提示、从未运行兜底）。
v0.10.0 新增覆盖：玩家身份校验 / UUID 失败回执 / 各 client 返回分支（成功/4xx/哨兵/None）
的中文回执 + 渲染卡片（已加入/未加入）。
RText 在 _stubs 里 __str__ 返回纯文本，故用 ``str(...)`` 断言关键字。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401,E402  触发 stubs 安装与 sys.path 配置

import pch_system.construction_commands as cc  # noqa: E402
import pch_system.construction_client as cclient  # noqa: E402
from pch_system.config import PchSystemConfig  # noqa: E402


def _full_state():
    return {
        "enabled": True, "stats_dir": "world/stats", "stats_dir_ok": True,
        "online": 3, "active_sheets": 1, "heuristic_eligible": True,
        "flush_interval": 30.0, "last_at": "2026-07-28T00:00:00+00:00",
        "last_outcome": "ok", "last_reported": 2, "last_accepted": 2,
        "last_skipped": 0, "last_error": None, "baselined_players": 3,
    }


def _joined_active(sheet_id=7, title="主城", source="manual"):
    return {"active": {
        "sheet_id": sheet_id, "sheet_title": title,
        "joined_at": "2026-07-28T00:00:00+00:00", "join_source": source,
    }}


def _empty_active():
    return {"active": {
        "sheet_id": None, "sheet_title": None, "joined_at": None, "join_source": None,
    }}


class _FakeServer:
    """记录 server.tell 调用的最小 server stub。"""
    def __init__(self):
        self.tells = []

    def tell(self, player, msg):
        self.tells.append((player, msg))


class _FakePlayerSrc:
    """玩家源 stub：is_player=True / player="Alice" / get_server 返回 FakeServer。"""
    def __init__(self, name="Alice"):
        self.is_player = True
        self.player = name
        self._server = _FakeServer()

    def get_server(self):
        return self._server

    def reply(self, msg):
        self._server.tells.append((self.player, msg))


class _FakeConsoleSrc:
    """控制台源 stub：is_player=False。"""
    def __init__(self):
        self.is_player = False
        self.replies = []

    def reply(self, msg):
        self.replies.append(msg)


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
        s = str(cc._format_outcome("something_new"))
        self.assertIn("something_new", s)

    def test_outcome_map覆盖tracker全部字面量(self):
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
        self.assertIn("已禁用", s)

    def test_last_error附在结果行(self):
        state = _full_state()
        state["last_outcome"] = "http_error"
        state["last_error"] = "HTTP 500: boom"
        s = str(cc._render_status(state))
        self.assertIn("HTTP 错误", s)
        self.assertIn("HTTP 500: boom", s)

    def test_末尾多项目提示行常驻(self):
        self.assertIn("Web 端切客户端模组", str(cc._render_status(_full_state())))


class RenderActiveCardTest(unittest.TestCase):
    def test_已加入渲染_含项目标题与来源(self):
        card = str(cc._render_active_card(_joined_active()["active"]))
        self.assertIn("当前施工项目", card)
        self.assertIn("主城", card)
        self.assertIn("手动", card)
        self.assertIn("项目 ID", card)

    def test_auto来源文案(self):
        card = str(cc._render_active_card(_joined_active(source="auto")["active"]))
        self.assertIn("自动（备货触发）", card)

    def test_未知来源文案(self):
        card = str(cc._render_active_card(_joined_active(source=None)["active"]))
        self.assertIn("未知", card)

    def test_未加入_空态文案(self):
        card = str(cc._render_active_card({"sheet_id": None}))
        self.assertIn("未加入任何项目", card)


class ConstructionStatusCallbackTest(unittest.TestCase):
    def test_回调包new_thread并reply状态(self):
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


class JoinLeaveCurrentCallbackTest(unittest.TestCase):
    """v0.10.0 join/leave/current 命令回调端到端（mock client 返回 + 验证 tell 文案）。"""

    def setUp(self):
        # 注入测试 config（_resolve_uuid_or_tell 走 uuid_api_remake stub，无需 HTTP）
        cc.configure(PchSystemConfig())

    def _last_tell_text(self, src):
        """取最近一次 tell 的文案字符串。"""
        self.assertTrue(src._server.tells, "期望至少一次 tell")
        return str(src._server.tells[-1][1])

    # --- 控制台拒绝 ---

    def test_join控制台拒绝(self):
        src = _FakeConsoleSrc()
        cc._construction_join(src, {})
        self.assertTrue(src.replies)
        self.assertIn("只能玩家在游戏内执行", str(src.replies[-1]))

    def test_leave控制台拒绝(self):
        src = _FakeConsoleSrc()
        cc._construction_leave(src, {})
        self.assertTrue(src.replies)
        self.assertIn("只能玩家在游戏内执行", str(src.replies[-1]))

    def test_current控制台拒绝(self):
        src = _FakeConsoleSrc()
        cc._construction_current(src, {})
        self.assertTrue(src.replies)
        self.assertIn("只能玩家在游戏内执行", str(src.replies[-1]))

    # --- UUID 失败 ---

    def test_join_UUID失败红字(self):
        src = _FakePlayerSrc()
        with mock.patch.object(cc.uuid_api_remake, "get_uuid", side_effect=RuntimeError("boom")):
            cc._construction_join(src, {"sheet_id": 7})
        text = self._last_tell_text(src)
        self.assertIn("UUID 推导失败", text)

    # --- join ---

    def test_join显式sheet_id成功回执(self):
        src = _FakePlayerSrc()
        with mock.patch.object(cc.uuid_api_remake, "get_uuid", return_value="uuid-alice"), \
             mock.patch.object(cc.construction_client, "join_construction",
                                return_value=_joined_active(sheet_id=7, title="主城")):
            cc._construction_join(src, {"sheet_id": 7})
        text = self._last_tell_text(src)
        self.assertIn("已加入施工", text)
        self.assertIn("主城", text)

    def test_join无参_已加入提示先leave(self):
        src = _FakePlayerSrc()
        with mock.patch.object(cc.uuid_api_remake, "get_uuid", return_value="uuid-alice"), \
             mock.patch.object(cc.construction_client, "get_my_construction",
                                return_value=_joined_active(sheet_id=7, title="主城")):
            cc._construction_join(src, {})
        text = self._last_tell_text(src)
        self.assertIn("主城", text)
        self.assertIn("leave 后再加入", text)

    def test_join无参_未加入引导(self):
        src = _FakePlayerSrc()
        with mock.patch.object(cc.uuid_api_remake, "get_uuid", return_value="uuid-alice"), \
             mock.patch.object(cc.construction_client, "get_my_construction",
                                return_value=_empty_active()):
            cc._construction_join(src, {})
        text = self._last_tell_text(src)
        self.assertIn("未加入任何施工项目", text)
        self.assertIn("!!PCH construction join <sheet_id>", text)

    def test_join_409冲突回执detail(self):
        src = _FakePlayerSrc()
        err = cclient.HttpError(409, "已活跃加入项目 id=7，先退出或切换")
        with mock.patch.object(cc.uuid_api_remake, "get_uuid", return_value="uuid-alice"), \
             mock.patch.object(cc.construction_client, "join_construction", return_value=err):
            cc._construction_join(src, {"sheet_id": 42})
        text = self._last_tell_text(src)
        self.assertIn("已加入其他项目", text)
        self.assertIn("先退出或切换", text)

    def test_join_403未绑定账号引导bind(self):
        src = _FakePlayerSrc()
        err = cclient.HttpError(403, "player not bound to web account")
        with mock.patch.object(cc.uuid_api_remake, "get_uuid", return_value="uuid-alice"), \
             mock.patch.object(cc.construction_client, "join_construction", return_value=err):
            cc._construction_join(src, {"sheet_id": 42})
        text = self._last_tell_text(src)
        self.assertIn("未绑定 Web 账号", text)
        self.assertIn("!!PCH bind", text)

    def test_join_404项目不存在(self):
        src = _FakePlayerSrc()
        err = cclient.HttpError(404, "sheet not found")
        with mock.patch.object(cc.uuid_api_remake, "get_uuid", return_value="uuid-alice"), \
             mock.patch.object(cc.construction_client, "join_construction", return_value=err):
            cc._construction_join(src, {"sheet_id": 999})
        text = self._last_tell_text(src)
        self.assertIn("项目不存在", text)

    def test_join_RATE_LIMITED哨兵回执(self):
        src = _FakePlayerSrc()
        with mock.patch.object(cc.uuid_api_remake, "get_uuid", return_value="uuid-alice"), \
             mock.patch.object(cc.construction_client, "join_construction",
                                return_value=cclient.RATE_LIMITED):
            cc._construction_join(src, {"sheet_id": 42})
        text = self._last_tell_text(src)
        self.assertIn("操作太频繁", text)

    def test_join_网络失败None_服务不可用(self):
        src = _FakePlayerSrc()
        with mock.patch.object(cc.uuid_api_remake, "get_uuid", return_value="uuid-alice"), \
             mock.patch.object(cc.construction_client, "join_construction", return_value=None):
            cc._construction_join(src, {"sheet_id": 42})
        text = self._last_tell_text(src)
        self.assertIn("服务暂不可用", text)

    # --- leave ---

    def test_leave成功未加入幂等(self):
        src = _FakePlayerSrc()
        with mock.patch.object(cc.uuid_api_remake, "get_uuid", return_value="uuid-alice"), \
             mock.patch.object(cc.construction_client, "leave_construction",
                                return_value=_empty_active()):
            cc._construction_leave(src, {})
        text = self._last_tell_text(src)
        self.assertIn("已退出施工项目", text)

    def test_leave_REMOVED哨兵_权限不足(self):
        src = _FakePlayerSrc()
        with mock.patch.object(cc.uuid_api_remake, "get_uuid", return_value="uuid-alice"), \
             mock.patch.object(cc.construction_client, "leave_construction",
                                return_value=cclient.REMOVED):
            cc._construction_leave(src, {})
        text = self._last_tell_text(src)
        self.assertIn("权限不足", text)

    # --- current ---

    def test_current已加入回显卡片(self):
        src = _FakePlayerSrc()
        with mock.patch.object(cc.uuid_api_remake, "get_uuid", return_value="uuid-alice"), \
             mock.patch.object(cc.construction_client, "get_my_construction",
                                return_value=_joined_active(sheet_id=7, title="主城")):
            cc._construction_current(src, {})
        text = self._last_tell_text(src)
        self.assertIn("当前施工项目", text)
        self.assertIn("主城", text)

    def test_current未加入空态(self):
        src = _FakePlayerSrc()
        with mock.patch.object(cc.uuid_api_remake, "get_uuid", return_value="uuid-alice"), \
             mock.patch.object(cc.construction_client, "get_my_construction",
                                return_value=_empty_active()):
            cc._construction_current(src, {})
        text = self._last_tell_text(src)
        self.assertIn("未加入任何施工项目", text)


if __name__ == "__main__":
    unittest.main()
