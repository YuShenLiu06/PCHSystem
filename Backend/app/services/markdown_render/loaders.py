"""可选 trait：从 JSON 文件加载静态 ``TemplateSection``。

仅支持**静态** TemplateSection（产品/运营改 header / footer 文案不动代码）。
动态 FunctionSection 无法序列化为 JSON，需在代码里手工注册（见归档服务 renderer.py）。

文件格式（UTF-8 JSON）：
- 单对象：``{"name": str, "order": int, "template": str}``
- 数组：``[{...}, {...}]``（一个文件含多分节）

校验：必填字段缺失或类型错 → ``ValueError``（带文件路径）。目录扫描时**逐个 load**，
失败的分节 ``logger.warning`` 后跳过，不整体失败（容错优于中断）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Union

from app.services.markdown_render.sections import TemplateSection

_logger = logging.getLogger(__name__)

# 单个 JSON 文件可含一个分节对象或一个分节数组。
_SectionJson = dict
_SectionSpec = Union[_SectionJson, List[_SectionJson]]


def _validate_section_obj(obj: object, source: Path) -> TemplateSection:
    """从 dict 构造 TemplateSection，校验必填字段与类型。

    缺字段或类型错抛 ``ValueError``（消息含 ``source`` 路径），不吞错。
    """
    if not isinstance(obj, dict):
        raise ValueError(f"{source}: 分节定义必须是 JSON 对象，实际 {type(obj).__name__}")

    name = obj.get("name")
    order = obj.get("order")
    template = obj.get("template")

    if not isinstance(name, str) or not name:
        raise ValueError(f"{source}: 缺少必填字段 'name' 或类型非字符串")
    if not isinstance(order, int) or isinstance(order, bool):
        # 显式拒绝 bool：Python 中 bool 是 int 子类，需单独排除。
        raise ValueError(f"{source}: 字段 'order' 必须是整数，实际 {type(order).__name__}")
    if not isinstance(template, str):
        raise ValueError(f"{source}: 缺少必填字段 'template' 或类型非字符串")

    return TemplateSection(name=name, order=order, template=template)


def load_template_section(path: Path) -> TemplateSection:
    """从单个 JSON 文件加载一个 TemplateSection。

    - 文件必须是单对象 JSON（数组请用 :func:`load_template_sections_from_dir`）。
    - JSON 语法错、字段缺失/类型错 → ``ValueError``（消息含路径）。
    """
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source_desc(path)}: JSON 语法错误 - {exc.msg}") from exc
    except OSError as exc:
        raise ValueError(f"{source_desc(path)}: 文件读取失败 - {exc}") from exc

    if isinstance(raw, list):
        raise ValueError(
            f"{source_desc(path)}: load_template_section 仅接受单对象 JSON，"
            f"收到数组请改用 load_template_sections_from_dir"
        )
    return _validate_section_obj(raw, path)


def load_template_sections_from_dir(
    dir_path: Path, *, recursive: bool = True
) -> List[TemplateSection]:
    """递归扫描 ``*.json``，逐文件加载 TemplateSection 列表。

    每个文件可含单对象或数组；逐个分节构造，失败的 ``logger.warning`` 后跳过
    （不整体失败）。``recursive=False`` 时只扫顶层目录。

    返回顺序：``rglob`` / ``glob`` 的文件遍历顺序，每个文件内对象数组顺序——
    调用方不应依赖此顺序（最终渲染顺序由 ``order`` 决定，与加载顺序无关）。
    """
    dir_path = Path(dir_path)
    if not dir_path.is_dir():
        # 目录不存在时直接返空，与「无分节」语义一致；不抛错以容错。
        _logger.warning("markdown_render.loaders: 目录不存在，跳过: %s", dir_path)
        return []

    globber = dir_path.rglob("*.json") if recursive else dir_path.glob("*.json")

    results: List[TemplateSection] = []
    for json_file in globber:
        try:
            raw = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            _logger.warning(
                "markdown_render.loaders: 跳过 %s，JSON 解析失败: %s", json_file, exc
            )
            continue

        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            try:
                results.append(_validate_section_obj(item, json_file))
            except ValueError as exc:
                _logger.warning(
                    "markdown_render.loaders: 跳过 %s 中的一个分节定义: %s",
                    json_file,
                    exc,
                )
    return results


def source_desc(path: Path) -> str:
    """统一文件路径描述（错误消息用）。"""
    return str(path)
