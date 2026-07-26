from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class SheetCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=128)


class SheetPatchRequest(BaseModel):
    title: str = Field(min_length=1, max_length=128)


class ManagerGrantRequest(BaseModel):
    """``POST /sheets/{id}/managers``：授予协管员。

    按 player_uuid 授予（MCDR 端先用 uuid_api_remake 把玩家名转 UUID 再调本端点）。
    后端解析 target Player → ``web_account_id``；未绑 Web 账号 → 422（B7）。
    目标 account == owner account → 409 ``SheetOwnerCannotBeManager``（B7）。
    """

    player_uuid: UUID


class ManagerRevokeRequest(BaseModel):
    """``DELETE /sheets/{id}/managers``：撤销协管员（account 锚）。

    收 ``web_account_id``（非 player_uuid）以匹配存储模型；同一账号下任一 UUID
    都可 self-revoke（B6 守卫：``player.web_account_id is not None`` 显式拒
    None==None 误匹配——未绑账号玩家不能 self-revoke）。
    """

    web_account_id: int


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
    """progress 行的贡献者（按 Web 账号聚合：同账号多 UUID 合并为一条）。

    ``account_id`` 为 None 表示该贡献者未绑 Web 账号（历史数据），按 player 退化为一对一。
    ``display_name`` 统一显示名（自定义昵称优先，否则该账号下最近活跃 UUID 的游戏名）。
    """

    account_id: int | None = None
    display_name: str
    member_uuids: list[UUID]
    contributed_qty: int


class SheetManagerEntry(BaseModel):
    """项目级协管员（迁移 0014，account 锚，R-5 落地）。

    - ``web_account_id``：manager 锚定的 Web 账号 id（PK 一员）。
    - ``display_name``：账号显示名（``WebAccount.display_name`` 优先，否则账号下
      ``last_seen_at`` 最新 UUID 的 ``current_name``）——经
      ``web_account_repo.resolve_account_briefs`` 解析。
    - ``member_uuids``：账号下全部 UUID（含 inactive）；客户端按 viewer_uuids 做
      交集判定 ``is_manager``（前端 auth store 持有绑定 UUIDs；MCDR sheet detail
      持有 viewer_uuids），无需感知 account_id。
    """

    web_account_id: int
    display_name: str
    member_uuids: list[UUID]
    granted_at: datetime


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
    # 当前查看者所属 Web 账号的全部 UUID（R-5 主锚：权限/可见性升 account 级）。
    # 前端/MCDR 据此判断 owner/claimant 可见性（含同 account 多 UUID 共享权限）；
    # 真实权限仍以后端 RBAC 为准（R-9），此处仅服务可见性。
    viewer_uuids: list[UUID] = Field(default_factory=list)
    # 项目协管员列表（迁移 0014，account 锚）；客户端按 member_uuids ∩ viewer_uuids
    # 判定 is_manager。展示用 display_name；撤销传 web_account_id。
    managers: list[SheetManagerEntry] = []


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


class BatchSubmitItem(BaseModel):
    """批量提交单条材料（``POST /sheets/{id}/submit-batch``）。

    ``registry_id`` 精确匹配行（``namespace:path``，如 ``minecraft:oak_log``）；
    ``qty`` 为本次申报数量（0 = 未携带/申报零；progress 视为「未提交此物」→ skip）。
    """

    registry_id: str = Field(min_length=1, max_length=128)
    qty: int = Field(ge=0)


class BatchSubmitRequest(BaseModel):
    """批量提交请求体：仅材料列表，actor 由鉴权决定（JWT 或 service-token+UUID）。

    ``to_map`` 聚合重复 registry_id（求和 qty），用于 batch_submit 内部按
    registry_id 单次匹配行。
    """

    items: list[BatchSubmitItem] = Field(min_length=1, max_length=2000)

    def to_map(self) -> dict[str, int]:
        """聚合重复 registry_id（求和 qty）。"""
        agg: dict[str, int] = {}
        for it in self.items:
            agg[it.registry_id] = agg.get(it.registry_id, 0) + it.qty
        return agg


class BatchRowOutcome(BaseModel):
    """批量提交单行结果。

    - ``action``：``delivered``（lock 完成）/ ``contributed``（progress 上交）/ ``skipped``。
    - ``qty``：实际交付/上交量；skipped=0。
    - ``reason``：skipped 时填（``BATCH_REASON_READY``/``BATCH_REASON_NO_ITEM``/
      ``需先认领``/``已被他人认领``/``无需求``/``数量不足（{have}/{need}）``/
      ``不满足上交条件``/``行状态变化``/``行已删除``）。
    - ``is_claimant``：lock 行 claimant ∈ actor account_uuids（P3 MCDR skip_is_noise
      折叠用——非本人认领的 lock skip 行折叠）。
    - ``delivered_qty``/``need_qty``：写后行快照（skipped 取当前值）。
    """

    row_id: int
    registry_id: str
    item_name: str
    mode: int  # 0=lock, 1=progress
    action: Literal["delivered", "contributed", "skipped"]
    qty: int = 0
    reason: str = ""
    is_claimant: bool = False
    delivered_qty: int
    need_qty: int


class BatchSubmitTotals(BaseModel):
    """批量提交汇总（按 action 分类计数）。"""

    delivered: int = 0
    contributed: int = 0
    skipped: int = 0


class BatchSubmitResult(BaseModel):
    """批量提交完整结果（HTTP 200 响应体）。

    ``actor_uuid`` 由鉴权决定：JWT 通道 = ``JWT.active_uuid``；service-token 通道
    = ``X-Player-UUID``。客户端据此渲染回执。
    """

    sheet_id: int
    actor_uuid: UUID
    outcomes: list[BatchRowOutcome]
    totals: BatchSubmitTotals
