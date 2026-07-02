"""解析层数据模型（frozen，遵循不可变约束）。

解析器只产出 registry id + 数量，不含翻译结果；翻译由上层独立完成。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MaterialEntry:
    """单个材料条目：registry id（namespace:path）+ 数量。"""

    item_id: str
    count: int


@dataclass(frozen=True)
class ParseMeta:
    """投影元信息（来源文件 + 统计概要）。"""

    filename: str
    schematic_name: str
    author: str
    region_count: int
    total_blocks: int
    total_volume: int


@dataclass(frozen=True)
class ParsedMaterialList:
    """解析器输出：方块组 + 容器物品组（均按数量降序）。

    - ``blocks``：已放置方块计数（建筑本体材料）。
    - ``container_items``：容器内物品计数（箱子/木桶/漏斗等的 Items）。
    """

    blocks: tuple[MaterialEntry, ...]
    container_items: tuple[MaterialEntry, ...]
    meta: ParseMeta
