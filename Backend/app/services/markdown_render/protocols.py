"""markdown 渲染抽象层协议定义（Route C：SectionRenderer Protocol）。

定义文档分节渲染的扩展点契约。本模块只声明协议，不提供实现——
具体实现见 :mod:`app.services.markdown_render.sections`。

设计要点：
- ``MarkdownContext`` 是扁平 dict（``Mapping[str, Any]``），由调用方（如归档服务）
  预算好后注入；渲染层不做数据获取。
- ``SectionRenderer`` 是结构化分节渲染扩展点：任意能按 ``name`` 标识、按 ``order``
  排序、产出 ``str`` 的对象都满足。与 ``Notifier`` Protocol 同范式（RS-9）。
- ``@runtime_checkable`` 允许 ``isinstance`` 判定，便于校验注册对象。
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

# 扁平上下文：调用方预算后注入（如 sheet/rows/owner_name/时间戳）。
MarkdownContext = Mapping[str, Any]


@runtime_checkable
class SectionRenderer(Protocol):
    """文档分节渲染器扩展点。

    实现者约定：
    - ``name``：分节唯一标识，作为同名 override 的键（不应在文档内重复）。
    - ``order``：文档内位置，按升序渲染（同 order 时注册顺序兜底，见 MarkdownDocument）。
    - ``render``：返回该分节的 markdown 文本；返回空串/纯空白会被文档层过滤。
    """

    name: str
    order: int

    def render(self, context: MarkdownContext) -> str:  # pragma: no cover - 协议
        ...
