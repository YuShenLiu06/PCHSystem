#!/usr/bin/env python3
"""从 CHANGELOG.md 抽取指定 tag 的 section，输出到 stdout。

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
"""
import re
import sys
from pathlib import Path

# 仓库根 = .github/scripts/ 的祖父目录
REPO_ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = REPO_ROOT / "CHANGELOG.md"


def extract_section(tag: str, text: str) -> str:
    """返回 ``## [<tag>]`` section 的正文（不含标题行、不含末尾分隔线）。"""
    # 匹配 `## [<tag>]`（tag 后紧跟 `]`，可能跟 ` - date`）
    header = re.compile(rf"^## \[{re.escape(tag)}\]\s*[^\n]*$", re.MULTILINE)
    m = header.search(text)
    if m is None:
        raise SystemExit(
            f"CHANGELOG.md 未找到 tag {tag!r} 的 section（期望标题行 `## [{tag}] ...`）。"
            f"请先在 CHANGELOG.md 固化该版本段，再发版。"
        )

    # section 正文：从标题行之后，到下一个二级标题 `## ` 之前
    start = m.end()
    next_h2 = re.search(r"^## ", text[start:], re.MULTILINE)
    end = start + next_h2.start() if next_h2 else len(text)
    body = text[start:end]

    # 去掉末尾的 `---` 分隔线与空行
    lines = body.rstrip().splitlines()
    while lines and lines[-1].strip() in ("", "---"):
        lines.pop()
    return "\n".join(lines).strip()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("用法: changelog_section.py <tag>")
    print(extract_section(sys.argv[1], CHANGELOG.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
