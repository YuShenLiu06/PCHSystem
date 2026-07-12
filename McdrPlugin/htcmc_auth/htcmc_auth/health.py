"""前后端可达性嗅探 + 自检报告（on_load 控制台自检 + ``!!PCH status``）。

纯函数为主，便于单测（mock ``requests.get``）。所有探针设计为 **1 次尝试、短超时、
best-effort 吞异常**，由调用方放进 ``@new_thread`` 后台线程跑（RS-6），绝不阻塞/炸 ``on_load``。

状态矩阵与设计见 ``Docs/Reports/mcdr-publishing-strategy.md`` §5 与计划
``.claude/plans/mcdr-release-generic-fern.md``。

MCDR API 已联网核实（S-1）：
  - ``server.logger.info|warning``：<https://docs.mcdreforged.com/zh-cn/latest/plugin_dev/event.html>
  - ``@new_thread``：本仓 ``_start_notifier`` / ``_login`` 已用，实测可行
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import requests
from mcdreforged.api.rtext import RColor, RStyle, RText, RTextList, RAction

from .config import HtcmcAuthConfig
from .messages import rtext_link

_log = logging.getLogger("htcmc_auth.health")

# === 常量 ===

# service_token 占位串（config.py 默认值）。真部署必轮换（install.sh 用 openssl rand 生成），
# 故「token 仍是占位」=「插件未配置」——这是「是否安装过」唯一可靠的可区分信号（R-7 不落持久化状态）。
DEFAULT_SERVICE_TOKEN = "change_me_service_token"
MIN_BACKEND_VERSION = "0.6.0"

REPO_URL = "https://github.com/YuShenLiu06/PCHSystem"
RELEASE_URL = "https://github.com/YuShenLiu06/PCHSystem/releases/latest"
BACKEND_DOC_URL = "https://github.com/YuShenLiu06/PCHSystem/blob/main/Docs/RUNBOOK.md"
FRONTEND_DOC_URL = (
    "https://github.com/YuShenLiu06/PCHSystem/blob/main/Docs/architecture/frontend.md"
)

# 本插件 id（mcdreforged.plugin.json:id），用于 get_plugin_metadata 取自身元数据
PLUGIN_ID = "htcmc_auth"
# 元数据解析失败时回落的作者名（json:author = "YuShen"）
PLUGIN_AUTHOR_FALLBACK = "YuShen"

# 探针单次超时上限：on_load 即使在后台线程也不宜久等
_PROBE_TIMEOUT_CAP = 3.0

# token 探针用的占位玩家 UUID：nil UUID 绝不在 Player 表（离线 UUID 由玩家名确定性
# 推导，永不为 nil）。后端 require_service_token 先于 _require_player 解析
# （FastAPI dependencies 顺序），故 401 = token 错；token 对则走到 _require_player
# 返 404（player not found）。非 401 即「token 被接受」。
_PROBE_PLAYER_UUID = "00000000-0000-0000-0000-000000000000"

# severity → (rank, 控制台前缀, 游戏色, 游戏符号)
_RANK = {"ok": 0, "warn": 1, "error": 2}
_CONSOLE_PREFIX = {"ok": "[OK]", "warn": "[WARN]", "error": "[ERROR]"}
_GAME_COLOR = {"ok": RColor.green, "warn": RColor.yellow, "error": RColor.red}
_GAME_SYM = {"ok": "✓", "warn": "⚠", "error": "✗"}
_COMP_LABEL = {"plugin": "插件", "backend": "后端", "token": "令牌", "frontend": "前端"}


# === 数据类（不可变）===


@dataclass(frozen=True)
class BackendStatus:
    online: bool
    version: Optional[str] = None         # /info 返回的真版本；/healthz 回退或离线时 None
    web_base_url: Optional[str] = None    # /info 返回的前端地址（面向浏览器）；回退或离线时 None
    web_online: Optional[bool] = None     # /info 返回的前端可达性（后端探 web_probe_url）；None=后端未上报
    web_version: Optional[str] = None     # /info 返回的前端版本（version.json）；未探/旧版 None
    detail: str = ""                      # 失败原因（供日志排查）


@dataclass(frozen=True)
class FrontendStatus:
    reachable: Optional[bool] = None    # True=收到响应 / False=异常 / None=未探测（无 url）
    detail: str = ""


@dataclass(frozen=True)
class Finding:
    """一条自检结论：严重度 + 组件 + 完整文案 + 可点链接。renderer 只做展示。"""

    severity: str   # "ok" | "warn" | "error"
    component: str  # "plugin" | "backend" | "token" | "frontend"
    message: str
    links: tuple[tuple[str, str], ...] = ()   # ((label, url), ...)


@dataclass(frozen=True)
class TokenStatus:
    """token 探针结果：``accepted`` 三态区分「确认对 / 确认错 / 无法判定」。"""

    accepted: Optional[bool]   # True=后端接受 / False=401 拒绝 / None=探针异常无法判定
    detail: str = ""


@dataclass(frozen=True)
class PluginMeta:
    """本插件自身元数据（version + author），供「插件」finding 与作者页脚渲染。"""

    version: str          # "unknown" 表示未能解析（packed 插件异常等）
    author: str           # 回落 PLUGIN_AUTHOR_FALLBACK


# === 工具 ===


def _is_loopback_url(url: str) -> bool:
    """``web_base_url`` 指向回环/本机 → 插件容器内 ``requests.get`` 会命中容器自身。

    ``web_base_url`` 是面向玩家浏览器的地址（``!!PCH login`` 回链）。当它用 localhost/
    127.x 时，MCDR 插件从容器内探该地址探测的是容器自身（无前端端口），不代表玩家侧
    可达性——此时自探结果无意义，应判「未知」而非误报「不可达」。
    """
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    return host in ("localhost", "::1", "0.0.0.0") or host.startswith("127.")


def _probe_timeout(cfg: HtcmcAuthConfig) -> float:
    return min(cfg.http_timeout_seconds, _PROBE_TIMEOUT_CAP)


def _version_tuple(v: Optional[str]) -> tuple[int, ...]:
    """``'0.6.0'`` → ``(0, 6, 0)``；非数字段当 0；空/None → ``(0,)``。

    仅做粗粒度「低于推荐版本」告警，不做严格 API 协商（catalogue 混版本兜底用）。
    """
    if not v:
        return (0,)
    parts: list[int] = []
    for seg in v.split("."):
        digits = "".join(ch for ch in seg if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def _worst(findings: list[Finding]) -> str:
    if not findings:
        return "ok"
    return max(findings, key=lambda f: _RANK.get(f.severity, 0)).severity


# === 探针（纯函数，mockable）===


def is_backend_configured(cfg: HtcmcAuthConfig) -> bool:
    """``service_token`` 已脱离默认占位 → 视为「已配置」。"""
    return bool(cfg.service_token) and cfg.service_token != DEFAULT_SERVICE_TOKEN


def probe_backend(cfg: HtcmcAuthConfig) -> BackendStatus:
    """``GET {api_url}/info``（404 回退 ``/healthz``），1 次尝试不重试，best-effort 吞异常。"""
    timeout = _probe_timeout(cfg)
    base = cfg.api_url.rstrip("/")
    try:
        resp = requests.get(f"{base}/info", timeout=timeout)
    except requests.RequestException as e:
        return BackendStatus(online=False, detail=repr(e))

    if resp.status_code == 404:
        # 旧后端无 /info → 回退 /healthz 仅判活（拿不到 version/web_base_url）
        return _probe_healthz(base, timeout)
    if 200 <= resp.status_code < 300:
        try:
            data = resp.json() or {}
        except ValueError as e:
            return BackendStatus(online=False, detail=f"/info parse error: {e!r}")
        return BackendStatus(
            online=True,
            version=(data.get("version") or None),
            web_base_url=(data.get("web_base_url") or None),
            web_online=data.get("web_online"),
            web_version=(data.get("web_version") or None),
        )
    return BackendStatus(online=False, detail=f"/info HTTP {resp.status_code}")


def _probe_healthz(base: str, timeout: float) -> BackendStatus:
    try:
        resp = requests.get(f"{base}/healthz", timeout=timeout)
    except requests.RequestException as e:
        return BackendStatus(online=False, detail=repr(e))
    if 200 <= resp.status_code < 300:
        return BackendStatus(online=True, version=None, web_base_url=None)
    return BackendStatus(online=False, detail=f"/healthz HTTP {resp.status_code}")


def probe_frontend(
    web_base_url: Optional[str],
    timeout: float,
    web_online: Optional[bool] = None,
) -> FrontendStatus:
    """前端可达性探测。

    优先信后端 ``/info`` 的 ``web_online``（后端与 web 同 compose 网络探服务名最可靠，
    避开 ``web_base_url`` 的 localhost 在插件容器内命中容器自身的误报）：

      * ``web_online`` 非 None（True/False）→ 直接采用（detail 标 backend-reported）。
      * ``web_online`` is None（旧后端 / 后端未配 ``WEB_PROBE_URL``）→ 回退自探
        ``web_base_url``：回环地址（localhost/127.x）→ ``None``（未知，容器内无法验证
        玩家侧）；非回环 → ``GET`` 收任意响应=在线、``RequestException``=离线。

    前端「已装 vs 离线」HTTP 层无法可靠区分（nginx 返 404 也是有服务），诚实兜底。
    """
    if web_online is not None:
        return FrontendStatus(reachable=web_online, detail="backend-reported via /info")
    if not web_base_url:
        return FrontendStatus(reachable=None, detail="后端未上报 web_base_url")
    if _is_loopback_url(web_base_url):
        return FrontendStatus(
            reachable=None,
            detail=f"本机地址（{web_base_url}）：插件容器内无法验证玩家浏览器侧可达性",
        )
    try:
        resp = requests.get(web_base_url, timeout=timeout)
    except requests.RequestException as e:
        return FrontendStatus(reachable=False, detail=repr(e))
    return FrontendStatus(reachable=True, detail=f"HTTP {resp.status_code}")


def resolve_plugin_meta(server) -> PluginMeta:
    """从 MCDR 取本插件元数据（version + author）；best-effort，失败回落常量。

    S-1：``PluginServerInterface`` 继承 ``ServerInterface.get_plugin_metadata(plugin_id)``
    返 ``Metadata``（``.version: Version``、``.authors: list``）——
    https://docs.mcdreforged.com/en/latest/code_references/ServerInterface.html 。
    任何异常（插件未加载完 / packed 加载异常 / 测试 mock 缺失）→ 回落，绝不抛。
    """
    try:
        meta = server.get_plugin_metadata(PLUGIN_ID)
    except Exception:
        return PluginMeta(version="unknown", author=PLUGIN_AUTHOR_FALLBACK)
    if meta is None:
        return PluginMeta(version="unknown", author=PLUGIN_AUTHOR_FALLBACK)
    version = "unknown"
    try:
        if getattr(meta, "version", None) is not None:
            version = str(meta.version)
    except Exception:
        pass
    author = PLUGIN_AUTHOR_FALLBACK
    try:
        authors = list(getattr(meta, "authors", None) or [])
        if authors:
            author = ", ".join(str(a) for a in authors)
    except Exception:
        pass
    return PluginMeta(version=version, author=author)


def probe_token(cfg: HtcmcAuthConfig) -> TokenStatus:
    """``GET /notifications/pending?player_uuid=<nil>&limit=1`` 带 ``X-Service-Token``。

    后端 ``require_service_token`` 先于 ``_require_player`` 解析（FastAPI
    ``dependencies=[Depends(require_service_token)]`` 顺序）：

      * **401** → token 与后端 ``MCDR_SERVICE_TOKEN`` 不一致（``compare_digest`` 拒绝）
        —— 这是真·鉴权失败信号（区别于 ``/info`` 公开端点只能证可达性）。
      * **非 401**（404 player not found / 200 / 400）→ token 被接受。
      * ``RequestException`` → ``accepted=None``（探针异常无法判定，不噪声误报）。

    1 次尝试不重试，best-effort 吞异常，由调用方放 ``@new_thread`` 后台线程。
    """
    timeout = _probe_timeout(cfg)
    base = cfg.api_url.rstrip("/")
    headers = {"X-Service-Token": cfg.service_token or ""}
    try:
        resp = requests.get(
            f"{base}/notifications/pending",
            headers=headers,
            params={"player_uuid": _PROBE_PLAYER_UUID, "limit": 1},
            timeout=timeout,
        )
    except requests.RequestException as e:
        return TokenStatus(accepted=None, detail=repr(e))
    if resp.status_code == 401:
        return TokenStatus(accepted=False, detail="HTTP 401")
    return TokenStatus(accepted=True, detail=f"HTTP {resp.status_code}")


# === 分类（facts → findings，文案在此烘焙完整）===


def classify(
    cfg: HtcmcAuthConfig,
    plugin_meta: Optional[PluginMeta] = None,
) -> list[Finding]:
    """组合 插件 / 后端 / 令牌 / 前端 探针，按状态矩阵产出 findings（含应展示链接）。

    ``plugin_meta`` 缺省时回落 ``PluginMeta("unknown", fallback)``——便于纯函数单测
    （无需 MCDR server）。生产由调用方先 ``resolve_plugin_meta(server)`` 再传入。
    """
    if plugin_meta is None:
        plugin_meta = PluginMeta(version="unknown", author=PLUGIN_AUTHOR_FALLBACK)
    findings: list[Finding] = []

    # --- 插件（始终 ok：能跑这段就证明插件已加载；展示自身版本号便于排障对齐）---
    ver = plugin_meta.version
    if ver and ver != "unknown":
        findings.append(Finding(severity="ok", component="plugin", message=f"htcmc_auth v{ver}"))
    else:
        findings.append(Finding(severity="ok", component="plugin", message="htcmc_auth（版本未知）"))

    configured = is_backend_configured(cfg)
    backend = probe_backend(cfg)

    # --- 后端 ---
    if not backend.online:
        if configured:
            findings.append(Finding(
                severity="error",
                component="backend",
                message=f"后端不可达：{cfg.api_url}（已配置但连不上，可能未启动/地址错）",
                links=(("运维手册", BACKEND_DOC_URL), ("最新 release", RELEASE_URL)),
            ))
        else:
            findings.append(Finding(
                severity="error",
                component="backend",
                message=(
                    "后端尚未部署/配置：service_token 仍是默认值，且 "
                    f"{cfg.api_url} 不可达。请部署后端后，在 config.json 把 "
                    "api_url / service_token 改为真实值（service_token 与后端 .env 的 "
                    "MCDR_SERVICE_TOKEN 同值）"
                ),
                links=(("仓库/部署", REPO_URL), ("最新 release", RELEASE_URL)),
            ))
    else:
        # 后端在线：版本分档（在线/低版本）
        ver_text = f" v{backend.version}" if backend.version else ""
        too_low = backend.version is not None and (
            _version_tuple(backend.version) < _version_tuple(MIN_BACKEND_VERSION)
        )
        if too_low:
            findings.append(Finding(
                severity="warn",
                component="backend",
                message=f"后端在线{ver_text}，低于推荐 v{MIN_BACKEND_VERSION}（建议升级后端）",
                links=(("最新 release", RELEASE_URL),),
            ))
        else:
            findings.append(Finding(
                severity="ok",
                component="backend",
                message=f"后端在线{ver_text}",
            ))

        # --- 令牌（仅后端在线才探：真打 service-token 端点，区分「确认对/错/未知」）---
        # 不再靠 service_token 占位串启发式判定——那无法区分「两边都用占位（能用）」与
        # 「插件占位/后端真值（代写必 401）」。改由后端 compare_digest 的 401 真信号裁决。
        token = probe_token(cfg)
        if token.accepted is False:
            findings.append(Finding(
                severity="error",
                component="token",
                message=(
                    "service_token 与后端不一致：代玩家写（!!PCH login / sheet 全套）"
                    "将 401 失败。请把 config.json 的 service_token 改成与后端 .env 的 "
                    "MCDR_SERVICE_TOKEN 同值后 reload"
                ),
                links=(("运维手册", BACKEND_DOC_URL),),
            ))
        elif token.accepted is None:
            findings.append(Finding(
                severity="warn",
                component="token",
                message="令牌一致性未能确认（探针网络异常），建议稍后 !!PCH status 复检",
            ))
        else:
            findings.append(Finding(
                severity="ok",
                component="token",
                message="service_token 与后端一致（代写链路通）",
            ))

        # --- 前端（仅后端在线才探；后端离线时后端错误优先，不重复噪声）---
        # 优先信后端 /info 的 web_online（同 compose 网络探服务名，避 localhost 误报）；
        # 后端未上报（web_online=None）→ probe_frontend 回退自探 web_base_url
        frontend = probe_frontend(backend.web_base_url, _probe_timeout(cfg), backend.web_online)
        if frontend.reachable is None:
            # None 来源：后端未上报 web_base_url / web_base_url 回环（容器内无法验证）。
            # detail 已是完整人话；仅「未上报」情况给 release 链接（旧后端升级）
            links = (("最新 release", RELEASE_URL),) if "未上报" in frontend.detail else ()
            findings.append(Finding(
                severity="warn",
                component="frontend",
                message=f"前端可达性未知：{frontend.detail}",
                links=links,
            ))
        elif not frontend.reachable:
            findings.append(Finding(
                severity="error",
                component="frontend",
                message=f"前端不可达：{backend.web_base_url}（可能未部署或已停止）",
                links=(("前端部署文档", FRONTEND_DOC_URL), ("最新 release", RELEASE_URL)),
            ))
        else:
            ver = backend.web_version
            findings.append(Finding(
                severity="ok",
                component="frontend",
                message=f"前端在线 v{ver}" if ver else "前端在线",
            ))

    return findings


# === 渲染 ===


def format_console_report(
    findings: list[Finding],
    plugin_meta: Optional[PluginMeta] = None,
) -> str:
    """多行纯文本（``serv.logger`` 用，URL 可复制不可点）。末尾固定作者页脚。"""
    author = (plugin_meta.author if plugin_meta else PLUGIN_AUTHOR_FALLBACK)
    lines = ["PCH 自检："]
    for f in findings:
        prefix = _CONSOLE_PREFIX.get(f.severity, "[?]")
        lines.append(f"  {prefix} [{_COMP_LABEL.get(f.component, f.component)}] {f.message}")
        for label, url in f.links:
            lines.append(f"      {label}: {url}")
    lines.append(f"作者：{author}")
    lines.append("提示：游戏内 !!PCH status 查看可点击链接与随时复检")
    return "\n".join(lines)


def format_game_report(
    findings: list[Finding],
    plugin_meta: Optional[PluginMeta] = None,
) -> RTextList:
    """RText 状态表 + 可点击链接段（``!!PCH status`` 用）。

    复用 ``messages.rtext_link``（green + bold + ``RAction.open_url``）；状态色对齐
    McdrPlugin/CLAUDE.md §6（✓ green / ⚠ yellow / ✗ red）。末尾固定作者页脚。
    """
    author = (plugin_meta.author if plugin_meta else PLUGIN_AUTHOR_FALLBACK)
    parts: list = [RText("[PCH 连接状态]\n", color=RColor.gold).set_styles(RStyle.bold)]
    for f in findings:
        color = _GAME_COLOR.get(f.severity, RColor.gray)
        sym = _GAME_SYM.get(f.severity, "?")
        comp = _COMP_LABEL.get(f.component, f.component)
        parts.append(RText(f"{sym} {comp}：", color=color))
        parts.append(RText(f.message, color=RColor.gray))
        parts.append(RText("\n"))
        for label, url in f.links:
            parts.append(RText("   "))
            parts.append(rtext_link(url, label=f"[{label}]"))
            parts.append(RText("\n"))
    parts.append(RText(f"作者：{author}\n", color=RColor.gray))
    parts.append(RText("输入 !!PCH status 可随时复检\n", color=RColor.gray))
    return RTextList(*parts)


# === on_load 入口（best-effort，绝不抛）===


def run_console_check(server, cfg: HtcmcAuthConfig) -> None:
    """on_load 调用：嗅探 + 控制台日志。全 ok → info；有 warn/error → warning。

    外层 ``try/except`` 吞所有异常——探针失败绝不影响插件加载（reload 不炸）。
    """
    try:
        meta = resolve_plugin_meta(server)
        findings = classify(cfg, meta)
        msg = format_console_report(findings, meta)
        if _worst(findings) == "ok":
            server.logger.info(msg)
        else:
            server.logger.warning(msg)
    except Exception as e:
        try:
            server.logger.warning("PCH 自检异常（已忽略，不影响插件加载）: %s", e)
        except Exception:
            _log.exception("health check failed silently")
