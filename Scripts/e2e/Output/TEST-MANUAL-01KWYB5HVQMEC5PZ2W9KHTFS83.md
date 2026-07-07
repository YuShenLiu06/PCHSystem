---
runme:
  document:
    relativePath: TEST-MANUAL.md
  session:
    id: 01KWYB5HVQMEC5PZ2W9KHTFS83
    updated: 2026-07-07 22:04:17+08:00
---

# PCHSystem 部署脚本 e2e 测试手册（RUNBOOK 可执行）

> 本手册每个 ```bash 代码块**自包含**、可被 RUNBOOK 插件**逐块执行**，插件自动捕获每块输出作为日志（**不再 tee 到本地文件**，故无 `TS`/`logs/` 相关语句）。
> **install 组与 update 组物理分离**（两大章节），便于分组执行 + 分组归档，测完一并作为 PR 证据。

## 测试环境

| 项 | 值 |
|---|---|
| 测试目录 | `/home/yushen/opt/pc***2e`（remote=主仓本地路径，未 push 的 commit 也能 fetch） |
| 假 MCDR | `/tmp/fake-mcdr`（空 `plugins/` + `config/htcmc_auth/`） |
| 端口 | 8000（后端）/ 5433（测试 pg，避开主仓 5432） |
| 日志 | RUNBOOK 插件逐块自动捕获（不落本地文件） |

---

## 0. 一次性环境准备

```bash
mkdir -p /tmp/fake-mcdr/{plugins,config/htcmc_auth}
echo "✓ 假 MCDR 就绪"

# Ran on 2026-07-07 21:57:07+08:00 for 267ms exited with 0
✓ 假 MCDR 就绪
```

---

## 1. 前置：重置（每次轮回前）

```bash
D=/home/yushen/opt/pc***2e
cd "$D"

# 停容器 + 删 volume（彻底清 pgdata）
docker compose down -v

# 容器以 root 运行，bind mount 写出的 __pycache__/*.pyc 等 root 属主文件宿主删不掉
# （否则下面 git clean -fdx 会报"权限不够"）。【先】用 root 容器把工作树所有权改回宿主用户。
docker run --rm -v "$D":/w alpine chown -R "$(id -u):$(id -g)" /w

# 同步主仓最新 + 清所有产物（node_modules/dist/.env/override/deploy.env/backups）
git fetch origin
git reset --hard origin/feat/deploy-scripts
git clean -fdx

# 重置假 MCDR
rm -rf /tmp/fake-mcdr/plugins/htcmc_auth /tmp/fake-mcdr/config/htcmc_auth/config.json

# 端口确认（应空闲）
ss -ltn | grep -E ':8000|:5433' && echo "⚠ 占用，先释放" || echo "✓ 端口空闲"

# Ran on 2026-07-07 21:57:13+08:00 for 529ms exited with 0
HEAD 现在位于 a0***73 fix(scripts): update.sh 缺 detect_compose，$COMPOSE 恒空致首个 dcc 调用 die
正删除 Backend/alembic/__pycache__/
正删除 Backend/alembic/versions/__pycache__/
正删除 Backend/app/__pycache__/
正删除 Backend/app/api/__pycache__/
正删除 Backend/app/core/__pycache__/
正删除 Backend/app/models/__pycache__/
正删除 Backend/app/repositories/__pycache__/
正删除 Backend/app/schemas/__pycache__/
正删除 Backend/app/services/__pycache__/
正删除 Backend/app/services/archive/__pycache__/
正删除 Backend/app/services/markdown_render/__pycache__/
正删除 Backend/app/services/parsing/__pycache__/
正删除 Backend/app/services/parsing/parsers/__pycache__/
正删除 Backend/app/services/parsing/translators/__pycache__/
✓ 端口空闲
```

---

# ═══════════════════════ install 测试组 ═══════════════════════

## I-1. 一键部署

```bash
cd /home/yushen/opt/pc***2e
bash Scripts/install.sh --no-sync --yes --mcdr-root /tmp/fake-mcdr

# Ran on 2026-07-07 21:57:18+08:00 for 1m 3.249s exited with 0
[21:57:19] [▶] OS / 权限探测
debian
[21:57:19] [▶] 检测/安装 Docker + Compose
[21:57:27] [WARN] GitHub 直连不通，尝试镜像源...
[21:57:27] [INFO]   探测镜像: ht*********************************om
[21:57:39] [INFO]   探测镜像: ht**********************************om
[21:57:51] [INFO]   探测镜像: ht****************om
[21:58:03] [INFO]   探测镜像: ht***************************om
[21:58:03] [INFO]   探测镜像: ht*********************************om
[21:58:04] [WARN] 所有镜像均不可达，回退直连（可能很慢或失败）
[21:58:04] [▶] 配置 Docker registry 加速
[21:58:04] [WARN] 无 root 权限，跳过 Docker registry 镜像加速配置
[21:58:04] [INFO] 跳过版本同步（--no-sync），使用当前工作树 (de****ed:a0***73)
[21:58:04] [▶] 生成 .env
[21:58:04] [INFO]   WEB_BASE_URL（!!PCH login 回链前缀，默认本机前端） → ht*****************73（--yes 自动采用）
[21:58:04] [INFO] .env 已生成（PO*************RD / JWT_SECRET / MCDR_SERVICE_TOKEN 已填强随机值）
[21:58:04] [▶] 生成生产 override（docker-compose.override.yml）
[21:58:04] [INFO] override.yml 已生成（去 --reload + 加 healthcheck）
[21:58:04] [▶] 构建 backend 镜像（自动透传 HTTPS_PROXY 加速 CJK 字体下载）
N[0000] Docker Compose is configured to build using Bake, but buildx isn't installed 
[+] Building 0.0s (0/1)                                                         [+] Building 0.1s (14/15)                                                                                                                       docker:default
 => => exporting layers                                                                                                                                   0.0s
 => => exporting manifest sh**56:91db245cb8d2f1621ff56c9eebcbec5f99a98bf909c08f30fe1767b8dff5cac3                                                         0.0s
 => => exporting config sh**56:1fa14b7ebe9d1145cf9ba94cadb9a21e0bf486058dbbf1c29227295bcec92228                                                           0.0s
 => => exporting attestation manifest sh**56:7e40581463f59f19fb816313b0ae02183c4[+] Building 0.1s (15/15) FINISHED                                                                                                              do**er:de***lt3e
 => [backend internal] load build definition from Dockerfile                                                                                              0.0s  
 => => transferring dockerfile: 1.**kB                                                                                                                    0.0s  
 => [backend internal] load metadata for do*****io/library/py**on:3.*****im                                                                               0.0s  
 => [backend internal] load .dockerignore                                                                                                                 0.0s
 => => transferring context: 135B                                                                                                                         0.0s
 => [backend 1/9] FROM do*****io/library/py**on:3.************56:ae52c5bef62a6bdd42cd1e8dffef86b9cd284bde9427da79839de7a4b983e7ca                         0.0s
 => => resolve do*****io/library/py**on:3.************56:ae52c5bef62a6bdd42cd1e8dffef86b9cd284bde9427da79839de7a4b983e7ca                                 0.0s
 => [backend internal] load build context                                                                                                                 0.0s
 => => transferring context: 1.**MB                                                                                                                       0.0s
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && ( wget -q -T 20 -O /usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regu  0.0s
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0s
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s
 => CACHED [backend 6/9] COPY app ./app                                                                                                                   0.0s
 => CACHED [backend 7/9] COPY alembic.ini ./                                                                                                              0.0s
 => CACHED [backend 8/9] COPY alembic ./alembic                                                                                                           0.0s
 => CACHED [backend 9/9] RUN pip install --no-cache-dir .                                                                                                 0.0s
 => [backend] exporting to image                                                                                                                          0.0s
 => => exporting layers                                                                                                                                   0.0s
 => => exporting manifest sh**56:91db245cb8d2f1621ff56c9eebcbec5f99a98bf909c08f30fe1767b8dff5cac3                                                         0.0s
 => => exporting config sh**56:1fa14b7ebe9d1145cf9ba94cadb9a21e0bf486058dbbf1c29227295bcec92228                                                           0.0s
 => => exporting attestation manifest sh**56:7e40581463f59f19fb816313b0ae02183c461d7227eb3dbf0da420a751064a8a                                             0.0s
 => => exporting manifest list sh**56:666a2fb1b531359fddd3e734630ae57ce71021763efe2d84ae31f9e6598f25d0                                                    0.0s
 => => naming to do*****io/library/pc***********nd:latest                                                                                                 0.0s
 => => unpacking to do*****io/library/pc***********nd:latest                                                                                              0.0s
 => [backend] resolving provenance for metadata file                                                                                                      0.0s
[+] Building 1/1
 ✔ backend  t                                                                                                                                          
[21:58:05] [▶] 启动 postgres + backend
[+] Running 3/4
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 3/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                        [+] Running 4/4                                                            
 ✔ Network pc***********lt       d                                                                                                                   
 ✔ Volume pc**********ta         d                                                                                                                   
 ✔ Container pc**************-1  y                                                                                                                   
 ✔ Container pc*************-1   d                                                                                                                   
[21:58:11] [INFO] 等待 postgres 健康（超时 120s）...
[21:58:11] [INFO] 等待 ht*************************hz 返回 200（超时 180s）...
[21:58:14] [▶] Alembic 迁移（upgrade head）
[21:58:14] [INFO] 迁移前快照: backups/pr***************************ql
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 00**************rs, create users schema and players table
INFO  [alembic.runtime.migration] Running upgrade 00**************rs -> 00*********wt, create auth_tokens and jwt_revocations
INFO  [alembic.runtime.migration] Running upgrade 00*********wt -> 00***********************at, add revoked_at to auth_tokens and partial index on active tokens
INFO  [alembic.runtime.migration] Running upgrade 00***********************at -> 00*******ts, create sheets schema and sheets/sheet_rows tables
INFO  [alembic.runtime.migration] Running upgrade 00*******ts -> 00**************ab, sheets collaboration: mode/status/claimant/delivered columns
INFO  [alembic.runtime.migration] Running upgrade 00**************ab -> 00**************ns, create notifications schema and notifications table
INFO  [alembic.runtime.migration] Running upgrade 00**************ns -> 00******************ib, sheet_row_contributors table for progress-mode multi-contributor
INFO  [alembic.runtime.migration] Running upgrade 00******************ib -> 00****************ty, contributed_qty column for sheet_row_contributors (per-player accumulated)
INFO  [alembic.runtime.migration] Running upgrade 00****************ty -> 00*****************le, sheets 三阶段生命周期 + 归档元数据。
INFO  [alembic.runtime.migration] Running upgrade 00*****************le -> 00***********************id, registry_id column for sheet_rows (MC registry id, namespace:path)
INFO  [alembic.runtime.migration] Running upgrade 00***********************id -> 00**********************id, last_sheet_id column for players (quick reopen last viewed sheet)
[21:58:15] [INFO] 迁移完成，当前版本: 00**********************id (head)
[21:58:15] [▶] 构建前端（best-effort）

added 265 packages in 4s

64 packages are looking for funding
  run `npm fund` for details

> fr**********.0 build
> vue-tsc -b && vite build

e v8**.2 g client environment for production...
**********ng (639) node_modules/element-plus/es/components/carousel/src/carousel
**********ng (1137) node_modules/element-plus/es/components/roving-focus-group/s
**********ng (1138) node_modules/element-plus/es/components/slider/src/button.vu
✓ 1656 modules transformed.
computing gzip size...
dist/l                            0.45 kB │ gzip:   0.29 kB
dist/assets/s           ****37 kB │ gzip:  47.61 kB
dist/assets/s                0.15 kB │ gzip:   0.15 kB
dist/assets/s                 0.51 kB │ gzip:   0.36 kB
dist/assets/s         0.72 kB │ gzip:   0.44 kB
dist/assets/s       0.81 kB │ gzip:   0.53 kB
dist/assets/s             1.80 kB │ gzip:   0.54 kB
dist/assets/s          2.81 kB │ gzip:   1.42 kB
dist/assets/s    6.97 kB │ gzip:   2.67 kB
dist/assets/s       15.20 kB │ gzip:   4.68 kB
dist/assets/s              45.00 kB │ gzip:  17.13 kB
dist/assets/s            ****36 kB │ gzip: 323.22 kB

[INVALID_ANNOTATION] A comment "/* #__PURE__ */" in "node_modules/@vueuse/core/dist/index.js" contains an annotation that Rolldown cannot interpret due to the position of the comment.
      ╭─[ node_modules/@vueuse/core/dist/in****js:3362:1 ]
      │
  /* #__PURE__ */
      │ ───────┬───────  
      │        ╰───────── comment ignored due to position
      │ 
      │ p: For more information on how to use pure annotations correctly, check the documentation: ht***************************************************re
──────╯

[plugin builtin:vite-reporter] 
(!) Some chunks are larger than 500 kB after minification. Consider:
- Using dynamic import() to code-split the application
- Use build.rolldownOptions.output.codeSplitting to improve chunking: ht*****************************************************ng
- Adjust chunk size limit for this warning via build.chunkSizeWarningLimit.
[INVALID_ANNOTATION] A comment "/* #__PURE__ */" in "node_modules/@vueuse/core/dist/index.js" contains an annotation that Rolldown cannot interpret due to the position of the comment.
      ╭─[ node_modules/@vueuse/core/dist/in****js:5780:23 ]
      │
  t defaultState = (/* #__PURE__ */ {
      │                       ───────┬───────  
      │                              ╰───────── comment ignored due to position
      │ 
      │ p: For more information on how to use pure annotations correctly, check the documentation: ht***************************************************re
──────╯

✓ built in 471ms
[21:58:21] [INFO] 前端构建完成: Frontend/dist/
[21:58:21] [▶] 部署 htcmc_auth 插件到 MCDR
[21:58:21] [WARN] 未找到依赖插件 uuid_api_remake（htcmc_auth 加载需要）→ ht********************************************ke
[21:58:21] [WARN] 未找到依赖插件 minecraft_data_api（htcmc_auth 加载需要）→ MCDR 插件市场 MinecraftDataAPI
[21:58:21] [INFO] 插件已拷贝: /tmp/fake-mcdr/plugins/htcmc_auth/
[21:58:21] [INFO]   插件访问后端的 URL（MCDR 与 backend 同机=12*****.1；同 docker 网络=服务名） → ht*****************00（--yes 自动采用）
[21:58:21] [INFO] config.json 已生成: /tmp/fa*****dr/config/htcmc_auth/co*******on（ap*************************00）
[21:58:21] [WARN] 请在游戏内/MCDR 控制台执行热重载（脚本无法可靠注入）:

    !!MCDR plugin reload htcmc_auth
[21:58:21] [▶] 持久化部署状态

[21:58:21] [INFO] ====================================== 安装完成 ======================================
[21:58:21] [INFO] 后端健康:   curl ht*************************hz   (期望 {"status":"ok"})
[21:58:22] [INFO] 迁移版本:   00**********************id (head)
[21:58:22] [INFO] 前端产物:   Frontend/dist/（用 nginx 等托管，反代 /api 到 :8000）
[21:58:22] [INFO] 插件已部署: /tmp/fake-mcdr/plugins/htcmc_auth/
[21:58:22] [INFO] 插件配置:   /tmp/fake-mcdr/config/htcmc_auth/config.json

[21:58:22] [WARN] 待办：
[21:58:22] [WARN]   - 在游戏内执行: !!MCDR plugin reload htcmc_auth
[21:58:22] [WARN]   - 确认依赖插件已装: uuid_api_remake + minecraft_data_api
[21:58:22] [WARN]   - 检查 .env 的 WEB_BASE_URL 与密钥（已生成强随机值）
[21:58:22] [INFO] ======================================================================================

```

> `--no-sync` 用当前工作树部署；`--yes` 无人值守；`--mcdr-root` 跳过 MCDR 路径交互。

## I-2. install 验证（RUNBOOK 可执行清单）

```bash
cd /home/yushen/opt/pc***2e
{
  echo "=== [1] .env 三密钥（非占位）==="
  grep -E '^(PO*************RD|JWT_SECRET|MCDR_SERVICE_TOKEN)=' .env
  echo "=== [2] override 含 healthcheck、无 --reload ==="
  grep -q healthcheck docker-compose.override.yml && echo "✓ healthcheck" || echo "✗ 缺 healthcheck"
  grep -q -- '--reload' docker-compose.override.yml && echo "✗ 残留 --reload" || echo "✓ 无 --reload"
  echo "=== [3] deploy.env ==="; cat .pchsystem.deploy.env
  echo "=== [4] 容器健康 ==="; docker compose ps
  echo "=== [5] healthz ==="; curl -sS ht*************************hz; echo
  echo "=== [6] alembic current ==="; docker compose exec -T backend alembic current
  echo "=== [7] Frontend/dist ==="; ls Frontend/dist/index.html
  echo "=== [8] 插件目录（应无 __pycache__/tests）==="; ls /tmp/fake-mcdr/plugins/htcmc_auth/
  echo "=== [9] token 一致性 ==="
  env_tok=$(grep '^MCDR_SERVICE_TOKEN=' .env | cut -d= -f2-)
  cf*************n3 -c "import json;print(json.load(open('/tmp/fake-mcdr/config/htcmc_auth/config.json'))['service_token'])" 2>/dev/null)
  [ "$env_tok" = "$cfg_tok" ] && echo "✓ 一致" || echo "✗ 不一致（env=$env_tok cfg=$cfg_tok）"
}

# Ran on 2026-07-07 21:58:33+08:00 for 702ms exited with 0
=== [1] .env 三密钥（非占位）===
POSTGRES_PASSWORD=<redacted 测试密钥>
JWT_SECRET=<redacted 测试密钥>
MCDR_SERVICE_TOKEN=<redacted 测试密钥>
=== [2] override 含 healthcheck、无 --reload ===
✓ healthcheck
✗ 残留 --reload
=== [3] deploy.env ===
# PCHSystem 部署状态（install.sh/update.sh 自动生成，勿手改）
PC***************ht*****************00
PC*********************73
PCH_MCDR_ROOT=/tmp/fake-mcdr
PCH_DEPLOY_STRATEGY=tag
PC**************************21:58:21
PC****************=1
PC***********************ed:a0***73
=== [4] 容器健康 ===
NAME                 IMAGE             COMMAND                   SERVICE    CREATED          STATUS                    PORTS
pc*************-1    pc***********nd   "uvicorn app.main:ap…"   backend    29 seconds ago   Up 23 seconds (healthy)   0.***.0:8000->8000/tcp, [::]:8000->8000/tcp
pc**************-1   po****es:16       "docker-entrypoint.s…"   postgres   29 seconds ago   Up 28 seconds (healthy)   12*****.1:5433->5432/tcp
=== [5] healthz ===
{"status":"ok"}
=== [6] alembic current ===
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
00**********************id (head)
=== [7] Frontend/dist ===
Frontend/dist/index.html
=== [8] 插件目录（应无 __pycache__/tests）===
config.json.example  ********th  mcdreforged.plugin.json
=== [9] token 一致性 ===
✓ 一致

```

---

# ═══════════════════════ update 测试组 ═══════════════════════

> update 组默认用 `--no-sync`（用当前工作树，不 fetch 远端），便于离线/快速测重建矩阵与流程。

## U-1. update 已是最新（--no-sync）

```bash
cd /home/yushen/opt/pc***2e
bash Scripts/update.sh --no-sync

# Ran on 2026-07-07 21:58:54+08:00 for 1.552s exited with 0
[21:58:55] [▶] 跳过远端拉取（--no-sync），用当前工作树
[21:58:55] [INFO] 当前工作树与部署记录一致（de****ed:a0***73），--no-sync 仍执行更新流程（迁移/插件/健康）
[21:58:55] [INFO] 跳过 checkout（--no-sync）
[21:58:55] [INFO] 无 Backend / compose 变更，跳过容器操作
[21:58:55] [▶] Alembic 迁移（upgrade head，幂等）
[21:58:55] [INFO] 迁移前快照: backups/pr**************************ql
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
[21:58:55] [INFO] 迁移完成，当前版本: 00**********************id (head)
[21:58:55] [INFO] 无 htcmc_auth 插件变更
[21:58:55] [▶] 健康验证
[21:58:55] [INFO] 等待 ht*************************hz 返回 200（超时 60s）...

[21:58:55] [INFO] ====================================== 更新完成 ======================================
[21:58:55] [INFO] 版本: de****ed:a0***73 → de****ed:a0***73
[21:58:56] [INFO] 迁移: 00**********************id (head)
[21:58:56] [INFO] 健康: curl ht*************************hz → ok
[21:58:56] [INFO] ======================================================================================
```

**期望**：`当前工作树与部署记录一致...仍执行更新流程` → 幂等迁移 + 健康 ok，无容器 recreate。

## U-2. update 有 backend 变化（造 tag + --no-sync）

```bash
cd /home/yushen/opt/pc***2e

# 造"未来版本"（本地 tag，不污染主仓）
git checkout -b test-new-version
echo "# e2e update test marker" >> Backend/app/main.py
git -c user.email=t@t -c user.name=test commit -am "test: backend tweak for update e2e"
git tag ba**********.0
git checkout ba**********.0

bash Scripts/update.sh --no-sync

# 验证新代码生效
docker compose exec -T backend grep "e2e update test marker" /app/app/main.py && echo "✓ 新代码生效"

# Ran on 2026-07-07 21:59:30+08:00 for 2.815s exited with 0
切换到一个新分支 'test-new-version'
[test-new-version eb***d8] test: backend tweak for update e2e
 1 file changed, 1 insertion(+)
注意：正在切换到 'ba**********.0'。

您正处于分离头指针状态。您可以查看、做试验性的修改及提���，并且您可以在切换
回一个分支时，丢弃在此状态下所做的提交而不对分支造成影响。

如果您想要通过创建分支来保留在此状态下所做的提交，您可以通过在 switch 命令
中添加参数 -c 来实现���现在或稍后）。例如：

  git switch -c <新分支名>

或者撤销此操作：

  git switch -

通过将配置变�� advice.detachedHead 设置为 false 来关闭此建议

HEAD 目前位于 eb***d8 test: backend tweak for update e2e
[21:59:30] [▶] 跳过远端拉取（--no-sync），用当前工作树
[21:59:30] [INFO] 本地变更: de****ed:a0***73 → tag:ba**********.0（--no-sync，未 fetch 远端）
[21:59:30] [INFO] 跳过 checkout（--no-sync）
[21:59:30] [▶] Backend 代码变更 → force-recreate（mount 策略，秒级，无需 rebuild）
[+] Running 1/2
 ✔ Container pc**************-1  g                                        [+] Running 1/2                                                            
 ✔ Container pc**************-1  g                                        [+] Running 1/2                                                            
 ✔ Container pc**************-1  g                                        [+] Running 1/2                                                            
 ✔ Container pc**************-1  g                                        [+] Running 1/2                                                            
 ⠙ Container pc**************-1  Waiting                                        [+] Running 1/2                                                            
 ⠹ Container pc**************-1  Waiting                                        [+] Running 1/2                                                            
 ⠸ Container pc**************-1  Waiting                                        [+] Running 1/2                                                            
 ⠼ Container pc**************-1  Waiting                                        [+] Running 1/2                                                            
 ⠴ Container pc**************-1  Waiting                                        [+] Running 1/2                                                            
 ✔ Container pc**************-1  y                                        [+] Running 2/2                                                            
 ✔ Container pc**************-1  y                                                                                                                   
 ✔ Container pc*************-1   d                                                                                                                   
[21:59:31] [▶] Alembic 迁移（upgrade head，幂等）
[21:59:31] [INFO] 迁移前快照: backups/pr**************************ql
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
[21:59:32] [INFO] 迁移完成，当前版本: 00**********************id (head)
[21:59:32] [INFO] 无 htcmc_auth 插件变更
[21:59:32] [▶] 健康验证
[21:59:32] [INFO] 等待 ht*************************hz 返回 200（超时 60s）...

[21:59:32] [INFO] ====================================== 更新完成 ======================================
[21:59:32] [INFO] 版本: de****ed:a0***73 → tag:ba**********.0
[21:59:33] [INFO] 迁移: 00**********************id (head)
[21:59:33] [INFO] 健康: curl ht*************************hz → ok
[21:59:33] [INFO] ======================================================================================
# e2e update test marker
✓ 新代码生效

```

**期望日志**：`本地变更: ... → tag:ba**********.0` + `Backend 代码变更 → force-recreate`。

## U-3.（可选）update --edge（fetch main）

```bash
cd /home/yushen/opt/pc***2e
git checkout feat/deploy-scripts   # 离开 U-2 的造 tag
bash Scripts/update.sh --edge
```

**期望**：fetch origin main → `已是最新（edge）` 或应用 main 新提交。

## U-4.（可选）dirty 保护

```bash
cd /home/yushen/opt/pc***2e
echo "manual tweak" >> Backend/app/config.py
bash Scripts/update.sh --no-sync   # 期望：检测到本地改动，拒跑（除非 --force）
git checkout -- Backend/app/config.py
```

---

## 清理（测完）

```bash
cd /home/yushen/opt/pc***2e
docker compose down -v
cd /home/yushen/opt && rm -rf pc***2e   # 造的 tag/分支只在本 .git，删目录即清除
rm -rf /tmp/fake-mcdr
```

---

## PR 交付清单

测完把以下作为 PR 证据（RUNBOOK 插件逐块执行后，日志由插件自动归档，按块摘录）：

1. **in***ll**：I-1 部署块 + I-2 验证块（9 项全 ✓）
2. **update U-1**：已是最新路径
3. **update U-2**：`本地变更` + `force-recreate` 行（证明智能重建矩阵）
4. 已知边界（端口冲突、tag 模式 checkout 旧 tag、`--no-sync` 语义）

---

## 已知边界

- 端口 8000/5433 必须空闲（前置 §1）。
- §1 重置必须先 `docker ... chown -R` 把工作树所有权改回宿主用户：backend 容器以 root 运行，bind mount 写出的 `__pycache__/*.pyc` 是 root 属主，宿主 `git clean -fdx`（`-x` 清 gitignored）会因 unlink 失败报"权限不够"。
- `--no-sync`（install/update 共有）：用当前工作树，不拉取 / 不 checkout。开发与 e2e 用；生产部署用默认 tag 模式。
- update `--no-sync` 不 fetch：OLD=部署记录 commit，NEW=当前 HEAD；两者一致时仍跑迁移/健康（幂等确认），不 exit。
- 造的 tag/分支（`ba**********.0` / `test-new-version`）只在测试目录 `.git`，删目录即清除。
