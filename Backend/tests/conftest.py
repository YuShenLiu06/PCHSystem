"""pytest 全局 fixture。

`_truncate_db` 用同步 engine（独立连接，每次 dispose）跑 TRUNCATE：
避免 autouse async fixture 在同步测试上触发 pytest-asyncio 1.x 的 event loop 冲突。
app.core.db.engine 已改用 NullPool，async 测试间也不会跨 loop 残留连接。

新增：``seed_player_with_account`` —— 创建 Player + 临时 WebAccount 并挂接，
返回 (player_uuid, bearer)。Web 账号绑定后 JWT sub=account_id 才能命中身份解析。
"""
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text

from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.jwt import create_access_token
from app.main import create_app
from app.models.user import Player, WebAccount

_settings = get_settings()
_TRUNCATE_SQL = text(
    "TRUNCATE users.auth_tokens, users.jwt_revocations, users.players, "
    "users.web_accounts, users.bind_tokens, "
    "sheets.sheet_row_contributors, sheets.sheet_rows, sheets.sheets, "
    "notifications.notifications RESTART IDENTITY CASCADE"
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


async def seed_player_with_account(
    name: str = "alice", role: str = "user"
) -> tuple[uuid.UUID, str]:
    """seed 一个 Player + 临时 WebAccount（role 落在 account 上），返回 (uuid, bearer)。

    JWT 契约升级后 sub=account_id，必须先建账号再签 token。
    role 同步写到 WebAccount.role（RBAC 权威源）；Player.role 保留向后兼容值。
    """
    player_uuid = uuid.uuid4()
    async with async_session_factory() as s:
        account = WebAccount(role=role)
        s.add(account)
        await s.flush()
        s.add(
            Player(
                uuid=player_uuid,
                current_name=name,
                role=role,
                web_account_id=account.id,
            )
        )
        await s.commit()
        account_id = account.id
    token = create_access_token(account_id, role, active_uuid=player_uuid)
    return player_uuid, f"Bearer {token}"
