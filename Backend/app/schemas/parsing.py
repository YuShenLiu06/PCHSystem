"""投影解析预览的请求 / 响应模型。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class PreviewMeta(BaseModel):
    """投影元信息。"""

    filename: str
    schematic_name: str = ""
    author: str = ""
    region_count: int = Field(ge=0)
    total_blocks: int = Field(ge=0)
    total_volume: int = Field(ge=0)


class PreviewItem(BaseModel):
    """翻译后的单个材料条目。"""

    item_id: str  # registry id（namespace:path）
    item_name: str  # 中文显示名；未命中翻译时回退为 item_id
    count: int = Field(ge=0)


class ParsedMaterialPreview(BaseModel):
    """``POST /parsing/batch`` 响应中每文件的 ``preview`` 负载：分组材料预览（不落库）。"""

    meta: PreviewMeta
    blocks: list[PreviewItem]
    container_items: list[PreviewItem]
    untranslated: list[str]  # 未找到中文翻译的 registry id（item_name 回退为原 id）


class BatchFilePreview(BaseModel):
    """``POST /parsing/batch`` 中单个文件的结果（成功 / 失败隔离）。

    后端只负责解析、不收 multiplier（倍数是纯 UI 概念，前端聚合时应用，便于
    随时调倍数无需重新上传）。失败文件仍标 ``kind``（按扩展名判定的意图类型），
    ``status="error"`` 时 ``error`` 必填为玩家可读中文文案。
    """

    filename: str
    kind: str  # "litematic" | "nbt"（按扩展名判定，与 status 无关）
    status: str  # "ok" | "error"
    preview: ParsedMaterialPreview | None = None
    error: str | None = None


class BatchParsedPreview(BaseModel):
    """``POST /parsing/batch`` 响应：每文件独立预览（聚合在前端做，后端不收 multiplier）。"""

    files: list[BatchFilePreview]
