"""bind_token_repo 单元测试（AAA 结构）。

覆盖双向绑定短码：
- issue_game_init / issue_web_init：生成短码，软失效旧码（_revoke_active）
- consume_game_init / consume_web_init：消费（成功 / 失败 / 已用 / 已过期）
- 方向校验（game_init 短码不能被 web_init 消费反方向）
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.db import async_session_factory
from app.models.user import BindToken, Player, WebAccount
from app.repositories import bind_token_repo


async def _seed_player(name: str = "alice") -> uuid.UUID:
    async with async_session_factory() as s:
        account = WebAccount(role="user")
        s.add(account)
        await s.flush()
        u = uuid.uuid4()
        s.add(Player(uuid=u, current_name=name, web_account_id=account.id))
        await s.commit()
        return u


async def _seed_account() -> int:
    async with async_session_factory() as s:
        account = WebAccount(role="user")
        s.add(account)
        await s.commit()
        return account.id


# ===== issue_*_init + _revoke_active =====


@pytest.mark.asyncio
async def test_issue_game_init_creates_token_with_short_code():
    # Arrange
    player_uuid = await _seed_player()

    # Act
    async with async_session_factory() as s:
        token = await bind_token_repo.issue_game_init(s, player_uuid)
        await s.commit()

    # Assert
    assert token.direction == "game_init"
    assert token.player_uuid == player_uuid
    assert token.target_account_id is None
    assert token.short_code and len(token.short_code) == 6
    assert token.used_at is None


@pytest.mark.asyncio
async def test_issue_web_init_creates_token_with_short_code():
    # Arrange
    account_id = await _seed_account()

    # Act
    async with async_session_factory() as s:
        token = await bind_token_repo.issue_web_init(s, account_id)
        await s.commit()

    # Assert
    assert token.direction == "web_init"
    assert token.target_account_id == account_id
    assert token.player_uuid is None
    assert token.short_code and len(token.short_code) == 6


@pytest.mark.asyncio
async def test_issue_game_init_revokes_previous_unused_tokens_for_same_uuid():
    # Arrange
    player_uuid = await _seed_player()

    # Act：连续两次 issue
    async with async_session_factory() as s:
        first = await bind_token_repo.issue_game_init(s, player_uuid)
        await s.commit()
    async with async_session_factory() as s:
        second = await bind_token_repo.issue_game_init(s, player_uuid)
        await s.commit()

    # Assert：第一个 token 的 used_at 应被置位（软失效）
    async with async_session_factory() as s:
        rows = (
            await s.execute(
                select(BindToken).where(BindToken.player_uuid == player_uuid)
            )
        ).scalars().all()
        first_row = next(r for r in rows if r.short_code == first.short_code)
        assert first_row.used_at is not None  # 旧码被软失效


@pytest.mark.asyncio
async def test_issue_web_init_revokes_previous_unused_tokens_for_same_account():
    # Arrange
    account_id = await _seed_account()

    # Act
    async with async_session_factory() as s:
        first = await bind_token_repo.issue_web_init(s, account_id)
        await s.commit()
    async with async_session_factory() as s:
        await bind_token_repo.issue_web_init(s, account_id)
        await s.commit()

    # Assert
    async with async_session_factory() as s:
        first_row = (
            await s.execute(
                select(BindToken).where(BindToken.short_code == first.short_code)
            )
        ).scalar_one()
        assert first_row.used_at is not None


# ===== consume_game_init / consume_web_init =====


@pytest.mark.asyncio
async def test_consume_game_init_returns_player_uuid_on_success():
    # Arrange
    player_uuid = await _seed_player()
    async with async_session_factory() as s:
        token = await bind_token_repo.issue_game_init(s, player_uuid)
        await s.commit()
    account_id = await _seed_account()

    # Act
    async with async_session_factory() as s:
        result = await bind_token_repo.consume_game_init(
            s, token.short_code, account_id
        )
        await s.commit()

    # Assert
    assert result == player_uuid


@pytest.mark.asyncio
async def test_consume_web_init_returns_account_id_on_success():
    # Arrange
    account_id = await _seed_account()
    async with async_session_factory() as s:
        token = await bind_token_repo.issue_web_init(s, account_id)
        await s.commit()
    player_uuid = await _seed_player()

    # Act
    async with async_session_factory() as s:
        result = await bind_token_repo.consume_web_init(
            s, token.short_code, player_uuid
        )
        await s.commit()

    # Assert
    assert result == account_id


@pytest.mark.asyncio
async def test_consume_game_init_rejects_reused_code():
    # Arrange
    player_uuid = await _seed_player()
    async with async_session_factory() as s:
        token = await bind_token_repo.issue_game_init(s, player_uuid)
        await s.commit()
    account_id = await _seed_account()

    # Act：先消费一次成功
    async with async_session_factory() as s:
        first = await bind_token_repo.consume_game_init(
            s, token.short_code, account_id
        )
        await s.commit()
    assert first == player_uuid
    # 第二次消费应失败
    async with async_session_factory() as s:
        second = await bind_token_repo.consume_game_init(
            s, token.short_code, account_id
        )
        await s.commit()

    # Assert
    assert second is None


@pytest.mark.asyncio
async def test_consume_game_init_rejects_expired_code():
    # Arrange
    player_uuid = await _seed_player()
    async with async_session_factory() as s:
        # ttl=0 让其立即过期
        token = await bind_token_repo.issue_game_init(s, player_uuid, ttl=0)
        await s.commit()
    account_id = await _seed_account()

    # 手动改 expires_at 到过去（ttl=0 时 expires_at ≈ now，可能 borderline）
    async with async_session_factory() as s:
        row = (
            await s.execute(
                select(BindToken).where(BindToken.short_code == token.short_code)
            )
        ).scalar_one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await s.commit()

    # Act
    async with async_session_factory() as s:
        result = await bind_token_repo.consume_game_init(
            s, token.short_code, account_id
        )
        await s.commit()

    # Assert
    assert result is None


@pytest.mark.asyncio
async def test_consume_game_init_rejects_wrong_direction():
    # Arrange：发 web_init 短码，但用 consume_game_init 消费
    account_id = await _seed_account()
    async with async_session_factory() as s:
        token = await bind_token_repo.issue_web_init(s, account_id)
        await s.commit()

    # Act
    async with async_session_factory() as s:
        result = await bind_token_repo.consume_game_init(
            s, token.short_code, 99999
        )
        await s.commit()

    # Assert：consume_game_init 只查 direction=game_init，web_init 短码不命中
    assert result is None


@pytest.mark.asyncio
async def test_consume_web_init_rejects_wrong_direction():
    # Arrange：发 game_init 短码，但用 consume_web_init 消费
    player_uuid = await _seed_player()
    async with async_session_factory() as s:
        token = await bind_token_repo.issue_game_init(s, player_uuid)
        await s.commit()

    # Act
    async with async_session_factory() as s:
        result = await bind_token_repo.consume_web_init(
            s, token.short_code, uuid.uuid4()
        )
        await s.commit()

    # Assert
    assert result is None


@pytest.mark.asyncio
async def test_consume_unknown_short_code_returns_none():
    async with async_session_factory() as s:
        result = await bind_token_repo.consume_game_init(
            s, "UNKNOWN", 99999
        )
        assert result is None
        result2 = await bind_token_repo.consume_web_init(
            s, "UNKNOWN", uuid.uuid4()
        )
        assert result2 is None
