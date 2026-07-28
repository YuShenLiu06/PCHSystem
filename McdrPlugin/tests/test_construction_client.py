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


if __name__ == "__main__":
    unittest.main()
