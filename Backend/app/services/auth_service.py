import threading
import time
import uuid
from datetime import datetime, timezone

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


async def check_whitelist(session: AsyncSession, player_uuid: uuid.UUID) -> bool:
    stmt = select(Player.whitelist_state).where(Player.uuid == player_uuid)
    state = (await session.execute(stmt)).scalar_one_or_none()
    return state != "removed"
