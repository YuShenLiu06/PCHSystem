"""admin 托管账号环境同步测试（sync_admin_account + /auth/login 联动）。

ADMIN_USERNAME / ADMIN_PASSWORD 配置时启动同步一个 role=owner 的 WebAccount：
admin 与所有 sheet owner 平级（RBAC 天然放行）、不绑定游戏玩家（player=None，
JWT 无 active_uuid）。env 是该账号的密码权威源；未配置或不合规 → 静默跳过
（不 fail-fast，不破坏未启用面板的部署与测试）。
"""
import logging
import uuid

import pytest

from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.security import hash_password, verify_password
from app.models.user import Player, WebAccount
from app.repositories import web_account_repo
from app.services.admin_account_service import sync_admin_account


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


async def test_sync_creates_owner_account_without_players(client):
    # Act
    async with async_session_factory() as s:
        created = await sync_admin_account(s, _settings())

    # Assert — role=owner、无绑定玩家
    assert created is not None
    assert created.role == "owner"
    async with async_session_factory() as s:
        players = await web_account_repo.list_players(s, created.id)
    assert players == []

    # 联动：/auth/login 可登录，player=None（托管账号无玩家）
    resp = await client.post(
        "/auth/login", json={"username": "panel_admin", "password": "PanelPass1"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["player"] is None
    assert body["account"]["role"] == "owner"
    assert body["account"]["username"] == "panel_admin"


async def test_sync_idempotent_on_second_run():
    # Act — 连跑两次
    async with async_session_factory() as s:
        first = await sync_admin_account(s, _settings())
    async with async_session_factory() as s:
        second = await sync_admin_account(s, _settings())

    # Assert — 同一账号，不重复建
    assert first is not None and second is not None
    assert first.id == second.id


async def test_sync_upgrades_existing_user_role_to_owner():
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

    # Assert — 只升不降：user → owner
    account = await _fetch_account("panel_admin")
    assert account is not None
    assert account.role == "owner"


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
