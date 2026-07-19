"""bind_client 单测：request_bind_token / consume_bind_code。

复用 sheet_client 测试范式：mock requests.request，验证成功/哨兵/HttpError/None。
"""
import os
import sys
import unittest
from unittest import mock

# 安装替身 + 路径（必须在导入被测模块前）
sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401,E402  触发 stubs 安装与 sys.path 配置

import pch_system.bind_client as bc  # noqa: E402
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


class BindClientTest(unittest.TestCase):
    UUID = "11111111-2222-3333-4444-555555555555"
    NAME = "TestPlayer"

    def test_request_bind_token_success(self):
        """POST /bind/token 成功返回 short_code + expires_in。"""
        with mock.patch.object(bc.requests, "request", return_value=_resp(200, {"short_code": "ABC123", "expires_in": 600})):
            out = bc.request_bind_token(_cfg(), self.NAME, self.UUID)
        self.assertEqual(out, {"short_code": "ABC123", "expires_in": 600})

    def test_request_bind_token_headers_no_player_uuid(self):
        """request_bind_token 头不含 X-Player-UUID（玩家为自己申请）。"""
        captured = {}

        def _capture(method, url, params=None, json=None, headers=None, timeout=None):
            captured["headers"] = headers
            captured["json"] = json
            return _resp(200, {"short_code": "XYZ789"})

        with mock.patch.object(bc.requests, "request", side_effect=_capture):
            bc.request_bind_token(_cfg(), self.NAME, self.UUID)
        self.assertEqual(captured["headers"]["X-Service-Token"], "tok")
        self.assertNotIn("X-Player-UUID", captured["headers"])
        self.assertEqual(captured["json"], {"uuid": self.UUID, "name": self.NAME})

    def test_consume_bind_code_success(self):
        """POST /bind/consume 成功返回 {status, account, player}（frozen 契约，方案 §一.10）。

        对齐 /bind/confirm 的 TokenExchangeResponse 形状：
        account.username 来自 AccountBrief，player.uuid 来自 PlayerBrief。
        """
        body = {
            "status": "ok",
            "player": {"uuid": self.UUID, "name": self.NAME, "role": "user"},
            "account": {"id": 7, "is_temporary": False, "username": "webuser"},
        }
        with mock.patch.object(bc.requests, "request", return_value=_resp(200, body)):
            out = bc.consume_bind_code(_cfg(), self.UUID, "ABC123")
        self.assertEqual(out["account"]["username"], "webuser")
        self.assertEqual(out["player"]["uuid"], self.UUID)

    def test_consume_bind_code_headers_has_player_uuid(self):
        """consume_bind_code 头含 X-Player-UUID（双头，代玩家消费）。"""
        captured = {}

        def _capture(method, url, params=None, json=None, headers=None, timeout=None):
            captured["headers"] = headers
            captured["json"] = json
            return _resp(200, {"player": {}, "account": {}})

        with mock.patch.object(bc.requests, "request", side_effect=_capture):
            bc.consume_bind_code(_cfg(), self.UUID, "CODE1")
        self.assertEqual(captured["headers"]["X-Service-Token"], "tok")
        self.assertEqual(captured["headers"]["X-Player-UUID"], self.UUID)
        self.assertEqual(captured["json"], {"short_code": "CODE1"})

    def test_rate_limited_sentinel(self):
        """429 返回哨生 RATE_LIMITED。"""
        with mock.patch.object(bc.requests, "request", return_value=_resp(429)):
            out = bc.request_bind_token(_cfg(), self.NAME, self.UUID)
        self.assertEqual(out, bc.RATE_LIMITED)

    def test_forbidden_sentinel_removed(self):
        """403 返回哨生 REMOVED。"""
        with mock.patch.object(bc.requests, "request", return_value=_resp(403)):
            out = bc.consume_bind_code(_cfg(), self.UUID, "BAD")
        self.assertEqual(out, bc.REMOVED)

    def test_not_found_http_error(self):
        """404 短码无效/过期返回 HttpError。"""
        with mock.patch.object(bc.requests, "request", return_value=_resp(404, {"detail": "short code not found"})):
            out = bc.consume_bind_code(_cfg(), self.UUID, "NOEXIST")
        self.assertIsInstance(out, bc.HttpError)
        self.assertEqual(out.status, 404)
        self.assertIn("short code not found", out.detail)

    def test_http_error_text_fallback_when_json_invalid(self):
        """非 2xx 且响应非 JSON（如 5xx HTML）时，detail 回退到 resp.text[:200]。"""
        with mock.patch.object(bc.requests, "request", return_value=_resp(500, text="<html>Bad Gateway</html>")):
            out = bc.request_bind_token(_cfg(), self.NAME, self.UUID)
        self.assertIsInstance(out, bc.HttpError)
        self.assertEqual(out.status, 500)
        self.assertIn("Bad Gateway", out.detail)

    def test_conflict_http_error(self):
        """409 已绑定其他账号返回 HttpError。"""
        with mock.patch.object(bc.requests, "request", return_value=_resp(409, {"detail": "already bound to another account"})):
            out = bc.consume_bind_code(_cfg(), self.UUID, "USED")
        self.assertIsInstance(out, bc.HttpError)
        self.assertEqual(out.status, 409)

    def test_network_failure_returns_none(self):
        """网络失败返回 None（重试耗尽）。"""
        import requests as real_requests

        cfg = _cfg()
        cfg.http_retries = 2
        with mock.patch.object(bc.requests, "request", side_effect=real_requests.ConnectionError("down")):
            out = bc.request_bind_token(cfg, self.NAME, self.UUID)
        self.assertIsNone(out)

    def test_url_strips_trailing_slash(self):
        """api_url 末尾斜杠被正确剥离。"""
        cfg = _cfg()
        cfg.api_url = "http://backend:8000/"
        captured = {}

        def _capture(method, url, params=None, json=None, headers=None, timeout=None):
            captured["url"] = url
            return _resp(200, {})

        with mock.patch.object(bc.requests, "request", side_effect=_capture):
            bc.consume_bind_code(cfg, self.UUID, "X")
        self.assertEqual(captured["url"], "http://backend:8000/bind/consume")


if __name__ == "__main__":
    unittest.main()
