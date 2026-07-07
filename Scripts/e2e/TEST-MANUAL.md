# PCHSystem 部署脚本 e2e 测试手册（RUNBOOK 可执行）

> 本手册每个 ```bash 代码块**自包含**、可被 RUNBOOK 插件**逐块执行**，输出 `tee` 到 `logs/`。
> **install 组与 update 组物理分离**（两大章节），便于分组执行 + 分组归档日志，测完一并作为 PR 证据。

## 测试环境

| 项 | 值 |
|---|---|
| 测试目录 | `/home/yushen/opt/pch-e2e`（remote=主仓本地路径，未 push 的 commit 也能 fetch） |
| 假 MCDR | `/tmp/fake-mcdr`（空 `plugins/` + `config/htcmc_auth/`） |
| 端口 | 8000（后端）/ 5433（测试 pg，避开主仓 5432） |
| 日志归档 | `logs/<scenario>-<timestamp>.log`（本地 gitignored） |

---

## 0. 一次性环境准备

```bash
mkdir -p /tmp/fake-mcdr/{plugins,config/htcmc_auth}
mkdir -p /home/yushen/opt/pch-e2e/logs
grep -qx 'logs/' /home/yushen/opt/pch-e2e/.git/info/exclude 2>/dev/null \
  || echo 'logs/' >> /home/yushen/opt/pch-e2e/.git/info/exclude
echo "✓ 假 MCDR + logs/ 就绪"
```

---

## 1. 前置：重置（每次轮回前）

```bash
D=/home/yushen/opt/pch-e2e
cd "$D"

# 停容器 + 删 volume（彻底清 pgdata）
docker compose down -v

# 同步主仓最新 + 清所有产物（node_modules/dist/.env/override/deploy.env/backups）
git fetch origin
git reset --hard origin/feat/deploy-scripts
git clean -fdx
# 容器以 root 生成的 __pycache__ 宿主机删不掉，用 docker 清
docker run --rm -v "$D":/w alpine sh -c 'find /w -type d -name __pycache__ -prune -exec rm -rf {} +'

# 重置假 MCDR
rm -rf /tmp/fake-mcdr/plugins/htcmc_auth /tmp/fake-mcdr/config/htcmc_auth/config.json

# 端口确认（应空闲）
ss -ltn | grep -E ':8000|:5433' && echo "⚠ 占用，先释放" || echo "✓ 端口空闲"
```

---

# ═══════════════════════ install 测试组 ═══════════════════════

## I-1. 一键部署

```bash
cd /home/yushen/opt/pch-e2e
TS=$(date +%Y%m%d-%H%M%S)
bash Scripts/install.sh --no-sync --yes --mcdr-root /tmp/fake-mcdr 2>&1 | tee logs/install-$TS.log
```

> `--no-sync` 用当前工作树部署；`--yes` 无人值守；`--mcdr-root` 跳过 MCDR 路径交互。

## I-2. install 验证（RUNBOOK 可执行清单）

```bash
cd /home/yushen/opt/pch-e2e
TS=$(date +%Y%m%d-%H%M%S)
{
  echo "=== [1] .env 三密钥（非占位）==="
  grep -E '^(POSTGRES_PASSWORD|JWT_SECRET|MCDR_SERVICE_TOKEN)=' .env
  echo "=== [2] override 含 healthcheck、无 --reload ==="
  grep -q healthcheck docker-compose.override.yml && echo "✓ healthcheck" || echo "✗ 缺 healthcheck"
  grep -q -- '--reload' docker-compose.override.yml && echo "✗ 残留 --reload" || echo "✓ 无 --reload"
  echo "=== [3] deploy.env ==="; cat .pchsystem.deploy.env
  echo "=== [4] 容器健康 ==="; docker compose ps
  echo "=== [5] healthz ==="; curl -sS http://127.0.0.1:8000/healthz; echo
  echo "=== [6] alembic current ==="; docker compose exec -T backend alembic current
  echo "=== [7] Frontend/dist ==="; ls Frontend/dist/index.html
  echo "=== [8] 插件目录（应无 __pycache__/tests）==="; ls /tmp/fake-mcdr/plugins/htcmc_auth/
  echo "=== [9] token 一致性 ==="
  env_tok=$(grep '^MCDR_SERVICE_TOKEN=' .env | cut -d= -f2-)
  cfg_tok=$(python3 -c "import json;print(json.load(open('/tmp/fake-mcdr/config/htcmc_auth/config.json'))['service_token'])" 2>/dev/null)
  [ "$env_tok" = "$cfg_tok" ] && echo "✓ 一致" || echo "✗ 不一致（env=$env_tok cfg=$cfg_tok）"
} 2>&1 | tee logs/install-verify-$TS.log
```

---

# ═══════════════════════ update 测试组 ═══════════════════════

> update 组默认用 `--no-sync`（用当前工作树，不 fetch 远端），便于离线/快速测重建矩阵与流程。

## U-1. update 已是最新（--no-sync）

```bash
cd /home/yushen/opt/pch-e2e
TS=$(date +%Y%m%d-%H%M%S)
bash Scripts/update.sh --no-sync 2>&1 | tee logs/update-nochange-$TS.log
```

**期望**：`当前工作树与部署记录一致...仍执行更新流程` → 幂等迁移 + 健康 ok，无容器 recreate。

## U-2. update 有 backend 变化（造 tag + --no-sync）

```bash
cd /home/yushen/opt/pch-e2e

# 造"未来版本"（本地 tag，不污染主仓）
git checkout -b test-new-version
echo "# e2e update test marker" >> Backend/app/main.py
git -c user.email=t@t -c user.name=test commit -am "test: backend tweak for update e2e"
git tag backend-v0.9.0
git checkout backend-v0.9.0

TS=$(date +%Y%m%d-%H%M%S)
bash Scripts/update.sh --no-sync 2>&1 | tee logs/update-backend-$TS.log

# 验证新代码生效
docker compose exec -T backend grep "e2e update test marker" /app/app/main.py && echo "✓ 新代码生效"
```

**期望日志**：`本地变更: ... → tag:backend-v0.9.0` + `Backend 代码变更 → force-recreate`。

## U-3.（可选）update --edge（fetch main）

```bash
cd /home/yushen/opt/pch-e2e
git checkout feat/deploy-scripts   # 离开 U-2 的造 tag
TS=$(date +%Y%m%d-%H%M%S)
bash Scripts/update.sh --edge 2>&1 | tee logs/update-edge-$TS.log
```

**期望**：fetch origin main → `已是最新（edge）` 或应用 main 新提交。

## U-4.（可选）dirty 保护

```bash
cd /home/yushen/opt/pch-e2e
echo "manual tweak" >> Backend/app/config.py
bash Scripts/update.sh --no-sync   # 期望：检测到本地改动，拒跑（除非 --force）
git checkout -- Backend/app/config.py
```

---

## 清理（测完）

```bash
cd /home/yushen/opt/pch-e2e
docker compose down -v
cd /home/yushen/opt && rm -rf pch-e2e   # 造的 tag/分支只在本 .git，删目录即清除
rm -rf /tmp/fake-mcdr
```

---

## PR 交付清单

测完把以下作为 PR 证据（日志路径从 `logs/` 摘录，RUNBOOK 插件执行后自动归档）：
1. **install**：`logs/install-*.log` + `logs/install-verify-*.log`（I-2 验证块 9 项全 ✓）
2. **update U-1**：`logs/update-nochange-*.log`（已是最新路径）
3. **update U-2**：`logs/update-backend-*.log`（`本地变更` + `force-recreate` 行，证明智能重建矩阵）
4. 已知边界（端口冲突、tag 模式 checkout 旧 tag、`--no-sync` 语义）

---

## 已知边界

- 端口 8000/5433 必须空闲（前置 §1）。
- `--no-sync`（install/update 共有）：用当前工作树，不拉取 / 不 checkout。开发与 e2e 用；生产部署用默认 tag 模式。
- update `--no-sync` 不 fetch：OLD=部署记录 commit，NEW=当前 HEAD；两者一致时仍跑迁移/健康（幂等确认），不 exit。
- 造的 tag/分支（`backend-v0.9.0` / `test-new-version`）只在测试目录 `.git`，删目录即清除。
