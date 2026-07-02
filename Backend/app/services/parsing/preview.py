"""解析 + 翻译编排：把 ``ParsedMaterialList`` 翻译成预览条目，供 API 层组装响应。

保持 API 路由层 thin：路由只负责 IO（读上传字节、卸线程池、组装 Pydantic），
翻译/分组逻辑集中在此，便于复用与测试。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.parsing.models import ParsedMaterialList
from app.services.parsing.translators.base import ItemTranslator
from app.services.parsing.translators.lang_json import LangJsonTranslator


@dataclass(frozen=True)
class TranslatedEntry:
    """翻译后的预览条目（API 层据此构造 Pydantic ``PreviewItem``）。"""

    item_id: str
    item_name: str  # 中文；未命中翻译时 == item_id
    count: int


def get_default_translator() -> ItemTranslator:
    """v1 默认翻译器：包内内置 lang JSON。"""
    return LangJsonTranslator.default()


def _translate_entries(
    entries, translator: ItemTranslator
) -> tuple[list[TranslatedEntry], list[str]]:
    out: list[TranslatedEntry] = []
    untranslated: list[str] = []
    for entry in entries:
        name = translator.translate(entry.item_id)
        if name == entry.item_id:
            untranslated.append(entry.item_id)
        out.append(TranslatedEntry(item_id=entry.item_id, item_name=name, count=entry.count))
    return out, untranslated


def build_preview(
    parsed: ParsedMaterialList, translator: ItemTranslator
) -> tuple[list[TranslatedEntry], list[TranslatedEntry], list[str]]:
    """翻译 ``ParsedMaterialList`` → (方块预览, 容器预览, 去重未翻译 id 列表)。"""
    blocks, un_b = _translate_entries(parsed.blocks, translator)
    containers, un_c = _translate_entries(parsed.container_items, translator)
    untranslated = sorted(set(un_b) | set(un_c))
    return blocks, containers, untranslated
