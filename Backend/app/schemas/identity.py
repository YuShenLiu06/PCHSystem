"""身份管理 API 请求/响应模型。

与前端 Frontend/src/api/identity.ts 类型对齐：
- AccountBrief / PlayerBrief / AuthResponse=TokenExchangeResponse / MeResponse / MyAccountResponse
  / BindCodeResponse=BindTokenIssueResponse / BindResultResponse=BindConfirmResponse
"""
import re
import uuid

from pydantic import BaseModel, Field, field_validator


def _validate_username(v: str) -> str:
    from app.core.config import get_settings

    s = get_settings()
    if not (s.username_min_length <= len(v) <= s.username_max_length):
        raise ValueError(
            f"username length must be between {s.username_min_length} and {s.username_max_length}"
        )
    if not re.match(r"^[A-Za-z0-9_-]+$", v):
        raise ValueError("username may only contain letters, digits, _ and -")
    return v


def _validate_password(v: str) -> str:
    from app.core.config import get_settings

    s = get_settings()
    if not (s.password_min_length <= len(v) <= s.password_max_length):
        raise ValueError(
            f"password length must be between {s.password_min_length} and {s.password_max_length}"
        )
    return v


# ===== 请求模型 =====


class PasswordLoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        return _validate_username(v)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return _validate_password(v)


class ClaimBindRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        return _validate_username(v)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return _validate_password(v)


class BindTokenRequest(BaseModel):
    """POST /bind/token 请求体（MCDR bind_client.request_bind_token POST JSON body {uuid, name}）。"""

    uuid: uuid.UUID
    name: str


class BindConsumeRequest(BaseModel):
    """POST /bind/consume 请求体（仅 short_code；UUID 走 X-Player-UUID header）。"""

    short_code: str


class BindConfirmRequest(BaseModel):
    short_code: str


class UpdateDisplayNameRequest(BaseModel):
    """PATCH /web-accounts/me 请求体：设置自定义昵称（sheets 三端显示名主源）。

    空白 strip；schema ``min_length=1`` 拒纯空白（与迁移 CHECK 一致）。
    """

    display_name: str = Field(min_length=1, max_length=64)


# ===== 响应模型（引用 schemas.auth 的 AccountBrief/PlayerBrief 保持单一来源） =====


class BindTokenIssueResponse(BaseModel):
    """绑定短码响应（前端 BindCodeResponse）。"""
    short_code: str
    expires_in: int


class BindConfirmResponse(BaseModel):
    """POST /bind/confirm 响应（前端 BindResultResponse）。

    契约：绑定不改 account，前端继续用现有 JWT；响应只含 player + account，不含 token。
    """
    player: "PlayerBrief"
    account: "AccountBrief"


class BindConsumeResponse(BaseModel):
    """POST /bind/consume 响应（MCDR bind_client.consume_bind_code 依赖）。

    契约：{status, account, player} —— MCDR 客户端用 account+player 渲染回执。
    """
    status: str
    player: "PlayerBrief"
    account: "AccountBrief"


class MyAccountResponse(BaseModel):
    """GET /web-accounts/me 响应（前端 MyAccountResponse）。

    契约：{account: AccountBrief, players: [PlayerBrief]}。
    """
    account: "AccountBrief"
    players: list["PlayerBrief"]


# 延迟引用解析（避免与 schemas.auth 循环 import）
from app.schemas.auth import AccountBrief, PlayerBrief  # noqa: E402

BindConfirmResponse.model_rebuild()
BindConsumeResponse.model_rebuild()
MyAccountResponse.model_rebuild()
