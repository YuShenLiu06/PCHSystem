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


class PlayerBrief(BaseModel):
    uuid: uuid.UUID
    name: str
    role: str


class TokenExchangeResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    player: PlayerBrief


class RefreshRequest(BaseModel):
    refresh_token: str


class MeResponse(BaseModel):
    uuid: uuid.UUID
    name: str
    role: str


class LastSheetResponse(BaseModel):
    sheet_id: int | None
