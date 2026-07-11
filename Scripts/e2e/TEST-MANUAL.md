# PCHSystem 部署脚本 e2e 测试手册（RUNBOOK 可执行）

> 本手册每个 ```bash 代码块**自包含**、可被 RUNBOOK 插件**逐块执行**，插件自动捕获每块输出作为日志（**不再 tee 到本地文件**，故无 `TS`/`logs/` 相关语句）。
> **install 组与 update 组物理分离**（两大章节），便于分组执行 + 分组归档，测完一并作为 PR 证据。

## 测试环境

| 项 | 值 |
|---|---|
| 测试目录 | `/home/yushen/opt/pchsandbox`（独立 worktree，project=`pchsandbox`，**与生产 `pchsystem` 共存**） |
| 假 MCDR | `/tmp/fake-mcdr`（空 `plugins/` + `config/htcmc_auth/`） |
| 端口 | **8100（后端）/ 15433（pg，loopback）/ 6173（web）** —— 偏移避让生产 8000/5433/5173 |
| 日志 | RUNBOOK 插件逐块自动捕获（不落本地文件） |
| 前置 | pchsandbox 已由会话准备：worktree `sandbox-test` @ PR 快照 commit（含 web 部署 + 端口 env 化 + `Backend/Dockerfile` pip/字体镜像修复 + MCDR restart 误报修复） |

> **为何用沙盒**：生产 `pchsystem` 在跑（占 8000/5433/5173），默认端口冲突；沙盒独立 project + 偏移端口，跑 e2e 不动生产。

---

## 0. 一次性环境准备

```bash
# worktree 的 .git 是文件（gitdir 指针）非目录 → 用 -e（file/dir 均真），勿用 -d（worktree 恒假）。
[[ -e /home/yushen/opt/pchsandbox/.git ]] && echo "✓ pchsandbox worktree 就绪" || echo "✗ 缺 pchsandbox（需先建 worktree + 落 PR 改动）"
mkdir -p /tmp/fake-mcdr/{plugins,config/htcmc_auth}
echo "✓ 假 MCDR 就绪"
```

---

## 1. 前置：重置（每次轮回前）

```bash
D=/home/yushen/opt/pchsandbox
cd "$D"

# 停容器 + 删 volume（仅 project=pchsandbox，绝不碰 pchsystem-*）
docker compose -p pchsandbox down -v

# backend 容器以 root 运行，bind mount 写出的 __pycache__/*.pyc 是 root 属主；
# 宿主 git clean -fdx 会因 unlink 失败报"权限不够"。先 chown 回宿主用户。
docker run --rm -v "$D":/w alpine chown -R "$(id -u):$(id -g)" /w

# 回到 sandbox-test 快照（含全部 PR 改动 + 偏移端口 compose + Dockerfile pip/字体镜像修复），
# 清所有生成产物（.env/override/dist/deploy.env/backups）。PR 文件已 tracked，不受影响。
git checkout sandbox-test 2>/dev/null || true
git reset --hard HEAD
git clean -fdx

# 重置假 MCDR
rm -rf /tmp/fake-mcdr/plugins/htcmc_auth /tmp/fake-mcdr/config/htcmc_auth/config.json

# 端口确认（应空闲）
ss -ltn | grep -E ':8100|:15433|:6173' && echo "⚠ 占用，先释放" || echo "✓ 端口空闲"
```

---

# ═══════════════════════ install 测试组 ═══════════════════════

## I-1. 一键部署

```bash
cd /home/yushen/opt/pchsandbox
# 端口/镜像源用「命令前缀内联 env」传给 install.sh 进程（勿用独立 export 行：RUNBOOK 插件会把它当输入项拦截/合并 → 值变 "8100 15433 6173" 或丢失 → .env 落默认端口 → 撞生产 5433）。
# install.sh 内部自设 PIP_INDEX_URL=清华源，此处只需给四项。
BACKEND_PORT=8100 PG_PORT=15433 WEB_PORT=6173 NPM_REGISTRY=https://registry.npmmirror.com \
bash Scripts/install.sh --no-sync --yes \
  --mcdr-root /tmp/fake-mcdr --mcdr-api-url http://127.0.0.1:8100
```

> `--no-sync` 用当前工作树（=sandbox-test 快照）；`--yes` 无人值守；`--mcdr-root` 跳过 MCDR 交互；`--mcdr-api-url` 指向沙盒 backend `:8100`（默认拓扑推断返回 `:8000`=生产，必须覆盖）。

## I-2. install 验证（RUNBOOK 可执行清单，14 项）

```bash
cd /home/yushen/opt/pchsandbox
{
  echo "=== [1] .env 三密钥（非占位）==="
  grep -E '^(POSTGRES_PASSWORD|JWT_SECRET|MCDR_SERVICE_TOKEN)=' .env
  echo "=== [2] override 含 healthcheck、无 --reload ==="
  grep -q healthcheck docker-compose.override.yml && echo "✓ healthcheck" || echo "✗ 缺 healthcheck"
  grep -q -- '--reload' docker-compose.override.yml && echo "✗ 残留 --reload" || echo "✓ 无 --reload"
  echo "=== [3] deploy.env ==="; cat .pchsystem.deploy.env
  echo "=== [4] 容器健康（project=pchsandbox）==="; docker compose -p pchsandbox ps
  echo "=== [5] healthz（:8100）==="; curl -sS http://127.0.0.1:8100/healthz; echo
  echo "=== [6] alembic current ==="; docker compose -p pchsandbox exec -T backend alembic current
  echo "=== [7] web 镜像内 dist（web 启用时镜像内构建，host dist 可空）==="; docker compose -p pchsandbox exec -T web ls /usr/share/nginx/html/index.html
  echo "=== [8] 插件目录（应无 __pycache__/tests）==="; ls /tmp/fake-mcdr/plugins/htcmc_auth/
  echo "=== [9] token 一致性 + api_url 指向 :8100 ==="
  env_tok=$(grep '^MCDR_SERVICE_TOKEN=' .env | cut -d= -f2-)
  cfg_tok=$(python3 -c "import json;print(json.load(open('/tmp/fake-mcdr/config/htcmc_auth/config.json'))['service_token'])" 2>/dev/null)
  [ "$env_tok" = "$cfg_tok" ] && echo "✓ token 一致" || echo "✗ 不一致"
  grep -o '"api_url": *"[^"]*"' /tmp/fake-mcdr/config/htcmc_auth/config.json
  echo "=== [10] web 容器（COMPOSE_PROFILES=web 默认启用）==="; docker compose -p pchsandbox ps web 2>/dev/null | tail -1
  echo "=== [11] web / → 200（:6173）==="; curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:6173/
  echo "=== [12] web /api/healthz 反代（去 /api 前缀 → 容器内 backend:8000）==="; curl -sS http://127.0.0.1:6173/api/healthz; echo
  echo "=== [13] web SPA fallback /sheets/3 → 200（history 模式 try_files）==="; curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:6173/sheets/3
  echo "=== [14] .env COMPOSE_PROFILES / WEB_PORT ==="; grep -E '^(COMPOSE_PROFILES|WEB_PORT)=' .env
}
```

---

# ═══════════════════════ update 测试组 ═══════════════════════

> update 组默认用 `--no-sync`（用当前工作树，不 fetch 远端），便于离线/快速测重建矩阵与流程。

## U-1. update 已是最新（--no-sync）

```bash
cd /home/yushen/opt/pchsandbox
bash Scripts/update.sh --no-sync
```

**期望**：`当前工作树与部署记录一致...仍执行更新流程` → 幂等迁移 + 健康 ok，无容器 recreate。

## U-2. update 有 backend 变化（造 tag + --no-sync）

```bash
cd /home/yushen/opt/pchsandbox

# 造"未来版本"（本地 tag，不污染主仓）
git checkout -b test-new-version
echo "# e2e update test marker" >> Backend/app/main.py
git -c user.email=t@t -c user.name=test commit -am "test: backend tweak for update e2e"
git tag backend-v0.9.0
git checkout backend-v0.9.0

bash Scripts/update.sh --no-sync

# 验证新代码生效
docker compose -p pchsandbox exec -T backend grep "e2e update test marker" /app/app/main.py && echo "✓ 新代码生效"
```

**期望日志**：`本地变更: ... → tag:backend-v0.9.0` + `Backend 代码变更 → force-recreate`。

## U-3.（可选）update --edge（fetch main）

```bash
cd /home/yushen/opt/pchsandbox
git checkout sandbox-test     # 离开 U-2 的造 tag
bash Scripts/update.sh --edge
```

**期望**：fetch origin main → `已是最新（edge）` 或应用 main 新提交。

## U-4.（可选）dirty 保护

```bash
cd /home/yushen/opt/pchsandbox
echo "manual tweak" >> Backend/app/config.py
bash Scripts/update.sh --no-sync   # 期望：检测到本地改动，拒跑（除非 --force）
git checkout -- Backend/app/config.py
```

---

# ═══════════════════════ web 部署测试组 ═══════════════════════

> 本组验证本 PR 新增的「compose `web` 服务（nginx 托管 dist + 反代 /api）」。默认 install（I-1）已启用 web（`.env` `COMPOSE_PROFILES=web`），故 I-2 的 [10]~[14] 已覆盖「web 起来 + 全链路通」。W-2/W-3 补「禁用」与「更新重建」两条路径。

## W-2. install `--no-web`（禁用 web，走非容器路径）

```bash
cd /home/yushen/opt/pchsandbox
# 前置：先跑 §1 重置（确保干净 + 端口空闲），再带 --no-web 装一遍
BACKEND_PORT=8100 PG_PORT=15433 WEB_PORT=6173 NPM_REGISTRY=https://registry.npmmirror.com \
bash Scripts/install.sh --no-sync --yes \
  --mcdr-root /tmp/fake-mcdr --mcdr-api-url http://127.0.0.1:8100 --no-web
{
  echo "=== [1] web 应未启动 ==="
  docker compose -p pchsandbox ps web 2>/dev/null | tail -1 | grep -q 'web' && echo "✗ web 仍在" || echo "✓ web 未起"
  echo "=== [2] .env COMPOSE_PROFILES 应为空 ==="; grep '^COMPOSE_PROFILES=' .env
  echo "=== [3] Frontend/dist 仍应生成（web 禁用时 build_frontend 走宿主 npm）==="; ls Frontend/dist/index.html
  echo "=== [4] backend 仍健康（:8100）==="; curl -sS http://127.0.0.1:8100/healthz; echo
}
```

**期望**：web 容器不存在；`.env` `COMPOSE_PROFILES=`（空）；`Frontend/dist/index.html` 仍生成；backend 不受影响。

## W-3. update `Frontend/` 变更 → 重建 web 镜像（智能重建矩阵 web 分支）

```bash
cd /home/yushen/opt/pchsandbox
# 前提：处于 web 启用态（先跑过默认 I-1，而非 W-2 的 --no-web）
git checkout -b test-web-rebuild
echo "// e2e web rebuild marker" >> Frontend/src/main.ts
git -c user.email=t@t -c user.name=test commit -am "test: frontend tweak for web rebuild e2e"
bash Scripts/update.sh --no-sync 2>&1 | tee /tmp/w3.log
grep -q 'Frontend 变更（容器路径）→ 重建 web 镜像' /tmp/w3.log && echo "✓ web 镜像重建分支命中" || echo "✗ 未命中"
# 回到测试分支，清理造的分支
git checkout sandbox-test 2>/dev/null || true
git branch -D test-web-rebuild 2>/dev/null || true
```

**期望日志**：`Frontend 变更（容器路径）→ 重建 web 镜像`（`update.sh::decide_rebuild` 新增的 web 分支；`compose_build web` + `up -d web`）。

---

## S-1. 静态校验：MCDR restart 误报已修复 + web profile 判定

```bash
cd /home/yushen/opt/pchsandbox
{
  echo "=== [1] 旧「需重启 MCDR」误报文案应 0 命中 ==="
  grep -rn '需【重启 MCDR】\|需\*\*重启 MCDR\*\*' Scripts/ McdrPlugin/ Docs/ && echo "✗ 仍有残留" || echo "✓ 已清除"
  echo "=== [2] update.sh 插件变更统一为 reload 文案（含 mcdreforged.plugin.json）==="
  grep -q '请在游戏内执行: !!MCDR plugin reload htcmc_auth' Scripts/update.sh && echo "✓ 统一 reload"
  echo "=== [3] web_profile_active 整词不误判（website ≠ web）==="
  tmp=$(mktemp -d) && cd "$tmp"
  # shellcheck source=/dev/null
  source /home/yushen/opt/pchsandbox/Scripts/lib/common.sh
  printf 'COMPOSE_PROFILES=website\n' > .env; web_profile_active && echo "✗ website 被误判为 web" || echo "✓ website 不误判"
  printf 'COMPOSE_PROFILES=web,foo\n' > .env; web_profile_active && echo "✓ web,foo 命中" || echo "✗ 漏判"
  cd - >/dev/null && rm -rf "$tmp"
}
```

**期望**：[1] 0 命中；[2] 命中统一 reload；[3] `website` 不误判、`web,foo` 命中。

> **MCDR reload 语义依据**：`mcdreforged.plugin.json` 任何字段（version/dependencies/...）变更都随 `!!MCDR plugin reload` 重新读取并由 `DependencyWalker` 重校依赖，**无需重启 MCDR**。源码：[`plugin_manager.py`](https://github.com/MCDReforged/MCDReforged/blob/master/mcdreforged/plugin/plugin_manager.py)（reload = unload→load→check dept）。详见 [`Docs/Reports/mcdr-release-prep.md`](../../Docs/Reports/mcdr-release-prep.md)。

---

## 清理（测完）

```bash
# 拆沙盒栈（仅 pchsandbox，绝不碰 pchsystem-*）
cd /home/yushen/opt/pchsandbox
docker compose -p pchsandbox down -v
rm -rf /tmp/fake-mcdr
# 彻底删 worktree（可选，回主仓执行）：
#   git worktree remove /home/yushen/opt/pchsandbox --force && git branch -D sandbox-test
```

---

## PR 交付清单

测完把以下作为 PR 证据（RUNBOOK 插件逐块执行后，日志由插件自动归档，按块摘录）：
1. **install**：I-1 部署块 + I-2 验证块（14 项全 ✓，含 web [10]~[14]）
2. **web 禁用**：W-2 `--no-web`（web 未起 + dist 仍构建）
3. **web 重建**：W-3 `Frontend 变更（容器路径）→ 重建 web 镜像` 行
4. **MCDR 误报修复**：S-1 旧「需重启 MCDR」0 命中 + 统一 reload + `website` 不误判
5. **update U-1**：已是最新路径
6. **update U-2**：`本地变更` + `force-recreate` 行（证明智能重建矩阵 backend 分支）
7. 已知边界（端口偏移隔离 / tag 模式 checkout 旧 tag / `--no-sync` 语义 / Dockerfile pip+字体镜像）

---

## 已知边界

- **端口 8100/15433/6173 必须空闲**（前置 §1）；为与生产 `pchsystem`（8000/5433/5173）共存而偏移。compose 的 backend/pg/web 端口均 env 可配（`${BACKEND_PORT:-8000}`/`${PG_PORT:-5433}`/`${WEB_PORT:-5173}`），install 块以**命令前缀内联 env**（`BACKEND_PORT=8100 PG_PORT=15433 WEB_PORT=6173 NPM_REGISTRY=... bash install.sh`）传入 → `install.sh::ensure_env` 写入 `.env` 持久化（**compose/脚本本身不变**，默认值 8000/5433/5173 对真实部署零影响；不用独立 `export` 行：RUNBOOK 插件会拦截/合并致变量丢失）。
- 沙盒 project 隔离：容器/卷/网络前缀 `pchsandbox-*`，与生产 `pchsystem-*` 互不干扰；镜像缓存共享（无害）。
- §1 重置必须先 `docker ... chown -R` 把工作树所有权改回宿主用户：backend 容器以 root 运行，bind mount 写出的 `__pycache__/*.pyc` 是 root 属主，宿主 `git clean -fdx`（`-x` 清 gitignored）会因 unlink 失败报"权限不够"。
- `--no-sync`（install/update 共有）：用当前工作树（=sandbox-test 快照），不拉取 / 不 checkout。开发与 e2e 用；生产部署用默认 tag 模式。
- update `--no-sync` 不 fetch：OLD=部署记录 commit，NEW=当前 HEAD；两者一致时仍跑迁移/健康（幂等确认），不 exit。
- 造的 tag/分支（`backend-v0.9.0` / `test-new-version` / `test-web-rebuild`）只在 pchsandbox `.git`，删 worktree 即清除。
- **backend 镜像 pip 大包（matplotlib/numpy/Pillow…）**：`Backend/Dockerfile` 经 `ARG PIP_INDEX_URL` + `pip config set global.index-url` 消费 `install.sh/setup_mirrors` 透传的清华源（本 PR 修复，否则走 PyPI 直连国内极慢）。
- **CJK 字体 wget**：`Backend/Dockerfile` 多源链 ghfast.top → ghproxy → GitHub 直连 → apt 兜底（脚本层 `PCH_GH_MIRRORS` 仅重写 git clone/fetch，**不覆盖** wget，故在 Dockerfile 显式列 GitHub 代理源；本机探测仅 ghfast.top 通）。
- **web 镜像 npm**：`NPM_REGISTRY` build-arg 控制源（install 块已设 npmmirror）；容器内 Node 为 `node:22-alpine`。
- `COMPOSE_PROFILES=web` 启停 web 服务；`--no-web` 仅在**首次生成 .env** 时生效（已有 .env 则直接改 `.env` 的 `COMPOSE_PROFILES`）。
- `--mcdr-api-url http://127.0.0.1:8100` 必传：假 MCDR 路径 `/tmp/fake-mcdr` 非容器卷，`detect_mcdr_topology` 默认返回 `:8000`（生产），不覆盖会把插件 config 指向生产 backend。
