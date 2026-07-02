from mcdreforged.api.all import PluginServerInterface
from mcdreforged.api.command import Literal, Text, Integer

from .commands import configure, _pch_root, _not_impl, _login
from .config import HtcmcAuthConfig

CONFIG: HtcmcAuthConfig = HtcmcAuthConfig()


def on_load(server: PluginServerInterface, prev):
    global CONFIG
    CONFIG = server.load_config_simple("config.json", target_class=HtcmcAuthConfig)
    configure(CONFIG)
    _register_commands(server)
    server.logger.info("HTCMC Auth loaded (commands under !!PCH)")


def _register_commands(server: PluginServerInterface):
    # 命令树 API 已联网核实（MCDR 2.15.x）：
    # https://docs.mcdreforged.com/zh-cn/latest/plugin_dev/command.html
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
    )
    server.register_command(root)
    server.register_help_message(
        "!!PCH",
        "黄皮子积分系统（login/bind/submit/project/score/rank/title/info）",
    )
