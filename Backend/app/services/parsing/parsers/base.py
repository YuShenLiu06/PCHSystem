"""``MaterialParser`` 抽象基类。

实现类负责一种投影文件格式（``.litematic`` / 未来 ``.schem`` / ``.nbt`` ...）。
只产 registry id + 数量，不做翻译（翻译由上层 ``ItemTranslator`` 独立完成）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.services.parsing.models import ParsedMaterialList


class MaterialParser(ABC):
    """材料解析器接口。"""

    @abstractmethod
    def parse(self, data: bytes, filename: str) -> ParsedMaterialList:
        """解析文件字节流 → ``ParsedMaterialList``（blocks + container_items + meta）。

        实现应为纯函数（无副作用、不落库、不持久化文件）。
        """
        raise NotImplementedError
