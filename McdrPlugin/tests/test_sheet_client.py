"""sheet_client 单测：解析成功 dict / list / 403 / 409 / 422 / 网络失败 None / 哨兵字符串。

mock requests.request（sheet_client 走统一的 _request）。
"""
import os
import sys
import unittest
from unittest import mock

# 安装替身 + 路径（必须在导入被测模块前）
sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401,E402  触发 stubs 安装与 sys.path 配置

import htcmc_auth.sheet_client as sc  # noqa: E402
from htcmc_auth.config import HtcmcAuthConfig  # noqa: E402


def _cfg():
    c = HtcmcAuthConfig()
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


class SheetClientTest(unittest.TestCase):
    UUID = "11111111-2222-3333-4444-555555555555"

    def test_list_sheets_success(self):
        with mock.patch.object(sc.requests, "request", return_value=_resp(200, [{"id": 1}])):
            out = sc.list_sheets(_cfg(), self.UUID, mine=False)
        self.assertEqual(out, [{"id": 1}])

    def test_list_sheets_mine_query(self):
        captured = {}

        def _capture(method, url, params=None, json=None, headers=None, timeout=None):
            captured["params"] = params
            return _resp(200, [])

        with mock.patch.object(sc.requests, "request", side_effect=_capture):
            sc.list_sheets(_cfg(), self.UUID, mine=True)
        self.assertEqual(captured["params"], {"owner": "me"})

    def test_headers_include_service_token_and_player_uuid(self):
        captured = {}

        def _capture(method, url, params=None, json=None, headers=None, timeout=None):
            captured["headers"] = headers
            return _resp(200, {})

        with mock.patch.object(sc.requests, "request", side_effect=_capture):
            sc.view_sheet(_cfg(), self.UUID, 5)
        self.assertEqual(captured["headers"]["X-Service-Token"], "tok")
        self.assertEqual(captured["headers"]["X-Player-UUID"], self.UUID)
        self.assertNotIn("Authorization", captured["headers"])

    def test_rate_limited_sentinel(self):
        with mock.patch.object(sc.requests, "request", return_value=_resp(429)):
            out = sc.claim_row(_cfg(), self.UUID, 1, 2)
        self.assertEqual(out, sc.RATE_LIMITED)

    def test_forbidden_sentinel_removed(self):
        with mock.patch.object(sc.requests, "request", return_value=_resp(403, {"detail": "no perms"})):
            out = sc.deliver_row(_cfg(), self.UUID, 1, 2, 3)
        self.assertEqual(out, sc.REMOVED)

    def test_not_found_http_error(self):
        with mock.patch.object(sc.requests, "request", return_value=_resp(404, {"detail": "sheet gone"})):
            out = sc.view_sheet(_cfg(), self.UUID, 99)
        self.assertIsInstance(out, sc.HttpError)
        self.assertEqual(out.status, 404)
        self.assertIn("sheet gone", out.detail)

    def test_conflict_http_error(self):
        with mock.patch.object(sc.requests, "request", return_value=_resp(409, {"detail": "bad state"})):
            out = sc.claim_row(_cfg(), self.UUID, 1, 2)
        self.assertIsInstance(out, sc.HttpError)
        self.assertEqual(out.status, 409)

    def test_unprocessable_http_error(self):
        with mock.patch.object(sc.requests, "request", return_value=_resp(422, {"detail": "mode bad"})):
            out = sc.upsert_row(_cfg(), self.UUID, 1, "i", 5, 9, 0)
        self.assertIsInstance(out, sc.HttpError)
        self.assertEqual(out.status, 422)

    def test_5xx_http_error(self):
        with mock.patch.object(sc.requests, "request", return_value=_resp(500)):
            out = sc.view_sheet(_cfg(), self.UUID, 1)
        self.assertIsInstance(out, sc.HttpError)
        self.assertEqual(out.status, 500)

    def test_network_failure_returns_none_after_retries(self):
        import requests as real_requests

        cfg = _cfg()
        cfg.http_retries = 2
        with mock.patch.object(sc.requests, "request", side_effect=real_requests.ConnectionError("down")):
            out = sc.list_sheets(cfg, self.UUID)
        self.assertIsNone(out)

    def test_delete_returns_empty_dict_on_204(self):
        with mock.patch.object(sc.requests, "request", return_value=_resp(204)):
            out = sc.delete_sheet(_cfg(), self.UUID, 1)
        self.assertEqual(out, {})

    def test_notifications_pending_params(self):
        captured = {}

        def _capture(method, url, params=None, json=None, headers=None, timeout=None):
            captured["params"] = params
            captured["method"] = method
            captured["url"] = url
            return _resp(200, [{"id": 1, "category": "sheet_claimed"}])

        with mock.patch.object(sc.requests, "request", side_effect=_capture):
            out = sc.pending_notifications(_cfg(), self.UUID, 10)
        self.assertEqual(captured["method"], "GET")
        self.assertIn("/notifications/pending", captured["url"])
        self.assertEqual(captured["params"]["player_uuid"], self.UUID)
        self.assertEqual(captured["params"]["limit"], 10)
        self.assertEqual(len(out), 1)

    def test_notifications_ack_body(self):
        captured = {}

        def _capture(method, url, params=None, json=None, headers=None, timeout=None):
            captured["json"] = json
            captured["method"] = method
            return _resp(200, {"acked": 2})

        with mock.patch.object(sc.requests, "request", side_effect=_capture):
            out = sc.ack_notifications(_cfg(), self.UUID, [1, 2])
        self.assertEqual(captured["method"], "POST")
        # body 必须带 player_uuid（后端 NotificationAckRequest 必填，防越权 ack）+ ids
        self.assertEqual(captured["json"], {"player_uuid": self.UUID, "ids": [1, 2]})
        self.assertEqual(out, {"acked": 2})

    def test_url_strips_trailing_slash(self):
        cfg = _cfg()
        cfg.api_url = "http://backend:8000/"
        captured = {}

        def _capture(method, url, params=None, json=None, headers=None, timeout=None):
            captured["url"] = url
            return _resp(200, {})

        with mock.patch.object(sc.requests, "request", side_effect=_capture):
            sc.view_sheet(cfg, self.UUID, 1)
        self.assertEqual(captured["url"], "http://backend:8000/sheets/1")

    def test_contribute_row_posts_qty(self):
        captured = {}

        def _capture(method, url, params=None, json=None, headers=None, timeout=None):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            return _resp(200, {"id": 2, "delivered_qty": 5})

        with mock.patch.object(sc.requests, "request", side_effect=_capture):
            out = sc.contribute_row(_cfg(), self.UUID, 1, 2, 5)
        self.assertEqual(captured["method"], "POST")
        self.assertIn("/sheets/1/rows/2/contribute", captured["url"])
        self.assertEqual(captured["json"], {"qty": 5})
        self.assertEqual(out["delivered_qty"], 5)

    def test_set_row_progress_patches_delivered_qty(self):
        captured = {}

        def _capture(method, url, params=None, json=None, headers=None, timeout=None):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            return _resp(200, {"id": 2, "delivered_qty": 10})

        with mock.patch.object(sc.requests, "request", side_effect=_capture):
            out = sc.set_row_progress(_cfg(), self.UUID, 1, 2, 10)
        self.assertEqual(captured["method"], "PATCH")
        self.assertIn("/sheets/1/rows/2/progress", captured["url"])
        self.assertEqual(captured["json"], {"delivered_qty": 10})
        self.assertEqual(out["delivered_qty"], 10)


if __name__ == "__main__":
    unittest.main()
