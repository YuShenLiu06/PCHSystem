"""``ItemTranslator`` 抽象基类。

实现类负责一种 registry id → 中文显示名 的数据源
（内置 lang JSON / 远端拉取 / 手维护映射 ...）。
未来更多解析/翻译需求通过新增子类扩展（如 ``CrowdinTranslator``、``CompositeTranslator``）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class ItemTranslator(ABC):
    """registry id（namespace:path）→ 中文显示名 翻译接口。"""

    @abstractmethod
    def translate(self, item_id: str) -> str:
        """返回中文名；未命中建议返回原 ``item_id``（调用方据此收集 ``untranslated``）。"""
        raise NotImplementedError
