import logging
from typing import Optional

import requests

from .config import HtcmcAuthConfig

_log = logging.getLogger("htcmc_auth")


def request_login_url(cfg: HtcmcAuthConfig, player_name: str, player_uuid: str) -> Optional[str]:
    """调后端 POST /auth/token，返回 login_url 或哨兵字符串或 None。"""
    url = f"{cfg.api_url.rstrip('/')}/auth/token"
    payload = {"uuid": player_uuid, "name": player_name}
    headers = {"X-Service-Token": cfg.service_token, "Content-Type": "application/json"}
    last_err = None
    for attempt in range(cfg.http_retries + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=cfg.http_timeout_seconds)
            if resp.status_code == 200:
                return resp.json().get("login_url")
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
