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


class RowDetail(BaseModel):
    id: int
    item_name: str
    need_qty: int
    mode: int
    status: str
    claimant_uuid: UUID | None
    claimant_name: str | None
    delivered_qty: int
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


class SheetItemIn(BaseModel):
    """``/sheets/from-items`` 批量建行条目（与 ``RowUpsertRequest`` 同字段，mode 默认 lock）。"""

    item_name: str = Field(min_length=1, max_length=64)
    need_qty: int = Field(default=0, ge=0)
    mode: int = Field(default=0, ge=0, le=1)
    sort_order: int = Field(default=0, ge=0)


class SheetFromItemsRequest(BaseModel):
    """``POST /sheets/from-items``：一次性建表 + 批量行（用于「投影解析→生成表格」）。"""

    title: str = Field(min_length=1, max_length=128)
    items: list[SheetItemIn] = Field(default_factory=list, max_length=2000)
