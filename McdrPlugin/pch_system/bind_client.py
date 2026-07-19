"""bind 双向绑定 HTTP 客户端。

复用 sheet_client 的双头通道（X-Service-Token + X-Player-UUID）与哨兵机制。
契约与 sheet_client 对齐：
- POST /bind/token（仅 X-Service-Token，玩家为自己申请码）
- POST /bind/consume（双头，代玩家消费短码）

返回类型约定：
- 成功：dict（单对象）
- 哨兵字符串："__RATE_LIMITED__"（429）/ "__REMOVED__"（403）
- 状态码错误：HttpError(status, detail) 对象
- 网络失败：None
"""
import logging
from dataclasses import dataclass
from typing import Optional, Union

import requests

from .config import PchSystemConfig

_log = logging.getLogger("pch_system.bind_client")

# 哨兵字符串（与 sheet_client 一致）
RATE_LIMITED = "__RATE_LIMITED__"
REMOVED = "__REMOVED__"


@dataclass
class HttpError:
    """非 2xx 且非哨兵的状态码错误。"""
    status: int
    detail: str


# 联合返回类型
BindOutcome = Union[dict, str, HttpError, None]


def _base_headers(cfg: PchSystemConfig, player_uuid: Optional[str] = None) -> dict:
    """构建请求头：bind/token 仅需 service-token；bind/consume 需双头。"""
    headers = {
        "X-Service-Token": cfg.service_token,
        "Content-Type": "application/json",
    }
    if player_uuid:
        headers["X-Player-UUID"] = player_uuid
    return headers


def _request(
    cfg: PchSystemConfig,
    method: str,
    path: str,
    player_uuid: Optional[str] = None,
    *,
    json_body: Optional[dict] = None,
) -> BindOutcome:
    """统一请求入口：超时 + 重试 + 哨兵 + HttpError。"""
    url = f"{cfg.api_url.rstrip('/')}{path}"
    headers = _base_headers(cfg, player_uuid)
    last_err: Optional[str] = None
    for attempt in range(cfg.http_retries + 1):
        try:
            resp = requests.request(
                method,
                url,
                json=json_body,
                headers=headers,
                timeout=cfg.http_timeout_seconds,
            )
            status = resp.status_code
            if status == 429:
                _log.warning("bind %s %s rate limited", method, path)
                return RATE_LIMITED
            if status == 403:
                return REMOVED
            if 200 <= status < 300:
                return resp.json()
            # 404 / 409 / 422 / 5xx 等：非重试型业务错误
            detail = ""
            try:
                detail = resp.json().get("detail", "")
            except Exception:
                detail = resp.text[:200]
            return HttpError(status=status, detail=str(detail)[:200])
        except requests.RequestException as e:
            last_err = repr(e)
    _log.error("bind %s %s failed: %s", method, path, last_err)
    return None


def request_bind_token(cfg: PchSystemConfig, player_name: str, player_uuid: str) -> BindOutcome:
    """POST /bind/token {uuid, name} → {short_code, expires_in}。

    header 仅 X-Service-Token（玩家为自己申请码，同 login）。
    成功返回 dict: {"short_code": "ABC123", "expires_in": 600}
    """
    return _request(
        cfg,
        "POST",
        "/bind/token",
        player_uuid=None,  # 不带 X-Player-UUID
        json_body={"uuid": player_uuid, "name": player_name},
    )


def consume_bind_code(cfg: PchSystemConfig, player_uuid: str, short_code: str) -> BindOutcome:
    """POST /bind/consume {short_code} → {player: {...}, account: {...}}。

    header 需双头（X-Service-Token + X-Player-UUID），代玩家消费短码。
    成功返回 dict: {"player": {...}, "account": {...}}
    """
    return _request(
        cfg,
        "POST",
        "/bind/consume",
        player_uuid=player_uuid,
        json_body={"short_code": short_code},
    )
