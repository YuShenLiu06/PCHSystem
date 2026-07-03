"""wiki git publisher —— 把已归档 sheet 的产物推送到 wiki 内容 git 仓。

设计原则（务必读完再改）：

1. **默认 off**：``cfg.wiki_git_remote_url`` 为空串时视为未配置，``publish`` 立即 return，
   不 ``git init``、不报错（R-8 重写后：归档 DB 成功即生效，wiki 同步为可选副产物）。
2. **best-effort**：推送是「尽力而为」——任何 git/subprocess/IO 失败都**不向上抛、
   不回滚 DB**（归档已经 commit，wiki 同步失败不能让已成功的归档倒退）。失败只记日志
   + 给 owner 发一条 ``wiki_publish_failed`` 通知，运维后续人工补救。
3. **token 内嵌 push URL，不写 remote 配置**：``git config remote.origin.url`` 只存
   **不含 token** 的 remote_url；推送时构造一次性 tokenized URL 直接传给 ``git push``，
   token **绝不落盘**到 ``.git/config``（R-11 密钥安全）。

不变量：
- ``archive_root`` 是 git 工作树根（``.git`` 在此）；归档产物
  ``projects/<id>/index.md`` + ``contributions.png`` 已由归档服务（``writer.write_atomic``
  / 贡献图）写好，publisher 只 add/commit/push，不改文件。
- publisher **不复用调用方 session**：失败通知自开独立 session（归档事务可能已结束）。
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

from app.core.config import Settings
from app.core.db import async_session_factory
from app.services import notification_service

logger = logging.getLogger(__name__)

# subprocess 超时（秒）：git 操作偶发慢（大仓 clone/push），但 publisher 在归档成功路径上，
# 不能无限挂住调用方。30s 是 push 单步的硬上限。
_GIT_TIMEOUT_SECONDS = 30

# commit message 模板：含 sheet_id 便于在 wiki 仓历史里检索归档流水。
_COMMIT_MESSAGE_TEMPLATE = "archive(project): #{sheet_id} {title}"

# 失败通知的固定字段。
_NOTIFY_CATEGORY = "wiki_publish_failed"
_NOTIFY_TITLE = "Wiki 同步失败"
_NOTIFY_BODY_TEMPLATE = "项目#{sheet_id} 推送到 wiki 仓失败"
# payload 里 error 字段截断上限（notification_service 对 payload 整体还有 8KB 截断）。
_PAYLOAD_ERROR_MAX = 400

# token 内嵌用户名：GitHub 用 x-access-token；Gitea/GitLab 用 oauth2。
_TOKEN_USER_GITHUB = "x-access-token"
_TOKEN_USER_OTHER = "oauth2"
_GITHUB_HOST_MARKER = "github.com"
# 脱敏：匹配 ``<user>:<token>@`` 形式的内嵌凭证（R-11：token 不进日志/通知）。
_CREDS_IN_URL_PATTERN = re.compile(r"(://[^/@]*?:)[^/@]+(@)")


@dataclass(frozen=True)
class _PublishContext:
    """单次 publish 的不可变输入聚合（避免在私有函数间传一长串位置参数）。"""

    sheet_id: int
    title: str
    owner_uuid: object  # uuid.UUID，避免循环 import 用 object 占位
    archive_root: str
    remote_url: str
    branch: str
    token: str
    author_name: str
    author_email: str


async def publish(
    sheet_id: int,
    title: str,
    owner_uuid: object,
    *,
    archive_root: str,
    cfg: Settings,
) -> None:
    """把已归档 sheet 的产物推送到 wiki git 仓（best-effort）。

    参数：
    - sheet_id / title：归档项目标识（commit message + 通知 payload 用）。
    - owner_uuid：项目拥有者 UUID；推送失败时给该玩家发通知（由 service 层从
      ``sheet.owner_uuid`` 传入）。
    - archive_root：git 工作树根（``.git`` 在此；产物已由归档服务写好）。
    - cfg：``Settings`` 实例，读 ``wiki_git_*`` 字段。

    行为：
    - ``cfg.wiki_git_remote_url`` 空 → 立即 return（默认 off，不 init、不报错）。
    - 任一 git/subprocess/IO 错误 → ``logger.exception`` + 通知 owner + return。
      **绝不向上抛、绝不回滚 DB**。
    """
    if not cfg.wiki_git_remote_url:
        # 未配置 wiki 仓 = 默认 off（R-8 重写后）
        return

    ctx = _PublishContext(
        sheet_id=sheet_id,
        title=title,
        owner_uuid=owner_uuid,
        archive_root=archive_root,
        remote_url=cfg.wiki_git_remote_url,
        branch=cfg.wiki_git_branch,
        token=cfg.wiki_git_token,
        author_name=cfg.wiki_git_author_name,
        author_email=cfg.wiki_git_author_email,
    )

    try:
        _git_init_if_needed(ctx)
        _git_commit(ctx)
        _git_push(ctx)
    except Exception as exc:  # noqa: BLE001 —— best-effort：吞所有异常，仅日志+通知
        # 归档已 commit 成功；wiki 推送失败绝不能让 DB 倒退（best-effort）。
        # exc 文本可能含 tokenized push URL（token 内嵌），日志/通知前必须脱敏（R-11）。
        safe_msg = _scrub_token(str(exc), ctx.token)
        logger.exception(
            "wiki git publish failed (best-effort): sheet_id=%s detail=%s",
            sheet_id,
            safe_msg,
        )
        await _notify_failure(ctx, safe_msg)


def _scrub_token(text: str, token: str) -> str:
    """从异常文本里抹掉 token：① 内嵌凭证 URL 段；② token 字面量本身（防御兜底）。

    ``subprocess.CalledProcessError`` 的 ``str(exc)`` 含完整命令行（含 tokenized URL），
    其 stderr 也可能含；通知 payload + 日志必须脱敏（R-11）。
    """
    cleaned = _CREDS_IN_URL_PATTERN.sub(r"\1***\2", text)
    if token:
        cleaned = cleaned.replace(token, "***")
    return cleaned


# ---------- 私有步骤 ----------


def _run_git(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    """跑一条 git 子进程；check=True + capture 全部输出 + 硬超时。

    失败抛 ``subprocess.CalledProcessError``（含 stderr）/ ``TimeoutExpired`` / ``OSError``，
    由 ``publish`` 的 except 统一兜住。
    """
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
        check=True,
    )


def _git_init_if_needed(ctx: _PublishContext) -> None:
    """.git 不在 archive_root → ``git init`` + 配 remote（**remote_url 不含 token**）。

    幂等：已 init 过则跳过（不覆盖已有 remote 配置，避免误改）。
    """
    from pathlib import Path  # 局部 import：仅此函数用

    if Path(ctx.archive_root, ".git").exists():
        return
    _run_git(["init"], cwd=ctx.archive_root)
    # remote_url 来自配置（不含 token）；token 内嵌在每次 push 的临时 URL 里（R-11）。
    _run_git(
        ["config", "remote.origin.url", ctx.remote_url], cwd=ctx.archive_root
    )


def _git_commit(ctx: _PublishContext) -> None:
    """add 仅该 sheet 子目录 + 用 ``-c user.name/email`` commit。

    commit 的「nothing to commit」（产物已提交过/无变化）视为正常跳过：stderr/stdout
    含 ``nothing to commit`` 时静默 return，不进 except。
    """
    target = f"projects/{ctx.sheet_id}"
    _run_git(["add", "--", target], cwd=ctx.archive_root)
    message = _COMMIT_MESSAGE_TEMPLATE.format(sheet_id=ctx.sheet_id, title=ctx.title)
    try:
        _run_git(
            [
                "-c",
                f"user.name={ctx.author_name}",
                "-c",
                f"user.email={ctx.author_email}",
                "commit",
                "-m",
                message,
                "--",
                target,
            ],
            cwd=ctx.archive_root,
        )
    except subprocess.CalledProcessError as exc:
        # nothing to commit = 产物无变化（幂等重推），不报错。
        combined = (exc.stdout or "") + (exc.stderr or "")
        if "nothing to commit" in combined:
            return
        raise


def _git_push(ctx: _PublishContext) -> None:
    """推送到 tokenized URL（token 不落盘 remote 配置）。

    branch 缺省 ``main``；tokenized URL 仅在本次 push 进程的命令行里出现，
    不写入 ``.git/config``（R-11）。
    """
    tokenized_url = _build_tokenized_url(ctx.remote_url, ctx.token)
    _run_git(
        ["push", tokenized_url, f"HEAD:{ctx.branch}"], cwd=ctx.archive_root
    )


def _build_tokenized_url(remote_url: str, token: str) -> str:
    """构造内嵌 token 的 push URL（不写 remote 配置，token 不落盘）。

    - GitHub（host 含 ``github.com``）→ ``https://x-access-token:{token}@{host}{path}``
    - Gitea / GitLab 等 → ``https://oauth2:{token}@{host}{path}``
    - remote_url 不是 https(s) scheme（如本地 ``file://`` 测试用）→ 原样返回（token 无意义）。
    - token 为空 → 原样返回（公开仓 / 本地 file:// 测试场景）。
    """
    if not token:
        return remote_url
    parsed = urlparse(remote_url)
    if parsed.scheme not in ("http", "https"):
        # 本地 file:// / ssh 等：token 内嵌无意义，原样返回。
        return remote_url
    user = _TOKEN_USER_GITHUB if _GITHUB_HOST_MARKER in (parsed.netloc or "") else _TOKEN_USER_OTHER
    userinfo = f"{user}:{token}"
    new_netloc = f"{userinfo}@{parsed.netloc}"
    return urlunparse(parsed._replace(netloc=new_netloc))


async def _notify_failure(ctx: _PublishContext, safe_error: str) -> None:
    """推送失败：自开独立 session 给 owner 发一条 ``wiki_publish_failed`` 通知。

    不复用调用方 session（归档事务可能已 commit 结束 / 已关闭）。通知自带 commit。
    ``safe_error`` 已经过 ``_scrub_token`` 脱敏（不含 token）。
    """
    body = _NOTIFY_BODY_TEMPLATE.format(sheet_id=ctx.sheet_id)
    async with async_session_factory() as session:
        await notification_service.notify(
            session,
            recipient_uuid=ctx.owner_uuid,  # type: ignore[arg-type]
            category=_NOTIFY_CATEGORY,
            title=_NOTIFY_TITLE,
            body=body,
            payload={
                "sheet_id": ctx.sheet_id,
                "sheet_title": ctx.title,
                "error": safe_error[:_PAYLOAD_ERROR_MAX],
            },
        )
        await session.commit()
