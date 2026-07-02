from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SheetCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=128)


class SheetPatchRequest(BaseModel):
    title: str = Field(min_length=1, max_length=128)


class RowUpsertRequest(BaseModel):
    item_name: str = Field(min_length=1, max_length=64)
    need_qty: int = Field(default=0, ge=0)
    mode: int = Field(default=0, ge=0, le=1)
    sort_order: int = Field(default=0, ge=0)


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
