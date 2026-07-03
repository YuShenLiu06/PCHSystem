import threading

from mcdreforged.api.all import PluginServerInterface
from mcdreforged.api.command import Literal, Text, Integer, QuotableText

from . import notifier
from .commands import configure, _pch_root, _not_impl, _login
from .config import HtcmcAuthConfig
from .sheet_commands import (
    configure as sheet_configure,
    _sheet_root,
    _sheet_list,
    _sheet_list_mine,
    _sheet_view,
    _sheet_create,
    _sheet_rename,
    _sheet_delete,
    _sheet_upsert,
    _sheet_delrow,
    _sheet_claim,
    _sheet_deliver,
    _sheet_done,
    _sheet_progress,
    _sheet_release,
    _sheet_reject,
    _sheet_notify_list,
)

CONFIG: HtcmcAuthConfig = HtcmcAuthConfig()

# notifier 后台线程停止位：每次 on_load 新建一个，避免 reload 时老循环未退出
# 期间与新循环双循环重复投递（on_unload set 老实例，on_load 用全新实例启动）。
_notifier_stop: threading.Event = threading.Event()


def on_load(serv: PluginServerInterface, prev):
    global CONFIG, _notifier_stop
    CONFIG = serv.load_config_simple("config.json", target_class=HtcmcAuthConfig)
    configure(CONFIG)
    sheet_configure(CONFIG)
    notifier.configure(CONFIG)
    _register_commands(serv)

    # 事件监听（S-1：https://docs.mcdreforged.com/zh-cn/latest/plugin_dev/event.html）
    serv.register_event_listener("mcdr.player_joined", notifier.on_player_joined)
    serv.register_event_listener("mcdr.player_left", notifier.on_player_left)

    # 兜底：若服务端已启动，用 rcon 'list' 初始化当前在线玩家（重启插件时补齐集合）
    notifier.init_online_from_rcon(serv)

    # 启动通知轮询后台线程（RS-6：@new_thread 卸载阻塞，禁 schedule_task）。
    # 每次用全新 Event，避免 reload 时老循环未退出与新循环双循环重复投递。
    _notifier_stop = threading.Event()
    _start_notifier(serv)
    serv.logger.info("HTCMC Auth loaded (commands under !!PCH, sheets + notifier)")
    # 打印实际生效的轮询参数，便于部署后从日志确认（防 example/默认值漂移被静默吞掉）
    serv.logger.info(
        "notifier poll interval = %ss (max_per_poll = %s)",
        CONFIG.notify_poll_interval_seconds,
        CONFIG.notify_max_per_poll,
    )


def on_unload(serv: PluginServerInterface):
    # 停止 notifier 线程
    _notifier_stop.set()


def _start_notifier(serv: PluginServerInterface):
    from mcdreforged.api.decorator import new_thread

    @new_thread('htcmc_sheet_notifier')
    def _loop():
        notifier.run(serv, CONFIG, _notifier_stop)

    _loop()


def _register_commands(server: PluginServerInterface):
    # 命令树 API 已联网核实（MCDR 2.15.x）：
    # https://docs.mcdreforged.com/zh-cn/latest/plugin_dev/command.html
    # QuotableText 用于允许带空格的标题/物品名（玩家可 "我的 表格"）
    root = (
        Literal("!!PCH")
        .runs(_pch_root)
        .then(Literal("login").runs(_login))
        .then(Literal("bind").runs(_not_impl("bind")))
        .then(
            Literal("submit")
            .then(Literal("hand").then(Text("project").runs(_not_impl("submit hand"))))
            .then(
                Text("project")
                .then(Integer("x").then(Integer("y").then(Integer("z").runs(_not_impl("submit box")))))
            )
        )
        .then(
            Literal("project")
            .then(Literal("list").runs(_not_impl("project list")))
            .then(Text("project_id").then(Literal("info").runs(_not_impl("project info"))))
        )
        .then(Literal("score").runs(_not_impl("score")))
        .then(Literal("rank").runs(_not_impl("rank")))
        .then(
            Literal("title")
            .then(Literal("list").runs(_not_impl("title list")))
            .then(Text("title_id").then(Literal("set").runs(_not_impl("title set"))))
        )
        .then(Literal("info").runs(_not_impl("info")))
        # === sheets 子命令树（service-token + X-Player-UUID 代玩家写）===
        .then(
            Literal("sheet")
            .runs(_sheet_root)
            # 表级
            .then(
                Literal("list")
                .runs(_sheet_list)
                .then(Literal("--mine").runs(_sheet_list_mine))
            )
            .then(Literal("view").then(Integer("sheet_id").runs(_sheet_view)))
            .then(Literal("create").then(QuotableText("title").runs(_sheet_create)))
            .then(
                Literal("rename")
                .then(Integer("sheet_id").then(QuotableText("title").runs(_sheet_rename)))
            )
            .then(Literal("delete").then(Integer("sheet_id").runs(_sheet_delete)))
            # 行级 upsert：add / set 同端点，mode 可选（默认 lock；字面量 lock|progress），sort 可选
            .then(
                Literal("add")
                .then(
                    Integer("sheet_id")
                    .then(
                        QuotableText("item")
                        .then(
                            Integer("need").runs(_sheet_upsert)
                            .then(
                                Literal("lock").runs(_sheet_upsert)
                                .then(Integer("sort").runs(_sheet_upsert))
                            )
                            .then(
                                Literal("progress").runs(_sheet_upsert)
                                .then(Integer("sort").runs(_sheet_upsert))
                            )
                        )
                    )
                )
            )
            .then(
                Literal("set")
                .then(
                    Integer("sheet_id")
                    .then(
                        QuotableText("item")
                        .then(
                            Integer("need").runs(_sheet_upsert)
                            .then(
                                Literal("lock").runs(_sheet_upsert)
                                .then(Integer("sort").runs(_sheet_upsert))
                            )
                            .then(
                                Literal("progress").runs(_sheet_upsert)
                                .then(Integer("sort").runs(_sheet_upsert))
                            )
                        )
                    )
                )
            )
            .then(
                Literal("delrow")
                .then(Integer("sheet_id").then(Integer("row_id").runs(_sheet_delrow)))
            )
            # 协作
            .then(
                Literal("claim")
                .then(Integer("sheet_id").then(Integer("row_id").runs(_sheet_claim)))
            )
            .then(
                Literal("deliver")
                .then(
                    Integer("sheet_id")
                    .then(Integer("row_id").then(Integer("qty").runs(_sheet_deliver)))
                )
            )
            .then(
                Literal("done")
                .then(Integer("sheet_id").then(Integer("row_id").runs(_sheet_done)))
            )
            .then(
                Literal("progress")
                .then(
                    Integer("sheet_id")
                    .then(Integer("row_id").then(Integer("delivered_qty").runs(_sheet_progress)))
                )
            )
            .then(
                Literal("release")
                .then(Integer("sheet_id").then(Integer("row_id").runs(_sheet_release)))
            )
            .then(
                Literal("reject")
                .then(Integer("sheet_id").then(Integer("row_id").runs(_sheet_reject)))
            )
            # 通知
            .then(Literal("notify").then(Literal("list").runs(_sheet_notify_list)))
        )
    )
    server.register_command(root)
    # sheet 是 !!PCH 的子命令（命令树内 Literal("sheet")），不在 !!help 单列；
    # 其子命令清单由 `!!PCH sheet` 根回调 _sheet_root 展示。文案只留系统名（issue 1/2）。
    server.register_help_message("!!PCH", "黄皮子积分系统")
