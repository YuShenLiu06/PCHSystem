"""!!PCH 命令回调集合。"""
import uuid_api_remake  # 红线 S-1 / RS-8：get_uuid(name)->str

from mcdreforged.api.decorator import new_thread
from mcdreforged.api.rtext import RText, RTextList, RColor, RStyle, RAction

from . import health
from .client import request_login_url, LoginResult
from .bind_client import request_bind_token, consume_bind_code, RATE_LIMITED, REMOVED, HttpError
from .config import PchSystemConfig
from .messages import (
    rtext_link,
    LOGIN_RATE_LIMITED,
    LOGIN_REMOVED,
    LOGIN_SERVICE_DOWN,
    LOGIN_UUID_FAIL,
    NOT_IMPL_TEMPLATE,
    PLAYER_ONLY,
    BIND_RATE_LIMITED,
    BIND_SERVICE_DOWN,
    BIND_REMOVED,
    BIND_UUID_FAIL,
    BIND_OK_TEMPLATE,
    BIND_CONSUME_OK,
    BIND_CONSUME_FAIL,
)

# 由 __init__.py 在 on_load 中注入
CONFIG: PchSystemConfig = PchSystemConfig()


def configure(cfg: PchSystemConfig) -> None:
    global CONFIG
    CONFIG = cfg


def _pch_root(src, ctx):
    if not src.is_player:
        src.reply(PLAYER_ONLY.format(cmd="!!PCH"))
        return
    # 命令名列宽：ASCII 命令名按列宽补空格 → 描述起列对齐（MC 字体下 ASCII 等宽）。
    # 色板见 McdrPlugin/CLAUDE.md §6：标题 gold+bold、分组/命令名 aqua、描述 gray。
    name_w = len("!!PCH status")  # 12，按最长命令名对齐（MC 字体下 ASCII 等宽）

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
            "!!PCH bind", "绑定 Web 账号", "!!PCH bind ",
            "申请绑定短码（游戏内 !!PCH bind，Web 输入短码完成绑定）",
        ),
        _line(
            "!!PCH sheet", "在线表格协作", "!!PCH sheet ",
            "查看 list / view / create / claim / deliver 等子命令",
        ),
        _line(
            "!!PCH status", "前后端连接自检", "!!PCH status",
            "嗅探后端 / 前端可达性，分档回显可点击文档与 release 链接",
        ),
        RText("开发中：submit / project / score / rank / title / info\n", color=RColor.gray),
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
    @new_thread('pch_system login')
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
        # 需求 4：!!PCH login 时后端探了前端；前端挂 → 明确提示（链接可能打不开）
        if result.frontend_online is False:
            parts.append(RText(
                "§c⚠ 前端服务未启用：登录链接可能无法打开。"
                "可游戏内 !!PCH status 排查前后端状态§r\n"
            ))
        parts.append(RText("§7收到登录请求，请："))
        parts.append(rtext_link(result.login_url))
        parts.append(RText(f"§7（有效期 {result.expires_in // 60} 分钟）"))
        server.tell(player_name, RTextList(*parts))

    _do()  # @new_thread 装饰后，调用即派生 daemon 线程、立即返回，不阻塞主循环


def _status(src, ctx):
    """``!!PCH status``：前后端连接自检（运维/玩家均可，控制台亦可执行）。

    HTTP 探针放 ``@new_thread``（RS-6，镜像 ``_login``）；``src.reply`` 线程安全
    （ConsoleSource / PlayerSource 通用，同 ``_login`` 的 ``server.tell`` 机制；
    S-1 MCDR CommandSource.reply）。复用 ``health.classify`` + ``format_game_report``
    （插件版本 + 后端/令牌/前端状态 + 可点击链接 + 作者页脚）。
    """
    @new_thread('pch_system status')
    def _do():
        try:
            server = src.get_server()
            meta = health.resolve_plugin_meta(server)
            findings = health.classify(CONFIG, meta)
            src.reply(health.format_game_report(findings, meta))
        except Exception as e:
            src.reply(RText(f"§c状态检查失败: {e}§r"))

    _do()


def _bind(src, ctx):
    """``!!PCH bind``：申请 Web 绑定短码（game_init，无参）。

    玩家游戏内执行，后端生成 6 位短码，玩家在 Web 端输入完成绑定。
    完全镜像 _login 的 @new_thread 模板（RS-6）。
    """
    if not src.is_player:
        src.reply(PLAYER_ONLY.format(cmd="!!PCH bind"))
        return
    player_name = src.player
    server = src.get_server()

    @new_thread('pch_system bind')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, BIND_UUID_FAIL.format(err=e))
            return
        result = request_bind_token(CONFIG, player_name, player_uuid)
        if result is None:
            server.tell(player_name, BIND_SERVICE_DOWN)
            return
        if result == RATE_LIMITED:
            server.tell(player_name, BIND_RATE_LIMITED)
            return
        if result == REMOVED:
            server.tell(player_name, BIND_REMOVED)
            return
        if isinstance(result, HttpError):
            server.tell(player_name, BIND_CONSUME_FAIL.format(reason=f"HTTP {result.status}: {result.detail}"))
            return
        # result: dict {"short_code": "ABC123", "expires_in": 600}
        # 整行 BIND_OK_TEMPLATE 已带 §7 灰色前缀（敏感信息规则，禁 § 高亮），
        # 短码作为字符串嵌入即可（MC 聊天纯文本不可点击，无复制风险）。
        short_code = str(result.get("short_code", ""))
        expires_in = int(result.get("expires_in", 600))
        server.tell(player_name, BIND_OK_TEMPLATE.format(
            code=short_code,
            minutes=expires_in // 60,
        ))

    _do()


def _bind_consume(src, ctx):
    """``!!PCH bind <code>``：消费绑定短码（web_init，带 code 参数）。

    Text 节点值通过 ctx['code'] 获取（S-1：Text 节点存值到 Context）。
    走双头通道（X-Service-Token + X-Player-UUID），代玩家完成绑定。
    """
    if not src.is_player:
        src.reply(PLAYER_ONLY.format(cmd="!!PCH bind"))
        return
    player_name = src.player
    server = src.get_server()
    # 从 ctx 获取 Text 节点解析的 code（S-1 已核实）
    code = ctx.get("code", "")
    if not code:
        server.tell(player_name, BIND_CONSUME_FAIL.format(reason="短码为空"))
        return

    @new_thread('pch_system bind')
    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, BIND_UUID_FAIL.format(err=e))
            return
        result = consume_bind_code(CONFIG, player_uuid, code)
        if result is None:
            server.tell(player_name, BIND_SERVICE_DOWN)
            return
        if result == RATE_LIMITED:
            server.tell(player_name, BIND_RATE_LIMITED)
            return
        if result == REMOVED:
            server.tell(player_name, BIND_REMOVED)
            return
        if isinstance(result, HttpError):
            # 404=短码无效/过期，409=已绑定其他账号
            reason = f"HTTP {result.status}"
            if result.detail:
                reason += f": {result.detail}"
            server.tell(player_name, BIND_CONSUME_FAIL.format(reason=reason))
            return
        # 契约（frozen 2026-07-19，方案 §一.10）：/bind/consume 成功返回
        # {"status": "ok", "account": AccountBrief, "player": PlayerBrief}，对齐 /bind/confirm。
        # AccountBrief 无 uuid 字段（账号不挂 UUID），UUID 从 PlayerBrief 取。
        account = result.get("account") or {}
        player = result.get("player") or {}
        username = str(account.get("username") or "未知")
        player_uuid = str(player.get("uuid") or "")
        server.tell(player_name, BIND_CONSUME_OK.format(username=username, uuid=player_uuid))

    _do()
