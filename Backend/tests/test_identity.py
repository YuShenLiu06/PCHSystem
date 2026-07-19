"""identity 端点集成测试（AAA 范式）。

覆盖方案 §一.10 的 8 个端点（/auth/login + /web-accounts/{register,me} + /bind/{token,issue,confirm,consume,claim}）。
契约权威：方案 §一.10 + 跨端对端实现（McdrPlugin bind_client.py / Frontend api/identity.ts）。
"""
import uuid

import pytest

import app.api.deps as deps
from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.jwt import create_access_token
from app.models.user import Player, WebAccount
from app.repositories import web_account_repo
from app.core.security import hash_password


# ===== fixtures =====


@pytest.fixture(autouse=True)
def _svc_token(monkeypatch):
    """注入测试 service-token（与 test_auth_api.py 一致）。"""
    deps._settings = get_settings()
    deps._settings.mcdr_service_token = "svc"


def _svc_headers() -> dict:
    return {"X-Service-Token": "svc"}


def _player_uuid_headers(u: uuid.UUID) -> dict:
    return {"X-Service-Token": "svc", "X-Player-UUID": str(u)}


def _bearer(access: str) -> dict:
    return {"Authorization": f"Bearer {access}"}


async def _issue_temp_account_token(client, name: str = "alice") -> tuple[uuid.UUID, str, int]:
    """走完整 /auth/token + /auth/exchange 链路拿临时账号 JWT。

    返回 (player_uuid, access_token, account_id)。
    """
    u = uuid.uuid4()
    issue = await client.post(
        "/auth/token",
        json={"uuid": str(u), "name": name},
        headers=_svc_headers(),
    )
    assert issue.status_code == 200, issue.text
    token = issue.json()["login_url"].split("token=")[-1]
    ex = await client.post("/auth/exchange", json={"token": token})
    assert ex.status_code == 200, ex.text
    body = ex.json()
    return u, body["access_token"], body["account"]["id"]


async def _make_permanent_account(username: str, password: str, *player_uuids: uuid.UUID) -> int:
    """直接经 repo 建永久账号 + 挂给定 players（绕过 API 用于测试场景构造）。"""
    async with async_session_factory() as s:
        account = WebAccount(
            username=username,
            password_hash=hash_password(password),
            role="user",
        )
        s.add(account)
        await s.flush()
        for u in player_uuids:
            player = Player(uuid=u, current_name=f"player-{u.hex[:6]}", web_account_id=account.id)
            s.add(player)
        await s.commit()
        return account.id


# ===== POST /auth/login =====


@pytest.mark.asyncio
async def test_login_returns_full_auth_response_with_first_player(client):
    # Arrange — 建一个永久账号 + 2 个绑定 player
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    await _make_permanent_account("bob", "Secret123", u1, u2)

    # Act
    resp = await client.post("/auth/login", json={"username": "bob", "password": "Secret123"})

    # Assert — 完整 AuthResponse shape（access/refresh/token_type/player/account）
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    # player = 该永久账号第一个绑定 player（players[0]，按 UUID 顺序由 list_players 返回）
    assert body["player"]["uuid"] in {str(u1), str(u2)}
    assert body["player"]["name"]
    assert body["player"]["role"] == "user"
    # account 含 role（契约：AccountBrief {id, is_temporary, username, role}）
    assert body["account"]["is_temporary"] is False
    assert body["account"]["username"] == "bob"
    assert body["account"]["role"] == "user"


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(client):
    # Arrange
    await _make_permanent_account("bob", "Secret123", uuid.uuid4())

    # Act
    resp = await client.post("/auth/login", json={"username": "bob", "password": "WRONGpw9"})

    # Assert
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_user_returns_401(client):
    # Act
    resp = await client.post(
        "/auth/login", json={"username": "nobody", "password": "whatever1"}
    )

    # Assert
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_temporary_account_rejected(client):
    """临时账号（username null）不可密码登录。"""
    # Arrange — /auth/token 链路建一个临时账号
    _, _, _ = await _issue_temp_account_token(client, name="temp-only")

    # Act — 临时账号无 username/password，不可能凭据匹配；任何用户名都查不到永久账号
    resp = await client.post(
        "/auth/login", json={"username": "anypermanent", "password": "whatever1"}
    )

    # Assert
    assert resp.status_code == 401


# ===== POST /web-accounts/register =====


@pytest.mark.asyncio
async def test_register_temporary_to_permanent_success(client):
    # Arrange — 临时账号 JWT
    _, access, account_id = await _issue_temp_account_token(client, name="alice")

    # Act
    resp = await client.post(
        "/web-accounts/register",
        json={"username": "alice_permanent", "password": "SecurePass1"},
        headers=_bearer(access),
    )

    # Assert — 返回 AuthResponse（含新 token + account.is_temporary=False）
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["account"]["is_temporary"] is False
    assert body["account"]["username"] == "alice_permanent"
    assert body["account"]["role"] == "user"
    # player 保留（原临时账号绑定的 player）
    assert body["player"]["uuid"]

    # 端到端验证 register 的 commit 落地（曾漏 commit 导致 login 401）
    login_resp = await client.post(
        "/auth/login",
        json={"username": "alice_permanent", "password": "SecurePass1"},
    )
    assert login_resp.status_code == 200, login_resp.text


@pytest.mark.asyncio
async def test_register_already_permanent_returns_400(client):
    # Arrange — 直接建永久账号 + JWT
    u = uuid.uuid4()
    account_id = await _make_permanent_account("bob", "Secret123", u)
    access = create_access_token(account_id, "user", active_uuid=u)

    # Act
    resp = await client.post(
        "/web-accounts/register",
        json={"username": "newname", "password": "SecurePass1"},
        headers=_bearer(access),
    )

    # Assert
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_register_duplicate_username_returns_409(client):
    # Arrange — 第一个永久账号占用户名
    u1 = uuid.uuid4()
    await _make_permanent_account("taken_name", "Secret123", u1)
    # 第二个临时账号想注册同名
    _, access, _ = await _issue_temp_account_token(client, name="alice2")

    # Act
    resp = await client.post(
        "/web-accounts/register",
        json={"username": "taken_name", "password": "SecurePass1"},
        headers=_bearer(access),
    )

    # Assert
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_register_short_username_rejected(client):
    # Arrange
    _, access, _ = await _issue_temp_account_token(client, name="alice3")

    # Act — username 长度 < 3（config 默认 username_min_length=3）
    resp = await client.post(
        "/web-accounts/register",
        json={"username": "ab", "password": "SecurePass1"},
        headers=_bearer(access),
    )

    # Assert
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_short_password_rejected(client):
    # Arrange
    _, access, _ = await _issue_temp_account_token(client, name="alice4")

    # Act — password 长度 < 8
    resp = await client.post(
        "/web-accounts/register",
        json={"username": "valid_user", "password": "short"},
        headers=_bearer(access),
    )

    # Assert
    assert resp.status_code == 422


# ===== GET /web-accounts/me =====


@pytest.mark.asyncio
async def test_web_accounts_me_returns_account_and_players(client):
    # Arrange — 临时账号
    u, access, _ = await _issue_temp_account_token(client, name="alice")

    # Act
    resp = await client.get("/web-accounts/me", headers=_bearer(access))

    # Assert — 前端 MyAccountResponse shape：{account, players}
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "account" in body
    assert "players" in body
    # 旧字段（id/is_temporary/...）不应在顶层
    assert "id" not in body, "MyAccountResponse should wrap in account envelope"
    assert body["account"]["is_temporary"] is True
    assert body["account"]["role"] == "user"
    assert len(body["players"]) == 1
    assert body["players"][0]["uuid"] == str(u)


@pytest.mark.asyncio
async def test_web_accounts_me_requires_jwt(client):
    # Act
    resp = await client.get("/web-accounts/me")

    # Assert
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_web_accounts_me_aggregates_multiple_uuids(client):
    """同一 account 绑定两个 UUID → /web-accounts/me 返两个 player。"""
    # Arrange — 直接建账号 + 两个 player 挂同账号
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    async with async_session_factory() as s:
        account = WebAccount(
            username="multi",
            password_hash=hash_password("Secret123"),
            role="user",
        )
        s.add(account)
        await s.flush()
        s.add(Player(uuid=u1, current_name="p1", web_account_id=account.id))
        s.add(Player(uuid=u2, current_name="p2", web_account_id=account.id))
        await s.commit()
        account_id = account.id
    access = create_access_token(account_id, "user", active_uuid=u1)

    # Act
    resp = await client.get("/web-accounts/me", headers=_bearer(access))

    # Assert
    assert resp.status_code == 200
    body = resp.json()
    uuids = {p["uuid"] for p in body["players"]}
    assert uuids == {str(u1), str(u2)}


# ===== /bind/token + /bind/confirm（game_init 方向） =====


@pytest.mark.asyncio
async def test_game_init_bind_token_then_confirm_attaches_player(client):
    # Arrange — 永久账号 Web 端 + 新 UUID 游戏内发起绑定
    perm_u = uuid.uuid4()
    account_id = await _make_permanent_account("web_perm", "Secret123", perm_u)
    access = create_access_token(account_id, "user", active_uuid=perm_u)
    new_uuid = uuid.uuid4()

    # Act 1 — 游戏内 !!PCH bind → POST /bind/token {uuid, name} (service-token, body)
    issue = await client.post(
        "/bind/token",
        json={"uuid": str(new_uuid), "name": "newbie"},
        headers=_svc_headers(),  # 仅 service-token，无 X-Player-UUID
    )
    # Assert 1
    assert issue.status_code == 200, issue.text
    code = issue.json()["short_code"]
    assert len(code) == 6
    assert issue.json()["expires_in"] > 0

    # Act 2 — Web 端 /bind/confirm {short_code} (Bearer JWT)
    confirm = await client.post(
        "/bind/confirm",
        json={"short_code": code},
        headers=_bearer(access),
    )
    # Assert 2 — 契约 BindResultResponse {player, account}，无 token
    assert confirm.status_code == 200, confirm.text
    cb = confirm.json()
    assert "access_token" not in cb, "confirm 响应不含 token（前端继续用现有 JWT）"
    assert cb["player"]["uuid"] == str(new_uuid)
    assert cb["player"]["name"] == "newbie"
    assert cb["account"]["id"] == account_id

    # Verify DB 状态：new_uuid 现在挂在永久账号下
    async with async_session_factory() as s:
        attached = await web_account_repo.list_uuids(s, account_id)
    assert new_uuid in attached


@pytest.mark.asyncio
async def test_bind_confirm_invalid_code_returns_404(client):
    # Arrange — 永久账号
    u = uuid.uuid4()
    account_id = await _make_permanent_account("web_perm2", "Secret123", u)
    access = create_access_token(account_id, "user", active_uuid=u)

    # Act — 未签发的短码
    resp = await client.post(
        "/bind/confirm",
        json={"short_code": "BADBAD"},
        headers=_bearer(access),
    )

    # Assert
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_bind_confirm_expired_code_returns_404(client):
    # Arrange — 出码后手动置过期
    from datetime import datetime, timedelta, timezone
    from app.models.user import BindToken

    new_uuid = uuid.uuid4()
    async with async_session_factory() as s:
        # 必须先有 player 才能 issue_game_init（FK）
        s.add(Player(uuid=new_uuid, current_name="newbie"))
        await s.flush()
        from app.repositories import bind_token_repo
        token = await bind_token_repo.issue_game_init(s, new_uuid)
        token.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await s.commit()
        code = token.short_code

    u = uuid.uuid4()
    account_id = await _make_permanent_account("web_perm3", "Secret123", u)
    access = create_access_token(account_id, "user", active_uuid=u)

    # Act
    resp = await client.post(
        "/bind/confirm",
        json={"short_code": code},
        headers=_bearer(access),
    )

    # Assert
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_bind_confirm_reused_code_returns_404(client):
    """短码一次性，消费后不可再用。"""
    perm_u = uuid.uuid4()
    account_id = await _make_permanent_account("web_perm4", "Secret123", perm_u)
    access = create_access_token(account_id, "user", active_uuid=perm_u)
    new_uuid = uuid.uuid4()

    issue = await client.post(
        "/bind/token",
        json={"uuid": str(new_uuid), "name": "newbie"},
        headers=_svc_headers(),
    )
    code = issue.json()["short_code"]

    first = await client.post(
        "/bind/confirm", json={"short_code": code}, headers=_bearer(access)
    )
    assert first.status_code == 200

    # Act — 二次消费
    second = await client.post(
        "/bind/confirm", json={"short_code": code}, headers=_bearer(access)
    )
    # Assert
    assert second.status_code == 404


@pytest.mark.asyncio
async def test_bind_confirm_temporary_account_rejected(client):
    """临时账号不可执行 /bind/confirm（必须先 register 或 claim 到永久账号）。"""
    _, access, _ = await _issue_temp_account_token(client, name="alice")
    new_uuid = uuid.uuid4()
    issue = await client.post(
        "/bind/token",
        json={"uuid": str(new_uuid), "name": "newbie"},
        headers=_svc_headers(),
    )
    code = issue.json()["short_code"]

    # Act
    resp = await client.post(
        "/bind/confirm", json={"short_code": code}, headers=_bearer(access)
    )

    # Assert
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_bind_token_requires_service_token(client):
    # Act — 无 service-token
    resp = await client.post(
        "/bind/token",
        json={"uuid": str(uuid.uuid4()), "name": "x"},
    )
    # Assert
    assert resp.status_code == 401


# ===== /bind/issue + /bind/consume（web_init 方向） =====


@pytest.mark.asyncio
async def test_web_init_bind_issue_then_consume_attaches_player(client):
    # Arrange — 永久账号 Web 端 issue + 新 UUID 游戏内 consume
    perm_u = uuid.uuid4()
    account_id = await _make_permanent_account("web_perm5", "Secret123", perm_u)
    access = create_access_token(account_id, "user", active_uuid=perm_u)
    new_uuid = uuid.uuid4()

    # Act 1 — Web /bind/issue（Bearer JWT）
    issue = await client.post("/bind/issue", headers=_bearer(access))
    # Assert 1
    assert issue.status_code == 200, issue.text
    code = issue.json()["short_code"]

    # Act 2 — 游戏内 /bind/consume（service-token + X-Player-UUID header，body {short_code}）
    consume = await client.post(
        "/bind/consume",
        json={"short_code": code},
        headers=_player_uuid_headers(new_uuid),
    )
    # Assert 2 — 契约 BindConsumeResponse {status, account, player}
    assert consume.status_code == 200, consume.text
    cb = consume.json()
    assert cb["status"] == "ok"
    assert cb["player"]["uuid"] == str(new_uuid)
    assert cb["account"]["id"] == account_id

    # Verify DB
    async with async_session_factory() as s:
        attached = await web_account_repo.list_uuids(s, account_id)
    assert new_uuid in attached


@pytest.mark.asyncio
async def test_bind_issue_temporary_account_rejected(client):
    """临时账号不可 /bind/issue（必须永久账号）。"""
    _, access, _ = await _issue_temp_account_token(client, name="alice")

    # Act
    resp = await client.post("/bind/issue", headers=_bearer(access))

    # Assert
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_bind_consume_invalid_code_returns_404(client):
    # Arrange
    new_uuid = uuid.uuid4()

    # Act
    resp = await client.post(
        "/bind/consume",
        json={"short_code": "BADBAD"},
        headers=_player_uuid_headers(new_uuid),
    )

    # Assert
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_bind_consume_requires_player_uuid_header(client):
    """consume 端点 UUID 走 X-Player-UUID header（不是 query），缺失 → 422。"""
    # Act — 无 X-Player-UUID header
    resp = await client.post(
        "/bind/consume",
        json={"short_code": "ANBCDE"},
        headers=_svc_headers(),  # 只带 service-token
    )
    # Assert — FastAPI Header(...) required → 422
    assert resp.status_code == 422


# ===== /bind/claim（临时账号绑定到已有永久账号） =====


@pytest.mark.asyncio
async def test_claim_binds_temporary_to_permanent_and_returns_new_jwt(client):
    # Arrange — 永久账号（凭据）+ 临时账号 JWT
    perm_u = uuid.uuid4()
    await _make_permanent_account("target_perm", "Secret123", perm_u)
    temp_u, temp_access, _ = await _issue_temp_account_token(client, name="temp-player")

    # Act — 临时账号凭永久账号用户名密码 claim
    resp = await client.post(
        "/bind/claim",
        json={"username": "target_perm", "password": "Secret123"},
        headers=_bearer(temp_access),
    )

    # Assert — 换发永久账号 JWT；player 是迁移过来的原临时账号 player
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["account"]["username"] == "target_perm"
    assert body["account"]["is_temporary"] is False
    assert body["player"]["uuid"] == str(temp_u)

    # Verify DB：temp_u 现在挂在 target_perm 账号下
    async with async_session_factory() as s:
        target = await web_account_repo.get_by_username(s, "target_perm")
        uuids = await web_account_repo.list_uuids(s, target.id)
    assert temp_u in uuids


@pytest.mark.asyncio
async def test_claim_wrong_password_returns_401(client):
    # Arrange
    await _make_permanent_account("target_perm2", "Secret123", uuid.uuid4())
    _, temp_access, _ = await _issue_temp_account_token(client, name="temp")

    # Act
    resp = await client.post(
        "/bind/claim",
        json={"username": "target_perm2", "password": "WRONGpw99"},
        headers=_bearer(temp_access),
    )

    # Assert
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_claim_unknown_username_returns_401(client):
    # Arrange
    _, temp_access, _ = await _issue_temp_account_token(client, name="temp")

    # Act
    resp = await client.post(
        "/bind/claim",
        json={"username": "ghost", "password": "whatever1"},
        headers=_bearer(temp_access),
    )

    # Assert
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_claim_already_permanent_returns_400(client):
    # Arrange — JWT 是永久账号
    u = uuid.uuid4()
    account_id = await _make_permanent_account("already_perm", "Secret123", u)
    access = create_access_token(account_id, "user", active_uuid=u)

    # Act
    resp = await client.post(
        "/bind/claim",
        json={"username": "already_perm", "password": "Secret123"},
        headers=_bearer(access),
    )

    # Assert
    assert resp.status_code == 400
