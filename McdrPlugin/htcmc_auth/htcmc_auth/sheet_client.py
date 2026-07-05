"""sheets + notifications HTTP 客户端。

严格复用 `client.py` 的模式：同步 `requests` + 头 `X-Service-Token` + `X-Player-UUID`
+ 超时 `cfg.http_timeout_seconds` + 重试 `cfg.http_retries` + 哨兵返回。

返回类型约定（与 client.py 的 LoginOutcome 对齐）：
- 成功：dict（单对象）或 list（列表），由调用方按端点契约解析；
- 哨兵字符串：`"__RATE_LIMITED__"`（429）/ `"__REMOVED__"`（403，被移白名单语义泛化为权限拒绝）；
- 状态码错误：`HttpError(status, detail)` 对象（404/409/422 等，调用方按码译中文）；
- 网络失败：`None`（重试耗尽 / `RequestException`）。
"""
import logging
from dataclasses import dataclass
from typing import Optional, Union

import requests

from .config import HtcmcAuthConfig

_log = logging.getLogger("htcmc_auth.sheet_client")

# 哨兵字符串（与 client.py 一致，RS-11：必须回执玩家）
RATE_LIMITED = "__RATE_LIMITED__"
REMOVED = "__REMOVED__"


@dataclass
class HttpError:
    """非 2xx 且非哨兵的状态码错误（404/409/422 等），交给调用方按码译中文。"""

    status: int
    detail: str


# 联合返回类型：成功 dict|list / 哨兵字符串 / HttpError / None（网络失败）
SheetOutcome = Union[dict, list, str, HttpError, None]


def _base_headers(cfg: HtcmcAuthConfig, player_uuid: str) -> dict:
    """sheets 写通道鉴权头：service token + 代玩家 UUID（无 Authorization）。"""
    return {
        "X-Service-Token": cfg.service_token,
        "X-Player-UUID": player_uuid,
        "Content-Type": "application/json",
    }


def _request(
    cfg: HtcmcAuthConfig,
    method: str,
    path: str,
    player_uuid: str,
    *,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
) -> SheetOutcome:
    """统一请求入口：超时 + 重试 + 哨兵 + HttpError。"""
    url = f"{cfg.api_url.rstrip('/')}{path}"
    headers = _base_headers(cfg, player_uuid)
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
            status = resp.status_code
            if status == 429:
                _log.warning("sheet %s %s rate limited", method, path)
                return RATE_LIMITED
            if status == 403:
                return REMOVED
            if 200 <= status < 300:
                if status == 204:
                    return {}
                return resp.json()
            # 404 / 409 / 422 / 5xx 等：非重试型业务错误，直接返回 HttpError（重试无益）
            detail = ""
            try:
                detail = resp.json().get("detail", "")
            except Exception:
                detail = resp.text[:200]
            return HttpError(status=status, detail=str(detail)[:200])
        except requests.RequestException as e:
            last_err = repr(e)
    _log.error("sheet %s %s failed: %s", method, path, last_err)
    return None


# === sheets 表级 ===

def list_sheets(cfg: HtcmcAuthConfig, player_uuid: str, mine: bool = False) -> SheetOutcome:
    """GET /sheets[?owner=me] → list[SheetSummary]。"""
    params = {"owner": "me"} if mine else None
    return _request(cfg, "GET", "/sheets", player_uuid, params=params)


def view_sheet(cfg: HtcmcAuthConfig, player_uuid: str, sheet_id: int) -> SheetOutcome:
    """GET /sheets/{sheet_id} → SheetDetail（含 rows）。"""
    return _request(cfg, "GET", f"/sheets/{sheet_id}", player_uuid)


def create_sheet(cfg: HtcmcAuthConfig, player_uuid: str, title: str) -> SheetOutcome:
    """POST /sheets {title} → SheetDetail。"""
    return _request(cfg, "POST", "/sheets", player_uuid, json_body={"title": title})


def rename_sheet(cfg: HtcmcAuthConfig, player_uuid: str, sheet_id: int, title: str) -> SheetOutcome:
    """PATCH /sheets/{sheet_id} {title} → SheetDetail。"""
    return _request(cfg, "PATCH", f"/sheets/{sheet_id}", player_uuid, json_body={"title": title})


def delete_sheet(cfg: HtcmcAuthConfig, player_uuid: str, sheet_id: int) -> SheetOutcome:
    """DELETE /sheets/{sheet_id} → 204（成功返回 {}）。"""
    return _request(cfg, "DELETE", f"/sheets/{sheet_id}", player_uuid)


def advance_sheet(
    cfg: HtcmcAuthConfig,
    player_uuid: str,
    sheet_id: int,
    to: Optional[str] = None,
) -> SheetOutcome:
    """POST /sheets/{sheet_id}/advance[?to=<constructing|archived>] → SheetDetail。

    owner/admin 触发阶段流转（后端 RBAC；非 owner → 403）。
    to=None 时不带 query，按后端状态机默认推进下一态
    （collecting→constructing，constructing→archived）。
    成功返回 SheetDetail dict（含 status/archived_path/archived_at）。
    错误：400 非法 to / 403 非 owner / 404 / 409 已 archived 或非法转移 / 503 archive 未配置。
    """
    params = {"to": to} if to else None
    return _request(cfg, "POST", f"/sheets/{sheet_id}/advance", player_uuid, params=params)


# === sheets 行级 ===

def upsert_row(
    cfg: HtcmcAuthConfig,
    player_uuid: str,
    sheet_id: int,
    item: Optional[str],
    need: int,
    mode: int,
    sort: int,
    registry_id: Optional[str] = None,
) -> SheetOutcome:
    """PUT /sheets/{sheet_id}/rows {need_qty,mode,sort_order[,item_name][,registry_id]} → RowDetail。

    后端契约：item_name 与 registry_id 至少传一个；item_name 缺失时后端据 registry_id
    走翻译表补中文 item_name（A2）。need_qty/mode/sort_order 恒传。
    """
    body: dict = {
        "need_qty": need,
        "mode": mode,
        "sort_order": sort,
    }
    if item is not None:
        body["item_name"] = item
    if registry_id is not None:
        body["registry_id"] = registry_id
    return _request(
        cfg,
        "PUT",
        f"/sheets/{sheet_id}/rows",
        player_uuid,
        json_body=body,
    )


def delete_row(cfg: HtcmcAuthConfig, player_uuid: str, sheet_id: int, row_id: int) -> SheetOutcome:
    """DELETE /sheets/{sheet_id}/rows/{row_id} → 204。"""
    return _request(cfg, "DELETE", f"/sheets/{sheet_id}/rows/{row_id}", player_uuid)


def claim_row(cfg: HtcmcAuthConfig, player_uuid: str, sheet_id: int, row_id: int) -> SheetOutcome:
    """POST /sheets/{sheet_id}/rows/{row_id}/claim → RowDetail。"""
    return _request(cfg, "POST", f"/sheets/{sheet_id}/rows/{row_id}/claim", player_uuid)


def deliver_row(
    cfg: HtcmcAuthConfig,
    player_uuid: str,
    sheet_id: int,
    row_id: int,
    qty: int,
) -> SheetOutcome:
    """PATCH /sheets/{sheet_id}/rows/{row_id}/delivery {delivered_qty} → RowDetail。

    qty 是绝对值（与后端/前端契约一致，非增量）。仅 lock 模式：认领人维护总量。
    progress 行后端对此端点返 409 —— 调用方需先按 mode 分流到 contribute_row。
    """
    return _request(
        cfg,
        "PATCH",
        f"/sheets/{sheet_id}/rows/{row_id}/delivery",
        player_uuid,
        json_body={"delivered_qty": qty},
    )


def contribute_row(
    cfg: HtcmcAuthConfig,
    player_uuid: str,
    sheet_id: int,
    row_id: int,
    qty: int,
) -> SheetOutcome:
    """POST /sheets/{sheet_id}/rows/{row_id}/contribute {qty} → RowDetail。

    progress 模式专用：qty 是**增量**（本次上交数量，≥1），任意登录玩家可调，
    不要求认领人（progress 无认领概念）。后端累加 delivered_qty、幂等加入贡献者、
    按累计重算 status（>=need 自动 done）。
    """
    return _request(
        cfg,
        "POST",
        f"/sheets/{sheet_id}/rows/{row_id}/contribute",
        player_uuid,
        json_body={"qty": qty},
    )


def set_row_progress(
    cfg: HtcmcAuthConfig,
    player_uuid: str,
    sheet_id: int,
    row_id: int,
    delivered_qty: int,
) -> SheetOutcome:
    """PATCH /sheets/{sheet_id}/rows/{row_id}/progress {delivered_qty} → RowDetail。

    progress 模式 owner 专用：delivered_qty 是**绝对值**（直接修正进度，可增可减）。
    仅表的拥有者可调（后端 RBAC）；不动 contributors（保留上交历史），仅按新值重算 status。
    """
    return _request(
        cfg,
        "PATCH",
        f"/sheets/{sheet_id}/rows/{row_id}/progress",
        player_uuid,
        json_body={"delivered_qty": delivered_qty},
    )


def release_row(cfg: HtcmcAuthConfig, player_uuid: str, sheet_id: int, row_id: int) -> SheetOutcome:
    """POST /sheets/{sheet_id}/rows/{row_id}/release → RowDetail。"""
    return _request(cfg, "POST", f"/sheets/{sheet_id}/rows/{row_id}/release", player_uuid)


def reject_row(cfg: HtcmcAuthConfig, player_uuid: str, sheet_id: int, row_id: int) -> SheetOutcome:
    """POST /sheets/{sheet_id}/rows/{row_id}/reject → RowDetail。"""
    return _request(cfg, "POST", f"/sheets/{sheet_id}/rows/{row_id}/reject", player_uuid)


# === notifications ===

def pending_notifications(
    cfg: HtcmcAuthConfig,
    player_uuid: str,
    limit: int,
) -> SheetOutcome:
    """GET /notifications/pending?player_uuid=<uuid>&limit=N → list[Notification]。"""
    return _request(
        cfg,
        "GET",
        "/notifications/pending",
        player_uuid,
        params={"player_uuid": player_uuid, "limit": limit},
    )


def ack_notifications(cfg: HtcmcAuthConfig, player_uuid: str, ids: list) -> SheetOutcome:
    """POST /notifications/ack {player_uuid, ids} → 成功返回 {} 或 {acked: n}。

    body 必须带 player_uuid（后端 NotificationAckRequest 必填，用于归属校验防越权 ack）。
    """
    return _request(
        cfg,
        "POST",
        "/notifications/ack",
        player_uuid,
        json_body={"player_uuid": player_uuid, "ids": list(ids)},
    )
