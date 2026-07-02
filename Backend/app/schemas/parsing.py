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
    """``POST /parsing/litematic`` 响应：分组材料预览（不落库）。"""

    meta: PreviewMeta
    blocks: list[PreviewItem]
    container_items: list[PreviewItem]
    untranslated: list[str]  # 未找到中文翻译的 registry id（item_name 回退为原 id）
