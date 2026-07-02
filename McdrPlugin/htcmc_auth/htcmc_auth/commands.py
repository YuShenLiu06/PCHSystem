"""!!PCH 命令回调集合。"""
import uuid_api_remake  # 红线 S-1 / RS-8：get_uuid(name)->str

from mcdreforged.api.decorator import new_thread
from mcdreforged.api.rtext import RText, RTextList, RColor, RStyle

from .client import request_login_url, LoginResult
from .config import HtcmcAuthConfig
from .messages import (
    rtext_link,
    LOGIN_RATE_LIMITED,
    LOGIN_REMOVED,
    LOGIN_SERVICE_DOWN,
    LOGIN_UUID_FAIL,
    NOT_IMPL_TEMPLATE,
    PLAYER_ONLY,
)

# 由 __init__.py 在 on_load 中注入
CONFIG: HtcmcAuthConfig = HtcmcAuthConfig()


def configure(cfg: HtcmcAuthConfig) -> None:
    global CONFIG
    CONFIG = cfg


def _pch_root(src, ctx):
    if not src.is_player:
        src.reply(PLAYER_ONLY.format(cmd="!!PCH"))
        return
    src.reply(RTextList(
        RText("黄皮子积分系统（PCH）可用命令：\n", color=RColor.gold).set_styles(RStyle.bold),
        RText("  !!PCH login      - 申请 Web 登录链接\n", color=RColor.green),
        RText("  !!PCH sheet      - 在线表格协作（list/view/create/add/claim/deliver/done/release/reject ...）\n", color=RColor.green),
        RText("  !!PCH bind       - 申请 Web 绑定短码（开发中）\n", color=RColor.yellow),
        RText("  !!PCH submit      - 物品提交（开发中）\n", color=RColor.yellow),
        RText("  !!PCH project    - 项目查询（开发中）\n", color=RColor.yellow),
        RText("  !!PCH score      - 个人积分（开发中）\n", color=RColor.yellow),
        RText("  !!PCH rank       - 排行榜（开发中）\n", color=RColor.yellow),
        RText("  !!PCH title      - 称号管理（开发中）\n", color=RColor.yellow),
        RText("  !!PCH info       - 个人信息（开发中）\n", color=RColor.yellow),
        RText("\n输入 !!help 查看所有命令", color=RColor.gray),
    ))


def _not_impl(name: str):
    def _handler(src, ctx):
        src.reply(NOT_IMPL_TEMPLATE.format(name=name))

    return _handler


def _login(src, ctx):
    if not src.is_player:
        src.reply(PLAYER_ONLY.format(cmd="!!PCH login"))
        return
    player_name = src.player
    server = src.get_server()

    # R-12 / RS-6：阻塞式 HTTP 调用必须放后台线程，不能放 schedule_task
    # （schedule_task 的同步回调跑在 task executor = MCDR 主线程，会卡住主循环）。
    # server.tell() 线程安全，可在后台线程直接调用（S-1：MCDR 官方 PluginServerInterface 文档）。
    @new_thread('htcmc_auth login')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, LOGIN_UUID_FAIL.format(err=e))
            return
        result = request_login_url(CONFIG, player_name, player_uuid)
        if result is None:
            server.tell(player_name, LOGIN_SERVICE_DOWN)
            return
        if result == "__RATE_LIMITED__":
            server.tell(player_name, LOGIN_RATE_LIMITED)
            return
        if result == "__REMOVED__":
            server.tell(player_name, LOGIN_REMOVED)
            return
        # result: LoginResult
        parts = []
        if result.previous_tokens_revoked > 0:
            parts.append(RText("§c上一个登录链接已失效§r\n"))
        parts.append(RText("§7收到登录请求，请："))
        parts.append(rtext_link(result.login_url))
        parts.append(RText(f"§7（有效期 {result.expires_in // 60} 分钟）"))
        server.tell(player_name, RTextList(*parts))

    _do()  # @new_thread 装饰后，调用即派生 daemon 线程、立即返回，不阻塞主循环
