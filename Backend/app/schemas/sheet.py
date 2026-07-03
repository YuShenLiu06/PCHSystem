from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class SheetCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=128)


class SheetPatchRequest(BaseModel):
    title: str = Field(min_length=1, max_length=128)


class RowUpsertRequest(BaseModel):
    """行 upsert 请求（``PUT /sheets/{sid}/rows``）。

    ``item_name`` 与 ``registry_id`` 至少提供一个（model_validator 校验）：
    - Web / 投影解析路径：传中文 ``item_name`` + 可选 ``registry_id``。
    - MCDR 手持新建行（addhand）：仅传 ``registry_id``，API 层用 LangJsonTranslator
      翻译补默认 ``item_name``（未命中回退 registry_id 本身）。
    """

    item_name: str | None = Field(default=None, max_length=64)
    registry_id: str | None = Field(default=None, max_length=128)
    need_qty: int = Field(default=0, ge=0)
    mode: int = Field(default=0, ge=0, le=1)
    sort_order: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _require_name_or_registry(self) -> "RowUpsertRequest":
        if not self.item_name and not self.registry_id:
            raise ValueError("item_name 与 registry_id 至少提供一个")
        return self


class RowDeliveryRequest(BaseModel):
    delivered_qty: int = Field(ge=0)


class RowContributeRequest(BaseModel):
    """progress 模式增量上交（任意登录玩家）。qty 为本次新增交付量。"""

    qty: int = Field(ge=1)


class RowProgressRequest(BaseModel):
    """progress 模式 owner 直接修正进度（绝对值，可增可减）。仅表拥有者/admin。"""

    delivered_qty: int = Field(ge=0)


class RowContributor(BaseModel):
    """progress 行的贡献者（上交过材料的玩家）。"""

    player_uuid: UUID
    player_name: str


class RowDetail(BaseModel):
    id: int
    item_name: str
    registry_id: str | None = None
    need_qty: int
    mode: int
    status: str
    claimant_uuid: UUID | None
    claimant_name: str | None
    delivered_qty: int
    contributors: list[RowContributor] = []
    sort_order: int
    updated_at: datetime


class SheetSummary(BaseModel):
    id: int
    owner_uuid: UUID
    owner_name: str
    title: str
    created_at: datetime
    updated_at: datetime


class SheetDetail(SheetSummary):
    rows: list[RowDetail]


class SheetItemIn(RowUpsertRequest):
    """``/sheets/from-items`` 批量建行条目（继承 ``RowUpsertRequest`` 字段 + 校验，mode 默认 lock）。

    投影解析 ``PreviewItem`` 透传 ``registry_id``（= ``item_id``）+ 中文 ``item_name``。
    """


class SheetFromItemsRequest(BaseModel):
    """``POST /sheets/from-items``：一次性建表 + 批量行（用于「投影解析→生成表格」）。"""

    title: str = Field(min_length=1, max_length=128)
    items: list[SheetItemIn] = Field(default_factory=list, max_length=2000)
