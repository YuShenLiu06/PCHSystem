from mcdreforged.api.all import PluginServerInterface
from mcdreforged.api.command import Literal

from .config import HtcmcAuthConfig

CONFIG: HtcmcAuthConfig = HtcmcAuthConfig.get_default()


def on_load(server: PluginServerInterface, prev):
    global CONFIG
    CONFIG = server.load_config_simple("config.json", target_class=HtcmcAuthConfig)
    server.register_command(Literal("!!login").runs(lambda src, ctx: _login(src, ctx, server)))
    server.logger.info("HTCMC Auth loaded")


def _login(src, ctx, server):
    # Task M2 实现
    src.reply("!!login 尚未实现")
