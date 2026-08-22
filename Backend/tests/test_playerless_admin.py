"""player-less 管理账号全链路测试（issue #74）。

player-less = 账号无任何绑定玩家（JWT 无 ``active_uuid``）的泛化防御形态
（如 ``ADMIN_*`` 托管账号撞名不抢绑的回退、运维手建账号）。
历史缺陷：``require_role`` 基于 ``get_current_player``（需 active_uuid）→
施工管理端点 401；``GET /me`` 硬依赖 ``get_active_uuid``；``/auth/refresh``
对无 claim token 一律拒；``GET /sheets`` 列表/详情同踩——401 又触发前端
拦截器清会话踢回登录页。注：``ADMIN_*`` 托管账号现已默认绑定管理玩家
（``sync_admin_account``），常态不再 player-less（见 test_admin_account_sync）。

本套锁定修复后契约：
- 管理端点鉴权 = account 级 JWT（admin ≠ service-token；无玩家可用）
- ``GET /me`` 对无玩家账号返回 ``active_uuid=null``、``players=[]``
- refresh：无 claim + 无玩家 → 续签（player=None）；无 claim + 有玩家 → 401
- sheets 列表/详情（含 construction progress）对 player-less 开放（只读浏览）
"""
import uuid

from sqlalchemy import update

from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.jwt import create_access_token, create_refresh_token
from app.models.sheet import Sheet, SheetRow
from app.models.user import Player, WebAccount

_settings = get_settings()
SVC = _settings.mcdr_service_token


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _seed_playerless_account(role: str = "owner") -> tuple[int, str]:
    """建无绑定玩家的账号，返回 (account_id, bearer)。token 无 active_uuid。"""
    async with async_session_factory() as s:
        account = WebAccount(role=role)
        s.add(account)
        await s.commit()
        aid = account.id
    return aid, f"Bearer {create_access_token(aid, role)}"


async def _seed_player_with_account(name="alice", role="user") -> tuple[uuid.UUID, int, str]:
    """建普通玩家（有绑定账号），返回 (player_uuid, account_id, bearer)。"""
    puuid = uuid.uuid4()
    async with async_session_factory() as s:
        account = WebAccount(role=role)
        s.add(account)
        await s.flush()
        s.add(Player(uuid=puuid, current_name=name, role=role, web_account_id=account.id))
        await s.commit()
        aid = account.id
    return puuid, aid, f"Bearer {create_access_token(aid, role, active_uuid=puuid)}"


async def _seed_sheet(owner_uuid: uuid.UUID, title="测试项目") -> int:
    """直接落库建 sheet + 一行材料（不依赖写端点）。"""
    async with async_session_factory() as s:
        sheet = Sheet(owner_uuid=owner_uuid, title=title, status="constructing")
        s.add(sheet)
        await s.flush()
        s.add(
            SheetRow(
                sheet_id=sheet.id,
                item_name="minecraft:stone",
                registry_id="minecraft:stone",
                need_qty=10,
            )
        )
        await s.commit()
        return sheet.id


# ===========================================================================
# 施工管理端点（require_role → account 级 JWT）
# ===========================================================================


async def test_playerless_admin_reads_and_patches_construction_settings(client):
    # Arrange — 无 active_uuid 的 owner JWT
    _, bearer = await _seed_playerless_account()

    # Act / Assert — GET 200
    r = await client.get("/v1/construction/settings", headers={"Authorization": bearer})
    assert r.status_code == 200, r.text
    # PATCH 200（开关不动，仅回显）
    r = await client.patch(
        "/v1/construction/settings",
        json={},
        headers={"Authorization": bearer},
    )
    assert r.status_code == 200, r.text


async def test_playerless_admin_creates_mod_source_without_approver_uuid(client):
    # Arrange
    _, bearer = await _seed_playerless_account()

    # Act
    r = await client.post(
        "/v1/construction/mod-sources",
        json={"name": "panel-mod"},
        headers={"Authorization": bearer},
    )

    # Assert — 无玩家 → approved_by_uuid 为 null（schema 本就 Optional）
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "panel-mod"
    assert body["approved_by_uuid"] is None


async def test_service_token_rejected_on_construction_admin_endpoint(client):
    # Arrange — service-token 单头（历史通道曾可过 require_role）
    # Act
    r = await client.get(
        "/v1/construction/settings", headers={"X-Service-Token": SVC}
    )
    # Assert — admin ≠ service-token：管理端点仅接受 Bearer JWT
    assert r.status_code == 401


async def test_player_role_forbidden_on_construction_settings(client):
    # Arrange — 普通玩家（有 active_uuid，但 role=user）
    _, _, bearer = await _seed_player_with_account()

    # Act / Assert
    r = await client.get("/v1/construction/settings", headers={"Authorization": bearer})
    assert r.status_code == 403


# ===========================================================================
# GET /me + /auth/refresh
# ===========================================================================


async def test_me_returns_null_active_uuid_for_playerless_account(client):
    # Arrange
    _, bearer = await _seed_playerless_account()

    # Act
    r = await client.get("/me", headers={"Authorization": bearer})

    # Assert
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active_uuid"] is None
    assert body["players"] == []
    assert body["account"]["role"] == "owner"


async def test_refresh_playerless_account_succeeds(client):
    # Arrange — 无玩家的 refresh token（无 active_uuid claim）
    aid, _ = await _seed_playerless_account()
    refresh, _ = create_refresh_token(aid, "owner")

    # Act
    r = await client.post("/auth/refresh", json={"refresh_token": refresh})

    # Assert — 托管账号续签放行，player=None
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["player"] is None
    assert body["account"]["id"] == aid


async def test_refresh_legacy_token_with_players_still_rejected(client):
    # Arrange — 账号有绑定玩家，但 token 无 active_uuid（旧格式）
    _, aid, _ = await _seed_player_with_account()
    refresh, _ = create_refresh_token(aid, "user")

    # Act
    r = await client.post("/auth/refresh", json={"refresh_token": refresh})

    # Assert — 有玩家却无 claim = 旧格式 token，维持拒绝
    assert r.status_code == 401
    assert "legacy" in r.json()["detail"]


# ===========================================================================
# sheets 只读端点（列表 / 详情 / construction progress）
# ===========================================================================


async def test_playerless_admin_lists_sheets(client):
    # Arrange — 他人建的项目
    owner_uuid, _, _ = await _seed_player_with_account("owner_p")
    sid = await _seed_sheet(owner_uuid)
    _, bearer = await _seed_playerless_account()

    # Act
    r = await client.get("/sheets", headers={"Authorization": bearer})

    # Assert — 可浏览全部项目
    assert r.status_code == 200, r.text
    assert any(s["id"] == sid for s in r.json())


async def test_playerless_admin_owner_me_returns_empty(client):
    # Arrange
    owner_uuid, _, _ = await _seed_player_with_account("owner_p")
    await _seed_sheet(owner_uuid)
    _, bearer = await _seed_playerless_account()

    # Act
    r = await client.get("/sheets", params={"owner": "me"}, headers={"Authorization": bearer})

    # Assert — 无玩家身份 → 「我的项目」为空，而非泄漏全量
    assert r.status_code == 200, r.text
    assert r.json() == []


async def test_playerless_admin_views_sheet_detail(client):
    # Arrange
    owner_uuid, _, _ = await _seed_player_with_account("owner_p")
    sid = await _seed_sheet(owner_uuid)
    _, bearer = await _seed_playerless_account()

    # Act
    r = await client.get(f"/sheets/{sid}", headers={"Authorization": bearer})

    # Assert
    assert r.status_code == 200, r.text
    assert r.json()["id"] == sid


async def test_playerless_admin_views_construction_progress(client):
    # Arrange
    owner_uuid, _, _ = await _seed_player_with_account("owner_p")
    sid = await _seed_sheet(owner_uuid)
    _, bearer = await _seed_playerless_account()

    # Act
    r = await client.get(f"/v1/construction/{sid}/progress", headers={"Authorization": bearer})

    # Assert
    assert r.status_code == 200, r.text


async def test_sheets_service_token_channel_still_works(client):
    """回归：MCDR 代理通道（service-token + X-Player-UUID）列表不因依赖替换失效。"""
    # Arrange
    owner_uuid, _, _ = await _seed_player_with_account("owner_p")
    sid = await _seed_sheet(owner_uuid)

    # Act
    r = await client.get(
        "/sheets",
        headers={"X-Service-Token": SVC, "X-Player-UUID": str(owner_uuid)},
    )

    # Assert
    assert r.status_code == 200, r.text
    assert any(s["id"] == sid for s in r.json())


# ===========================================================================
# viewer JWT 通道 M1 复验 + refresh claim 守卫（CR 发现）
# ===========================================================================


async def test_viewer_rejects_stale_active_uuid_after_player_migration(client):
    """M1 复验（同 _player_from_jwt）：玩家迁到别的账号后，旧 token 不得再以玩家视角读。"""
    # Arrange — 账号 A + 玩家 P（token active_uuid=P）
    puuid, aid, bearer = await _seed_player_with_account("mover")
    # P 迁到账号 B
    async with async_session_factory() as s:
        account_b = WebAccount(role="user")
        s.add(account_b)
        await s.flush()
        await s.execute(
            update(Player).where(Player.uuid == puuid).values(web_account_id=account_b.id)
        )
        await s.commit()

    # Act — 旧 token 调列表（曾把 P 并入 uuids → owner=me 泄漏 B 的项目）
    r = await client.get("/sheets", params={"owner": "me"}, headers={"Authorization": bearer})

    # Assert — 与 get_current_player 同语义：401 player not bound to account
    assert r.status_code == 401
    assert r.json()["detail"] == "player not bound to account"


async def test_refresh_malformed_active_uuid_claim_rejected(client):
    """refresh token 的 active_uuid 非法格式 → 401（而非未捕获 ValueError → 500）。"""
    # Arrange — 手工伪造畸形 claim（签发端 bug / 伪造场景）
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone

    aid, _ = await _seed_playerless_account()
    token = pyjwt.encode(
        {
            "sub": str(aid),
            "role": "owner",
            "type": "refresh",
            "active_uuid": "not-a-uuid",
            "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
        },
        _settings.jwt_secret,
        algorithm="HS256",
    )

    # Act
    r = await client.post("/auth/refresh", json={"refresh_token": token})

    # Assert
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid active_uuid"
