"""SectionRenderer 的两种内置实现：TemplateSection 与 FunctionSection。

两者皆为 ``@dataclass(frozen=True)``（不可变，符合项目编码规范）。

- ``TemplateSection``：静态样板（header / status_line / meta / footer）。
  ``render`` 用 ``str.format_map`` 做占位符替换；缺 key → 空串 + ``logger.warning``，
  **不抛异常**（缺字段渲染空，比中断整篇归档更稳健）。值为 ``None`` 同样渲染空串。
- ``FunctionSection``：动态内容（material_table / contributor_stats / timeline）。
  ``render`` 直接 ``return self.func(context)``；循环/条件/空表处理由调用方在
  纯 Python 函数内写，不靠占位符引擎把逻辑推给模板（Route C 核心取舍）。

零依赖：纯 Python 标准库，不引入 Jinja2 或任何模板引擎。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from app.services.markdown_render.protocols import MarkdownContext

_logger = logging.getLogger(__name__)


class _SafeFormatDict(dict):
    """``str.format_map`` 的兜底 dict：缺 key 时返回占位符形态的空串来源。

    ``format_map`` 遇到缺 key 会抛 ``KeyError``；用一个会兜底的 Mapping 替代可让
    缺 key 的占位符渲染成空串，并由调用方记录 warning。``format_map`` 对每个出现的
    占位符名会查一次 ``__getitem__``，借此机会去重记录「该 key 缺失」。
    """

    __slots__ = ("_missing_seen",)

    def __init__(self, base: dict) -> None:
        super().__init__(base)
        self._missing_seen: set[str] = set()

    def __missing__(self, key: str) -> str:  # type: ignore[override]
        if key not in self._missing_seen:
            self._missing_seen.add(key)
            _logger.warning("markdown_render: 模板缺少上下文键 %r，已渲染为空串", key)
        return ""


def _safe_format(template: str, context: MarkdownContext) -> str:
    """安全 ``str.format_map``：None 值转空串，缺 key 渲染空串并记一次 warning。

    ``str.format_map`` 对 ``{name}`` 形式占位符做替换；遇到 ``{{`` / ``}}`` 视为字面量
    大括号（标准库行为，无需特殊处理）。占位符内含格式说明（``{n:>5}``）或属性访问
    （``{obj.x}``）时标准库仍走 ``__getitem__`` 取名，本兜底逻辑同样生效。
    """
    # None 值显式转空串，避免 ``{n}`` 渲染出字面 "None"。
    normalized = {k: ("" if v is None else v) for k, v in context.items()}
    safe_dict = _SafeFormatDict(normalized)
    return template.format_map(safe_dict)


@dataclass(frozen=True)
class TemplateSection:
    """静态样板分节。

    ``render`` 用 ``str.format_map`` 做占位符替换（``{name}`` 风格）；缺 key → 空串 +
    warning 不抛；``None`` 值 → 空串。

    注意：``template`` 内若含字面 ``{`` 或 ``}`` 需写成 ``{{`` / ``}}``（标准库约定）。
    """

    name: str
    order: int
    template: str

    def render(self, context: MarkdownContext) -> str:
        return _safe_format(self.template, context)


@dataclass(frozen=True)
class FunctionSection:
    """动态分节：把渲染委托给一个纯 Python 函数。

    ``func`` 签名 ``(MarkdownContext) -> str``；循环、条件、空表/None/mode 分支处理
    直接写在函数里（Route C：不靠占位符引擎把逻辑推给模板）。``render`` 只做转发。
    """

    name: str
    order: int
    func: Callable[[MarkdownContext], str]

    def render(self, context: MarkdownContext) -> str:
        return self.func(context)
