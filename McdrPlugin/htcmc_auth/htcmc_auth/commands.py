"""!!PCH 命令回调集合。"""
import uuid_api_remake  # 红线 S-1 / RS-8：get_uuid(name)->str

from mcdreforged.api.decorator import new_thread
from mcdreforged.api.rtext import RText, RTextList, RColor, RStyle, RAction

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
    # 命令名列宽：ASCII 命令名按列宽补空格 → 描述起列对齐（MC 字体下 ASCII 等宽）。
    # 色板见 McdrPlugin/CLAUDE.md §6：标题 gold+bold、分组/命令名 aqua、描述 gray。
    name_w = len("!!PCH login")  # 11，!!PCH login / !!PCH sheet 等长

    def _line(name, desc, suggest, hover):
        return RTextList(
            RText("  " + name.ljust(name_w), color=RColor.aqua)
            .c(RAction.suggest_command, suggest)
            .h(RText(hover, color=RColor.yellow)),
            RText("- " + desc + "\n", color=RColor.gray),
        )

    src.reply(RTextList(
        RText("黄皮子积分系统（PCH）\n", color=RColor.gold).set_styles(RStyle.bold),
        RText("已上线：\n", color=RColor.aqua),
        _line(
            "!!PCH login", "申请 Web 登录链接", "!!PCH login ",
            "申请一次性 Web 登录链接（约 10 分钟内有效）",
        ),
        _line(
            "!!PCH sheet", "在线表格协作", "!!PCH sheet ",
            "查看 list / view / create / claim / deliver 等子命令",
        ),
        RText("开发中：bind / submit / project / score / rank / title / info\n", color=RColor.gray),
        RText("输入 !!help 查看所有命令；sheet 子命令详见 !!PCH sheet", color=RColor.gray),
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
