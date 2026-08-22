#!/usr/bin/env python3
"""从 CHANGELOG.md 抽取指定 tag 的 section（含同批配套组件段），输出到 stdout。

用法::

    python3 changelog_section.py <tag>
    python3 changelog_section.py pch_system-v0.7.0

CHANGELOG.md 采用 Keep a Changelog 格式，section 标题形如::

    ## [pch_system-v0.7.0] - 2026-07-12

    ### Added
    - ...

    ---

本脚本捕获 ``## [<tag>]`` 行到下一个 ``## `` 二级标题之间的内容，
去掉末尾的 ``---`` 分隔线与空行。找不到则非零退出（CI 失败，提示维护者补 CHANGELOG 段）。

对 ``pch_system-v*`` 总发版 tag 额外并入**同批配套组件段**：文件顺序上位于
当前段之后、上一个 ``pch_system-v*`` 段之前的 ``backend-v*`` / ``frontend-v*``
段（CHANGELOG 新段在前，同批发布的组件段紧随其后）。这些段以 ``###`` 级标题
列在「## 配套前后端变化」下，段内 ``### Added/Fixed`` 小标题拍平为条目前缀
（Added 不加前缀，Fixed/Security/… 加「修复：/安全：/…」），与既有发布正文
排版一致，草稿 Release 无需再手工补前后端变化。
"""
import re
import sys
from pathlib import Path

# 仓库根 = .github/scripts/ 的祖父目录
REPO_ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

# 总发版 tag 前缀（三端 umbrella，触发 release.yml 的 tag）
UMBRELLA_PREFIX = "pch_system-v"

# 配套组件段内拍平：非 Added 分类对条目加的前缀
CATEGORY_PREFIX = {
    "Changed": "变更",
    "Deprecated": "弃用",
    "Removed": "移除",
    "Fixed": "修复",
    "Security": "安全",
}
_CATEGORY_RE = re.compile(r"^### (Added|Changed|Deprecated|Removed|Fixed|Security)\s*$")


def _flatten_companion(body: str) -> str:
    """拍平组件段内的分类小标题：Added 条目原样，其余分类加「修复：/安全：」前缀。"""
    out: list[str] = []
    prefix: str | None = None
    for line in body.splitlines():
        m = _CATEGORY_RE.match(line)
        if m:
            prefix = CATEGORY_PREFIX.get(m.group(1))
            continue
        if line.strip() == "- _暂无_":
            continue
        if prefix and line.startswith("- "):
            out.append(f"- {prefix}：{line[2:]}")
        else:
            out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _trim_section(body: str) -> str:
    """去掉 section 正文末尾的 `---` 分隔线与空行。"""
    lines = body.rstrip().splitlines()
    while lines and lines[-1].strip() in ("", "---"):
        lines.pop()
    return "\n".join(lines).strip()


def _section_spans(text: str) -> list[tuple[str, str, str]]:
    """按文件顺序返回 [(tag, 标题余部, 正文)]。

    正文为标题行之后到下一个 ``## `` 之前的内容（已去尾部分隔线）。
    """
    headers = list(re.finditer(r"^## \[([^\]]+)\]([^\n]*)$", text, re.MULTILINE))
    spans = []
    for i, m in enumerate(headers):
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        spans.append((m.group(1), m.group(2), _trim_section(text[start:end])))
    return spans


def extract_section(tag: str, text: str) -> str:
    """返回 ``## [<tag>]`` section 的正文（不含标题行、不含末尾分隔线）。

    ``pch_system-v*`` tag 时额外并入同批 ``backend-v*`` / ``frontend-v*`` 段
    （位于当前段与上一个 umbrella 段之间），置于「## 配套前后端变化」标题下。
    """
    spans = _section_spans(text)
    idx = next((i for i, (t, _, _) in enumerate(spans) if t == tag), None)
    if idx is None:
        raise SystemExit(
            f"CHANGELOG.md 未找到 tag {tag!r} 的 section（期望标题行 `## [{tag}] ...`）。"
            f"请先在 CHANGELOG.md 固化该版本段，再发版。"
        )

    parts = [spans[idx][2]]
    if tag.startswith(UMBRELLA_PREFIX):
        companions: list[str] = []
        prev_umbrella: str | None = None
        for t, rest, body in spans[idx + 1 :]:
            if t.startswith(UMBRELLA_PREFIX):
                prev_umbrella = t
                break
            if t == "Unreleased":
                break
            companions.append(f"### {t}{_date_suffix(rest)}\n\n{_flatten_companion(body)}")
        if companions:
            range_ = f"（{prev_umbrella} → {tag} 期间）" if prev_umbrella else ""
            parts.append(f"---\n\n## 配套前后端变化{range_}\n\n" + "\n\n".join(companions))
    return "\n\n".join(parts)


def _date_suffix(rest: str) -> str:
    """标题余部（如 `` - 2026-08-22``）转为全角括号日期后缀。"""
    date = re.search(r"\d{4}-\d{2}-\d{2}", rest)
    return f"（{date.group()}）" if date else ""


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("用法: changelog_section.py <tag>")
    print(extract_section(sys.argv[1], CHANGELOG.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
