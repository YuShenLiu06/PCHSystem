#!/usr/bin/env bash
# MCDR + Fabric 1.20.1 测试服务器入口
# 流程：下载 fabric launcher → 同意 eula → 准备配置 → 启动 MCDR（前台）
set -euo pipefail

cd /mcdr

MC_VERSION="${MC_VERSION:-1.20.1}"
FABRIC_LOADER_VERSION="${FABRIC_LOADER_VERSION:-0.19.3}"
FABRIC_INSTALLER_VERSION="${FABRIC_INSTALLER_VERSION:-1.0.1}"

SERVER_DIR="/mcdr/server"
FABRIC_JAR="${SERVER_DIR}/fabric-server.jar"

# ---------- Step 1: 下载 Fabric server launcher（仅首次） ----------
if [ ! -f "${FABRIC_JAR}" ]; then
    URL="https://meta.fabricmc.net/v2/versions/loader/${MC_VERSION}/${FABRIC_LOADER_VERSION}/${FABRIC_INSTALLER_VERSION}/server/jar"
    echo "[entrypoint] 下载 Fabric server launcher: ${URL}"
    curl -fL -o "${FABRIC_JAR}" "${URL}"
    echo "[entrypoint] 完成: $(ls -la "${FABRIC_JAR}")"
fi

# ---------- Step 2: 同意 EULA ----------
if [ ! -f "${SERVER_DIR}/eula.txt" ]; then
    echo "[entrypoint] 写入 eula.txt"
    printf 'eula=true\n' > "${SERVER_DIR}/eula.txt"
fi

# ---------- Step 3: 预置 server.properties（离线模式 + rcon） ----------
if [ ! -f "${SERVER_DIR}/server.properties" ]; then
    echo "[entrypoint] 写入 server.properties"
    cat > "${SERVER_DIR}/server.properties" <<'PROPS'
# Minecraft server properties
# HTCMC PCHSystem 验收用：离线模式 + rcon + 平坦世界 + 保护关
server-port=25565
online-mode=false
enable-rcon=true
rcon.port=25575
rcon.password=pch_test_rcon
motd=HTCMC PCHSystem Test Server
gamemode=survival
difficulty=peaceful
level-type=minecraft\:flat
generate-structures=false
spawn-protection=0
op-permission-level=4
allow-flight=true
max-players=20
view-distance=8
simulation-distance=8
white-list=false
enforce-whitelist=false
PROPS
fi

# ---------- Step 4: 启动 MCDR（前台 daemon 模式） ----------
# config.yml 已通过 Dockerfile COPY 到 /mcdr/config.yml
# --auto-init：自动生成缺失的 permission.yml 等运行时文件
#   注意：init 仅在文件缺失时生成默认值，已存在的 config.yml 不会被覆盖
echo "[entrypoint] 启动 MCDR..."
# 非交互启动（docker run -d 未带 -it）时，MCDR 控制台 stdin 会立即 EOF，
# readline() 死循环把空行当命令转发给服务端 → ~1500 空命令/秒 → 刷屏 → OOM。
# 非 tty 时喂一个永不断开的空 stdin（tail -f /dev/null 永不输出也永不关闭），
# 让 readline() 阻塞等待，从而无论是否 -it 都安全。
if [ -t 0 ]; then
    exec mcdreforged start --auto-init
else
    exec mcdreforged start --auto-init < <(tail -f /dev/null)
fi
