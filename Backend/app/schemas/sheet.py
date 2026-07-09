from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class SheetCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=128)


class SheetPatchRequest(BaseModel):
    title: str = Field(min_length=1, max_length=128)


class RowUpsertRequest(BaseModel):
    """行 upsert / 更新请求（``PUT /sheets/{sid}/rows``，单端点按 row_id 分流）。

    - **带 ``row_id``**：按主键 **更新**该行（"修改"以 id 为定位主轴）。此时
      ``item_name``/``registry_id``/``need_qty``/``mode``/``sort_order`` 全可选
      （部分更新，传哪个改哪个）；**改名 = 只传 ``row_id`` + ``item_name``**。
      更新路径不校验 name/registry 至少一个（可只改 need/mode/sort）。
    - **不带 ``row_id``**：按 ``item_name`` **新建**（原 upsert 新建语义），
      ``item_name`` 与 ``registry_id`` 至少提供一个（model_validator 校验）：
      - Web / 投影解析路径：传中文 ``item_name`` + 可选 ``registry_id``。
      - MCDR 手持新建行（addhand）：仅传 ``registry_id``，API 层用 LangJsonTranslator
        翻译补默认 ``item_name``（未命中回退 registry_id 本身）。

    issue #20：旧实现无 row_id，改名走 by-``item_name`` upsert 查不到旧行 → 新建 → 重复。

    子物品嵌套行（0012，0013 放宽倍数为小数）：
    - ``parent_row_id`` 非空时为子行：要求 ``registry_id`` 非空 + ``qty_per_unit`` > 0（支持 0.5 等小数）。
    - 子行 ``need_qty`` 由 API 派生（= ceil(qty_per_unit × 父行.need_qty)，向上取整成整数），请求传入时忽略。
    """

    row_id: int | None = Field(default=None, ge=1)
    item_name: str | None = Field(default=None, max_length=64)
    registry_id: str | None = Field(default=None, max_length=128)
    need_qty: int | None = Field(default=None, ge=0)
    mode: int | None = Field(default=None, ge=0, le=1)
    sort_order: int | None = Field(default=None, ge=0)
    parent_row_id: int | None = Field(default=None, ge=1)
    qty_per_unit: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _require_name_or_registry_when_create(self) -> "RowUpsertRequest":
        # 仅新建路径（无 row_id）要求 item_name/registry_id 至少一个；更新路径字段全可选
        if self.row_id is None and not self.item_name and not self.registry_id:
            raise ValueError("item_name 与 registry_id 至少提供一个")
        return self

    @model_validator(mode="after")
    def _validate_sub_item_requirements(self) -> "RowUpsertRequest":
        # 子物品路径：
        # - 新建（无 row_id）+ parent_row_id 非空：registry_id 必填 + qty_per_unit > 0
        # - 更新（有 row_id）+ parent_row_id 非空：registry_id 已落库不重判，
        #   仅当显式传了 qty_per_unit 才校验 > 0（防 PATCH reparent/改 need 因缺
        #   registry_id 被 422——issue #19 D6）
        if self.parent_row_id is not None and self.row_id is None:
            if self.registry_id is None:
                raise ValueError("子物品（parent_row_id 非空）必须提供 registry_id")
            if self.qty_per_unit is None or self.qty_per_unit <= 0:
                raise ValueError("子物品（parent_row_id 非空）qty_per_unit 必须 > 0")
        if self.qty_per_unit is not None and self.qty_per_unit <= 0:
            raise ValueError("qty_per_unit 必须 > 0")
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
    parent_row_id: int | None = None
    qty_per_unit: float | None = None


class SheetSummary(BaseModel):
    id: int
    owner_uuid: UUID
    owner_name: str
    title: str
    status: str
    archived_path: str | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SheetDetail(SheetSummary):
    rows: list[RowDetail]


class SheetItemIn(RowUpsertRequest):
    """``/sheets/from-items`` 批量建行条目（继承 ``RowUpsertRequest`` 字段 + 校验，mode 默认 lock）。

    投影解析 ``PreviewItem`` 透传 ``registry_id``（= ``item_id``）+ 中文 ``item_name``。
    每条均为**新建**（新表无既有行可定位）→ ``row_id`` 在此无意义，禁止携带：
    否则会绕过父类「name/registry 至少一个」校验（该豁免仅服务更新路径），
    使 ``item_name=None & registry_id=None`` 直抵 ``_resolve_item_name`` 的防御点 → 500。
    """

    @model_validator(mode="after")
    def _forbid_row_id_in_batch_create(self) -> "SheetItemIn":
        # row_id 是更新路径的定位主轴；批量新建携带它既无意义又会绕过 name/registry 校验
        if self.row_id is not None:
            raise ValueError("from-items 批量建行不支持 row_id（每行均为新建）")
        return self


class SheetFromItemsRequest(BaseModel):
    """``POST /sheets/from-items``：一次性建表 + 批量行（用于「投影解析→生成表格」）。"""

    title: str = Field(min_length=1, max_length=128)
    items: list[SheetItemIn] = Field(default_factory=list, max_length=2000)
