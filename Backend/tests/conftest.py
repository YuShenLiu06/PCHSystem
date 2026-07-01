"""pytest 全局 fixture。

`_truncate_db` 用同步 engine（独立连接，每次 dispose）跑 TRUNCATE：
避免 autouse async fixture 在同步测试上触发 pytest-asyncio 1.x 的 event loop 冲突。
app.core.db.engine 已改用 NullPool，async 测试间也不会跨 loop 残留连接。
"""
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text

from app.core.config import get_settings
from app.main import create_app

_settings = get_settings()
_TRUNCATE_SQL = text(
    "TRUNCATE users.auth_tokens, users.jwt_revocations, users.players CASCADE"
)


@pytest.fixture(autouse=True)
def _truncate_db():
    yield
    sync_engine = create_engine(_settings.postgres_dsn_sync)
    try:
        with sync_engine.begin() as conn:
            conn.execute(_TRUNCATE_SQL)
    finally:
        sync_engine.dispose()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
