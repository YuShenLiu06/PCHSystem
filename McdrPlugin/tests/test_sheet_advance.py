"""_sheet_advance 回调 + advance_sheet client 单测。

覆盖：
- advance_sheet client：query 参数（to 缺省/显式）、POST 路径、成功 dict。
- _sheet_advance_impl 错误码→中文文案分支：
  400 → SHEET_BAD_TARGET / 403 → SHEET_FORBIDDEN / 404 → SHEET_NOT_FOUND /
  409 已 archived → SHEET_ARCHIVED_READONLY / 409 其他 → SHEET_CONFLICT /
  503 → SHEET_ARCHIVE_UNCONFIGURED / 429 → RATE_LIMITED / None → SERVICE_DOWN。
- 成功回执：archived（含 archived_path） / constructing。
- format_phase_label：三状态颜色映射 + 未知兜底。
- format_owner_footer：按 status 渲染流转按钮集（collecting/constructing/archived）。

依赖 tests/_stubs.py 让 @new_thread passthrough（同步执行回调），便于直接断言 server.tell。
命令树字面量→回调的路由接线（constructing/archived 字面量如何触发对应包装回调）
属 MCDR 框架解析行为，_stubs 不实现解析器，无法在单测层覆盖；其正确性由
S-1 联网核实 + 游戏内热重载实测保证（见 McdrPlugin/CLAUDE.md §7）。
"""
import os
import sys
import unittest
from unittest import mock

# 安装替身 + 路径（必须在导入被测模块前）
sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401,E402  触发 stubs 安装与 sys.path 配置

import htcmc_auth.sheet_commands as sc  # noqa: E402
import htcmc_auth.sheet_client as sheet_client  # noqa: E402
from htcmc_auth.config import HtcmcAuthConfig  # noqa: E402
from htcmc_auth.messages import (  # noqa: E402
    format_phase_label,
    format_owner_footer,
    SHEET_OK_ADVANCED_CONSTRUCTING,
    SHEET_OK_ARCHIVED,
    SHEET_ARCHIVED_READONLY,
    SHEET_ARCHIVE_UNCONFIGURED,
    SHEET_BAD_TARGET,
    SHEET_FORBIDDEN,
    SHEET_NOT_FOUND,
    SHEET_CONFLICT,
    SHEET_RATE_LIMITED,
    SHEET_SERVICE_DOWN,
)


# === 公共替身 ===

class _FakeServer:
    def __init__(self):
        self.told = []

    def tell(self, name, msg):
        self.told.append((name, str(msg)))


class _FakeSrc:
    def __init__(self, player="tester"):
        self.player = player
        self.is_player = True
        self._server = _FakeServer()

    def get_server(self):
        return self._server

    def reply(self, msg):
        pass


def _click_values(obj):
    """递归提取 RTextList 中所有按钮的 suggest_command 值。"""
    out = []
    if hasattr(obj, "_click_value"):
        out.append(obj._click_value)
    if hasattr(obj, "parts"):
        for p in obj.parts:
            out.extend(_click_values(p))
    return out


# === advance_sheet client 单测 ===

def _cfg():
    c = HtcmcAuthConfig()
    c.api_url = "http://backend:8000"
    c.service_token = "tok"
    c.http_timeout_seconds = 1.0
    c.http_retries = 0
    return c


def _resp(status, body=None):
    r = mock.Mock()
    r.status_code = status
    r.text = ""
    if body is None:
        r.json.side_effect = Exception("no json")
    else:
        r.json.return_value = body
    return r


class AdvanceSheetClientTest(unittest.TestCase):
    UUID = "11111111-2222-3333-4444-555555555555"

    def test_advance_with_explicit_to_query(self):
        captured = {}

        def _capture(method, url, params=None, json=None, headers=None, timeout=None):
            captured.update(method=method, url=url, params=params)
            return _resp(200, {"id": 1, "status": "constructing"})

        with mock.patch.object(sheet_client.requests, "request", side_effect=_capture):
            out = sheet_client.advance_sheet(_cfg(), self.UUID, 1, to="constructing")
        self.assertEqual(captured["method"], "POST")
        self.assertIn("/sheets/1/advance", captured["url"])
        self.assertEqual(captured["params"], {"to": "constructing"})
        self.assertEqual(out["status"], "constructing")

    def test_advance_default_no_to_omits_query(self):
        # to=None：不带 query params（后端按状态机默认推进）
        captured = {}

        def _capture(method, url, params=None, json=None, headers=None, timeout=None):
            captured["params"] = params
            return _resp(200, {"id": 1, "status": "archived", "archived_path": "projects/1.md"})

        with mock.patch.object(sheet_client.requests, "request", side_effect=_capture):
            out = sheet_client.advance_sheet(_cfg(), self.UUID, 1, to=None)
        self.assertIsNone(captured["params"])
        self.assertEqual(out["archived_path"], "projects/1.md")

    def test_advance_503_returns_http_error(self):
        with mock.patch.object(
            sheet_client.requests, "request",
            return_value=_resp(503, {"detail": "archive root not configured"}),
        ):
            out = sheet_client.advance_sheet(_cfg(), self.UUID, 1, to="archived")
        self.assertIsInstance(out, sheet_client.HttpError)
        self.assertEqual(out.status, 503)


# === _sheet_advance_impl 回执分支单测 ===

class SheetAdvanceCallbackTest(unittest.TestCase):
    """跑一次 _sheet_advance_impl（经包装回调），mock advance_sheet 返回值，断言回执文案。

    走包装回调 _sheet_advance_to_constructing 验证 to 透传 + 回执；
    其余错误码用 _sheet_advance_impl 直接调（to=None）。
    """

    def _run(self, outcome, *, to=None, sheet_id=42):
        src = _FakeSrc()
        with mock.patch.object(sc.sheet_client, "advance_sheet", return_value=outcome):
            sc._sheet_advance_impl(src, {"sheet_id": sheet_id}, to=to)
        return src._server.told

    def test_success_archived_includes_path(self):
        data = {"id": 42, "status": "archived", "archived_path": "projects/42.md"}
        told = self._run(data, to="archived")
        self.assertEqual(len(told), 1)
        msg = told[0][1]
        self.assertIn("已归档", msg)
        self.assertIn("#42", msg)
        self.assertIn("projects/42.md", msg)  # 含相对路径，提示玩家去 Web 看完整文档

    def test_success_constructing(self):
        data = {"id": 42, "status": "constructing"}
        told = self._run(data, to="constructing")
        self.assertEqual(len(told), 1)
        self.assertEqual(told[0][1], SHEET_OK_ADVANCED_CONSTRUCTING.format(id=42))

    def test_400_bad_target(self):
        told = self._run(sheet_client.HttpError(400, "bad to"))
        self.assertEqual(told[0][1], SHEET_BAD_TARGET)

    def test_403_forbidden(self):
        # 非 owner 触发 advance：后端 403 → 通用 SHEET_FORBIDDEN
        told = self._run(sheet_client.HttpError(403, "not owner"))
        self.assertEqual(told[0][1], SHEET_FORBIDDEN)

    def test_404_not_found(self):
        told = self._run(sheet_client.HttpError(404, "sheet gone"))
        self.assertEqual(told[0][1], SHEET_NOT_FOUND)

    def test_409_archived_readonly(self):
        # detail 含 archived → SHEET_ARCHIVED_READONLY（已归档只读，区别于通用 conflict）
        told = self._run(sheet_client.HttpError(409, "Sheet is archived"))
        self.assertEqual(told[0][1], SHEET_ARCHIVED_READONLY)

    def test_409_other_falls_to_generic_conflict(self):
        # detail 不含 archived（如非法转移）→ 通用 SHEET_CONFLICT
        told = self._run(sheet_client.HttpError(409, "illegal transition"))
        self.assertEqual(told[0][1], SHEET_CONFLICT)

    def test_503_archive_unconfigured(self):
        told = self._run(sheet_client.HttpError(503, "archive root not configured"))
        self.assertEqual(told[0][1], SHEET_ARCHIVE_UNCONFIGURED)

    def test_429_rate_limited(self):
        told = self._run(sheet_client.RATE_LIMITED)
        self.assertEqual(told[0][1], SHEET_RATE_LIMITED)

    def test_network_failure_service_down(self):
        told = self._run(None)
        self.assertEqual(told[0][1], SHEET_SERVICE_DOWN)

    def test_to_constructing_wrapper_passes_to(self):
        # 端到端验证：包装回调 _sheet_advance_to_constructing 把 to="constructing" 透传给 client
        src = _FakeSrc()
        captured = {}

        def _fake_advance(cfg, uuid, sheet_id, to):
            captured["to"] = to
            return {"id": sheet_id, "status": "constructing"}

        with mock.patch.object(sc.sheet_client, "advance_sheet", side_effect=_fake_advance):
            sc._sheet_advance_to_constructing(src, {"sheet_id": 7})
        self.assertEqual(captured["to"], "constructing")
        self.assertIn("已进入施工", src._server.told[0][1])

    def test_to_archived_wrapper_passes_to(self):
        src = _FakeSrc()
        captured = {}

        def _fake_advance(cfg, uuid, sheet_id, to):
            captured["to"] = to
            return {"id": sheet_id, "status": "archived", "archived_path": "projects/9.md"}

        with mock.patch.object(sc.sheet_client, "advance_sheet", side_effect=_fake_advance):
            sc._sheet_advance_to_archived(src, {"sheet_id": 9})
        self.assertEqual(captured["to"], "archived")
        self.assertIn("已归档", src._server.told[0][1])

    def test_default_wrapper_passes_to_none(self):
        src = _FakeSrc()
        captured = {}

        def _fake_advance(cfg, uuid, sheet_id, to):
            captured["to"] = to
            return {"id": sheet_id, "status": "constructing"}

        with mock.patch.object(sc.sheet_client, "advance_sheet", side_effect=_fake_advance):
            sc._sheet_advance_default(src, {"sheet_id": 5})
        self.assertIsNone(captured["to"])


# === format_phase_label 单测 ===

class FormatPhaseLabelTest(unittest.TestCase):
    def test_collecting_aqua(self):
        s = format_phase_label("collecting")
        self.assertEqual(s, "§b收集中§r")

    def test_constructing_yellow(self):
        s = format_phase_label("constructing")
        self.assertEqual(s, "§e施工中§r")

    def test_archived_green(self):
        s = format_phase_label("archived")
        self.assertEqual(s, "§a已归档§r")

    def test_unknown_status_gray_fallback(self):
        # 后端未来扩展状态时不应崩溃，兜底灰字原值
        s = format_phase_label("unknown_phase")
        self.assertIn("unknown_phase", s)
        self.assertTrue(s.startswith("§7"))

    def test_none_status_does_not_crash(self):
        # data.get("status") 偶发 None：_sheet_view 兜底 "collecting"，但函数本身应健壮
        s = format_phase_label(None)
        self.assertIsInstance(s, str)


# === format_owner_footer 按 status 渲染按钮集 ===

class OwnerFooterByStatusTest(unittest.TestCase):
    def test_collecting_shows_advance_and_management_buttons(self):
        rtl = format_owner_footer(3, "collecting")
        s = str(rtl)
        self.assertIn("[进入施工]", s)
        self.assertIn("[直接归档]", s)
        # 非归档态保留增删改
        self.assertIn("[新增物品]", s)
        self.assertIn("[改标题]", s)
        self.assertIn("[删表]", s)
        cmds = _click_values(rtl)
        self.assertIn("!!PCH sheet advance 3 constructing", cmds)
        self.assertIn("!!PCH sheet advance 3 archived", cmds)

    def test_constructing_shows_archive_only(self):
        rtl = format_owner_footer(3, "constructing")
        s = str(rtl)
        self.assertIn("[标记施工完成并归档]", s)
        # constructing 态不应再显「进入施工」
        self.assertNotIn("[进入施工]", s)
        self.assertNotIn("[直接归档]", s)
        # 仍保留增删改（未归档）
        self.assertIn("[新增物品]", s)
        self.assertIn("[删表]", s)
        cmds = _click_values(rtl)
        self.assertIn("!!PCH sheet advance 3 archived", cmds)
        # 不应出现 constructing 流转命令
        self.assertFalse(any("constructing" in c for c in cmds), cmds)

    def test_archived_hides_all_management_buttons(self):
        # 归档只读：不渲染任何流转 / 增删改按钮（后端对写操作返 409）
        rtl = format_owner_footer(3, "archived")
        s = str(rtl)
        self.assertNotIn("[进入施工]", s)
        self.assertNotIn("[直接归档]", s)
        self.assertNotIn("[标记施工完成并归档]", s)
        self.assertNotIn("[新增物品]", s)
        self.assertNotIn("[改标题]", s)
        self.assertNotIn("[删表]", s)
        # 无任何可点击命令
        self.assertEqual(_click_values(rtl), [])

    def test_default_status_collecting_backward_compat(self):
        # 旧调用 format_owner_footer(3) 不带 status → 默认 collecting，保留原行为
        rtl = format_owner_footer(3)
        s = str(rtl)
        self.assertIn("[新增物品]", s)
        self.assertIn("[删表]", s)
        # 默认 collecting 也应有流转按钮
        self.assertIn("[进入施工]", s)

    def test_unknown_status_treated_as_collecting(self):
        # 未知 status 保守按 collecting 显示（避免误锁管理操作）
        rtl = format_owner_footer(3, "future_phase")
        s = str(rtl)
        self.assertIn("[新增物品]", s)


# === _sheet_view 阶段横幅端到端 ===

class ViewPhaseBannerTest(unittest.TestCase):
    def test_view_shows_phase_banner(self):
        src = _FakeSrc()
        detail = {
            "id": 11, "title": "清单X", "owner_name": "tester", "status": "constructing",
            "rows": [],
        }
        with mock.patch.object(sc.sheet_client, "view_sheet", return_value=detail):
            sc._sheet_view(src, {"sheet_id": 11})
        msg = src._server.told[0][1]
        self.assertIn("[阶段:", msg)
        self.assertIn("施工中", msg)

    def test_view_archived_owner_footer_no_management_buttons(self):
        # owner 看自己已归档表：横幅显「已归档」+ footer 无增删改按钮
        src = _FakeSrc()
        detail = {
            "id": 12, "title": "归档清单", "owner_name": "tester", "status": "archived",
            "rows": [],
        }
        with mock.patch.object(sc.sheet_client, "view_sheet", return_value=detail):
            sc._sheet_view(src, {"sheet_id": 12})
        msg = src._server.told[0][1]
        self.assertIn("已归档", msg)
        self.assertNotIn("[新增物品]", msg)
        self.assertNotIn("[删表]", msg)


if __name__ == "__main__":
    unittest.main()
