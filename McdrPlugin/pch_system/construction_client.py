"""施工进度上报 HTTP 客户端（``/v1/construction``）。

严格复刻 ``sheet_client._request`` 模式：同步 ``requests`` + 超时
``cfg.http_timeout_seconds`` + 重试 ``cfg.http_retries`` + 哨兵 + HttpError。

**头模式按端点分裂**（源识别要害，C-1/C-3，S-1 联网核实
[`api/construction.md`] §3 / 后端 ``deps.py:get_construction_reporter``）：

- ``POST /report`` → **单头** ``X-Service-Token``（无 ``Authorization``、无 ``X-Source-Id``、
  无 ``X-Player-UUID``）→ 后端识别为 ``{mcdr, official}`` 默认官方追踪器（C-1）。
  玩家身份在 body 每条 ``placements[].player_uuid``（多玩家 batch）。
  ::禁带 X-Source-Id：带了会变 ``{server_mod, <name>}`` 走白名单校验（C-1）。::

- ``GET /active-sheets`` → **双头** ``X-Service-Token`` + ``X-Player-UUID``（代一个在线
  玩家；后端 ``get_current_player`` 双通道，``construction.py:92``）。该端点返回全局
  归因信息（当前 constructing 项目 + 启发式可用性），代谁结果一致；无在线玩家则整轮跳过。

故 ``_request(player_uuid=None)``：``None`` → 单头（report）；有值 → 双头（active-sheets）。

返回类型约定（与 ``sheet_client.SheetOutcome`` 对齐）：
- 成功：dict（单对象）或 list；
- 哨兵字符串：``"__RATE_LIMITED__"``（429）/ ``"__REMOVED__"``（403）；
- 状态码错误：``HttpError(status, detail)``（404/409/422/5xx 等）；
- 网络失败：``None``（重试耗尽 / ``RequestException``）。
"""
import logging
from dataclasses import dataclass
from typing import Optional, Union

import requests

from .config import PchSystemConfig

_log = logging.getLogger("pch_system.construction_client")

# 连续网络错误计数（仅 RequestException；任意 HTTP 响应到达即重置）
_consecutive_net_errors: int = 0

# 哨兵字符串（与 sheet_client / client.py 一致，RS-11：必须回执玩家）
RATE_LIMITED = "__RATE_LIMITED__"
REMOVED = "__REMOVED__"


@dataclass
class HttpError:
    """非 2xx 且非哨兵的状态码错误（404/409/422/5xx 等），交给调用方按码译中文。"""

    status: int
    detail: str


ConstructionOutcome = Union[dict, list, str, HttpError, None]


def _headers(cfg: PchSystemConfig, player_uuid: Optional[str]) -> dict:
    """鉴权头：player_uuid 为 None → 单头（report，C-1）；有值 → 双头（active-sheets，RS-13）。"""
    h = {
        "X-Service-Token": cfg.service_token,
        "Content-Type": "application/json",
    }
    if player_uuid is not None:
        h["X-Player-UUID"] = player_uuid
    return h


def _reset_net_errors_if_any() -> None:
    """任意 HTTP 响应到达 = 后端可达 → 重置网络错误计数 + log 恢复。

    注意：``_log`` 是裸 Python logger，MCDR 生产环境 INFO 级不可见（无 handler）。
    操作者可见的恢复提示由 ``notifier.run()`` / ``construction_tracker.run()``
    经 ``server.logger.info`` 发出；此处的 INFO 仅供测试与非 MCDR 环境诊断。
    """
    global _consecutive_net_errors
    if _consecutive_net_errors > 0:
        _log.info("construction backend reachable again after %d consecutive errors", _consecutive_net_errors)
        _consecutive_net_errors = 0


def _request(
    cfg: PchSystemConfig,
    method: str,
    path: str,
    player_uuid: Optional[str] = None,
    *,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
) -> ConstructionOutcome:
    """统一请求入口：超时 + 重试 + 哨兵 + HttpError。

    ``player_uuid`` 决定单头/双头（见模块 docstring）。**绝不带** ``X-Source-Id``（C-1）。
    """
    global _consecutive_net_errors
    url = f"{cfg.api_url.rstrip('/')}{path}"
    headers = _headers(cfg, player_uuid)
    last_err: Optional[str] = None
    for _attempt in range(cfg.http_retries + 1):
        try:
            resp = requests.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=cfg.http_timeout_seconds,
            )
            _reset_net_errors_if_any()
            status = resp.status_code
            if status == 429:
                _log.warning("construction %s %s rate limited", method, path)
                return RATE_LIMITED
            if status == 403:
                return REMOVED
            if 200 <= status < 300:
                if status == 204:
                    return {}
                return resp.json()
            # 404 / 409 / 422 / 5xx：非重试型业务错误，直接 HttpError
            detail = ""
            try:
                detail = resp.json().get("detail", "")
            except Exception:
                detail = resp.text[:200]
            return HttpError(status=status, detail=str(detail)[:200])
        except requests.RequestException as e:
            last_err = repr(e)
    _consecutive_net_errors += 1
    if _consecutive_net_errors == 1:
        _log.error("construction %s %s failed: %s", method, path, last_err)
    else:
        _log.debug(
            "construction %s %s failed: %s (consecutive #%d)",
            method, path, last_err, _consecutive_net_errors,
        )
    return None


def report_placements(
    cfg: PchSystemConfig,
    placements: list,
    sheet_id: Optional[int] = None,
) -> ConstructionOutcome:
    """``POST /v1/construction/report``（单头，C-1）。

    - ``placements``：``list[{player_uuid, registry_id, placed_qty, broken_qty}]``，
      由 construction_tracker 按 baseline diff 产出（placed_qty = 增量 > 0）。
    - ``sheet_id``：``int`` 显式归因 / ``None`` 启发式归因（追踪器恒用 None）。
    - 成功 → ``PlacementReportResult`` dict（``{sheet_id, attribution_source, totals, outcomes}``）。

    幂等前提：后端 ``submit_report`` 单事务，2xx = 整批确认（accepted + skipped 都算确认）。
    调用方据 2xx 推进 baseline（见 construction_tracker）。
    """
    body = {"sheet_id": sheet_id, "placements": list(placements)}
    return _request(cfg, "POST", "/v1/construction/report", player_uuid=None, json_body=body)


def get_active_sheets(cfg: PchSystemConfig, player_uuid: str) -> ConstructionOutcome:
    """``GET /v1/construction/active-sheets``（双头代玩家，C-3）。

    返回 ``{sheets: [...], heuristic_eligible: bool}``（``heuristic_eligible`` = 恰 1 个
    constructing）。失败返回 None / HttpError / 哨兵。
    """
    return _request(cfg, "GET", "/v1/construction/active-sheets", player_uuid=player_uuid)


# === 加入施工（/v1/construction/me/*，双头代玩家）===
# 后端 ``get_current_player`` 双通道：MCDR 走 ``X-Service-Token`` + ``X-Player-UUID``
# （无 Authorization 头），与 sheets 写通道等价（RS-13）。_request(player_uuid=...) 即双头。


def lookup_active_by_uuids(
    cfg: PchSystemConfig, player_uuids
) -> ConstructionOutcome:
    """``POST /v1/construction/active-by-uuids``（单头 service-token，批量 UUID→sheet_id）。

    tracker 按玩家路由用；非敏感数据（仅 sheet_id，无 account 信息）。body
    ``{player_uuids: [str,...]}``（≤500）；响应 ``{mappings: {"<uuid>": <sheet_id>|None}}``。

    返回值约定（与 _request 一致，**额外一步**：成功 dict 时解包 ``mappings`` 为
    ``dict[str, int | None]`` 返回；非 2xx / 哨兵 / None 原样透传给调用方按错误处理）。
    """
    body = {"player_uuids": [str(p) for p in player_uuids]}
    raw = _request(
        cfg, "POST", "/v1/construction/active-by-uuids", player_uuid=None, json_body=body
    )
    if isinstance(raw, dict):
        mappings = raw.get("mappings")
        if isinstance(mappings, dict):
            # 后端 pydantic ``dict[UUID, int|None]`` → JSON object 键 = UUID 字符串
            normalized = {}
            for k, v in mappings.items():
                try:
                    normalized[str(k)] = int(v) if v is not None else None
                except (TypeError, ValueError):
                    normalized[str(k)] = None
            return normalized
        return raw  # mappings 字段缺失 / 类型异常 → 兜底返回原 dict（调用方按降级处理）
    return raw


def join_construction(
    cfg: PchSystemConfig, player_uuid: str, sheet_id: int
) -> ConstructionOutcome:
    """``POST /v1/construction/me/join``（双头代玩家）。

    body ``{sheet_id}``；返回 ``MyConstructionResult`` 形态：
    ``{active: {sheet_id, sheet_title, joined_at, join_source}}``（后端保证 active 非 None，
    字段全 None 表示未加入）。冲突 409 / 未绑账号 403 / sheet 404 / archived 409 → HttpError。
    """
    return _request(
        cfg,
        "POST",
        "/v1/construction/me/join",
        player_uuid=player_uuid,
        json_body={"sheet_id": int(sheet_id)},
    )


def leave_construction(cfg: PchSystemConfig, player_uuid: str) -> ConstructionOutcome:
    """``POST /v1/construction/me/leave``（双头代玩家，无 body）。

    返回 ``MyConstructionResult``（active 字段全 None = 已退出 / 本就未加入，幂等）。
    """
    return _request(
        cfg, "POST", "/v1/construction/me/leave", player_uuid=player_uuid
    )


def get_my_construction(cfg: PchSystemConfig, player_uuid: str) -> ConstructionOutcome:
    """``GET /v1/construction/me/construction``（双头代玩家）。

    返回 ``MyConstructionResult``：``active = {sheet_id, sheet_title, joined_at,
    join_source}``；字段全 None 表示未加入任何项目（后端 active 恒非 None）。
    """
    return _request(
        cfg, "GET", "/v1/construction/me/construction", player_uuid=player_uuid
    )
