"""admin 托管账号环境同步测试（sync_admin_account + /auth/login 联动）。

ADMIN_USERNAME / ADMIN_PASSWORD 配置时启动同步一个 role=owner 的 WebAccount：
admin 与所有 sheet owner 平级（RBAC 天然放行），并绑定一个同名管理玩家
（UUID 按 MC 离线模式确定性推导 → JWT 带 active_uuid，可执行建项目等
全部玩家级写操作）。管理玩家是不可登录锚点（whitelist_state=removed
阻断 !!PCH login 提权链），面板密码登录与写操作不受影响。env 是该账号
的密码权威源；未配置或不合规 → 静默跳过（不 fail-fast，不破坏未启用
面板的部署与测试）。
"""
import logging
import uuid

import pytest
from sqlalchemy import update

from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.security import hash_password, verify_password
from app.models.user import Player, WebAccount
from app.repositories import web_account_repo
from app.services.admin_account_service import offline_player_uuid, sync_admin_account


def _settings(**overrides) -> "get_settings().__class__":
    """构造带 ADMIN_* 覆盖的 Settings（其余取测试环境值）。"""
    base = {"admin_username": "panel_admin", "admin_password": "PanelPass1"}
    base.update(overrides)
    return get_settings().model_copy(update=base)


async def _fetch_account(username: str) -> WebAccount | None:
    async with async_session_factory() as s:
        return await web_account_repo.get_by_username(s, username)


# ===== 未配置 / 不合规 → 静默跳过 =====


async def test_sync_skipped_when_not_configured():
    # Act
    async with async_session_factory() as s:
        account = await sync_admin_account(s, _settings(admin_username="", admin_password=""))

    # Assert — 返回 None 且不建账号
    assert account is None
    assert await _fetch_account("panel_admin") is None


async def test_sync_skips_invalid_username(caplog):
    # Arrange — 用户名过短（< username_min_length），且含非法字符
    with caplog.at_level(logging.WARNING, logger="app.services.admin_account_service"):
        async with async_session_factory() as s:
            account = await sync_admin_account(s, _settings(admin_username="a!"))

    # Assert — warning + 跳过，不抛错不建号
    assert account is None
    assert await _fetch_account("a!") is None
    assert "ADMIN_USERNAME" in caplog.text or "ADMIN_PASSWORD" in caplog.text


async def test_sync_skips_invalid_password():
    # Act — 密码过短（< password_min_length）
    async with async_session_factory() as s:
        account = await sync_admin_account(s, _settings(admin_password="short"))

    # Assert
    assert account is None
    assert await _fetch_account("panel_admin") is None


# ===== 建号 / 幂等 / 升降级 / 密码轮换 =====


async def test_sync_creates_owner_account_with_admin_player(client):
    # Act
    async with async_session_factory() as s:
        created = await sync_admin_account(s, _settings())

    # Assert — role=owner、绑定同名管理玩家（MC 离线 UUID 推导，不可登录锚点）
    assert created is not None
    assert created.role == "owner"
    async with async_session_factory() as s:
        players = await web_account_repo.list_players(s, created.id)
    assert len(players) == 1
    assert players[0].current_name == "panel_admin"
    assert str(players[0].uuid) == offline_player_uuid("panel_admin")
    assert players[0].whitelist_state == "removed"

    # 联动：/auth/login 可登录，player 非 None → JWT 带 active_uuid（写端点可用）
    resp = await client.post(
        "/auth/login", json={"username": "panel_admin", "password": "PanelPass1"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["player"] is not None
    assert body["player"]["uuid"] == offline_player_uuid("panel_admin")
    assert body["account"]["role"] == "owner"
    assert body["account"]["username"] == "panel_admin"

    # 联动：带 active_uuid 的 JWT 可建项目（此前 player-less 形态会 401 missing active_uuid）
    created_sheet = await client.post(
        "/sheets",
        json={"title": "admin 建的项目"},
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert created_sheet.status_code == 201, created_sheet.text
    assert created_sheet.json()["title"] == "admin 建的项目"


async def test_sync_idempotent_on_second_run():
    # Act — 连跑两次
    async with async_session_factory() as s:
        first = await sync_admin_account(s, _settings())
    async with async_session_factory() as s:
        second = await sync_admin_account(s, _settings())

    # Assert — 同一账号，不重复建；管理玩家也不重复
    assert first is not None and second is not None
    assert first.id == second.id
    async with async_session_factory() as s:
        players = await web_account_repo.list_players(s, second.id)
    assert len(players) == 1


async def test_admin_player_cannot_get_login_token(client):
    """提权链截断：同名玩家游戏内 !!PCH login → /auth/token 被 whitelist 拒 403。

    任何人以 ADMIN_USERNAME 同名进离线服（离线 UUID 同值推导）也拿不到
    一次性 token，/auth/exchange 无密码换 owner JWT 的链路在源头断掉。
    """
    # Arrange — 同步 admin 账号 + 管理玩家（whitelist_state=removed）
    async with async_session_factory() as s:
        await sync_admin_account(s, _settings())

    # Act — 模拟 MCDR 为「同名玩家」签发登录 token
    resp = await client.post(
        "/auth/token",
        json={"uuid": offline_player_uuid("panel_admin"), "name": "panel_admin"},
        headers={"X-Service-Token": get_settings().mcdr_service_token},
    )

    # Assert — 403，面板密码登录（/auth/login）不受此影响
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "player removed"


async def test_sync_adopts_unbound_same_name_player_as_removed(caplog):
    """同名未绑定玩家（历史数据）挂靠 → whitelist_state 收回 removed + 告警留痕。"""
    # Arrange — 预置同名、未绑账号的玩家（whitelist_state 默认 active）
    puuid = uuid.UUID(offline_player_uuid("panel_admin"))
    async with async_session_factory() as s:
        s.add(Player(uuid=puuid, current_name="panel_admin", role="user"))
        await s.commit()

    # Act
    with caplog.at_level(logging.WARNING, logger="app.services.admin_account_service"):
        async with async_session_factory() as s:
            account = await sync_admin_account(s, _settings())

    # Assert — 挂靠成功 + 不可登录锚点 + warning（与「不抢绑」告警风格对齐）
    assert account is not None
    async with async_session_factory() as s:
        players = await web_account_repo.list_players(s, account.id)
    assert len(players) == 1
    assert players[0].whitelist_state == "removed"
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "panel_admin" in joined and "挂靠" in joined


async def test_sync_repairs_legacy_active_admin_player():
    """幂等修正：已绑本账号但 whitelist_state 非 removed（历史同步产物）→ 收回。"""
    # Arrange — 正常同步（建 removed 管理玩家）后人为改回 active
    async with async_session_factory() as s:
        account = await sync_admin_account(s, _settings())
    puuid = uuid.UUID(offline_player_uuid("panel_admin"))
    async with async_session_factory() as s:
        await s.execute(
            update(Player).where(Player.uuid == puuid).values(whitelist_state="active")
        )
        await s.commit()

    # Act — 再次同步
    async with async_session_factory() as s:
        await sync_admin_account(s, _settings())

    # Assert — whitelist_state 收回 removed（仅该字段不符时写，不整行覆盖）
    async with async_session_factory() as s:
        players = await web_account_repo.list_players(s, account.id)
    assert len(players) == 1
    assert players[0].whitelist_state == "removed"


async def test_sync_does_not_steal_player_bound_to_other_account(caplog):
    """同名管理玩家已被其他账号绑定（真实玩家先注册了该用户名）→ 不抢绑。"""
    # Arrange — 其他账号 + 已绑定同名玩家（离线 UUID 推导同值）
    other_uuid = uuid.UUID(offline_player_uuid("panel_admin"))
    async with async_session_factory() as s:
        other = WebAccount(username="someone_else", password_hash=hash_password("P1"), role="user")
        s.add(other)
        await s.flush()
        s.add(Player(uuid=other_uuid, current_name="panel_admin", role="user", web_account_id=other.id))
        await s.commit()

    # Act
    with caplog.at_level(logging.WARNING, logger="app.services.admin_account_service"):
        async with async_session_factory() as s:
            account = await sync_admin_account(s, _settings())

    # Assert — 账号照常建，但不抢他人玩家（admin 账号保持无玩家，回退只读形态）
    assert account is not None
    async with async_session_factory() as s:
        players = await web_account_repo.list_players(s, account.id)
    assert players == []
    assert "不抢绑" in caplog.text


async def test_sync_upgrades_existing_user_role_to_owner(caplog):
    # Arrange — 预置同名 role=user 永久账号
    async with async_session_factory() as s:
        s.add(
            WebAccount(
                username="panel_admin",
                password_hash=hash_password("OldPass123"),
                role="user",
            )
        )
        await s.commit()

    # Act
    async with async_session_factory() as s:
        await sync_admin_account(s, _settings())

    # Assert — 只升不降：user → owner；撞名接管必须留 warning 日志供运维复核
    account = await _fetch_account("panel_admin")
    assert account is not None
    assert account.role == "owner"
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "撞名" in joined and "panel_admin" in joined


async def test_sync_never_touches_existing_admin_role():
    # Arrange — 预置同名 role=admin 账号（已是特权，不强制改 owner）
    async with async_session_factory() as s:
        s.add(
            WebAccount(
                username="panel_admin",
                password_hash=hash_password("PanelPass1"),
                role="admin",
            )
        )
        await s.commit()

    # Act
    async with async_session_factory() as s:
        await sync_admin_account(s, _settings())

    # Assert — role 保持 admin
    account = await _fetch_account("panel_admin")
    assert account is not None
    assert account.role == "admin"


async def test_sync_rotates_password_when_env_changed(client):
    # Arrange — 旧密码建的账号
    async with async_session_factory() as s:
        s.add(
            WebAccount(
                username="panel_admin",
                password_hash=hash_password("OldPass123"),
                role="owner",
            )
        )
        await s.commit()

    # Act — env 换新密码后同步
    async with async_session_factory() as s:
        await sync_admin_account(s, _settings(admin_password="NewPass456"))

    # Assert — 新密码可登，旧密码失效（env 是密码权威源）
    ok = await client.post(
        "/auth/login", json={"username": "panel_admin", "password": "NewPass456"}
    )
    assert ok.status_code == 200, ok.text
    old = await client.post(
        "/auth/login", json={"username": "panel_admin", "password": "OldPass123"}
    )
    assert old.status_code == 401
