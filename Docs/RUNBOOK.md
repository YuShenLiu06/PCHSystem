# 运维手册（RUNBOOK）

> HTCMC PCHSystem · **dev / staging 运维手册**。
> 生产部署（wiki.js 纳入 compose、外部反代、监控告警）待相关阶段落地后补；当前覆盖本地 + 测试服运维。
> 日常高频**指令清单**不重复，见 [`Cheatsheets/dev-cheatsheet.md`](./Cheatsheets/dev-cheatsheet.md)；本文聚焦**流程 / 排错 / 回滚**。

---

## 1. 前置依赖

- Docker + Docker Compose（根编排 + TestServer 测试服）
- Python 3.11+（后端本地调试 / MCDR 插件）
- Node.js 18+（前端开发）
- Minecraft Fabric 服务端（Fabric + Create + Carpet，离线模式）—— 仅联调 MCDR 时需要

---

## 2. 拓扑与端口

| 组件 | 来源 | 端口 / 路径 | 备注 |
|---|---|---|---|
| backend（FastAPI） | 根 `docker-compose.yml` | `localhost:8000` | 源码挂载 + `uvicorn --reload` |
| postgres:16 | 根 `docker-compose.yml` | `127.0.0.1:5433` → 容器 5432 | 仅本机可达；数据落 `pgdata` volume |
| 前端 dev server | 宿主机 `npm run dev` | `http://localhost:5173` | 不在 compose；`/api/*` 代理到 `:8000` |
| mc-test（MCDR + Fabric） | `TestServer/docker-compose.yml` | `25565`（MC）/ `25575`（RCON） | 加入 `pchsystem_default` 网络，容器内访问 `http://pchsystem-backend-1:8000` |

> wiki.js **规划中**，尚未纳入 compose（见根 [`CLAUDE.md`](../CLAUDE.md) §2 / §7）。

---

## 3. 部署流程

### 3.1 首次启动（backend + DB）

```bash
cp .env.example .env                              # 改 POSTGRES_PASSWORD / JWT_SECRET / MCDR_SERVICE_TOKEN
docker compose up -d                              # 启 postgres + backend（首次自动 build）
docker compose exec backend alembic upgrade head  # 跑迁移到最新
curl -sS http://localhost:8000/healthz            # → {"status":"ok"}
```

### 3.2 日常启动 / 源码热重载

| 改动 | 生效方式 |
|---|---|
| `Backend/app/**/*.py` | uvicorn `--reload` 自动重启，无需 rebuild |
| `alembic/versions/*.py` | 手动 `docker compose exec backend alembic upgrade head` |
| `Backend/pyproject.toml`（加依赖） | `docker compose up -d --build backend` |
| 前端 `.vue`/`.ts` | Vite HMR |
| `vite.config.ts` / `package.json` | Ctrl+C 重启 dev server |
| MCDR 插件 `.py` | 游戏内 `!!MCDR plugin reload htcmc_auth`（源码已挂载） |

### 3.3 测试服（mc-test）

```bash
docker compose -f TestServer/docker-compose.yml up -d   # 首次会下 ~300MB MC 文件
docker logs pchsystem-mc-test-1 2>&1 | grep htcmc_auth  # 确认插件加载
```

---

## 4. 健康检查

| 检查 | 命令 | 期望 |
|---|---|---|
| backend 存活 | `curl -sS http://localhost:8000/healthz` | `{"status":"ok"}` |
| backend 鉴权链路 | `curl -sS http://localhost:8000/me` | `401`（未带 JWT，证明路由在跑） |
| 迁移版本 | `docker compose exec backend alembic current` | 与 `alembic/versions/` 最新一致（当前 `0006_notifications`） |
| mc-test 插件 | `docker logs pchsystem-mc-test-1 2>&1 \| grep htcmc_auth` | 命令注册 + help message |
| 前端联调 | 浏览器开 `http://localhost:5173` | `/auth` 页可走 token 兑换 |

---

## 5. 常见故障与修复

| 症状 | 排查 | 修复 |
|---|---|---|
| 改后端代码行为没变 | 容器跑旧镜像 / reload 未触发 | `docker logs pchsystem-backend-1 --tail 30` 看 reload 事件；必要时 `docker compose up -d backend --force-recreate` |
| `Target database is not up to date` | 迁移落后 | `alembic current` → `alembic upgrade head` |
| 前端所有请求 502 / ECONNREFUSED | backend 没起 | `curl :8000/healthz` 确认 backend 在跑 |
| `!!MCDR plugin reload` 后行为没变 | 插件源码未挂载 / 挂载点指向空目录 | `docker inspect pchsystem-mc-test-1 \| grep -A2 plugins/htcmc_auth`；语法错先 `python -c "import ast; ast.parse(open('<file>').read())"` |
| `/auth` 兑换 401 | token 过期（TTL 10 分钟）或被新一次签发 revoke | 重新 `!!PCH login` 拿新 token |
| 401 后未跳 `/auth` | axios 响应拦截器未装 | 检查 `Frontend/src/utils/http.ts`（RS-5） |
| attach 测试服误按 `Ctrl+C` | 直接 SIGINT 杀服 | 只用 `Ctrl+P, Ctrl+Q` 脱离；重启 `docker compose -f TestServer/docker-compose.yml up -d` |
| 空白 `MCDR_SERVICE_TOKEN` | 后端启动 fail-fast | 根 `.env` 设非空值（`config.py` 校验，R-11） |

---

## 6. 回滚

### 6.1 代码回滚

```bash
git revert <commit>                       # 推荐：保留历史
docker compose up -d --build backend      # 让容器跑回滚后的代码
```

### 6.2 迁移回滚

```bash
docker compose exec backend alembic current        # 看当前版本
docker compose exec backend alembic downgrade -1   # 回退一档（需迁移脚本实现了 downgrade 路径）
```

> ⚠️ **积分流水 append-only（R-2）**：`score_ledger` 禁止 UPDATE/DELETE；降级迁移仅用于 schema 结构，不得破坏已落库流水。

### 6.3 数据回滚（极端）

postgres 数据落 `pgdata` volume；需整体恢复时用 volume 备份，**禁止** `docker compose down -v`（`-v` 删 volume 清库）。

---

## 7. 监控与告警（当前状态）

- **当前无生产监控 / 告警通道**：alert-service 规划中（见 [`architecture/services/alert-service.md`](./architecture/services/alert-service.md)）。
- 开发期靠 `docker compose logs -f backend` + `docker logs pchsystem-mc-test-1` 人工观察。
- 通知投递链路：MCDR 轮询 `GET /notifications/pending` → `server.tell` → `POST /notifications/ack`（见 [`architecture/services/notification-service.md`](./architecture/services/notification-service.md)）。

---

## 8. 互链

| 文档 | 用途 |
|---|---|
| [`Cheatsheets/dev-cheatsheet.md`](./Cheatsheets/dev-cheatsheet.md) | 日常高频指令速查（命令清单） |
| [`../Backend/CLAUDE.md`](../Backend/CLAUDE.md) §5 | 后端热重载工作流 / 排错 |
| [`../McdrPlugin/CLAUDE.md`](../McdrPlugin/CLAUDE.md) §7 | MCDR 插件热重载 / 排错 |
| [`../Frontend/CLAUDE.md`](../Frontend/CLAUDE.md) §6 | 前端热重载 / 排错 |
| [`architecture.md`](./architecture.md) | 三端架构 / 跨服务流程 |

---

*最后更新：2026-07-02（dev/staging 版；生产段待 wiki.js + 监控落地后补）*
