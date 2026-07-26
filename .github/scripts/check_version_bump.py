#!/usr/bin/env python3
"""PR 时版本元数据校验（Layer 1）。

两种模式：
  --detect-only : 判定 release 意图（五源 OR），写 ``is_release=true|false`` 到
                  ``$GITHUB_OUTPUT``（本地无此环境变量则打印到 stdout），始终 exit 0。
  --validate    : MCDR(plugin.json) 发版的严格校验——SemVer 合法 / 必须 bump / 严格前进 /
                  CHANGELOG 段存在。非 MCDR 发版（仅 backend/frontend version 改动）跳过
                  严格校验（手动发版），exit 0。

release 意图五源（任一命中）：
  1. PR head 分支 ``release/**`` 或 ``hotfix/**``
  2. PR 标题含 ``release``
  3. PR 带 ``release`` label
  4. PR diff 改了**任一**三端 version 字段（plugin.json / package.json / pyproject.toml）
  5. PR commit 被任一 release tag 指向（``pch_system-v*`` / ``backend-v*`` / ``frontend-v*``）

输入环境变量（workflow 从 github context 注入）::
  PR_BRANCH  PR head 分支名
  PR_TITLE   PR 标题
  PR_LABELS  PR 标签，逗号分隔
  PR_BASE    PR base sha
  PR_HEAD    PR head sha

严格校验对象 = ``McdrPlugin/mcdreforged.plugin.json`` 的 ``version``（对应 ``pch_system-v*`` tag，
MCDR 自动化发版）。backend / frontend 为手动发版，仅打 release 标签、不强制校验。

SemVer 支持 ``X.Y.Z`` 与预发布 ``X.Y.Z-rc.N``（release > 同 mmp 的 -rc.N）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN_JSON = REPO / "McdrPlugin" / "mcdreforged.plugin.json"
CHANGELOG = REPO / "CHANGELOG.md"
TAG_PREFIX = "pch_system-v"
RELEASE_TAG_PREFIXES = ("pch_system-v", "backend-v", "frontend-v")
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?$")

# 三端 version 文件 + 其 version 行的匹配模式（剥离 diff 的 +/- 前缀后）
VERSION_FILES = {
    "McdrPlugin/mcdreforged.plugin.json": re.compile(r'^\s*"version"\s*:'),
    "Frontend/package.json": re.compile(r'^\s*"version"\s*:'),
    "Backend/pyproject.toml": re.compile(r"^\s*version\s*="),
}


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
    )
    return proc.stdout


def fail(message: str) -> "NoReturn":  # noqa: F821
    print(f"::error::{message}", file=sys.stderr)
    sys.exit(1)


def plugin_version() -> str:
    try:
        return json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"]
    except (OSError, KeyError, ValueError) as e:
        fail(f"读取 plugin.json version 失败: {e}")


def parse_semver(v: str):
    """(major, minor, patch, is_release, rc)；非法返回 None。release>预发布。"""
    m = SEMVER_RE.match(v)
    if not m:
        return None
    major, minor, patch, rc = m.groups()
    return (int(major), int(minor), int(patch), 1 if rc is None else 0, 0 if rc is None else int(rc))


def latest_tag_version(exclude_pointing_at: str | None = None) -> str | None:
    pointed: set[str] = set()
    if exclude_pointing_at:
        pointed = {
            line.strip()
            for line in git("tag", "--points-at", exclude_pointing_at).splitlines()
            if line.strip()
        }
    tags = [
        line.strip()
        for line in git("tag", "--list", f"{TAG_PREFIX}*", "--sort=-v:refname").splitlines()
        if line.strip() and line.strip() not in pointed
    ]
    return tags[0][len(TAG_PREFIX):] if tags else None


def mcd_tag_at_head(head: str) -> str | None:
    for line in git("tag", "--points-at", head).splitlines():
        if line.strip().startswith(TAG_PREFIX):
            return line.strip()
    return None


def _diff_version_line_changed(base: str, head: str, file: str, pattern) -> bool:
    if not base:
        return False
    for line in git("diff", f"{base}..{head}", "--", file).splitlines():
        if line[:1] in "+-" and not line.startswith(("+++", "---")):
            if pattern.match(line[1:]):
                return True
    return False


def any_version_changed(base: str, head: str) -> bool:
    """三端任一 version 字段在 PR diff 中变化。"""
    return any(
        _diff_version_line_changed(base, head, f, p) for f, p in VERSION_FILES.items()
    )


def plugin_version_changed(base: str, head: str) -> bool:
    return _diff_version_line_changed(
        base, head, "McdrPlugin/mcdreforged.plugin.json", VERSION_FILES["McdrPlugin/mcdreforged.plugin.json"]
    )


def any_release_tag_at(head: str) -> bool:
    for line in git("tag", "--points-at", head).splitlines():
        if any(line.strip().startswith(p) for p in RELEASE_TAG_PREFIXES):
            return True
    return False


def changelog_has(version: str) -> bool:
    try:
        return f"## [{TAG_PREFIX}{version}]" in CHANGELOG.read_text(encoding="utf-8")
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# 意图判定（五源 OR）
# --------------------------------------------------------------------------- #
def detect_intent(env) -> tuple[bool, str]:
    branch = env.get("PR_BRANCH", "")
    title = env.get("PR_TITLE", "")
    labels = [x.strip() for x in env.get("PR_LABELS", "").split(",") if x.strip()]
    base = env.get("PR_BASE", "")
    head = env.get("PR_HEAD", "") or "HEAD"

    if re.match(r"^(release|hotfix)/", branch):
        return True, "branch"
    if "release" in title.lower():
        return True, "title"
    if "release" in labels:
        return True, "label"
    if any_version_changed(base, head):
        return True, "version-diff"
    if any_release_tag_at(head):
        return True, "tag-points-at"
    return False, ""


def write_output(key: str, value: str) -> None:
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")
    else:
        print(f"{key}={value}")


# --------------------------------------------------------------------------- #
# 模式入口
# --------------------------------------------------------------------------- #
def run_detect_only() -> "NoReturn":  # noqa: F821
    is_release, source = detect_intent(os.environ)
    write_output("is_release", "true" if is_release else "false")
    print(
        f"release 意图: {is_release}"
        + (f"（命中来源: {source}）" if is_release else "（非 release PR，跳过）")
    )
    sys.exit(0)


def run_validate() -> None:
    is_release, source = detect_intent(os.environ)
    if not is_release:
        print("非 release PR，跳过校验")
        sys.exit(0)

    head = os.environ.get("PR_HEAD", "") or "HEAD"
    base = os.environ.get("PR_BASE", "")

    # release 意图但未 bump 任何 version 字段 → 拒（捕获「忘 bump」，任一信号触发均适用）
    if not any_version_changed(base, head):
        fail(
            f"检测到 release 意图（来源: {source}）但 PR 未 bump 任何 version 字段"
            "（plugin.json / package.json / pyproject.toml）→ 必须 bump"
        )

    # 仅 MCDR(plugin.json) 发版跑严格校验；backend/frontend 手动发版只打标签、不强制校验
    if not plugin_version_changed(base, head):
        print(
            f"release 意图（来源: {source}）但未改 plugin.json——"
            "非 MCDR 自动化发版，跳过严格校验（backend/frontend 手动发版）"
        )
        sys.exit(0)

    new = plugin_version()
    new_parsed = parse_semver(new)
    if new_parsed is None:
        fail(f"plugin.json version '{new}' 不是合法 SemVer（期望 X.Y.Z 或 X.Y.Z-rc.N）")

    # tag 指向 HEAD：version 须 == 该 tag
    head_tag = mcd_tag_at_head(head)
    if head_tag:
        head_tag_ver = head_tag[len(TAG_PREFIX):]
        if new != head_tag_ver:
            fail(f"plugin.json version '{new}' != 指向 HEAD 的 tag '{head_tag}' 版本 '{head_tag_ver}'")

    # 严格前进（排除 HEAD 指向的 tag 后的最新 pch_system-v* tag）
    prev = latest_tag_version(exclude_pointing_at=head)
    if prev is not None:
        prev_parsed = parse_semver(prev)
        if prev_parsed is None:
            fail(f"最新 tag {TAG_PREFIX}{prev} 版本号非法，无法比较")
        if new_parsed <= prev_parsed:
            fail(f"plugin.json version '{new}' 未严格前进于最新 tag '{TAG_PREFIX}{prev}'（拒绝 同版本/倒退）")

    # CHANGELOG 段存在
    if not changelog_has(new):
        fail(f"CHANGELOG.md 缺少 '## [{TAG_PREFIX}{new}]' 段 → 发版前须补 CHANGELOG")

    print(f"✓ MCDR 版本元数据校验通过: {TAG_PREFIX}{new}（来源: {source}）")
    sys.exit(0)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--detect-only":
        run_detect_only()
    elif mode == "--validate":
        run_validate()
    else:
        print("用法: check_version_bump.py --detect-only | --validate", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
