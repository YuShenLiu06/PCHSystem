"""markdown 渲染抽象层（Route C：SectionRenderer Protocol）。

通用 markdown 文档渲染模块，首版供归档服务（sheet 三阶段生命周期的 archived 产物）消费，
未来榜单/报表等其他消费者可复用同一抽象——「支持多种内容新增」= 注册一个新的 SectionRenderer。

架构风格（保留自 PromptStore 的有价值部分，对齐项目 Notifier Protocol 范式 RS-9）：
- 不可变 frozen + ``register`` 返回新对象 + 有序聚合 + 同名 override。

抛弃的 PromptStore 机制（不适配结构化 markdown 渲染）：
- ``template`` 调度维度（prompt 模板 A/B 变体）
- ``dispatch`` 二级仲裁 / WILD_CARD 全局注入
- body fallback / ``{placeholder}`` 自研引擎

详见 ``Docs/architecture/services/markdown-service.md``（待补）与
``Backend/CLAUDE.md`` RS-9 风格说明。

公共导出（推荐使用）::

    from app.services.markdown_render import (
        FunctionSection,
        MarkdownDocument,
        SectionRenderer,
        TemplateSection,
        load_template_section,
        load_template_sections_from_dir,
    )
"""
from app.services.markdown_render.document import MarkdownDocument
from app.services.markdown_render.loaders import (
    load_template_section,
    load_template_sections_from_dir,
)
from app.services.markdown_render.protocols import MarkdownContext, SectionRenderer
from app.services.markdown_render.sections import FunctionSection, TemplateSection

__all__ = [
    "FunctionSection",
    "MarkdownContext",
    "MarkdownDocument",
    "SectionRenderer",
    "TemplateSection",
    "load_template_section",
    "load_template_sections_from_dir",
]
