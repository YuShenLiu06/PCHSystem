"""基于内置 lang JSON 的翻译器（v1 默认实现）。

加载包内 ``lang/*.zh_cn.json``（vanilla ``minecraft`` + 模组如 ``create`` 的中文语言文件），
按 ``block.<namespace>.<path>`` → ``item.<namespace>.<path>`` 候选查表，未命中回退原 id。

数据文件由实现时从官方资产获取（见 ``Docs/architecture/api/parsing.md`` §数据来源），
随仓库打包，``importlib.resources`` 读取，进程级单例（``lru_cache``）。
"""
from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

from .base import ItemTranslator


def lang_key_candidates(item_id: str) -> list[str]:
    """registry id → 候选 lang key（先 block. 后 item.）。

    例：``minecraft:stone`` → ``["block.minecraft.stone", "item.minecraft.stone"]``；
    无命名空间的字符串返回 ``[]``。
    """
    if ":" not in item_id:
        return []
    namespace, path = item_id.split(":", 1)
    return [f"block.{namespace}.{path}", f"item.{namespace}.{path}"]


@lru_cache(maxsize=1)
def load_bundled_table() -> dict[str, str]:
    """加载并合并包内所有 ``*.zh_cn.json``，返回 ``{lang_key: 中文}`` 单例 dict。

    缺文件 / 解析失败 / 非对象均跳过（返回空 dict 时翻译回退原 id）。
    """
    merged: dict[str, str] = {}
    try:
        lang_dir = resources.files(__package__).joinpath("lang")
        for resource in lang_dir.iterdir():
            name = resource.name
            if not name.endswith(".zh_cn.json"):
                continue
            try:
                data = json.loads(resource.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                merged.update({str(k): str(v) for k, v in data.items()})
    except (FileNotFoundError, OSError):
        return merged
    return merged


class LangJsonTranslator(ItemTranslator):
    """按 ``block.`` / ``item.`` 候选查合并 lang 表的翻译器。"""

    def __init__(self, table: dict[str, str]):
        self._table = table

    def translate(self, item_id: str) -> str:
        for key in lang_key_candidates(item_id):
            val = self._table.get(key)
            if val:
                return val
        return item_id  # 未命中：回退原 id（调用方收集 untranslated）

    @classmethod
    def default(cls) -> "LangJsonTranslator":
        """使用包内内置 lang 文件的默认实例（合并表为进程级单例）。"""
        return cls(load_bundled_table())
