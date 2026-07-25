#!/usr/bin/env python3
"""PR 时版本元数据校验（Layer 1）。

两种模式：
  --detect-only : 判定 release 意图（五源 OR），写 ``is_release=true|false`` 到
                  ``$GITHUB_OUTPUT``（本地无此环境变量则打印到 stdout），始终 exit 0。
  --validate    : 跑四项校验（SemVer 合法 / 必须 bump / 严格前进 / CHANGELOG 段存在），
                  任一失败 ``::error::`` + exit 1；非 release 意图 exit 0（不校验）。

输入环境变量（workflow 从 github context 注入）::
  PR_BRANCH  PR head 分支名（github.head_ref）
  PR_TITLE   PR 标题
  PR_LABELS  PR 标签，逗号分隔
  PR_BASE    PR base sha（github.event.pull_request.base.sha）
  PR_HEAD    PR head sha（github.event.pull_request.head.sha）

校验对象 = ``McdrPlugin/mcdreforged.plugin.json`` 的 ``version``（``pch_system-v*`` tag 只对应它）。
依赖 git（需 fetch-depth:0 以读 tag 历史）与 ``CHANGELOG.md``。

 SemVer 支持 ``X.Y.Z`` 与预发布 ``X.Y.Z-rc.N``（release > 同 mmp 的 -rc.N；rc.N 随 N 递增）。
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
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?$")


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
    """返回可比较元组 (major, minor, patch, is_release, rc)；非法返回 None。

    release 用 is_release=1 > 预发布 is_release=0，故同 mmp 下 release > 任何 -rc.N。
    """
    m = SEMVER_RE.match(v)
    if not m:
        return None
    major, minor, patch, rc = m.groups()
    return (
        int(major),
        int(minor),
        int(patch),
        1 if rc is None else 0,
        0 if rc is None else int(rc),
    )


def latest_tag_version(exclude_pointing_at: str | None = None) -> str | None:
    """最新 ``pch_system-v*`` tag 的版本号；可排除指向某 commit 的 tag。

    排除 HEAD 指向的 tag 是为"tag 已推到本 PR commit"时，前进比较应用前一个 tag。
    """
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


def tag_at_head(head: str) -> str | None:
    """若 HEAD 被 pch_system-v* tag 指向，返回该 tag 名；否则 None。"""
    for line in git("tag", "--points-at", head).splitlines():
        line = line.strip()
        if line.startswith(TAG_PREFIX):
            return line
    return None


def version_changed_in_diff(base: str, head: str) -> bool:
    """PR diff 是否改了 plugin.json 的 version 字段。"""
    if not base:
        return False
    diff = git("diff", f"{base}..{head}", "--", "McdrPlugin/mcdreforged.plugin.json")
    for line in diff.splitlines():
        if (line.startswith("+") or line.startswith("-")) and '"version"' in line:
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
    """返回 (是否 release 意图, 命中来源)。"""
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
    if version_changed_in_diff(base, head):
        return True, "version-diff"
    if tag_at_head(head):
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
        + (f"（命中来源: {source}）" if is_release else "（非 release PR，跳过校验）")
    )
    sys.exit(0)


def run_validate() -> None:
    is_release, source = detect_intent(os.environ)
    if not is_release:
        print("非 release PR，跳过校验")
        sys.exit(0)

    head = os.environ.get("PR_HEAD", "") or "HEAD"
    base = os.environ.get("PR_BASE", "")
    new = plugin_version()
    new_parsed = parse_semver(new)
    if new_parsed is None:
        fail(f"plugin.json version '{new}' 不是合法 SemVer（期望 X.Y.Z 或 X.Y.Z-rc.N）")

    # 1. 必须 bump（version 字段在 PR diff 中变化）
    if not version_changed_in_diff(base, head):
        fail(
            "检测到 release 意图但 plugin.json version 未在 PR 中更改"
            "（命中来源: " + source + "）→ 必须 bump version"
        )

    # 2. tag 指向 HEAD 的特殊情况：version 须 == 该 tag
    head_tag = tag_at_head(head)
    if head_tag:
        head_tag_ver = head_tag[len(TAG_PREFIX):]
        if new != head_tag_ver:
            fail(
                f"plugin.json version '{new}' != 指向 HEAD 的 tag '{head_tag}' 版本 '{head_tag_ver}'"
            )

    # 3. 严格前进（排除 HEAD 指向的 tag 后的最新 tag）
    prev = latest_tag_version(exclude_pointing_at=head)
    if prev is not None:
        prev_parsed = parse_semver(prev)
        if prev_parsed is None:
            fail(f"最新 tag {TAG_PREFIX}{prev} 版本号非法，无法比较")
        if new_parsed <= prev_parsed:
            fail(
                f"plugin.json version '{new}' 未严格前进于最新 tag '{TAG_PREFIX}{prev}'"
                f"（拒绝 同版本/倒退）"
            )

    # 4. CHANGELOG 段存在
    if not changelog_has(new):
        fail(
            f"CHANGELOG.md 缺少 '## [{TAG_PREFIX}{new}]' 段"
            f"→ 发版前须补 CHANGELOG"
        )

    print(f"✓ 版本元数据校验通过: {TAG_PREFIX}{new}（来源: {source}）")
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
