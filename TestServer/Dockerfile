# syntax=docker/dockerfile:1
# Debian 12 (bookworm) 仍有 openjdk-17-jre-headless；Trixie 已移除
FROM python:3.11-bookworm

# MC 1.20.1 要求 Java 17；MCDR 是 Python 包，需要 Python 3.8+
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-17-jre-headless \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# MCDR + 插件 Python 依赖：
#   - mcdreforged：MCDR 本体；-r McdrPlugin/requirements.txt：pch_system 自身 Python 依赖（requests）
#   - 其余插件 Python 依赖（hjson / parse 等）由下方 pim pipi 按各 .mcdr 内 requirements.txt 自动安装
#   单独成层，改 pip 依赖时 apt 层缓存仍命中。
COPY McdrPlugin/requirements.txt /tmp/pch-requirements.txt
RUN pip install --no-cache-dir 'mcdreforged>=2.14,<3' -r /tmp/pch-requirements.txt

WORKDIR /mcdr

# build context = 项目根（见 docker-compose.yml）
# MCDR 需要可写 config.yml（load 时会 atomic rename 补字段），不能用 :ro volume 挂载
COPY TestServer/config/mcdr_config.yml /mcdr/config.yml
# pch_system 依赖的 MCDR 插件（chest_scanner_lib / uuid_api_remake / minecraft_data_api）：
# 经 MCDR 原生 pim CLI 从官方插件目录下载 latest + 自动安装包内 Python 依赖
# （取代旧的「.mcdr 二进制提交进 git + Dockerfile COPY + 手写 pip」方案，版本随 latest）
# mkdir 必须显式做：WORKDIR 只建 /mcdr；且 pim 对无效输出目录静默 exit 0。
# 校验：pim download/pipi 对部分失败也 exit 0，靠退出码拦不住——
#   逐 id 校验产物存在（拦「3 个只下到 2 个」），sha256 清单随构建日志留档可审计。
RUN mkdir -p /mcdr/plugins \
 && mcdreforged pim download chest_scanner_lib uuid_api_remake minecraft_data_api \
        -o /mcdr/plugins/ \
 && mcdreforged pim pipi /mcdr/plugins/*.mcdr \
 && ls -la /mcdr/plugins/ \
 && sha256sum /mcdr/plugins/*.mcdr \
 && for id in chest_scanner_lib uuid_api_remake MinecraftDataAPI; do \
        ls /mcdr/plugins/ | grep -q "$id" || { echo "missing plugin asset: $id" >&2; exit 1; }; \
    done
COPY McdrPlugin /mcdr/plugins/pch_system
# pch_system 配置：load_config_simple("config.json") 默认从 <MCDR>/config/<plugin_id>/config.json 加载
COPY TestServer/config/pch_system_config.json /mcdr/config/pch_system/config.json

COPY TestServer/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 25565 25575

STOPSIGNAL SIGTERM

ENTRYPOINT ["/entrypoint.sh"]
