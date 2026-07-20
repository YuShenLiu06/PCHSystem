import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Hashable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.user import Player

_settings = get_settings()


class RateLimiter:
    """按 uuid 内存滑窗限频。单进程 MVP 足够；多 worker 需换 Redis。"""

    def __init__(self, window_seconds: int = _settings.auth_token_rate_limit_seconds) -> None:
        self._window = window_seconds
        self._last: dict[uuid.UUID, float] = {}
        self._lock = threading.Lock()

    def check_and_record(self, player_uuid: uuid.UUID) -> bool:
        now = time.monotonic()
        with self._lock:
            last = self._last.get(player_uuid)
            if last is not None and now - last < self._window:
                return False
            self._last[player_uuid] = now
            return True


rate_limiter = RateLimiter()


class WindowRateLimiter:
    """按 key 限频：滑动窗口内最多 ``max_count`` 次。

    与 :class:`RateLimiter`（单次滑窗、按 UUID）互补——密码登录需窗口内允许多次尝试
    （用户输错密码能重试）并按字符串/元组键（username / IP）限频。单进程 MVP；多 worker 需换 Redis。
    """

    def __init__(self, window_seconds: int, max_count: int) -> None:
        self._window = window_seconds
        self._max = max_count
        self._hits: dict[Hashable, list[float]] = {}
        self._lock = threading.Lock()

    def check_and_record(self, key: Hashable) -> bool:
        """窗口内未达上限 → 记录并返回 True；已达上限 → 返回 False（不记录本次）。"""
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if t > cutoff]
            if len(hits) >= self._max:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True

    def reset(self, key: Hashable) -> None:
        """清除该 key 计数（登录成功后调用，避免正常用户累积触发限频）。"""
        with self._lock:
            self._hits.pop(key, None)


# 密码登录双维度限频：IP 维度防撞库扫号，credential(username, ip) 维度防针对单账号爆破。
login_by_ip = WindowRateLimiter(
    window_seconds=_settings.login_rate_limit_window_seconds,
    max_count=_settings.login_rate_limit_max_per_ip,
)
login_by_credential = WindowRateLimiter(
    window_seconds=_settings.login_rate_limit_window_seconds,
    max_count=_settings.login_rate_limit_max_per_credential,
)


async def check_whitelist(session: AsyncSession, player_uuid: uuid.UUID) -> bool:
    stmt = select(Player.whitelist_state).where(Player.uuid == player_uuid)
    state = (await session.execute(stmt)).scalar_one_or_none()
    return state != "removed"
