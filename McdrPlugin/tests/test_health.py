"""health 嗅探 + 渲染单测（纯函数，mock requests.get）。

镜像 test_sheet_client.py 的 AAA + mock.patch.object 套路；覆盖状态矩阵全分支。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401  触发 stubs 安装与 sys.path 配置

import requests

import pch_system.health as health
from pch_system.config import PchSystemConfig


def _cfg(*, api_url="http://backend:8000", token="change_me_service_token",
         timeout=2.0, retries=0):
    c = PchSystemConfig()
    c.api_url = api_url
    c.service_token = token
    c.http_timeout_seconds = timeout
    c.http_retries = retries
    return c


class _Resp:
    """最小响应替身。body=None 时 json() 抛错（模拟无 JSON 体）。"""

    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no json body")
        return self._body


def _dispatch(table):
    """url 子串 → _Resp | Exception 的分派器（按 url 决定响应，覆盖 /info 回退 /healthz）。"""
    def _impl(url, *args, **kwargs):
        for key, val in table.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val
        return _Resp(404)
    return _impl


class VersionTupleTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(health._version_tuple("0.6.0"), (0, 6, 0))

    def test_semver_beats_string_compare(self):
        # 字符串比较会误判 "0.10.0" < "0.6.0"；元组比较正确
        self.assertGreater(health._version_tuple("0.10.0"), health._version_tuple("0.6.0"))

    def test_low_version_detected(self):
        self.assertLess(
            health._version_tuple("0.5.0"),
            health._version_tuple(health.MIN_BACKEND_VERSION),
        )

    def test_none_and_garbage_safe(self):
        self.assertEqual(health._version_tuple(None), (0,))
        self.assertEqual(health._version_tuple("garbage"), (0,))


class IsBackendConfiguredTest(unittest.TestCase):
    def test_default_token_not_configured(self):
        self.assertFalse(health.is_backend_configured(_cfg(token="change_me_service_token")))

    def test_empty_token_not_configured(self):
        self.assertFalse(health.is_backend_configured(_cfg(token="")))

    def test_real_token_configured(self):
        self.assertTrue(health.is_backend_configured(_cfg(token="real-token-xyz")))


class ProbeBackendTest(unittest.TestCase):
    def test_info_ok_with_version_and_web(self):
        with mock.patch.object(health.requests, "get", side_effect=_dispatch({
            "/info": _Resp(200, {"version": "0.6.0", "web_base_url": "http://web:5173",
                                 "web_online": True, "web_version": "0.6.1"}),
        })):
            st = health.probe_backend(_cfg(token="t"))
        self.assertTrue(st.online)
        self.assertEqual(st.version, "0.6.0")
        self.assertEqual(st.web_base_url, "http://web:5173")
        self.assertTrue(st.web_online)
        self.assertEqual(st.web_version, "0.6.1")

    def test_info_404_fallback_healthz(self):
        with mock.patch.object(health.requests, "get", side_effect=_dispatch({
            "/info": _Resp(404), "/healthz": _Resp(200),
        })):
            st = health.probe_backend(_cfg(token="t"))
        self.assertTrue(st.online)
        self.assertIsNone(st.version)
        self.assertIsNone(st.web_base_url)

    def test_connection_error_offline(self):
        with mock.patch.object(health.requests, "get", side_effect=_dispatch({
            "/info": requests.ConnectionError("boom"),
        })):
            st = health.probe_backend(_cfg(token="t"))
        self.assertFalse(st.online)

    def test_info_500_offline_no_fallback(self):
        # 非 404 的失败不回退 healthz
        with mock.patch.object(health.requests, "get", side_effect=_dispatch({
            "/info": _Resp(500),
        })):
            st = health.probe_backend(_cfg(token="t"))
        self.assertFalse(st.online)
        self.assertIn("500", st.detail)


class ProbeFrontendTest(unittest.TestCase):
    def test_empty_url_unknown(self):
        self.assertIsNone(health.probe_frontend("", 2.0).reachable)
        self.assertIsNone(health.probe_frontend(None, 2.0).reachable)

    def test_any_response_reachable(self):
        with mock.patch.object(health.requests, "get", return_value=_Resp(404)):
            self.assertTrue(health.probe_frontend("http://web:5173", 2.0).reachable)

    def test_connection_error_offline(self):
        with mock.patch.object(health.requests, "get",
                               side_effect=requests.ConnectionError("x")):
            self.assertFalse(health.probe_frontend("http://web:5173", 2.0).reachable)

    def test_web_online_true_trumps_self_probe(self):
        # 后端 /info 上报 web_online=True → 直接信，不发 HTTP（避开 localhost 容器内误报）
        with mock.patch.object(health.requests, "get", side_effect=AssertionError("must not probe")):
            self.assertTrue(health.probe_frontend("http://localhost:5173", 2.0, web_online=True).reachable)

    def test_web_online_false_trumps_self_probe(self):
        with mock.patch.object(health.requests, "get", side_effect=AssertionError("must not probe")):
            self.assertFalse(health.probe_frontend("http://localhost:5173", 2.0, web_online=False).reachable)

    def test_loopback_url_unknown_when_no_web_online(self):
        # web_online=None + localhost → 容器内自探无意义 → 未知（不误报不可达）
        with mock.patch.object(health.requests, "get", side_effect=AssertionError("must not probe")):
            st = health.probe_frontend("http://localhost:5173", 2.0, web_online=None)
        self.assertIsNone(st.reachable)
        self.assertIn("本机地址", st.detail)

    def test_non_loopback_probes_when_no_web_online(self):
        # web_online=None + 非回环域名 → 回退自探
        with mock.patch.object(health.requests, "get", return_value=_Resp(200)):
            self.assertTrue(health.probe_frontend("http://mc.example.com", 2.0, web_online=None).reachable)


class LoopbackUrlTest(unittest.TestCase):
    def test_loopback_hosts(self):
        for u in ("http://localhost:5173", "http://127.0.0.1:5173",
                  "http://127.1.2.3/", "https://[::1]/", "http://0.0.0.0/"):
            self.assertTrue(health._is_loopback_url(u), u)

    def test_non_loopback_hosts(self):
        for u in ("http://web", "http://web:80", "http://mc.example.com", "http://192.168.1.5:5173"):
            self.assertFalse(health._is_loopback_url(u), u)

    def test_empty_and_none_not_loopback(self):
        self.assertFalse(health._is_loopback_url(""))
        self.assertFalse(health._is_loopback_url(None))


class ClassifyTest(unittest.TestCase):
    def _patch(self, table):
        return mock.patch.object(health.requests, "get", side_effect=_dispatch(table))

    def _backend(self, findings):
        return next(f for f in findings if f.component == "backend")

    def _token(self, findings):
        return next(f for f in findings if f.component == "token")

    def test_not_configured_and_offline(self):
        with self._patch({"/info": requests.ConnectionError("x")}):
            findings = health.classify(_cfg(token="change_me_service_token"))
        b = self._backend(findings)
        self.assertEqual(b.severity, "error")
        self.assertIn("仓库/部署", {l for l, _ in b.links})
        # 后端离线 → 不探前端（不产生 frontend finding）
        self.assertEqual([f for f in findings if f.component == "frontend"], [])

    def test_configured_offline(self):
        with self._patch({"/info": requests.ConnectionError("x")}):
            findings = health.classify(_cfg(token="real"))
        b = self._backend(findings)
        self.assertEqual(b.severity, "error")
        self.assertIn("运维手册", {l for l, _ in b.links})

    def test_online_ok_frontend_ok(self):
        with self._patch({
            "/info": _Resp(200, {"version": "0.6.0", "web_base_url": "http://web:5173"}),
            "/notifications/pending": _Resp(404),   # nil player → 404，但 token 已接受
            "http://web:5173": _Resp(200),
        }):
            findings = health.classify(_cfg(token="real"))
        # plugin(ok) + backend(ok) + token(ok, 非 401=接受) + frontend(ok)
        self.assertEqual([f.severity for f in findings], ["ok", "ok", "ok", "ok"])
        self.assertEqual(
            [f.component for f in findings],
            ["plugin", "backend", "token", "frontend"],
        )

    def test_online_version_low(self):
        with self._patch({
            "/info": _Resp(200, {"version": "0.5.0", "web_base_url": "http://web:5173"}),
            "http://web:5173": _Resp(200),
        }):
            findings = health.classify(_cfg(token="real"))
        self.assertEqual(self._backend(findings).severity, "warn")
        self.assertIn("0.5.0", self._backend(findings).message)

    def test_online_with_default_token_still_ok(self):
        # 两边都用占位（后端 compare_digest 也对占位通过）→ token 探针 404=nil player 非 401
        # → 真·接受 → ok。不再靠占位串启发式猜，而是后端 401 信号裁决
        with self._patch({
            "/info": _Resp(200, {"version": "0.6.0", "web_base_url": "http://web:5173"}),
            "/notifications/pending": _Resp(404),
            "http://web:5173": _Resp(200),
        }):
            findings = health.classify(_cfg(token="change_me_service_token"))
        self.assertEqual(self._backend(findings).severity, "ok")
        self.assertEqual(self._token(findings).severity, "ok")

    def test_frontend_unknown_when_no_web_base_url(self):
        # /info 404 → healthz 判活，但 web_base_url=None → 前端「未知」
        with self._patch({"/info": _Resp(404), "/healthz": _Resp(200)}):
            findings = health.classify(_cfg(token="real"))
        f = next(f for f in findings if f.component == "frontend")
        self.assertEqual(f.severity, "warn")

    def test_frontend_unreachable(self):
        with self._patch({
            "/info": _Resp(200, {"version": "0.6.0", "web_base_url": "http://web:5173"}),
            "http://web:5173": requests.ConnectionError("x"),
        }):
            findings = health.classify(_cfg(token="real"))
        f = next(f for f in findings if f.component == "frontend")
        self.assertEqual(f.severity, "error")
        self.assertIn("前端部署文档", {l for l, _ in f.links})

    def test_online_token_mismatch(self):
        # 插件 token 与后端不一致 → /notifications/pending 401 → error
        with self._patch({
            "/info": _Resp(200, {"version": "0.6.0", "web_base_url": "http://web:5173"}),
            "/notifications/pending": _Resp(401),
            "http://web:5173": _Resp(200),
        }):
            findings = health.classify(_cfg(token="wrong-token"))
        t = self._token(findings)
        self.assertEqual(t.severity, "error")
        self.assertIn("不一致", t.message)

    def test_online_token_unknown_on_conn_err(self):
        with self._patch({
            "/info": _Resp(200, {"version": "0.6.0", "web_base_url": "http://web:5173"}),
            "/notifications/pending": requests.ConnectionError("x"),
            "http://web:5173": _Resp(200),
        }):
            findings = health.classify(_cfg(token="real"))
        self.assertEqual(self._token(findings).severity, "warn")

    def test_plugin_finding_always_first(self):
        # 插件 finding 始终在最前，且即使后端离线也 ok（能跑 classify 即已加载）
        with self._patch({"/info": requests.ConnectionError("x")}):
            findings = health.classify(_cfg(token="real"))
        self.assertEqual(findings[0].component, "plugin")
        self.assertEqual(findings[0].severity, "ok")
        # 后端离线时不探 token
        self.assertEqual([f for f in findings if f.component == "token"], [])

    def test_online_with_backend_reported_web_online(self):
        # /info 上报 web_online=True + web_version → 插件直接信，前端 ok 且显示版本
        # （即使 web_base_url 是 localhost，也不自探→不误报）。/notifications/pending 给 nil player 404
        with self._patch({
            "/info": _Resp(200, {"version": "0.6.0", "web_base_url": "http://localhost:5173",
                                 "web_online": True, "web_version": "0.6.1"}),
            "/notifications/pending": _Resp(404),
        }):
            findings = health.classify(_cfg(token="real"))
        f = next(x for x in findings if x.component == "frontend")
        self.assertEqual(f.severity, "ok")
        self.assertIn("v0.6.1", f.message)   # 前端版本号进 status


class ProbeTokenTest(unittest.TestCase):
    def test_401_rejected(self):
        with mock.patch.object(health.requests, "get", return_value=_Resp(401)):
            self.assertFalse(health.probe_token(_cfg(token="t")).accepted)

    def test_404_accepted_nil_player(self):
        # nil UUID 不在 Player 表 → 404，但 require_service_token 已通过 → 非 401 = 接受
        with mock.patch.object(health.requests, "get", return_value=_Resp(404)):
            self.assertTrue(health.probe_token(_cfg(token="t")).accepted)

    def test_200_accepted(self):
        with mock.patch.object(health.requests, "get", return_value=_Resp(200, [])):
            self.assertTrue(health.probe_token(_cfg(token="t")).accepted)

    def test_connection_error_unknown(self):
        with mock.patch.object(health.requests, "get",
                               side_effect=requests.ConnectionError("x")):
            self.assertIsNone(health.probe_token(_cfg(token="t")).accepted)

    def test_sends_service_token_header_and_nil_uuid(self):
        captured = {}

        def _capture(url, *a, **k):
            captured["headers"] = k.get("headers", {})
            captured["params"] = k.get("params", {})
            return _Resp(404)

        with mock.patch.object(health.requests, "get", side_effect=_capture):
            health.probe_token(_cfg(token="some-token"))
        self.assertEqual(captured["headers"].get("X-Service-Token"), "some-token")
        self.assertEqual(captured["params"].get("player_uuid"), health._PROBE_PLAYER_UUID)


class ResolvePluginMetaTest(unittest.TestCase):
    def _server(self, **meta_kwargs):
        s = mock.Mock()
        s.get_plugin_metadata.return_value = mock.Mock(**meta_kwargs)
        return s

    def test_reads_version_and_authors(self):
        meta = health.resolve_plugin_meta(self._server(version="0.6.1", authors=["YuShen"]))
        self.assertEqual(meta.version, "0.6.1")
        self.assertEqual(meta.author, "YuShen")

    def test_multiple_authors_joined(self):
        meta = health.resolve_plugin_meta(self._server(version="0.6.1", authors=["Alice", "Bob"]))
        self.assertEqual(meta.author, "Alice, Bob")

    def test_empty_authors_falls_back(self):
        meta = health.resolve_plugin_meta(self._server(version="0.6.1", authors=[]))
        self.assertEqual(meta.author, health.PLUGIN_AUTHOR_FALLBACK)

    def test_metadata_none(self):
        s = mock.Mock()
        s.get_plugin_metadata.return_value = None
        meta = health.resolve_plugin_meta(s)
        self.assertEqual(meta.version, "unknown")
        self.assertEqual(meta.author, health.PLUGIN_AUTHOR_FALLBACK)

    def test_api_raises_falls_back(self):
        s = mock.Mock()
        s.get_plugin_metadata.side_effect = RuntimeError("boom")
        meta = health.resolve_plugin_meta(s)
        self.assertEqual(meta.version, "unknown")
        self.assertEqual(meta.author, health.PLUGIN_AUTHOR_FALLBACK)


class RenderTest(unittest.TestCase):
    def test_console_report_contains_links_and_hint(self):
        findings = [health.Finding(
            severity="error", component="backend", message="后端不可达",
            links=(("运维手册", health.BACKEND_DOC_URL), ("最新 release", health.RELEASE_URL)),
        )]
        out = health.format_console_report(findings)
        self.assertIn("后端不可达", out)
        self.assertIn(health.RELEASE_URL, out)
        self.assertIn("!!PCH status", out)
        self.assertIn("作者：YuShen", out)   # 作者页脚（plugin_meta 缺省回落）

    def test_game_report_plain_text(self):
        findings = [
            health.Finding(severity="ok", component="plugin", message="pch_system v0.6.1"),
            health.Finding(severity="ok", component="backend", message="后端在线 v0.6.0"),
            health.Finding(severity="error", component="frontend", message="前端不可达",
                           links=(("前端部署文档", health.FRONTEND_DOC_URL),)),
        ]
        text = health.format_game_report(findings).to_plain_text()
        self.assertIn("pch_system v0.6.1", text)
        self.assertIn("后端在线", text)
        self.assertIn("前端不可达", text)
        self.assertIn("[前端部署文档]", text)
        self.assertIn("作者：YuShen", text)


class RunConsoleCheckTest(unittest.TestCase):
    def test_all_ok_logs_info(self):
        server = mock.Mock()
        server.get_plugin_metadata.return_value = mock.Mock(version="0.6.1", authors=["YuShen"])
        with mock.patch.object(health.requests, "get", side_effect=_dispatch({
            "/info": _Resp(200, {"version": "0.6.0", "web_base_url": "http://web:5173"}),
            "http://web:5173": _Resp(200),
        })):
            health.run_console_check(server, _cfg(token="real"))
        server.logger.info.assert_called_once()
        server.logger.warning.assert_not_called()

    def test_offline_logs_warning(self):
        server = mock.Mock()
        server.get_plugin_metadata.return_value = mock.Mock(version="0.6.1", authors=["YuShen"])
        with mock.patch.object(health.requests, "get", side_effect=_dispatch({
            "/info": requests.ConnectionError("x"),
        })):
            health.run_console_check(server, _cfg(token="real"))
        server.logger.warning.assert_called_once()

    def test_swallows_exceptions_never_raises(self):
        server = mock.Mock()
        with mock.patch.object(health, "classify", side_effect=RuntimeError("boom")):
            # 不得外抛
            health.run_console_check(server, _cfg(token="real"))
        server.logger.warning.assert_called()


class WorstTest(unittest.TestCase):
    def test_error_beats_ok(self):
        f = [health.Finding("ok", "backend", "x"), health.Finding("error", "frontend", "y")]
        self.assertEqual(health._worst(f), "error")

    def test_empty_defaults_ok(self):
        self.assertEqual(health._worst([]), "ok")


if __name__ == "__main__":
    unittest.main()
