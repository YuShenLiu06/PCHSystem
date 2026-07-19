import uuid
from pydantic import BaseModel


class TokenIssueRequest(BaseModel):
    uuid: uuid.UUID
    name: str


class TokenIssueResponse(BaseModel):
    login_url: str
    expires_in: int
    previous_tokens_revoked: int
    # 需求 4：后端探 web 可达性上报，供插件 !!PCH login 回执提示「前端未启用」。
    # None = 后端未配 WEB_PROBE_URL（旧版/未探）；True/False = 已探结论
    frontend_online: bool | None = None


class TokenExchangeRequest(BaseModel):
    token: uuid.UUID


class AccountBrief(BaseModel):
    """Web 账号摘要（与前端 Frontend/src/api/identity.ts AccountBrief 对齐）。"""
    id: int
    is_temporary: bool
    username: str | None = None
    role: str


class PlayerBrief(BaseModel):
    uuid: uuid.UUID
    name: str
    role: str


class TokenExchangeResponse(BaseModel):
    """exchange / login / register / claim-bind 共用 shape（前端 AuthResponse）。

    player 永久账号必有至少一个绑定 player（!!PCH login 即自动挂临时账号）；
    register/claim 边界场景下若暂无 player 允许 None，由调用方保证语义。
    """
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    player: PlayerBrief | None = None
    account: AccountBrief


class RefreshRequest(BaseModel):
    refresh_token: str


class MeResponse(BaseModel):
    """当前身份响应（升级为 account + players + active_uuid）。"""
    account: AccountBrief
    players: list[PlayerBrief]
    active_uuid: uuid.UUID


class LastSheetResponse(BaseModel):
    sheet_id: int | None
