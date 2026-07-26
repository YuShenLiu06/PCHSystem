#!/usr/bin/env python3
"""PR 时版本元数据校验（Layer 1）。

两种模式：
  --detect-only : 判定 release 意图（五源 OR），写 ``is_release=true|false`` 到
                  ``$GITHUB_OUTPUT``（本地无此环境变量则打印到 stdout），始终 exit 0。
  --validate    : 对 PR 内每个被 bump 的组件校验 CHANGELOG 段；MCDR(plugin.json) 额外
                  校验 SemVer 合法 / 严格前进。任一失败 ``::error::`` + exit 1。

release 意图五源（任一命中）：
  1. PR head 分支 ``release/**`` 或 ``hotfix/**``
  2. PR 标题含 ``release``
  3. PR 带 ``release`` label
  4. PR diff 改了**任一**三端 version 字段（plugin.json / package.json / pyproject.toml）
  5. PR commit 被任一 release tag 指向（``pch_system-v*`` / ``backend-v*`` / ``frontend-v*``）

组件 → tag 前缀 → CHANGELOG 段：
  plugin.json  → pch_system-v → ## [pch_system-vX.Y.Z]（+ SemVer/前进 严格校验）
  pyproject.toml → backend-v  → ## [backend-vX.Y.Z]
  package.json → frontend-v → ## [frontend-vX.Y.Z]

输入环境变量：PR_BRANCH / PR_TITLE / PR_LABELS / PR_BASE / PR_HEAD。
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
CHANGELOG = REPO / "CHANGELOG.md"
TAG_PREFIX = "pch_system-v"
RELEASE_TAG_PREFIXES = ("pch_system-v", "backend-v", "frontend-v")
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?$")

JSON_VERSION_RE = re.compile(r'^\s*"version"\s*:')
TOML_VERSION_RE = re.compile(r"^\s*version\s*=")
TOML_VERSION_VAL_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)

# 三端组件：version 文件 + diff 行模式 + tag 前缀 + 版本读取
COMPONENTS = [
    {
        "name": "pch_system",
        "file": "McdrPlugin/mcdreforged.plugin.json",
        "pattern": JSON_VERSION_RE,
        "prefix": "pch_system-v",
    },
    {
        "name": "backend",
        "file": "Backend/pyproject.toml",
        "pattern": TOML_VERSION_RE,
        "prefix": "backend-v",
    },
    {
        "name": "frontend",
        "file": "Frontend/package.json",
        "pattern": JSON_VERSION_RE,
        "prefix": "frontend-v",
    },
]


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def git(*args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=False)
    return proc.stdout


def fail(message: str) -> "NoReturn":  # noqa: F821
    print(f"::error::{message}", file=sys.stderr)
    sys.exit(1)


def parse_semver(v: str):
    """(major, minor, patch, is_release, rc)；非法返回 None。release>预发布。"""
    m = SEMVER_RE.match(v)
    if not m:
        return None
    major, minor, patch, rc = m.groups()
    return (int(major), int(minor), int(patch), 1 if rc is None else 0, 0 if rc is None else int(rc))


def read_version(comp) -> str:
    rel = comp["file"]
    path = REPO / rel
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        fail(f"读取 {rel} 失败: {e}")
    if rel.endswith(".toml"):
        m = TOML_VERSION_VAL_RE.search(text)
        if not m:
            fail(f"无法从 {rel} 解析 version")
        return m.group(1)
    try:
        return json.loads(text)["version"]
    except (KeyError, ValueError) as e:
        fail(f"无法从 {rel} 解析 version: {e}")


def latest_tag_version(prefix: str, exclude_pointing_at: str | None = None) -> str | None:
    pointed: set[str] = set()
    if exclude_pointing_at:
        pointed = {l.strip() for l in git("tag", "--points-at", exclude_pointing_at).splitlines() if l.strip()}
    tags = [
        l.strip()
        for l in git("tag", "--list", f"{prefix}*", "--sort=-v:refname").splitlines()
        if l.strip() and l.strip() not in pointed
    ]
    return tags[0][len(prefix):] if tags else None


def mcd_tag_at_head(head: str) -> str | None:
    for line in git("tag", "--points-at", head).splitlines():
        if line.strip().startswith(TAG_PREFIX):
            return line.strip()
    return None


def diff_version_changed(base: str, head: str, file: str, pattern) -> bool:
    if not base:
        return False
    for line in git("diff", f"{base}..{head}", "--", file).splitlines():
        if line[:1] in "+-" and not line.startswith(("+++", "---")) and pattern.match(line[1:]):
            return True
    return False


def any_version_changed(base: str, head: str) -> bool:
    return any(diff_version_changed(base, head, c["file"], c["pattern"]) for c in COMPONENTS)


def any_release_tag_at(head: str) -> bool:
    for line in git("tag", "--points-at", head).splitlines():
        if any(line.strip().startswith(p) for p in RELEASE_TAG_PREFIXES):
            return True
    return False


def changelog_has(prefix: str, version: str) -> bool:
    try:
        return f"## [{prefix}{version}]" in CHANGELOG.read_text(encoding="utf-8")
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
    print(f"release 意图: {is_release}" + (f"（命中来源: {source}）" if is_release else "（非 release PR，跳过）"))
    sys.exit(0)


def validate_mcd_extras(new: str, head: str) -> None:
    """MCDR 专属：SemVer 合法 + tag 指向 HEAD 时 == tag + 严格前进。"""
    new_parsed = parse_semver(new)
    if new_parsed is None:
        fail(f"plugin.json version '{new}' 不是合法 SemVer（期望 X.Y.Z 或 X.Y.Z-rc.N）")
    head_tag = mcd_tag_at_head(head)
    if head_tag:
        head_tag_ver = head_tag[len(TAG_PREFIX):]
        if new != head_tag_ver:
            fail(f"plugin.json version '{new}' != 指向 HEAD 的 tag '{head_tag}' 版本 '{head_tag_ver}'")
    prev = latest_tag_version(TAG_PREFIX, exclude_pointing_at=head)
    if prev is not None:
        prev_parsed = parse_semver(prev)
        if prev_parsed is None:
            fail(f"最新 tag {TAG_PREFIX}{prev} 版本号非法，无法比较")
        if new_parsed <= prev_parsed:
            fail(f"plugin.json version '{new}' 未严格前进于最新 tag '{TAG_PREFIX}{prev}'（拒绝 同版本/倒退）")


def run_validate() -> None:
    is_release, source = detect_intent(os.environ)
    if not is_release:
        print("非 release PR，跳过校验")
        sys.exit(0)

    head = os.environ.get("PR_HEAD", "") or "HEAD"
    base = os.environ.get("PR_BASE", "")

    # release 意图但未 bump 任何 version 字段 → 拒（捕获「忘 bump」）
    if not any_version_changed(base, head):
        fail(
            f"检测到 release 意图（来源: {source}）但 PR 未 bump 任何 version 字段"
            "（plugin.json / package.json / pyproject.toml）→ 必须 bump"
        )

    # 对每个被 bump 的组件：校验 CHANGELOG 段；MCDR 额外 SemVer + 前进
    checked = []
    for comp in COMPONENTS:
        if not diff_version_changed(base, head, comp["file"], comp["pattern"]):
            continue
        ver = read_version(comp)
        if not changelog_has(comp["prefix"], ver):
            fail(
                f"CHANGELOG.md 缺少 '## [{comp['prefix']}{ver}]' 段"
                f"→ {comp['name']} 发版前须补 CHANGELOG"
            )
        if comp["name"] == "pch_system":
            validate_mcd_extras(ver, head)
        checked.append(f"{comp['name']}@{ver}")

    if not checked:
        fail("未检测到任何组件 version 变化（不应到达，请反馈）")

    print(f"✓ 版本元数据校验通过: {', '.join(checked)}（来源: {source}）")
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
