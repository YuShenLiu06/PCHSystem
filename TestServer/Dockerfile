# syntax=docker/dockerfile:1
# Debian 12 (bookworm) 仍有 openjdk-17-jre-headless；Trixie 已移除
FROM python:3.11-bookworm

# MC 1.20.1 要求 Java 17；MCDR 是 Python 包，需要 Python 3.8+
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-17-jre-headless \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir 'mcdreforged>=2.14,<3' requests

WORKDIR /mcdr

# build context = 项目根（见 docker-compose.yml）
# MCDR 需要可写 config.yml（load 时会 atomic rename 补字段），不能用 :ro volume 挂载
COPY TestServer/config/mcdr_config.yml /mcdr/config.yml
COPY TestServer/plugins/uuid_api_remake.mcdr /mcdr/plugins/uuid_api_remake.mcdr
COPY McdrPlugin/htcmc_auth /mcdr/plugins/htcmc_auth
# htcmc_auth 配置：load_config_simple("config.json") 默认从 <MCDR>/config/<plugin_id>/config.json 加载
COPY TestServer/config/htcmc_auth_config.json /mcdr/config/htcmc_auth/config.json

COPY TestServer/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 25565 25575

STOPSIGNAL SIGTERM

ENTRYPOINT ["/entrypoint.sh"]
