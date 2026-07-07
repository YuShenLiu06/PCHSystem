# PCHSystem 部署脚本 e2e 测试手册

> 本手册是部署脚本（`install.sh` / `update.sh` / `lib/common.sh`）的**端到端测试规范**，作为 PR 评审证据。
> 每次测试产出日志归档到 `logs/`（本地 gitignored），关键结果填入各场景的验证清单。

---

## 测试环境

| 项 | 值 |
|---|---|
| 测试目录 | `/home/yushen/opt/pch-e2e`（从主仓本地 clone，`remote=主仓路径`，未 push 的 commit 也能 fetch 到） |
| 假 MCDR | `/tmp/fake-mcdr`（空 `plugins/` + `config/htcmc_auth/`） |
| 端口 | 8000（后端）/ 5433（测试 pg，避开主仓 5432） |
| 日志归档 | `logs/<scenario>-<timestamp>.log`（PR 证据） |

```bash
# 建假 MCDR（首次）
mkdir -p /tmp/fake-mcdr/{plugins,config/htcmc_auth}
```

---

## 日志暴露（PR 强制要求）

**所有测试命令必须 `2>&1 | tee logs/xxx.log`**，跑完把日志路径贴进 PR 描述。reviewer 可直接读日志文件，无需手动复制粘贴。

```bash
cd /home/yushen/opt/pch-e2e
TS=$(date +%Y%m%d-%H%M%S)        # 时间戳归档，保留每次测试日志

# install
bash Scripts/install.sh --no-sync --yes --mcdr-root /tmp/fake-mcdr 2>&1 | tee logs/install-$TS.log

# update
bash Scripts/update.sh 2>&1 | tee logs/update-$TS.log

# 只看报错
grep -niE '错误|失败|error|✗|traceback|die|不可用' logs/install-$TS.log
```

> 为什么不用 VSCode 终端直接看：默认 scrollback 仅 1000 行，install 全流程（build+迁移+npm）会截断早期日志；`tee` 到文件不受 scrollback 限制。

---

## 前置 0：重置测试目录 + 腾端口（每次轮回前）

```bash
D=/home/yushen/opt/pch-e2e

# 1. 停容器 + 删 volume（彻底清 pgdata）
cd "$D" && docker compose down -v

# 2. 同步主仓最新 + 清所有产物（node_modules/dist/.env/override/deploy.env/backups）
git -C "$D" fetch origin
git -C "$D" reset --hard origin/feat/deploy-scripts
git -C "$D" clean -fdx
# 容器以 root 生成的 __pycache__ 宿主机删不掉，用 docker 清：
docker run --rm -v "$D":/w alpine sh -c 'find /w -type d -name __pycache__ -prune -exec rm -rf {} +'

# 3. 重置假 MCDR
rm -rf /tmp/fake-mcdr/plugins/htcmc_auth /tmp/fake-mcdr/config/htcmc_auth/config.json

# 4. 建日志目录（首次）+ 本地 ignore
mkdir -p "$D/logs"
grep -qx 'logs/' "$D/.git/info/exclude" 2>/dev/null || echo 'logs/' >> "$D/.git/info/exclude"

# 5. 端口确认（应空闲）
ss -ltn | grep -E ':8000|:5433' && echo "⚠ 占用，先释放" || echo "✓ 空闲"
```

---

## 测试 1：install（模拟玩家 clone 后一键部署）

```bash
cd /home/yushen/opt/pch-e2e
TS=$(date +%Y%m%d-%H%M%S)
bash Scripts/install.sh --no-sync --yes --mcdr-root /tmp/fake-mcdr 2>&1 | tee logs/install-$TS.log
```

> `--no-sync`：用当前工作树（feat/deploy-scripts）部署，不 checkout 到无 `Scripts/` 的旧 tag。
> `--yes`：无人值守；`--mcdr-root`：跳过 MCDR 路径交互。

**验证清单（PR 评审逐项确认）**：
- [ ] `.env` 生成，三密钥非占位：`grep -E 'POSTGRES_PASSWORD|JWT_SECRET|MCDR_SERVICE_TOKEN' .env`
- [ ] `docker-compose.override.yml` 含 `healthcheck`、无 `--reload`
- [ ] `.pchsystem.deploy.env` 存在
- [ ] 容器健康：`docker compose ps`（postgres healthy / backend healthy）
- [ ] `curl http://127.0.0.1:8000/healthz` → `{"status":"ok"}`
- [ ] `docker compose exec backend alembic current` → `0011_players_last_sheet_id`
- [ ] `Frontend/dist/` 存在
- [ ] `/tmp/fake-mcdr/plugins/htcmc_auth/` 含插件、无 `__pycache__`/`tests`
- [ ] `/tmp/fake-mcdr/config/htcmc_auth/config.json` 的 `service_token` 与 `.env` 的 `MCDR_SERVICE_TOKEN` 一致

---

## 测试 2：update —— 已是最新（无更新路径）

```bash
cd /home/yushen/opt/pch-e2e
TS=$(date +%Y%m%d-%H%M%S)
bash Scripts/update.sh 2>&1 | tee logs/update-nochange-$TS.log
```

**期望**：日志出现 `已是最新` 并退出 0（install 装的 HEAD == update 的 latest，OLD_SHA == NEW_SHA）。

---

## 测试 3：update —— 有 backend 变化（force-recreate 路径）

模拟"发了新版本"，在测试目录本地造含 backend 改动的新 tag（不污染主仓）：

```bash
cd /home/yushen/opt/pch-e2e
git checkout -b test-new-version
echo "# e2e update test marker" >> Backend/app/main.py
git -c user.email=t@t -c user.name=test commit -am "test: backend tweak for update e2e"
git tag backend-v0.9.0
git checkout feat/deploy-scripts
TS=$(date +%Y%m%d-%H%M%S)
bash Scripts/update.sh 2>&1 | tee logs/update-backend-$TS.log
```

**期望日志**：
- `版本变更: ... → tag:backend-v0.9.0`
- `Backend 代码变更 → force-recreate`
- 跑 `alembic upgrade head`（幂等）；前端/插件无变更 → 跳过
- `curl :8000/healthz → ok`

**验证**：
- [ ] `.pchsystem.deploy.env` 更新为 `PCH_DEPLOY_VERSION=tag:backend-v0.9.0`
- [ ] backend 容器已 force-recreate（容器 ID 变化）
- [ ] `docker compose exec backend grep "e2e update test marker" /app/app/main.py` 命中（新代码生效）

---

## 测试 4（可选）：update `--edge`

```bash
cd /home/yushen/opt/pch-e2e
git checkout feat/deploy-scripts   # 离开测试 3 的造 tag
TS=$(date +%Y%m%d-%H%M%S)
bash Scripts/update.sh --edge 2>&1 | tee logs/update-edge-$TS.log
```

期望：fetch origin main → 若 main 无新 commit 则 `已是最新（edge）`。

---

## 测试 5（可选）：dirty 保护

```bash
cd /home/yushen/opt/pch-e2e
echo "manual tweak" >> Backend/app/config.py
bash Scripts/update.sh
# 期望：检测到本地改动，拒跑（除非 --force）
git checkout -- Backend/app/config.py
```

---

## 清理（测完）

```bash
cd /home/yushen/opt/pch-e2e
docker compose down -v          # 停容器 + 删 pgdata
# 测试目录里的造 tag/分支（backend-v0.9.0 / test-new-version）只在本 .git，删目录即清除
cd /home/yushen/opt && rm -rf pch-e2e
rm -rf /tmp/fake-mcdr
```

---

## PR 交付清单

提交 PR 时，在描述里附：
1. 各场景日志路径或关键摘要（从 `logs/` 摘录）
2. 测试 1 验证清单逐项 ✓
3. 测试 3 的 `版本变更` + `force-recreate` 日志行（证明智能重建矩阵生效）
4. 已知边界（端口冲突、tag 模式 checkout 旧 tag 等）

---

## 已知边界

- 端口 8000/5433 必须空闲（前置 0）。
- install `--no-sync` 用当前工作树；默认 tag 模式会 checkout 到无 `Scripts/` 的旧 tag，开发/测试场景用 `--no-sync` 规避。
- 所有造的 tag/分支在测试目录本地 `.git`，不影响主仓。
