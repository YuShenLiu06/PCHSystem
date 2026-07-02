import logging
from dataclasses import dataclass
from typing import Optional, Union

import requests

from .config import HtcmcAuthConfig

_log = logging.getLogger("htcmc_auth")


@dataclass
class LoginResult:
    login_url: str
    expires_in: int
    previous_tokens_revoked: int


# 联合返回类型：LoginResult 成功 / 哨兵字符串 / None（网络失败）
LoginOutcome = Union[LoginResult, str, None]


def request_login_url(cfg: HtcmcAuthConfig, player_name: str, player_uuid: str) -> LoginOutcome:
    """调后端 POST /auth/token，返回 LoginResult 或哨兵 '__RATE_LIMITED__' / '__REMOVED__' 或 None。"""
    url = f"{cfg.api_url.rstrip('/')}/auth/token"
    payload = {"uuid": player_uuid, "name": player_name}
    headers = {"X-Service-Token": cfg.service_token, "Content-Type": "application/json"}
    last_err: Optional[str] = None
    for attempt in range(cfg.http_retries + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=cfg.http_timeout_seconds)
            if resp.status_code == 200:
                data = resp.json()
                return LoginResult(
                    login_url=data["login_url"],
                    expires_in=int(data.get("expires_in", 600)),
                    previous_tokens_revoked=int(data.get("previous_tokens_revoked", 0)),
                )
            if resp.status_code == 429:
                _log.warning("login rate limited for %s", player_name)
                return "__RATE_LIMITED__"
            if resp.status_code == 403:
                return "__REMOVED__"
            last_err = f"HTTP {resp.status_code}: {resp.text[:120]}"
        except requests.RequestException as e:
            last_err = repr(e)
    _log.error("request_login_url failed for %s: %s", player_name, last_err)
    return None
