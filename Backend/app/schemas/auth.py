import uuid
from pydantic import BaseModel


class TokenIssueRequest(BaseModel):
    uuid: uuid.UUID
    name: str


class TokenIssueResponse(BaseModel):
    login_url: str
    expires_in: int
    previous_tokens_revoked: int


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
