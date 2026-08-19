"""admin 托管账号环境同步（admin 面板登录）。

ADMIN_USERNAME / ADMIN_PASSWORD 配置时，后端启动经 lifespan 幂等同步一个
role=owner 的 WebAccount：admin 与所有 sheet owner 平级（RBAC 天然放行项目/
积分管理），且不绑定任何游戏玩家（登录响应 player=None，JWT 无 active_uuid）。
env 是该托管账号的密码权威源；未配置或不合规 → 静默跳过（不 fail-fast，
不影响未启用面板的部署与测试环境）。
"""
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import hash_password, verify_password
from app.models.user import WebAccount
from app.repositories import web_account_repo

logger = logging.getLogger(__name__)

# 与 schemas/identity.py::_validate_username / 前端 Login.vue 预校验一致：
# env 用户名不合规时面板 UI 会被前端挡住，同步阶段即告警跳过
_ADMIN_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _is_valid_config(settings: Settings) -> bool:
    """校验 ADMIN_* 配置合规（长度界 + 字符集，对齐 register 用户名策略）。"""
    username = settings.admin_username.strip()
    if not (
        settings.username_min_length
        <= len(username)
        <= settings.username_max_length
        and _ADMIN_USERNAME_RE.match(username)
    ):
        return False
    return settings.password_min_length <= len(settings.admin_password) <= (
        settings.password_max_length
    )


async def sync_admin_account(
    session: AsyncSession, settings: Settings
) -> WebAccount | None:
    """幂等同步 admin 托管账号；未配置/不合规返回 None（记日志不抛错）。

    - 不存在 → 新建 role=owner 永久账号（无绑定玩家）
    - 存在 → 幂等修正：role 非 admin/owner 时升为 owner（只升不降）；
      env 密码与库内哈希不符时重哈希（env 为该账号密码权威源）
    """
    if not settings.admin_account_configured:
        return None
    if not _is_valid_config(settings):
        logger.warning(
            "admin account sync skipped: ADMIN_USERNAME/ADMIN_PASSWORD 不合规"
            "（用户名 %d-%d 位且仅字母/数字/_/-，密码 %d-%d 位）",
            settings.username_min_length,
            settings.username_max_length,
            settings.password_min_length,
            settings.password_max_length,
        )
        return None

    username = settings.admin_username.strip()
    account = await web_account_repo.get_by_username(session, username)
    if account is None:
        account = WebAccount(
            username=username,
            password_hash=hash_password(settings.admin_password),
            role="owner",
        )
        session.add(account)
        await session.commit()
        logger.info(
            "admin account created username=%s account_id=%s role=owner",
            username,
            account.id,
        )
        return account

    changed = False
    if account.role not in ("admin", "owner"):
        account.role = "owner"  # 只升不降：已是特权角色则不动
        changed = True
    if not account.password_hash or not verify_password(
        settings.admin_password, account.password_hash
    ):
        account.password_hash = hash_password(settings.admin_password)
        changed = True
    if changed:
        await session.commit()
    logger.info(
        "admin account synced username=%s account_id=%s role=%s",
        username,
        account.id,
        account.role,
    )
    return account
