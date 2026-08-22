"""admin 托管账号环境同步（admin 面板登录）。

ADMIN_USERNAME / ADMIN_PASSWORD 配置时，后端启动经 lifespan 幂等同步一个
role=owner 的 WebAccount：admin 与所有 sheet owner 平级（RBAC 天然放行项目/
积分管理），并绑定一个同名管理玩家（UUID 按 MC 离线模式确定性推导）——
登录 JWT 带 active_uuid，可执行建项目等全部玩家级写操作。管理玩家是
不可登录锚点（whitelist_state=removed 阻断 !!PCH login 提权链），面板
密码登录与写操作不受影响。env 是该账号的密码权威源；未配置或不合规
→ 静默跳过（不 fail-fast，不影响未启用面板的部署与测试环境）。
"""
import hashlib
import logging
import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import hash_password, verify_password
from app.models.user import Player, WebAccount
from app.repositories import player_repo, web_account_repo

logger = logging.getLogger(__name__)

# 与 schemas/identity.py::_validate_username / 前端 Login.vue 预校验一致：
# env 用户名不合规时面板 UI 会被前端挡住，同步阶段即告警跳过
_ADMIN_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def offline_player_uuid(name: str) -> str:
    """MC 离线模式玩家 UUID 推导（Mojang ``nameUUIDFromBytes("OfflinePlayer:"+name)``）。

    与游戏服务端 / uuid_api_remake 插件同算法：MD5 摘要按 RFC 4122
    version-3 布局置版本与 variant 位。管理玩家用它锚定，同名账号进
    游戏即命中同一 Player 行（R-5：离线 UUID 由玩家名确定性推导）。
    """
    digest = hashlib.md5(f"OfflinePlayer:{name}".encode("utf-8")).digest()
    b = bytearray(digest)
    b[6] = (b[6] & 0x0F) | 0x30  # version 3
    b[8] = (b[8] & 0x3F) | 0x80  # variant RFC 4122
    return str(uuid.UUID(bytes=bytes(b)))


async def _ensure_admin_player(
    session: AsyncSession, account: WebAccount, username: str
) -> None:
    """确保 admin 账号绑定同名管理玩家；同名玩家已被他人绑定时不抢（只告警）。

    管理玩家一律 whitelist_state="removed"（不可登录锚点）：任何人以
    ADMIN_USERNAME 同名进离线服（离线 UUID 同值推导）也拿不到一次性
    token（/auth/token 的 check_whitelist 拒 403），无法经 /auth/exchange
    无密码换 owner JWT；已绑本账号但 state 非 removed（历史同步产物）
    幂等修正，仅该字段不符时写。
    """
    parsed = uuid.UUID(offline_player_uuid(username))
    player = await player_repo.get_by_uuid(session, parsed)
    if player is None:
        session.add(
            Player(
                uuid=parsed,
                current_name=username,
                role="owner",
                whitelist_state="removed",
                web_account_id=account.id,
            )
        )
        return
    if player.web_account_id == account.id:
        if player.whitelist_state != "removed":
            player.whitelist_state = "removed"  # 幂等修正历史产物
        return
    if player.web_account_id is None:
        logger.warning(
            "admin account sync: 同名未绑定玩家 %s 挂靠到 admin 账号 "
            "(account_id=%s)，whitelist_state 收回为 removed（阻断 !!PCH "
            "login 提权链）——若该玩家实为真人请更换 ADMIN_USERNAME",
            username,
            account.id,
        )
        player.web_account_id = account.id  # 未绑定的同名玩家（历史数据）挂靠
        player.whitelist_state = "removed"
        return
    logger.warning(
        "admin account sync: 管理玩家 %s 已绑定其他账号 (account_id=%s)，不抢绑"
        "——admin 账号保持无玩家（回退只读形态），如需写操作请在游戏内绑定",
        username,
        player.web_account_id,
    )


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

    - 不存在 → 新建 role=owner 永久账号 + 同名管理玩家（不可登录锚点，
      whitelist_state=removed）
    - 存在 → 幂等修正：role 非 admin/owner 时升为 owner（只升不降）；
      env 密码与库内哈希不符时重哈希（env 为该账号密码权威源）；
      缺同名管理玩家时补绑（已被他人绑定则不抢；绑定者一律
      whitelist_state=removed）
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
    created = False
    if account is None:
        account = WebAccount(
            username=username,
            password_hash=hash_password(settings.admin_password),
            role="owner",
        )
        session.add(account)
        await session.flush()
        created = True
    else:
        changed = False
        if account.role not in ("admin", "owner"):
            # 撞名接管信号：普通账号（大概率玩家注册名）被 ADMIN_USERNAME 命中
            # → 升 owner + env 密码覆盖（原密码失效），日志警示供运维复核
            logger.warning(
                "admin account sync: ADMIN_USERNAME=%r 与现有非特权账号 "
                "(account_id=%s role=%s) 撞名，将升为 owner 并以 env 密码为准"
                "（原密码失效）——若非有意接管请更换 ADMIN_USERNAME",
                username,
                account.id,
                account.role,
            )
            account.role = "owner"  # 只升不降：已是特权角色则不动
            changed = True
        if not account.password_hash or not verify_password(
            settings.admin_password, account.password_hash
        ):
            account.password_hash = hash_password(settings.admin_password)
            changed = True

    await _ensure_admin_player(session, account, username)
    await session.commit()
    if created:
        logger.info(
            "admin account created username=%s account_id=%s role=owner",
            username,
            account.id,
        )
    else:
        logger.info(
            "admin account synced username=%s account_id=%s role=%s",
            username,
            account.id,
            account.role,
        )
    return account
