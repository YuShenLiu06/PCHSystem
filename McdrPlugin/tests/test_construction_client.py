"""construction_client 单测。

重点验证**头模式按端点分裂**（C-1 源识别要害）：
- ``report_placements`` → 单头（仅 X-Service-Token，无 X-Player-UUID / X-Source-Id）；
- ``get_active_sheets`` → 双头（X-Service-Token + X-Player-UUID）；
以及 ``sheet_client`` 同款的 哨兵 / HttpError / None / 204 / 重试 语义。

mock requests.request（construction_client 走统一 _request）。
"""
import os
import sys
import unittest
from unittest import mock

# 安装替身 + 路径（必须在导入被测模块前）
sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401,E402  触发 stubs 安装与 sys.path 配置

import pch_system.construction_client as cc  # noqa: E402
from pch_system.config import PchSystemConfig  # noqa: E402


def _cfg():
    c = PchSystemConfig()
    c.api_url = "http://backend:8000"
    c.service_token = "tok"
    c.http_timeout_seconds = 1.0
    c.http_retries = 0
    return c


def _resp(status, body=None, *, text=""):
    r = mock.Mock()
    r.status_code = status
    r.text = text
    if body is None:
        r.json.side_effect = Exception("no json")
    else:
        r.json.return_value = body
    return r


class ConstructionClientTest(unittest.TestCase):
    UUID = "11111111-2222-3333-4444-555555555555"

    # --- 头模式分裂（核心：源识别）---

    def test_report_单头_无player_uuid_无source_id(self):
        """C-1：report 仅 X-Service-Token，绝不带 X-Player-UUID / X-Source-Id。"""
        captured = {}

        def _capture(method, url, params=None, json=None, headers=None, timeout=None):
            captured["headers"] = headers
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            return _resp(200, {"sheet_id": None, "totals": {"accepted": 1, "skipped": 0}, "outcomes": []})

        placements = [{"player_uuid": self.UUID, "registry_id": "minecraft:stone",
                       "placed_qty": 3, "broken_qty": 0}]
        with mock.patch.object(cc.requests, "request", side_effect=_capture):
            out = cc.report_placements(_cfg(), placements, sheet_id=None)
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["url"], "http://backend:8000/v1/construction/report")
        self.assertEqual(captured["headers"]["X-Service-Token"], "tok")
        self.assertNotIn("X-Player-UUID", captured["headers"], "report 必须单头，禁带 X-Player-UUID")
        self.assertNotIn("X-Source-Id", captured["headers"], "C-1：禁带 X-Source-Id（会变 server_mod）")
        self.assertNotIn("Authorization", captured["headers"])
        # body 形态
        self.assertEqual(captured["json"], {"sheet_id": None, "placements": placements})
        self.assertEqual(out["totals"]["accepted"], 1)

    def test_report_显式sheet_id透传(self):
        captured = {}

        def _capture(method, url, params=None, json=None, headers=None, timeout=None):
            captured["json"] = json
            return _resp(200, {"totals": {}})

        with mock.patch.object(cc.requests, "request", side_effect=_capture):
            cc.report_placements(_cfg(), [], sheet_id=7)
        self.assertEqual(captured["json"]["sheet_id"], 7)

    def test_active_sheets_双头_带player_uuid(self):
        """active-sheets 代玩家双头（get_current_player 双通道）。"""
        captured = {}

        def _capture(method, url, params=None, json=None, headers=None, timeout=None):
            captured["headers"] = headers
            captured["method"] = method
            captured["url"] = url
            return _resp(200, {"sheets": [{"id": 1}], "heuristic_eligible": True})

        with mock.patch.object(cc.requests, "request", side_effect=_capture):
            out = cc.get_active_sheets(_cfg(), self.UUID)
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["url"], "http://backend:8000/v1/construction/active-sheets")
        self.assertEqual(captured["headers"]["X-Service-Token"], "tok")
        self.assertEqual(captured["headers"]["X-Player-UUID"], self.UUID)
        self.assertNotIn("X-Source-Id", captured["headers"])
        self.assertTrue(out["heuristic_eligible"])

    # --- 哨兵 / HttpError / None / 204 语义（同 sheet_client）---

    def test_rate_limited_sentinel(self):
        with mock.patch.object(cc.requests, "request", return_value=_resp(429)):
            out = cc.report_placements(_cfg(), [], sheet_id=None)
        self.assertEqual(out, cc.RATE_LIMITED)

    def test_forbidden_sentinel_removed(self):
        with mock.patch.object(cc.requests, "request", return_value=_resp(403, {"detail": "no"})):
            out = cc.get_active_sheets(_cfg(), self.UUID)
        self.assertEqual(out, cc.REMOVED)

    def test_http_error_409(self):
        with mock.patch.object(cc.requests, "request", return_value=_resp(409, {"detail": "bad state"})):
            out = cc.report_placements(_cfg(), [], sheet_id=None)
        self.assertIsInstance(out, cc.HttpError)
        self.assertEqual(out.status, 409)
        self.assertIn("bad state", out.detail)

    def test_http_error_422(self):
        with mock.patch.object(cc.requests, "request", return_value=_resp(422, {"detail": "bad"})):
            out = cc.report_placements(_cfg(), [{"player_uuid": "x", "registry_id": "y",
                                                 "placed_qty": -1, "broken_qty": 0}], sheet_id=None)
        self.assertIsInstance(out, cc.HttpError)
        self.assertEqual(out.status, 422)

    def test_5xx_http_error(self):
        with mock.patch.object(cc.requests, "request", return_value=_resp(500)):
            out = cc.get_active_sheets(_cfg(), self.UUID)
        self.assertIsInstance(out, cc.HttpError)
        self.assertEqual(out.status, 500)

    def test_network_failure_returns_none_after_retries(self):
        import requests as real_requests

        cfg = _cfg()
        cfg.http_retries = 2
        with mock.patch.object(cc.requests, "request", side_effect=real_requests.ConnectionError("down")):
            out = cc.report_placements(cfg, [], sheet_id=None)
        self.assertIsNone(out)

    def test_network_failure_active_sheets_none(self):
        import requests as real_requests

        with mock.patch.object(cc.requests, "request", side_effect=real_requests.ConnectionError("down")):
            out = cc.get_active_sheets(_cfg(), self.UUID)
        self.assertIsNone(out)

    def test_non_json_body_text_in_detail(self):
        # 5xx 无 JSON body → detail 取 text[:200]
        with mock.patch.object(cc.requests, "request", return_value=_resp(502, text="gateway boom")):
            out = cc.report_placements(_cfg(), [], sheet_id=None)
        self.assertIsInstance(out, cc.HttpError)
        self.assertIn("gateway boom", out.detail)

    def test_url_strips_trailing_slash(self):
        cfg = _cfg()
        cfg.api_url = "http://backend:8000/"
        captured = {}

        def _capture(method, url, params=None, json=None, headers=None, timeout=None):
            captured["url"] = url
            return _resp(200, {"sheets": [], "heuristic_eligible": False})

        with mock.patch.object(cc.requests, "request", side_effect=_capture):
            cc.get_active_sheets(cfg, self.UUID)
        self.assertEqual(captured["url"], "http://backend:8000/v1/construction/active-sheets")


class ConstructionClientV2Test(unittest.TestCase):
    """v0.10.0 加入施工机制：lookup_active_by_uuids / join / leave / my_construction。

    鉴权分裂（与既有 ``report`` 单头 / ``active-sheets`` 双头 并列）：
    - ``lookup_active_by_uuids`` → **单头** service-token（无 X-Player-UUID；批量查询）；
    - ``/me/join|leave|construction`` → **双头** service-token + X-Player-UUID（代玩家）。
    """

    UUID_A = "11111111-2222-3333-4444-555555555555"
    UUID_B = "22222222-3333-4444-5555-666666666666"

    def _capture_headers(self):
        captured = {}

        def _cap(method, url, params=None, json=None, headers=None, timeout=None):
            captured["headers"] = headers
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            return _resp(200, {})
        return captured, _cap

    # --- lookup_active_by_uuids ---

    def test_lookup_单头_无player_uuid(self):
        """active-by-uuids 是 service-token 单头（require_service_token）。"""
        captured, _cap = self._capture_headers()
        with mock.patch.object(cc.requests, "request", side_effect=_cap):
            cc.lookup_active_by_uuids(_cfg(), [self.UUID_A, self.UUID_B])
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["url"], "http://backend:8000/v1/construction/active-by-uuids")
        self.assertEqual(captured["headers"]["X-Service-Token"], "tok")
        self.assertNotIn("X-Player-UUID", captured["headers"])
        self.assertNotIn("X-Source-Id", captured["headers"])
        # body 形态：uuid 转字符串
        self.assertEqual(
            captured["json"],
            {"player_uuids": [self.UUID_A, self.UUID_B]},
        )

    def test_lookup_解包mappings_字段(self):
        """成功 dict 时解包 mappings → dict[str, int|None] 返回。"""
        resp = _resp(200, {
            "mappings": {
                self.UUID_A: 7,
                self.UUID_B: None,
            }
        })
        with mock.patch.object(cc.requests, "request", return_value=resp):
            out = cc.lookup_active_by_uuids(_cfg(), [self.UUID_A, self.UUID_B])
        self.assertEqual(out, {self.UUID_A: 7, self.UUID_B: None})

    def test_lookup_mappings缺失_兜底返原dict(self):
        """mappings 字段缺失 / 类型异常 → 兜底返回原 dict（调用方按降级处理）。"""
        resp = _resp(200, {"unexpected": "shape"})
        with mock.patch.object(cc.requests, "request", return_value=resp):
            out = cc.lookup_active_by_uuids(_cfg(), [self.UUID_A])
        # 返回原 dict（非 dict[str,int|None]），调用方按 isinstance(_, dict) 判定后 .get() 仍容错
        self.assertIsInstance(out, dict)
        self.assertNotIn(self.UUID_A, out)

    def test_lookup_无效sheet_id值归None(self):
        """mappings 值非 int（如字符串/嵌套） → 归 None（防御后端契约偏差）。"""
        resp = _resp(200, {"mappings": {self.UUID_A: "not-an-int"}})
        with mock.patch.object(cc.requests, "request", return_value=resp):
            out = cc.lookup_active_by_uuids(_cfg(), [self.UUID_A])
        self.assertEqual(out, {self.UUID_A: None})

    def test_lookup_网络失败返回None(self):
        """网络失败 → None（调用方按降级处理）。"""
        import requests as real_requests

        with mock.patch.object(cc.requests, "request",
                               side_effect=real_requests.ConnectionError("down")):
            out = cc.lookup_active_by_uuids(_cfg(), [self.UUID_A])
        self.assertIsNone(out)

    def test_lookup_403返回REMOVED哨兵(self):
        """403 → REMOVED 哨兵（_request 统一处理，lookup 不二次解析）。"""
        with mock.patch.object(cc.requests, "request", return_value=_resp(403)):
            out = cc.lookup_active_by_uuids(_cfg(), [self.UUID_A])
        self.assertEqual(out, cc.REMOVED)

    def test_lookup_429返回RATE_LIMITED哨兵(self):
        with mock.patch.object(cc.requests, "request", return_value=_resp(429)):
            out = cc.lookup_active_by_uuids(_cfg(), [self.UUID_A])
        self.assertEqual(out, cc.RATE_LIMITED)

    def test_lookup_500返回HttpError(self):
        with mock.patch.object(cc.requests, "request", return_value=_resp(500)):
            out = cc.lookup_active_by_uuids(_cfg(), [self.UUID_A])
        self.assertIsInstance(out, cc.HttpError)
        self.assertEqual(out.status, 500)

    # --- join / leave / get_my_construction（/me/* 双头代玩家）---

    def test_join_双头带player_uuid_and_sheet_id_body(self):
        captured, _cap = self._capture_headers()
        with mock.patch.object(cc.requests, "request", side_effect=_cap):
            cc.join_construction(_cfg(), self.UUID_A, 42)
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["url"], "http://backend:8000/v1/construction/me/join")
        self.assertEqual(captured["headers"]["X-Service-Token"], "tok")
        self.assertEqual(captured["headers"]["X-Player-UUID"], self.UUID_A)
        self.assertNotIn("X-Source-Id", captured["headers"])
        self.assertEqual(captured["json"], {"sheet_id": 42})

    def test_leave_双头_无body(self):
        captured, _cap = self._capture_headers()
        with mock.patch.object(cc.requests, "request", side_effect=_cap):
            cc.leave_construction(_cfg(), self.UUID_A)
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["url"], "http://backend:8000/v1/construction/me/leave")
        self.assertEqual(captured["headers"]["X-Player-UUID"], self.UUID_A)
        # json_body=None → requests 不发 body
        self.assertIsNone(captured["json"])

    def test_get_my_construction_双头_GET(self):
        captured, _cap = self._capture_headers()
        with mock.patch.object(cc.requests, "request", side_effect=_cap):
            cc.get_my_construction(_cfg(), self.UUID_A)
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["url"], "http://backend:8000/v1/construction/me/construction")
        self.assertEqual(captured["headers"]["X-Player-UUID"], self.UUID_A)

    def test_join_409返HttpError携带detail(self):
        """冲突 409 → HttpError，detail 含「先退出或切换」提示（命令端按 detail 渲染）。"""
        with mock.patch.object(cc.requests, "request",
                               return_value=_resp(409, {"detail": "已活跃加入项目 id=7，先退出或切换"})):
            out = cc.join_construction(_cfg(), self.UUID_A, 42)
        self.assertIsInstance(out, cc.HttpError)
        self.assertEqual(out.status, 409)
        self.assertIn("先退出或切换", out.detail)

    def test_leave_网络失败返None(self):
        import requests as real_requests

        with mock.patch.object(cc.requests, "request",
                               side_effect=real_requests.ConnectionError("down")):
            out = cc.leave_construction(_cfg(), self.UUID_A)
        self.assertIsNone(out)

    def test_get_my_construction_返回active字典(self):
        """成功 dict 透传（含 active 字段；命令端 _active_state_dict 提取）。"""
        resp = _resp(200, {
            "active": {
                "sheet_id": 7, "sheet_title": "主城",
                "joined_at": "2026-07-28T00:00:00+00:00", "join_source": "manual",
            }
        })
        with mock.patch.object(cc.requests, "request", return_value=resp):
            out = cc.get_my_construction(_cfg(), self.UUID_A)
        self.assertEqual(out["active"]["sheet_id"], 7)
        self.assertEqual(out["active"]["sheet_title"], "主城")


if __name__ == "__main__":
    unittest.main()
