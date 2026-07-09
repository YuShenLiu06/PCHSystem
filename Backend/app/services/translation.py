"""
翻译服务：物品名称翻译（公共方法，不属于 parsing）

解决 sheets → parsing 的反向依赖问题。sheets、parsing 都依赖本模块。
"""

from app.services.parsing.translators.lang_json import LangJsonTranslator


def get_translator() -> LangJsonTranslator:
    """获取默认翻译器（单例）。"""
    return LangJsonTranslator.default()


def resolve_item_name(item_name: str | None, registry_id: str | None) -> str:
    """
    解析物品名称。

    优先使用 item_name；缺失则根据 registry_id 翻译。
    两者皆空抛 ValueError（调用方映射 HTTP 422）。

    Args:
        item_name: 中文名称（用户输入优先）
        registry_id: MC registry id（namespace:path）

    Returns:
        str: 最终中文名称

    Raises:
        ValueError: item_name 和 registry_id 均为空
    """
    if item_name:
        return item_name

    if registry_id:
        return get_translator().translate(registry_id)

    raise ValueError("item_name 与 registry_id 不能同时为空")
