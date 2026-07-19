"""web_account_repo 单元测试（AAA 结构）。

覆盖：
- create_temp：建临时账号（username/password_hash 均 NULL）
- register：临时→永久，username 唯一冲突 → ValueError
- get_by_id/get_by_username/list_players/list_uuids：基础查询
- attach_player：幂等（同账号 NOP）/ 跨账号 UPDATE
"""
import uuid

import pytest
from sqlalchemy import select

from app.core.db import async_session_factory
from app.core.security import hash_password
from app.models.user import Player, WebAccount
from app.repositories import web_account_repo


@pytest.mark.asyncio
async def test_create_temp_returns_temporary_account():
    # Act
    async with async_session_factory() as s:
        account = await web_account_repo.create_temp(s)
        await s.commit()
        # Assert
        assert account.username is None
        assert account.password_hash is None
        assert account.role == "user"
        assert account.is_temporary is True


@pytest.mark.asyncio
async def test_register_transfers_temp_to_permanent_with_username_password():
    # Arrange
    async with async_session_factory() as s:
        account = await web_account_repo.create_temp(s)
        await s.commit()
        account_id = account.id

    # Act
    pwd_hash = hash_password("password123")
    async with async_session_factory() as s:
        updated = await web_account_repo.register(
            s, account_id, "alice", pwd_hash
        )
        await s.commit()
        # Assert
        assert updated.username == "alice"
        assert updated.password_hash == pwd_hash
        assert updated.is_temporary is False


@pytest.mark.asyncio
async def test_register_duplicate_username_raises_value_error():
    # Arrange：先注册一个 alice
    async with async_session_factory() as s:
        first = await web_account_repo.create_temp(s)
        await s.commit()
        first_id = first.id
    async with async_session_factory() as s:
        await web_account_repo.register(
            s, first_id, "alice", hash_password("pw1")
        )
        await s.commit()

    # 再建一个临时账号尝试抢同名
    async with async_session_factory() as s:
        second = await web_account_repo.create_temp(s)
        await s.commit()
        second_id = second.id

    # Act & Assert
    async with async_session_factory() as s:
        with pytest.raises(ValueError, match="already taken"):
            await web_account_repo.register(
                s, second_id, "alice", hash_password("pw2")
            )


@pytest.mark.asyncio
async def test_get_by_id_and_username_round_trip():
    async with async_session_factory() as s:
        account = await web_account_repo.create_temp(s)
        await web_account_repo.register(
            s, account.id, "bob", hash_password("pw")
        )
        await s.commit()
        aid = account.id

    async with async_session_factory() as s:
        by_id = await web_account_repo.get_by_id(s, aid)
        assert by_id is not None and by_id.username == "bob"
        by_name = await web_account_repo.get_by_username(s, "bob")
        assert by_name is not None and by_name.id == aid
        # 不存在的查询
        assert await web_account_repo.get_by_id(s, 99999) is None
        assert await web_account_repo.get_by_username(s, "ghost") is None


@pytest.mark.asyncio
async def test_list_players_and_list_uuids_aggregate_account_uuids():
    # Arrange：建账号 + 挂 2 个 player
    async with async_session_factory() as s:
        account = await web_account_repo.create_temp(s)
        await s.commit()
        aid = account.id
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=u1, current_name="a", web_account_id=aid))
        s.add(Player(uuid=u2, current_name="b", web_account_id=aid))
        await s.commit()

    # Act & Assert
    async with async_session_factory() as s:
        players = await web_account_repo.list_players(s, aid)
        uuids = await web_account_repo.list_uuids(s, aid)
        assert {p.uuid for p in players} == {u1, u2}
        assert set(uuids) == {u1, u2}
        # 空账号
        empty = await web_account_repo.list_uuids(s, 99999)
        assert empty == []


@pytest.mark.asyncio
async def test_attach_player_is_idempotent_same_account_and_cross_account_updates():
    # Arrange：两个账号 + 一个 player
    async with async_session_factory() as s:
        a1 = await web_account_repo.create_temp(s)
        a2 = await web_account_repo.create_temp(s)
        await s.commit()
        a1_id, a2_id = a1.id, a2.id
    player_uuid = uuid.uuid4()
    async with async_session_factory() as s:
        s.add(Player(uuid=player_uuid, current_name="p", web_account_id=a1_id))
        await s.commit()

    # Act 1：挂同账号（幂等，无变化）
    async with async_session_factory() as s:
        await web_account_repo.attach_player(s, a1_id, player_uuid)
        await s.commit()

    async with async_session_factory() as s:
        p = (await s.execute(select(Player).where(Player.uuid == player_uuid))).scalar_one()
        assert p.web_account_id == a1_id  # 仍是 a1

    # Act 2：挂到另一账号（迁移）
    async with async_session_factory() as s:
        await web_account_repo.attach_player(s, a2_id, player_uuid)
        await s.commit()

    async with async_session_factory() as s:
        p = (await s.execute(select(Player).where(Player.uuid == player_uuid))).scalar_one()
        assert p.web_account_id == a2_id  # 切到 a2
