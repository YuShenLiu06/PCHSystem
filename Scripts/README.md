# Scripts —— PCHSystem 一键安装 / 更新

面向玩家/服主，在自己 Linux 服务器上一键部署与更新 PCHSystem（FastAPI 后端 + PostgreSQL + htcmc_auth MCDR 插件 + 前端静态构建）。自动检测/安装 Docker、自适应国内网络（GitHub / Docker Hub / PyPI / npm 镜像）、智能判断是否需要重建镜像。

- `install.sh` —— 首次安装
- `update.sh` —— 一键更新
- `lib/common.sh` —— 共享函数库（被上面两个脚本 source）

---

## 1. 快速开始

```bash
# 1) 先 clone 仓库（GitHub 直连不通时用镜像，见 §5）
git clone https://github.com/YuShenLiu06/PCHSystem.git
cd PCHSystem

# 2) 一键安装（交互式，会询问 MCDR 路径等）
bash Scripts/install.sh

# 之后日常更新
bash Scripts/update.sh
```

> install.sh / update.sh **必须在仓库内执行**（`cd PCHSystem` 之后）。脚本会自动检测/安装 Docker，但装 Docker 需要免密 sudo 或 root（详见 §6）。

---

## 2. install.sh —— 首次安装

```
bash Scripts/install.sh [选项]
```

| 选项 | 说明 |
|---|---|
| `--edge` | 拉 `main` 最新提交（默认拉最新发版 tag `*-v*`，即最近一次发版的完整快照） |
| `--yes` | 无人值守，全部用默认值（等价 `PCH_YES=1`） |
| `--mcdr-root DIR` | MCDR 根目录（含 `plugins/` 和 `config/`），等价 `PCH_MCDR_ROOT` |
| `--mcdr-api-url URL` | 插件访问后端的 URL，等价 `PCH_MCDR_API_URL` |
| `--mcdr-overwrite-config` | 强制覆盖玩家已有的 `htcmc_auth/config.json` |
| `--no-frontend` | 跳过前端构建 |
| `--no-mcdr` | 跳过 MCDR 插件拷贝 |
| `--no-sync` | 跳过版本同步（用当前工作树，开发/测试用） |
| `-h` / `--help` | 帮助 |

**环境变量**：`PCH_YES` / `PCH_MCDR_ROOT` / `PCH_MCDR_API_URL` / `WEB_BASE_URL`。

### 做了什么（12 步）
1. 检测/安装 Docker + Compose（`get.docker.com --mirror Aliyun`）
2. 探测 GitHub 连通性 → 自动选镜像源
3. 同步仓库到目标版本（最新 tag 或 `--edge` main）
4. 生成 `.env`（**已存在绝不覆盖**；新装则用 `openssl rand` 填三个强随机密钥）
5. 生成 `docker-compose.override.yml`（生产模式：去掉 `--reload` + 加 healthcheck，保留源码挂载）
6. `docker compose up -d` → 等 postgres healthy → 起 backend → 等 `/healthz` 200
7. `docker compose exec backend alembic upgrade head`（迁移前 `pg_dump` 快照）
8. 前端 `npm run build` → `Frontend/dist/`（best-effort，失败不阻后端）
9. 拷 `htcmc_auth` 到你的 MCDR `plugins/`，生成 `config/htcmc_auth/config.json`
10. 持久化部署状态到 `.pchsystem.deploy.env`
11. 打印摘要 + 待办（`newgrp docker` / 装依赖插件 / 游戏内 reload / nginx 托管前端）

每步**幂等**：装完 Docker 重新登录后重跑不会重复安装、不覆盖已有 `.env` / override。

---

## 3. update.sh —— 一键更新

```
bash Scripts/update.sh [选项]
```

| 选项 | 说明 |
|---|---|
| `--edge` | 本次临时拉 `main` 最新（不改部署策略） |
| `--yes` | 无人值守 |
| `--force` | 接管非脚本安装的部署 / 跳过本地改动保护 |
| `--frontend` | 强制重建前端（即使无 `Frontend/` 变更） |
| `--no-mcdr` | 跳过 MCDR 插件增量更新 |
| `--mcdr-root DIR` | 覆盖部署配置里的 MCDR 根目录 |

### 智能重建矩阵（核心）
按 `git diff` 路径自动决定容器操作，避免不必要的 rebuild：

| 变更 | 动作 |
|---|---|
| `Backend/Dockerfile` 或 `Backend/pyproject.toml` | `up -d --build backend`（rebuild 镜像） |
| 仅 `Backend/app/**` / `Backend/alembic/**` | `up -d --force-recreate backend`（秒级，源码已挂载） |
| `docker-compose.yml` / override | `up -d`（自动 recreate） |
| 无 Backend / compose 变更 | 跳过容器操作 |

之后总是跑 `alembic upgrade head`（幂等）、按需构建前端、增量同步插件。

### 安全保障
- **迁移前 `pg_dump`**；迁移失败**绝不自动 downgrade**（`score_ledger` append-only，RS-2），只给手动恢复指引。
- **dirty 保护**：本地有跟踪文件改动时拒跑（`.env`/override/deploy.env 等已 gitignored，不计入）。
- **不自动回滚**：健康检查失败只给手动回滚命令（git revert + 迁移恢复），避免数据风险。

---

## 4. 版本来源策略

- **默认最新发版 tag**：`git tag --list '*-v*'` 按 creatordate 倒序取首个 = 最近一次发版的完整仓库快照（三端独立 tag `backend-v*` / `htcmc_auth-v*` / `frontend-v*`，每个 tag 都含所有端代码）。
- **`--edge`**：拉 `main` 最新提交（尝鲜 / 联调）。
- update 沿用 install 时记录的策略（`.pchsystem.deploy.env` 的 `PCH_DEPLOY_STRATEGY`），可用 `--edge` 临时覆盖。

---

## 5. 网络镜像自适应（国内）

脚本探测→候选→best-effort→失败给手动指引，**单一镜像不可用绝不阻断**。

| 类别 | 处理 |
|---|---|
| **GitHub**（clone/fetch） | 直连探测失败时，依次试 `ghfast.top` / `ghproxy.com` / `kkgithub.com` / `gitclone.com` / `gh.zwy.one`（用 `git insteadOf` 重写） |
| **Docker Hub** | `get.docker.com --mirror Aliyun` 装时走阿里云；运行时写 `/etc/docker/daemon.json` 的 `registry-mirrors`（`docker.nju.edu.cn` / `docker.1ms.run` / `docker.m.daocloud.io` / `mirror.baidubce.com`） |
| **PyPI**（build 时） | `PIP_INDEX_URL` 透传清华源（注：当前 Dockerfile 未消费此 build-arg，主要靠代理透传 `HTTPS_PROXY` 助 CJK 字体 `wget`） |
| **npm** | `npm config set registry https://registry.npmmirror.com` |

> ⚠ **公共镜像 2024–2025 大批关停**（ustc、网易、中科大、阿里云部分等）。脚本里的候选以**探测结果为准**，可能随时失效。若全失败，回退直连（慢但通常最终能成），或自行挂代理后重跑。

### 手动镜像 clone（install 前仓库都还没有时）
```bash
# kkgithub（域名替换）
git clone https://kkgithub.com/YuShenLiu06/PCHSystem.git
# 或 ghproxy（前缀式）
git -c url.https://ghproxy.com/https://github.com.insteadOf=https://github.com \
    clone https://github.com/YuShenLiu06/PCHSystem.git
```

---

## 6. Docker 安装说明

- `install.sh` 自动用 `get.docker.com --mirror Aliyun` 装最新 Docker + Compose v2 plugin；失败回退发行版原生包（`apt`/`dnf`/`apk`/`pacman`）。
- **需要免密 sudo 或 root**：装 Docker、写 `/etc/docker/daemon.json`、`usermod -aG docker` 这几步要提权。脚本在需要时才提权，主体不强制 root。
- 装完会把当前用户加入 `docker` 组，提示 `newgrp docker` 或重新登录——之后即可免 sudo 使用 docker，再重跑 `install.sh` 续装（幂等）。

---

## 7. MCDR 插件（htcmc_auth）部署

脚本只负责**后端容器**（backend + postgres）。MC 服务端（Fabric + MCDReforged）由你自己持有。

### 前置依赖（必须）
`htcmc_auth` 依赖另外两个 MCDR 插件，缺则加载失败：
- **`uuid_api_remake`** —— <https://github.com/gubaiovo/MCDR_uuid_api_remake>（离线 UUID 推导）
- **`minecraft_data_api`** —— MCDR 插件市场的 `MinecraftDataAPI`

`install.sh` / `update.sh` 会扫描你的 `plugins/` 并在缺失时 warn。

### config.json 的 api_url（关键）
插件配置 `<MCDR>/config/htcmc_auth/config.json` 的 `api_url` 必须是**插件容器/进程能访问到后端的地址**：
- MCDR 与后端**同机**（裸进程/systemd）→ `http://127.0.0.1:8000`
- MCDR 与 backend **同 docker 网络** → `http://pchsystem-backend-1:8000`（需把 MCDR 容器加入 `pchsystem_default` 网络）
- 脚本按 `--mcdr-root` 路径形态推断默认（路径含 `/var/lib/docker/volumes/` 视为 docker 化），**务必核对**。

> `config.json.example` 里的 `api_url` 是容器服务名，仅在「MCDR 容器 + 同网络」时可达——脚本不会照抄，会按拓扑覆盖。

### 热重载
脚本拷完插件后**不自动 reload**（MCDR 仅接受游戏内 / 控制台 stdin，宿主脚本无法可靠注入），请手动：
```
!!MCDR plugin reload htcmc_auth
```
若 `mcdreforged.plugin.json`（version/dependencies）变更，update 会提示**需重启 MCDR**而非仅 reload。

### docker 化 MCDR 的两种形态
- **bind mount / volume 挂载点可见**（如 `/var/lib/docker/volumes/xxx/_data`）：`--mcdr-root` 指向该宿主路径，脚本直接 rsync。
- **named volume 无挂载点**：脚本无法自动拷贝，请用 `docker cp McdrPlugin/htcmc_auth/. <mcdr容器>:/mcdr/plugins/htcmc_auth/` 手动拷贝。

---

## 8. token 双写与密钥轮换

后端 `.env` 的 `MCDR_SERVICE_TOKEN` 与插件 `config.json` 的 `service_token` **必须同值**：
- `install.sh` 生成 `.env` 时填强随机值，并**复用同一个值**写入插件 config。
- `update.sh` 每次校验两边一致，不一致只 warn（不擅改你的 config）并给修复命令。

**轮换密钥**需手动同步两处：
```bash
# 1. 改 .env
$EDITOR .env  # 改 MCDR_SERVICE_TOKEN（及 POSTGRES_PASSWORD / JWT_SECRET 若也要轮换）
# 2. 同步到插件 config
$EDITOR <MCDR>/config/htcmc_auth/config.json  # service_token 改成同值
# 3. 重启后端 + reload 插件
docker compose restart backend
# 游戏内: !!MCDR plugin reload htcmc_auth
```
> 轮换 `POSTGRES_PASSWORD` 还需更新 postgres 容器与 `pgdata`（改密码需 SQL `ALTER USER`，谨慎）。

---

## 9. 生产 override 策略

`install.sh` 生成 `docker-compose.override.yml`（已 `.gitignore`）：
- 覆盖 `backend.command` 为无 `--reload` 的生产 CMD（与 `Backend/Dockerfile` 一致）；
- 加 backend healthcheck（`/healthz` 存活探针，slim 镜像无 curl 故用 python）；
- **保留源码 volume 挂载**（`./Backend/app` / `./Backend/alembic`）：更新只需 `git pull` + `force-recreate`，秒级、不触网。

> 纯镜像模式（代码打进镜像、每次 rebuild）因 override 无法干净清空继承的 volume 列表，暂未提供；玩家场景保留挂载更实用（更新快、国内网络友好）。

---

## 10. 前端托管（dist 怎么用）

`install.sh` 第 8 步只 `npm run build` 出 `Frontend/dist/`，**不起任何 web 服务器**。要能访问，需自己起一个 HTTP 服务器托管它。

### dist 不是"启动"的
`dist/` 是纯静态文件（`index.html` + `assets/*`），没有进程会自己跑、不监听端口。"启动前端" = **起一个 HTTP 服务器，把 `dist/` 作为文件根**。

前端写死 `baseURL: '/api'`（`Frontend/src/utils/http.ts`），所以 SPA 完整工作需服务器同时做两件事：
1. **提供静态文件**（`/`、`/assets/*`）—— 任何 HTTP 服务器都行；
2. **反代 `/api` 到后端**（`/api/me` → `http://127.0.0.1:8000/me`，去 `/api` 前缀）—— 需 nginx / Caddy 这类能配 proxy 的。

> 只做 ① 不做 ②（如 `python3 -m http.server`、GitHub Pages）→ 能看 UI 壳，但登录/数据全 404。

### 生产：nginx 同域反代（推荐）
```nginx
server {
    listen 80;
    server_name your.domain.or.ip;
    root /path/to/Frontend/dist;
    index index.html;

    location / { try_files $uri $uri/ /index.html; }   # SPA 路由 fallback
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;             # 末尾 / 必带 → 去 /api 前缀
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    location /assets/ { expires 1y; add_header Cache-Control "public, immutable"; }
}
```
> `proxy_pass` 末尾 `/` 是命门：带 `/` 才会去掉 `/api` 前缀（对齐 vite dev 的 rewrite）；漏了 → `/api/me` 变 `:8000/api/me` 后端 404。

### 临时验证（docker nginx 一行）
```bash
cd Frontend
cat > /tmp/pch-preview.conf <<'EOF'
server {
    listen 8081;
    root /usr/share/nginx/html;
    location / { try_files $uri $uri/ /index.html; }
    location /api/ { proxy_pass http://host.docker.internal:8000/; }
}
EOF
docker run --rm -d --name pch-preview -p 8081:8081 \
  --add-host=host.docker.internal:host-gateway \
  -v "$(pwd)/dist:/usr/share/nginx/html:ro" \
  -v /tmp/pch-preview.conf:/etc/nginx/conf.d/default.conf:ro \
  nginx:alpine
# 访问 http://localhost:8081 ；用完 docker stop pch-preview
```

### 只瞄一眼 UI（不看数据）
```bash
cd Frontend/dist && python3 -m http.server 8099   # 或 npx serve
```

### 部署后改 `WEB_BASE_URL`
`.env` 的 `WEB_BASE_URL` 是 `!!PCH login` **二维码回链**（玩家扫码后浏览器打开的地址），默认 `http://localhost:5173`。前端上线后必须改成真实访问地址，否则玩家扫码打开不存在的 5173：
```bash
sed -i 's|^WEB_BASE_URL=.*|WEB_BASE_URL=http://your.domain|' .env
docker compose restart backend
```

---

## 11. 排错

| 现象 | 排查 |
|---|---|
| `docker compose` 不可用 | 装 Docker 后 `newgrp docker` 或重新登录；确认 `docker info` 正常 |
| backend `/healthz` 不通 | `docker compose logs --tail 80 backend`；postgres 未 healthy 也会拖住 backend（`depends_on`） |
| alembic `Target database is not up to date` | 手动 `docker compose exec backend alembic upgrade head` |
| 插件 `!!PCH` 命令不存在 | 确认依赖插件 `uuid_api_remake` + `minecraft_data_api` 已装；`docker logs <mcdr> \| grep htcmc_auth` 看加载日志 |
| 插件 reload 后行为没变 | 确认拷贝目标是 MCDR 实际加载的 `plugins/htcmc_auth/`；元数据变更需重启 MCDR |
| `!!PCH login` 链接打不开 | 核对 `.env` 的 `WEB_BASE_URL`（login 回链前缀）与前端实际地址 |
| token 401 | `.env` `MCDR_SERVICE_TOKEN` 与插件 config `service_token` 是否同值（见 §8） |
| clone/pull 卡住 | 见 §5 镜像；或挂代理后 `HTTPS_PROXY=http://宿主:port bash Scripts/install.sh`（自动透传给 build 助 CJK 字体下载） |

---

## 12. 不做的事（边界）

- **不部署 MC 服务端**（Fabric + MCDR 由你持有）；不部署 TestServer 的 `mc-test` 容器。
- **不托管前端**（只 build 出 `dist/`，托管方式见 §10）。
- **不自动 reload/restart MCDR**（只提示命令）。
- **不自动回滚**（迁移涉及数据，只给手动步骤）。
- **不覆盖已有 `.env` / override**（保留你的改动）。
