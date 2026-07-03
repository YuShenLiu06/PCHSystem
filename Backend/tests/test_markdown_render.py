"""markdown_render 通用模块测试（Route C：SectionRenderer Protocol）。

纯函数 / 文件 IO 测试，无 DB 依赖（TDD）。

覆盖：
- TemplateSection.render：占位符替换 / 缺 key 空串不抛 / None 值空串。
- FunctionSection.render：调用 func 传 context 返回结果。
- MarkdownDocument.register：同名 override（替换 + 用新 order）。
- MarkdownDocument.register_many：批量折叠。
- MarkdownDocument.render：按 order 升序拼接、\\n\\n 分隔、空 section 过滤、空 doc 返空串。
- MarkdownDocument.list_sections：去重排序。
- SectionRenderer runtime_checkable：TemplateSection / FunctionSection 满足 Protocol。
- loaders：单对象 JSON / 数组 JSON / 目录递归 / 非法 JSON 跳过 + warning / 字段缺失抛 ValueError。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.services.markdown_render import (
    FunctionSection,
    MarkdownDocument,
    SectionRenderer,
    TemplateSection,
    load_template_section,
    load_template_sections_from_dir,
)

# ---------------------------------------------------------------------------
# TemplateSection
# ---------------------------------------------------------------------------


class TestTemplateSectionRender:
    def test_replaces_placeholders_with_context_values(self) -> None:
        # Arrange
        section = TemplateSection(
            name="header", order=100, template="# {title}\n\n状态: {status}"
        )
        # Act
        out = section.render({"title": "我的项目", "status": "收集中"})
        # Assert
        assert out == "# 我的项目\n\n状态: 收集中"

    def test_missing_key_renders_empty_string_without_raising(self) -> None:
        # Arrange
        section = TemplateSection(name="meta", order=300, template="作者: {author}")
        # Act
        out = section.render({})  # 缺 author
        # Assert
        assert out == "作者: "

    def test_missing_key_emits_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        # Arrange
        section = TemplateSection(name="meta", order=300, template="{missing}")
        # Act
        with caplog.at_level(logging.WARNING, logger="app.services.markdown_render.sections"):
            out = section.render({})
        # Assert
        assert out == ""
        assert any("missing" in r.message for r in caplog.records)

    def test_none_value_renders_empty_string(self) -> None:
        # Arrange
        section = TemplateSection(name="meta", order=300, template="备注: {note}")
        # Act
        out = section.render({"note": None})
        # Assert
        assert out == "备注: "

    def test_template_without_placeholders_returns_unchanged(self) -> None:
        # Arrange
        section = TemplateSection(name="footer", order=900, template="---\n结束")
        # Act
        out = section.render({"anything": "ignored"})
        # Assert
        assert out == "---\n结束"

    def test_multiple_keys_share_same_missing_key_warned_once(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Arrange — 同一缺 key 出现两次，warning 去重只记一次
        section = TemplateSection(name="x", order=1, template="{a}-{a}")
        # Act
        with caplog.at_level(logging.WARNING, logger="app.services.markdown_render.sections"):
            section.render({})
        # Assert
        missing_warnings = [r for r in caplog.records if "a" in r.message]
        assert len(missing_warnings) == 1


# ---------------------------------------------------------------------------
# FunctionSection
# ---------------------------------------------------------------------------


class TestFunctionSectionRender:
    def test_calls_func_passing_context_and_returns_result(self) -> None:
        # Arrange
        def render_rows(ctx):
            rows = ctx.get("rows", [])
            if not rows:
                return ""
            lines = ["| 物品 | 数量 |", "|---|---|"]
            for r in rows:
                lines.append(f"| {r['name']} | {r['qty']} |")
            return "\n".join(lines)

        section = FunctionSection(name="material_table", order=400, func=render_rows)
        # Act
        out = section.render({"rows": [{"name": "铁锭", "qty": 64}]})
        # Assert
        assert "| 铁锭 | 64 |" in out
        assert "| 物品 | 数量 |" in out

    def test_func_returning_empty_string_is_preserved(self) -> None:
        # Arrange
        section = FunctionSection(name="empty", order=500, func=lambda ctx: "")
        # Act
        out = section.render({})
        # Assert
        assert out == ""


# ---------------------------------------------------------------------------
# SectionRenderer Protocol (runtime_checkable)
# ---------------------------------------------------------------------------


class TestSectionRendererProtocol:
    def test_template_section_satisfies_protocol(self) -> None:
        section = TemplateSection(name="a", order=1, template="x")
        assert isinstance(section, SectionRenderer)

    def test_function_section_satisfies_protocol(self) -> None:
        section = FunctionSection(name="a", order=1, func=lambda ctx: "x")
        assert isinstance(section, SectionRenderer)

    def test_protocol_requires_render_method(self) -> None:
        # Arrange — 没有 render 方法的对象不满足 Protocol
        class NotASection:
            name = "a"
            order = 1

        # Assert
        assert not isinstance(NotASection(), SectionRenderer)


# ---------------------------------------------------------------------------
# MarkdownDocument — register / register_many / immutability
# ---------------------------------------------------------------------------


class TestMarkdownDocumentRegister:
    def test_register_returns_new_instance(self) -> None:
        # Arrange
        doc = MarkdownDocument()
        section = TemplateSection(name="a", order=1, template="A")
        # Act
        new_doc = doc.register(section)
        # Assert — 不可变：原 doc 不变，新 doc 含 section
        assert list(doc.list_sections()) == []
        assert list(new_doc.list_sections()) == ["a"]

    def test_register_same_name_overrides_keeping_new_order(self) -> None:
        # Arrange
        doc = (
            MarkdownDocument()
            .register(TemplateSection(name="a", order=10, template="旧"))
            .register(TemplateSection(name="a", order=90, template="新"))
        )
        # Act
        out = doc.render({})
        # Assert — override 后只剩一个 "a"，order 用新的 90
        assert out == "新"
        assert list(doc.list_sections()) == ["a"]

    def test_register_preserves_other_sections_on_override(self) -> None:
        # Arrange
        doc = (
            MarkdownDocument()
            .register(TemplateSection(name="a", order=10, template="A"))
            .register(TemplateSection(name="b", order=20, template="B"))
            .register(TemplateSection(name="a", order=30, template="A2"))
        )
        # Act
        out = doc.render({})
        # Assert — b 仍在，a 被替换为新内容；按 order 升序：b(20) < a(30)
        assert out == "B\n\nA2"
        assert list(doc.list_sections()) == ["a", "b"]

    def test_register_many_adds_all(self) -> None:
        # Arrange
        sections = [
            TemplateSection(name="a", order=10, template="A"),
            FunctionSection(name="b", order=20, func=lambda ctx: "B"),
        ]
        # Act
        doc = MarkdownDocument().register_many(sections)
        # Assert
        assert list(doc.list_sections()) == ["a", "b"]
        assert doc.render({}) == "A\n\nB"

    def test_register_many_with_empty_iterable_returns_equivalent(self) -> None:
        # Act
        doc = MarkdownDocument().register_many([])
        # Assert
        assert list(doc.list_sections()) == []


# ---------------------------------------------------------------------------
# MarkdownDocument — render ordering / join / empty filtering
# ---------------------------------------------------------------------------


class TestMarkdownDocumentRender:
    def test_renders_in_ascending_order_joined_by_double_newline(self) -> None:
        # Arrange — 故意乱序注册，验证按 order 排
        doc = (
            MarkdownDocument()
            .register(TemplateSection(name="footer", order=900, template="F"))
            .register(TemplateSection(name="header", order=100, template="H"))
            .register(TemplateSection(name="body", order=500, template="B"))
        )
        # Act
        out = doc.render({})
        # Assert
        assert out == "H\n\nB\n\nF"

    def test_filters_out_empty_or_whitespace_only_sections(self) -> None:
        # Arrange — 中间一个空 section 不应产生多余空行
        doc = (
            MarkdownDocument()
            .register(TemplateSection(name="a", order=10, template="A"))
            .register(TemplateSection(name="empty", order=20, template=""))
            .register(FunctionSection(name="ws", order=30, func=lambda ctx: "   \n  "))
            .register(TemplateSection(name="b", order=40, template="B"))
        )
        # Act
        out = doc.render({})
        # Assert
        assert out == "A\n\nB"

    def test_empty_document_renders_empty_string(self) -> None:
        # Act
        out = MarkdownDocument().render({})
        # Assert
        assert out == ""

    def test_all_empty_sections_renders_empty_string(self) -> None:
        # Arrange
        doc = (
            MarkdownDocument()
            .register(TemplateSection(name="a", order=10, template=""))
            .register(FunctionSection(name="b", order=20, func=lambda ctx: ""))
        )
        # Act
        out = doc.render({})
        # Assert
        assert out == ""

    def test_render_passes_context_to_each_section(self) -> None:
        # Arrange
        captured = []

        def spy(ctx):
            captured.append(ctx)
            return "x"

        doc = MarkdownDocument().register(FunctionSection(name="s", order=1, func=spy))
        ctx = {"k": "v"}
        # Act
        doc.render(ctx)
        # Assert — 传入的 context 透传给 section
        assert captured == [ctx]

    def test_render_with_none_context_uses_empty_dict(self) -> None:
        # Arrange
        captured = []

        def spy(ctx):
            captured.append(ctx)
            return "x"

        doc = MarkdownDocument().register(FunctionSection(name="s", order=1, func=spy))
        # Act
        doc.render(None)
        # Assert
        assert captured == [{}]

    def test_sections_with_same_order_preserve_registration_order_stably(self) -> None:
        # Arrange — 相同 order 时不应抛错，输出稳定（注册顺序兜底）
        doc = (
            MarkdownDocument()
            .register(TemplateSection(name="a", order=10, template="A"))
            .register(TemplateSection(name="b", order=10, template="B"))
        )
        # Act
        out = doc.render({})
        # Assert — 两段都出现（顺序稳定即可，不强约束先后）
        assert "A" in out and "B" in out


# ---------------------------------------------------------------------------
# MarkdownDocument — list_sections
# ---------------------------------------------------------------------------


class TestMarkdownDocumentListSections:
    def test_returns_unique_sorted_names(self) -> None:
        # Arrange
        doc = (
            MarkdownDocument()
            .register(TemplateSection(name="c", order=3, template="x"))
            .register(TemplateSection(name="a", order=1, template="x"))
            .register(TemplateSection(name="b", order=2, template="x"))
        )
        # Act
        names = doc.list_sections()
        # Assert
        assert list(names) == ["a", "b", "c"]

    def test_returns_tuple(self) -> None:
        # Act
        names = MarkdownDocument().list_sections()
        # Assert
        assert isinstance(names, tuple)

    def test_override_does_not_list_duplicate_name(self) -> None:
        # Arrange
        doc = (
            MarkdownDocument()
            .register(TemplateSection(name="a", order=1, template="x"))
            .register(TemplateSection(name="a", order=2, template="y"))
        )
        # Act / Assert
        assert list(doc.list_sections()) == ["a"]


# ---------------------------------------------------------------------------
# loaders — load_template_section
# ---------------------------------------------------------------------------


class TestLoadTemplateSection:
    def test_loads_single_valid_object(self, tmp_path: Path) -> None:
        # Arrange
        p = tmp_path / "header.json"
        p.write_text(
            json.dumps({"name": "header", "order": 100, "template": "# {title}"}),
            encoding="utf-8",
        )
        # Act
        section = load_template_section(p)
        # Assert
        assert section.name == "header"
        assert section.order == 100
        assert section.template == "# {title}"
        assert section.render({"title": "T"}) == "# T"

    def test_missing_name_raises_value_error_with_path(self, tmp_path: Path) -> None:
        # Arrange
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"order": 1, "template": "x"}), encoding="utf-8")
        # Act / Assert
        with pytest.raises(ValueError) as exc:
            load_template_section(p)
        assert "name" in str(exc.value).lower()
        assert str(p) in str(exc.value)

    def test_missing_template_raises_value_error(self, tmp_path: Path) -> None:
        # Arrange
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"name": "a", "order": 1}), encoding="utf-8")
        # Act / Assert
        with pytest.raises(ValueError) as exc:
            load_template_section(p)
        assert "template" in str(exc.value).lower()

    def test_missing_order_raises_value_error(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"name": "a", "template": "x"}), encoding="utf-8")
        with pytest.raises(ValueError) as exc:
            load_template_section(p)
        assert "order" in str(exc.value).lower()

    def test_wrong_type_template_raises_value_error(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text(
            json.dumps({"name": "a", "order": 1, "template": 123}), encoding="utf-8"
        )
        with pytest.raises(ValueError):
            load_template_section(p)

    def test_wrong_type_order_raises_value_error(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text(
            json.dumps({"name": "a", "order": "x", "template": "t"}), encoding="utf-8"
        )
        with pytest.raises(ValueError):
            load_template_section(p)

    def test_invalid_json_raises_value_error(self, tmp_path: Path) -> None:
        # Arrange
        p = tmp_path / "broken.json"
        p.write_text("{not json", encoding="utf-8")
        # Act / Assert — 单文件 load 对 JSON 语法错抛错
        with pytest.raises(ValueError):
            load_template_section(p)

    def test_top_level_non_object_raises_value_error(self, tmp_path: Path) -> None:
        # Arrange — JSON 顶层是字符串而非对象
        p = tmp_path / "str.json"
        p.write_text(json.dumps("not an object"), encoding="utf-8")
        # Act / Assert
        with pytest.raises(ValueError):
            load_template_section(p)


# ---------------------------------------------------------------------------
# loaders — load_template_sections_from_dir
# ---------------------------------------------------------------------------


class TestLoadTemplateSectionsFromDir:
    def test_loads_array_json_multiple_sections(self, tmp_path: Path) -> None:
        # Arrange — 单文件含数组
        (tmp_path / "batch.json").write_text(
            json.dumps(
                [
                    {"name": "a", "order": 1, "template": "A"},
                    {"name": "b", "order": 2, "template": "B"},
                ]
            ),
            encoding="utf-8",
        )
        # Act
        sections = load_template_sections_from_dir(tmp_path)
        # Assert
        names = sorted(s.name for s in sections)
        assert names == ["a", "b"]

    def test_loads_multiple_files(self, tmp_path: Path) -> None:
        # Arrange
        (tmp_path / "a.json").write_text(
            json.dumps({"name": "a", "order": 1, "template": "A"}), encoding="utf-8"
        )
        (tmp_path / "b.json").write_text(
            json.dumps({"name": "b", "order": 2, "template": "B"}), encoding="utf-8"
        )
        # Act
        sections = load_template_sections_from_dir(tmp_path)
        # Assert
        assert sorted(s.name for s in sections) == ["a", "b"]

    def test_recursive_traverses_subdirectories(self, tmp_path: Path) -> None:
        # Arrange
        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "a.json").write_text(
            json.dumps({"name": "a", "order": 1, "template": "A"}), encoding="utf-8"
        )
        (sub / "b.json").write_text(
            json.dumps({"name": "b", "order": 2, "template": "B"}), encoding="utf-8"
        )
        # Act
        sections = load_template_sections_from_dir(tmp_path, recursive=True)
        # Assert
        assert sorted(s.name for s in sections) == ["a", "b"]

    def test_non_recursive_skips_subdirectories(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "a.json").write_text(
            json.dumps({"name": "a", "order": 1, "template": "A"}), encoding="utf-8"
        )
        (sub / "b.json").write_text(
            json.dumps({"name": "b", "order": 2, "template": "B"}), encoding="utf-8"
        )
        sections = load_template_sections_from_dir(tmp_path, recursive=False)
        assert sorted(s.name for s in sections) == ["a"]

    def test_invalid_json_skipped_with_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Arrange — 一个坏 JSON + 一个好 JSON；坏的不整体失败
        (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
        (tmp_path / "good.json").write_text(
            json.dumps({"name": "a", "order": 1, "template": "A"}), encoding="utf-8"
        )
        # Act
        with caplog.at_level(logging.WARNING, logger="app.services.markdown_render.loaders"):
            sections = load_template_sections_from_dir(tmp_path)
        # Assert
        assert sorted(s.name for s in sections) == ["a"]
        assert any("broken.json" in r.message for r in caplog.records)

    def test_invalid_object_in_array_skipped_individually(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Arrange — 数组里一个对象字段缺失；只跳过那一个，其余正常
        (tmp_path / "batch.json").write_text(
            json.dumps(
                [
                    {"name": "a", "order": 1, "template": "A"},
                    {"name": "b"},  # 缺 order/template
                    {"name": "c", "order": 3, "template": "C"},
                ]
            ),
            encoding="utf-8",
        )
        # Act
        with caplog.at_level(logging.WARNING, logger="app.services.markdown_render.loaders"):
            sections = load_template_sections_from_dir(tmp_path)
        # Assert
        assert sorted(s.name for s in sections) == ["a", "c"]

    def test_empty_dir_returns_empty_list(self, tmp_path: Path) -> None:
        assert load_template_sections_from_dir(tmp_path) == []

    def test_non_json_files_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "readme.txt").write_text("hi", encoding="utf-8")
        (tmp_path / "config.yaml").write_text("a: 1", encoding="utf-8")
        assert load_template_sections_from_dir(tmp_path) == []
