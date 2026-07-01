import uuid_api_remake  # 红线 S-1 已联网核实：get_uuid(name)->str

from mcdreforged.api.all import PluginServerInterface
from mcdreforged.api.command import Literal
from mcdreforged.api.rtext import RText, RAction, RColor

from .client import request_login_url
from .config import HtcmcAuthConfig

CONFIG: HtcmcAuthConfig = HtcmcAuthConfig.get_default()


def on_load(server: PluginServerInterface, prev):
    global CONFIG
    CONFIG = server.load_config_simple("config.json", target_class=HtcmcAuthConfig)
    server.register_command(Literal("!!login").runs(_login))
    server.logger.info("HTCMC Auth loaded")


def _login(src, ctx):
    from mcdreforged.api.command import PlayerCommandSource

    if not isinstance(src, PlayerCommandSource):
        src.reply("§c!!login 只能玩家在游戏内执行")
        return
    player_name = src.player   # 红线 S-1 已核实：PlayerCommandSource.player -> str
    server = src.get_server()

    def _do():
        try:
            player_uuid = uuid_api_remake.get_uuid(player_name)
        except Exception as e:
            server.tell(player_name, f"§c获取 UUID 失败: {e}")
            return
        result = request_login_url(CONFIG, player_name, player_uuid)
        if result is None:
            server.tell(player_name, "§c登录服务暂不可用，请稍后重试")
        elif result == "__RATE_LIMITED__":
            server.tell(player_name, "§e操作太频繁，请稍后再试")
        elif result == "__REMOVED__":
            server.tell(player_name, "§c你已被移出白名单")
        else:
            link = RText("§a§l[点击此处打开网页登录]").c(RAction.open_url, result)
            server.tell(player_name, RText("§7收到登录请求，请：").append(link))

    # R-12：HTTP 是耗时调用，放 task 线程，避免阻塞主线程
    server.schedule_task(_do)
