from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SheetCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=128)


class SheetPatchRequest(BaseModel):
    title: str = Field(min_length=1, max_length=128)


class RowUpsertRequest(BaseModel):
    item_name: str = Field(min_length=1, max_length=128)
    need_qty: int = Field(default=0, ge=0)
    done_flag: int = Field(default=0, ge=0, le=1)
    sort_order: int = Field(default=0, ge=0)


class RowDetail(BaseModel):
    id: int
    item_name: str
    need_qty: int
    done_flag: int
    sort_order: int
    updated_at: datetime


class SheetSummary(BaseModel):
    id: int
    owner_uuid: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class SheetDetail(SheetSummary):
    rows: list[RowDetail]
