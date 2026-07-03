"""MarkdownDocument：不可变的有序分节注册表 + render 编排。

设计要点（Route C）：
- **不可变**：``@dataclass(frozen=True)``，``_sections`` 为 tuple；``register`` /
  ``register_many`` 返回**新**实例，绝不就地改。
- **同名 override**：``register`` 移除同 name 的旧分节，再加入新分节（保留新分节
  自带的 ``order``）。
- **编排层不区分静态/动态**：``render`` 只调 ``section.render(context)``，按 ``order``
  升序遍历；过滤空/纯空白结果避免多余 ``\\n\\n``；用 ``\\n\\n`` join。
- 同 ``order`` 时按注册顺序兜底（``sorted`` 稳定排序）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Tuple

from app.services.markdown_render.protocols import MarkdownContext, SectionRenderer

# _sections 用 tuple 而非 list：保证 frozen dataclass 可哈希且不可变。
# 注册顺序即注册顺序；render 时再按 order 稳定排序。
_Sections = Tuple[SectionRenderer, ...]


def _replace_or_append(
    sections: _Sections, new_section: SectionRenderer
) -> _Sections:
    """返回新 tuple：移除同名分节后追加 ``new_section``（不可变）。"""
    filtered = tuple(s for s in sections if s.name != new_section.name)
    return (*filtered, new_section)


@dataclass(frozen=True)
class MarkdownDocument:
    """不可变的有序分节注册表。

    使用方式::

        doc = (
            MarkdownDocument()
            .register(TemplateSection("header", 100, "# {title}"))
            .register(FunctionSection("material_table", 400, render_rows))
        )
        md = doc.render({"title": "...", "rows": [...]})
    """

    # frozen dataclass 用默认工厂避免共享可变默认值；这里用不可变空 tuple 即可。
    _sections: _Sections = field(default_factory=tuple)

    def register(self, section: SectionRenderer) -> MarkdownDocument:
        """返回新 MarkdownDocument：同名 override（移除旧同名，保留新 order）。"""
        return MarkdownDocument(_sections=_replace_or_append(self._sections, section))

    def register_many(
        self, sections: Iterable[SectionRenderer]
    ) -> MarkdownDocument:
        """折叠 ``register``：依次注册，返回新实例。"""
        doc = self
        for section in sections:
            doc = doc.register(section)
        return doc

    def render(self, context: MarkdownContext | None = None) -> str:
        """按 order 升序渲染各分节，过滤空/纯空白结果，``\\n\\n`` 连接。"""
        ctx: MarkdownContext = context if context is not None else {}
        # 稳定排序：order 相同时保留注册顺序（Python sorted 稳定）。
        ordered = sorted(self._sections, key=lambda s: s.order)
        rendered = (section.render(ctx) for section in ordered)
        # 过滤纯空白（含空串）避免多余空行。
        non_empty = [text for text in rendered if text.strip()]
        return "\n\n".join(non_empty)

    def list_sections(self) -> tuple[str, ...]:
        """返回已注册分节 name 的去重升序元组（调试 / 自省用）。"""
        return tuple(sorted({s.name for s in self._sections}))
