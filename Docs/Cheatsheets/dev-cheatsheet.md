# 开发指令速查表

> 日常开发高频指令速查。完整流程与排错见各服务 README / CLAUDE.md。
> MCDR **API** 速查（命令节点 / RText / 权限 …）另见 [`../McdrPlugin/mcdr-api-cheatsheet.md`](../McdrPlugin/mcdr-api-cheatsheet.md)，本表只收**运维 / 调试指令**。

---

## MCDR / 测试服

### 接管 MCDR 控制台

```bash
docker attach pchsystem-mc-test-1
```

进入 MCDR 交互控制台，可直接输入 `!!help`、`!!MCDR reload plugin pch_system` 等命令。

| 操作 | 按键 | 说明 |
|---|---|---|
| 脱离控制台（保持服务运行） | `Ctrl+P` 然后 `Ctrl+Q` | 推荐，安全退出 attach |
| 停止服务 | `Ctrl+C` | **禁止** —— 会 SIGINT 杀掉 MCDR / MC 服务端 |

> ⚠️ **雷点**：脱离只能用 `Ctrl+P, Ctrl+Q`；误按 `Ctrl+C` 会直接关服导致玩家掉线。
> attach 后若被日志刷屏，按一次回车即可重新看到 MCDR 输入提示。

---

## 环境变量（根 `.env`，compose 插值读取）

> compose 用 `${VAR}` 插值读**根 `.env`**（compose 文件同目录），**不是** `Backend/.env`。
> 变量定义见 [`../../Backend/app/core/config.py`](../../Backend/app/core/config.py)（`Settings`，pydantic-settings）。

<!-- AUTO-GENERATED from .env.example + Backend/app/core/config.py；drift 时同步 -->
| 变量 | 必需 | 默认（config.py） | 说明 |
|---|---|---|---|
| `POSTGRES_USER` | 是（compose） | `pch` | DB 用户 |
| `POSTGRES_PASSWORD` | **是** | `""`（空） | DB 密码，须设强随机值 |
| `POSTGRES_DB` | 是（compose） | `pchsystem` | DB 名 |
| `POSTGRES_HOST` | 是（compose） | `localhost`（compose 注入 `postgres`） | DB 主机 |
| `POSTGRES_PORT` | 否 | `5432` | DB 端口 |
| `JWT_SECRET` | **是** | `""`（空） | JWT 签名密钥，须设长随机串 |
| `JWT_ACCESS_TTL_SECONDS` | 否 | `3600` | access token 有效期（秒） |
| `JWT_REFRESH_TTL_SECONDS` | 否 | `604800`（7 天） | refresh token 有效期（秒） |
| `AUTH_TOKEN_TTL_SECONDS` | 否 | `600`（10 分钟） | 一次性登录 token 有效期（秒） |
| `AUTH_TOKEN_RATE_LIMIT_SECONDS` | 否 | `30` | 同 UUID 登录 token 限频窗口（秒） |
| `MCDR_SERVICE_TOKEN` | **是** | `""`（**fail-fast**） | MCDR 代玩家写通道共享密钥；空则后端启动报错（`config.py` 校验，R-11） |
| `WEB_BASE_URL` | 是（联调） | `http://localhost:5173` | 前端 base URL，`!!PCH login` 回链前缀；改端口须同步 |
<!-- AUTO-GENERATED END -->

> ⚠️ 本地 venv 直跑 uvicorn 读 `Backend/.env`（`POSTGRES_HOST=postgres` 连不上 DB）；开箱即用走 compose（读根 `.env`）。

---

## 后端 FastAPI

> 容器编排：根 [`../../docker-compose.yml`](../../docker-compose.yml)（postgres + backend）。
> backend 容器已挂载源码 + `uvicorn --reload`，改 `app/**/*.py` 保存即热重载，无需 rebuild。
> 完整流程 / 排错见 [`../../Backend/CLAUDE.md`](../../Backend/CLAUDE.md) §5。

### 启动 / 停止

```bash
docker compose up -d                                # 启动 postgres + backend（首次自动 build 镜像）
```
```bash
docker compose up -d --build backend                # 改了 pyproject.toml 依赖后强制 rebuild 镜像
```
```bash
docker compose down                                 # 停止并移除容器（postgres 数据保留在 pgdata volume）
```

| 端口 | 用途 |
|---|---|
| `localhost:8000` | backend API（映射自容器 8000） |
| `127.0.0.1:5433` | postgres（仅本机可达，映射自容器 5432） |

> ⚠️ **雷点**：compose 用 `${VAR}` 插值，读的是**根 `.env`**（compose 文件同目录），**不是** `Backend/.env`。
> 启动前确认根 `.env` 已设 `POSTGRES_*` / `JWT_SECRET` / `MCDR_SERVICE_TOKEN`（空 `MCDR_SERVICE_TOKEN` 会 fail-fast，RS-4 / `config.py` 校验）。
> 本地 venv 直跑 uvicorn 会因 `Backend/.env` 的 `POSTGRES_HOST=postgres` 连不上 DB —— 走 compose 才是开箱即用路径。

### 跑迁移 / 验证健康

```bash
docker compose exec backend alembic upgrade head    # 新增迁移后必跑
docker compose exec backend alembic current         # 看当前版本（排错 "Target database is not up to date"）

curl -sS http://localhost:8000/healthz              # → {"status":"ok"}
curl -sS http://localhost:8000/me                   # → 401（未带 JWT，证明服务在跑）
```

### 日志 / 测试

```bash
docker compose logs -f --tail 30 backend            # 实时日志（热重载事件也在此）
cd Backend && pytest tests/ -v                      # 宿主机 venv 跑测试（conftest mock，不连真 DB）
docker compose exec backend pytest                  # 容器内跑测试
```

---

## 前端 Vue3（Vite）

> 前端**不在 docker 里跑**（compose 只含 postgres + backend），开发时宿主机直接 `npm run dev`，依赖 Vite HMR。
> 完整流程 / 排错见 [`../../Frontend/CLAUDE.md`](../../Frontend/CLAUDE.md) §6。

### 启动 / 构建

```bash
cd Frontend && npm run dev            # 启 Vite dev server（http://localhost:5173，前台常驻 / HMR）
cd Frontend && npm run build          # 生产构建（vue-tsc 类型检查 + vite build → dist/）
cd Frontend && npm run preview        # 预览构建产物
cd Frontend && npx vitest             # 跑单测（package.json 无 test script，直接调 vitest）
```

| 路径 | 说明 |
|---|---|
| `http://localhost:5173` | dev server（Vite client + HMR） |
| `/api/*` → `localhost:8000/*` | `vite.config.ts` 代理，**自动剥离 `/api` 前缀**（前端写 `/api/me` → 后端 `/me`） |

> ⚠️ **雷点 / 联调依赖**：
> - **依赖 backend 在跑**：dev server 仅代理 `/api`，后端没起则所有请求 502 / ECONNREFUSED。
> - **`WEB_BASE_URL` 决定回链**：根 `.env` 里 `WEB_BASE_URL=http://localhost:5173`，是 `!!PCH login` 拼一次性登录 URL 的前缀；改端口要同步改。
> - **改 `vite.config.ts` / `package.json` 不热重载**：需 Ctrl+C 重启 dev server（HMR 只覆盖 `.vue` / `.ts` 源码）。
> - **后台 dev server 停不掉时**：`lsof -ti :5173 | xargs -r kill`（或停掉拉起它的后台任务）。
> - **外部域名访问被 Vite 拦截**（`Blocked request. This host (...) is not allowed.`）：经反代 / tunnel 域名（如 `dev-git.xxx.nyat.app`）访问 dev server 时，需在 `vite.config.ts` 的 `server.allowedHosts` 显式加该域名（防 DNS rebinding）；改 config 不热重载，需重启 dev server。
