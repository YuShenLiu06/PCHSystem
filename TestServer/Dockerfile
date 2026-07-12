# syntax=docker/dockerfile:1
# Debian 12 (bookworm) 仍有 openjdk-17-jre-headless；Trixie 已移除
FROM python:3.11-bookworm

# MC 1.20.1 要求 Java 17；MCDR 是 Python 包，需要 Python 3.8+
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-17-jre-headless \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# MCDR + 插件 Python 依赖：
#   - mcdreforged / requests：MCDR 本体 + pch_system HTTP 客户端
#   - hjson / parse：MinecraftDataAPI 的 requirements（之前漏装 → minecraft_data_api 加载失败
#     → pch_system 因缺依赖被卸载）。单独成层，改 pip 依赖时 apt 层缓存仍命中。
RUN pip install --no-cache-dir 'mcdreforged>=2.14,<3' requests hjson parse

WORKDIR /mcdr

# build context = 项目根（见 docker-compose.yml）
# MCDR 需要可写 config.yml（load 时会 atomic rename 补字段），不能用 :ro volume 挂载
COPY TestServer/config/mcdr_config.yml /mcdr/config.yml
COPY TestServer/plugins/uuid_api_remake.mcdr /mcdr/plugins/uuid_api_remake.mcdr
# pch_system 运行时依赖的 MCDR 插件（之前漏 COPY，靠 docker cp 进可写层维持，recreate 即丢）
COPY TestServer/plugins/MinecraftDataAPI-v1.6.1.mcdr /mcdr/plugins/MinecraftDataAPI-v1.6.1.mcdr
COPY McdrPlugin /mcdr/plugins/pch_system
# pch_system 配置：load_config_simple("config.json") 默认从 <MCDR>/config/<plugin_id>/config.json 加载
COPY TestServer/config/pch_system_config.json /mcdr/config/pch_system/config.json

COPY TestServer/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 25565 25575

STOPSIGNAL SIGTERM

ENTRYPOINT ["/entrypoint.sh"]
