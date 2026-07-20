# HTCMC PCHSystem 测试服务器

> 用于 V1 端到端验收：MC（1.20.1 Fabric · 离线模式） + MCDReforged + pch_system 插件 → 后端 → 前端三端联通。

## 端口

| 端口 | 用途 |
|---|---|
| `25565` | Minecraft Java Edition 客户端连接 |
| `25575` | rcon（调试可选，密码 `pch_test_rcon`） |

## 镜像

- 基础：`python:3.11-slim` + `openjdk-17-jre-headless`
- pip 装 `mcdreforged>=2.14,<3`（满足 `pch_system` 的依赖）
- 加入 `pchsystem_default` 外部网络，容器内通过 `http://pchsystem-backend-1:8000` 访问后端

## 目录约定

```
TestServer/
├── Dockerfile                    # Java17 + Python + MCDR
├── docker-compose.yml
├── entrypoint.sh                 # 自动下载 Fabric launcher、生成 eula、启动 MCDR
├── config/
│   ├── mcdr_config.yml           # MCDR: vanilla_handler + UTF-8（Fabric 专用）
│   └── pch_system_config.json    # 覆盖 pch_system 默认配置，指向容器网络内 backend
├── plugins/
│   └── uuid_api_remake.mcdr      # pch_system 的运行时依赖
└── server/                       # 持久化卷（fabric jar + world + libraries，gitignored）
```

`pch_system` 源码目录通过 docker volume 直接挂载（`../McdrPlugin`），改插件源码后 `!!MCDR plugin reload pch_system` 即可热更新，**无需重建镜像**。

## 启动

```bash
cd /home/yushen/opt/PCHSystem/TestServer
docker compose up -d --build
docker compose logs -f mc-test
```

首次启动 Fabric launcher 会下载 MC server 文件（约 300MB，需几分钟）。日志出现 `Done (...)!` 即服务器就绪。

## 进游戏验收

1. Minecraft Java Edition 1.20.1 客户端，添加服务器：
   - 地址：`localhost` 或 `127.0.0.1`
   - 离线模式（无需正版账号）
2. 进服后聊天框输入：`!!PCH login`
3. 应收到一条带 `[点击此处打开网页登录]` 的可点击消息
4. 点击链接 → 浏览器打开 `http://localhost:5173/auth?token=<uuid>`（前端 dev server）
5. 前端用 token 调 `/auth/exchange` → 拿到 JWT → 跳 `/me` 显示 UUID/名称/角色

## 调试命令

```bash
# 看 MCDR + Fabric 日志
docker compose logs -f mc-test

# 进入 MCDR 控制台（attach 后 !!help 查看命令，Ctrl+P Ctrl+Q 退出）
docker attach pchsystem-mc-test-1

# 在容器内执行单次命令（例：发服务器命令）
docker exec -i pchsystem-mc-test-1 rcon-cli 'say hello'  # 需装 rcon-cli，本镜像未装
docker exec pchsystem-mc-test-1 bash

# 热重载 pch_system（改完 ../McdrPlugin/ 下源码后）
docker exec -i pchsystem-mc-test-1 bash -c "echo '!!MCDR plugin reload pch_system' | mcdreforged start --no-server-stop" 2>/dev/null \
  || echo "改用 attach 进入控制台输入 !!MCDR plugin reload pch_system"

# 看后端日志确认 token 请求
docker logs -f pchsystem-backend-1

# 重置所有状态（世界 + 后端 players 表）
docker compose -f /home/yushen/opt/PCHSystem/docker-compose.yml exec backend python -c "
import asyncio; from app.core.db import async_session_factory; from sqlalchemy import text
async def f():
    async with async_session_factory() as s:
        await s.execute(text('TRUNCATE users.auth_tokens, users.jwt_revocations, users.bind_tokens, users.web_accounts, users.players, sheets.sheet_managers CASCADE;'))
        await s.commit()
asyncio.run(f())
"
```

## 注意

- MCDR 配置文件 `mcdr_config.yml` 通过 volume 挂载，**不要在容器内手动改**
- Fabric 第一次启动需要外网（下载 MC server）
- 后端 `WEB_BASE_URL=http://localhost:5173` 是给玩家浏览器看的，由后端拼接到 token URL；MCDR 给玩家发的链接也用这个值（玩家在宿主跑浏览器）
