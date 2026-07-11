---
runme:
  document:
    relativePath: TEST-MANUAL.md
  session:
    id: 01KX8TE9TSBXV09KESPKHT82W3
    updated: 2026-07-11 23:55:49+08:00
---

# PCHSystem 部署脚本 e2e 测试手册（RUNBOOK 可执行）

> 本手册每个 ```bash 代码块**自包含**、可被 RUNBOOK 插件**逐块执行**，插件自动捕获每块输出作为日志（**不再 tee 到本地文件**，故无 `TS`/`logs/` 相关语句）。
> **install 组与 update 组物理分离**（两大章节），便于分组执行 + 分组归档，测完一并作为 PR 证据。

## 测试环境

| 项 | 值 |
|---|---|
| 测试目录 | `/home/yushen/opt/pchsandbox`（独立 worktree，project=`pchsandbox`，__与生产 `pchsystem` 共存__） |
| 假 MCDR | `/tmp/fake-mcdr`（空 `plugins/` + `config/htcmc_auth/`） |
| 端口 | __8100（后端）/ 15433（pg，loopback）/ 6173（web）__ —— 偏移避让生产 8000/5433/5173 |
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

# Ran on 2026-07-11 23:51:12+08:00 for 414ms exited with 0
✓ pchsandbox worktree 就绪
✓ 假 MCDR 就绪
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

# Ran on 2026-07-11 23:51:14+08:00 for 954ms exited with 0
N[0000] Warning: No resource found to remove for project "pchsandbox". 
HEAD 现在位于 cf***0c te****2e): §0 用 -e 判定 worktree（-d 对 worktree .git 文件恒假）
正删除 .env
正删除 docker-compose.override.yml
✓ 端口空闲
```

---

# ═══════════════════════ install 测试组 ═══════════════════════

## I-1. 一键部署

```bash
cd /home/yushen/opt/pchsandbox
# 端口/镜像源用「命令前缀内联 env」传给 install.sh 进程（勿用独立 export 行：RUNBOOK 插件会把它当输入项拦截/合并 → 值变 "81***********73" 或丢失 → .env 落默认端口 → 撞生产 5433）。
# install.sh 内部自设 PIP_INDEX_URL=清华源，此处只需给四项。
BA*************00 PG*********33 WE*********73 NPM_REGISTRY=ht**************************om \
bash Scripts/install.sh --no-sync --yes \
  --mcdr-root /tmp/fake-mcdr --mcdr-api-url ht*****************00

# Ran on 2026-07-11 23:51:24+08:00 for 25.043s exited with 0
[23:51:25] [▶] OS / 权限探测
debian
[23:51:25] [▶] 检测/安装 Docker + Compose
[23:51:33] [WARN] GitHub 直连不通，尝试镜像源...
[23:51:33] [INFO]   探测镜像: ht*********************************om
[23:51:35] [INFO]   选用镜像: ht*********************************om
[23:51:35] [▶] 配置 Docker registry 加速
[23:51:35] [WARN] 无 root 权限，跳过 Docker registry 镜像加速配置
[23:51:35] [INFO] 跳过版本同步（--no-sync），使用当前工作树 (de****ed:cf***0c)
[23:51:35] [▶] 生成 .env
[23:51:35] [INFO]   WEB_BASE_URL（!!PCH login 回链前缀，默认本机前端） → ht*****************73（--yes 自动采用）
[23:51:35] [INFO] .env 已生成（PO*************RD / JWT_SECRET / MCDR_SERVICE_TOKEN 已填强随机值）
[23:51:35] [▶] 生成生产 override（docker-compose.override.yml）
[23:51:35] [INFO] override.yml 已生成（去 --reload + 加 healthcheck）
[23:51:35] [▶] 构建 backend 镜像（自动透传 HTTPS_PROXY 加速 CJK 字体下载）
N[0000] Docker Compose is configured to build using Bake, but buildx isn't installed 
[+] Building 0.0s (0/1)                                                         [+] Building 0.2s (1/2)                                                                                                                         docker:default
 => [backend internal] load build definition from Dockerfile                                                                                              0.0s
[+] Building 0.3s (1/2)                                                                                                                         docker:default
 => [backend internal] load build definition from Dockerfile                                                                                              0.0s
[+] Building 0.5s (1/2)                                                                                                                         docker:default
 => [backend internal] load build definition from Dockerfile                                                                                              0.0s
[+] Building 0.6s (1/2)                                                                                                                         docker:default
 => [backend internal] load build definition from Dockerfile                                                                                              0.0s
[+] Building 0.8s (1/2)                                                                                                                         docker:default
 => [backend internal] load build definition from Dockerfile                                                                                              0.0s
[+] Building 0.9s (2/2)                                                                                                                         docker:default
 => [backend internal] load build definition from Dockerfile                                                                                              0.0s
[+] Building 1.0s (14/15)                                                                                                                       docker:default
 => => exporting layers                                                                                                                                   0.0s
 => => exporting manifest sh**56:<redacted>                                                         0.0s
 => => exporting config sh**56:<redacted>                                                           0.0s
 => => exporting attestation manifest sh**56:bbb684168ffc163b0738a467b7f57ca1581[+] Building 1.0s (15/15) FINISHED                                                                                                              do**er:de***lt74
 => [backend internal] load build definition from Dockerfile                                                                                              0.0s  
 => => transferring dockerfile: 2.**kB                                                                                                                    0.0s  
 => [backend internal] load metadata for do*****io/library/py**on:3.*****im                                                                               0.9s  
 => [backend internal] load .dockerignore                                                                                                                 0.0s
 => => transferring context: 135B                                                                                                                         0.0s
 => [backend 1/9] FROM do*****io/library/py**on:3.************56:<redacted>                         0.0s
 => => resolve do*****io/library/py**on:3.************56:<redacted>                                 0.0s
 => [backend internal] load build context                                                                                                                 0.0s
 => => transferring context: 4.**kB                                                                                                                       0.0s
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0s
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s
 => CACHED [backend 6/9] COPY app ./app                                                                                                                   0.0s
 => CACHED [backend 7/9] COPY alembic.ini ./                                                                                                              0.0s
 => CACHED [backend 8/9] COPY alembic ./alembic                                                                                                           0.0s
 => CACHED [backend 9/9] RUN if [ -n "ht************************************le" ]; then pip config set global.index-url "ht**************************du.  0.0s
 => [backend] exporting to image                                                                                                                          0.0s
 => => exporting layers                                                                                                                                   0.0s
 => => exporting manifest sh**56:<redacted>                                                         0.0s
 => => exporting config sh**56:<redacted>                                                           0.0s
 => => exporting attestation manifest sh**56:<redacted>                                             0.0s
 => => exporting manifest list sh**56:<redacted>                                                    0.0s
 => => naming to docker.io/library/pchsandbox-backend:latest                                                                                              0.0s
 => => unpacking to docker.io/library/pchsandbox-backend:latest                                                                                           0.0s
 => [backend] resolving provenance for metadata file                                                                                                      0.0s
[+] Building 1/1
 ✔ backend  t                                                                                                                                          
[23:51:36] [▶] 构建 web 镜像（前端，容器内 npm build；NPM_REGISTRY/代理经 build-arg 透传）
N[0000] Docker Compose is configured to build using Bake, but buildx isn't installed 
[+] Building 0.0s (0/1)                                                         [+] Building 0.2s (1/2)                                                                                                                         docker:default
 => [web internal] load build definition from Dockerfile                                                                                                  0.0s
[+] Building 0.3s (1/2)                                                                                                                         docker:default
 => [web internal] load build definition from Dockerfile                                                                                                  0.0s
[+] Building 0.5s (1/2)                                                                                                                         docker:default
 => [web internal] load build definition from Dockerfile                                                                                                  0.0s
[+] Building 0.6s (1/2)                                                                                                                         docker:default
 => [web internal] load build definition from Dockerfile                                                                                                  0.0s
[+] Building 0.7s (2/3)                                                                                                                         docker:default
 => [web internal] load build definition from Dockerfile                                                                                                  0.0s
[+] Building 0.9s (3/5)                                                                                                                         docker:default
 => [web internal] load build definition from Dockerfile                                                                                                  0.0s
 => => transferring dockerfile: 1.**kB                                                                                                                    0.0s
 => [web] resolve image config for do********ge://do*****io/docker/do******le:1 **************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************:1@****56:<redacted>                     0.0s
 => => resolve do*****io/docker/do******le:1@****56:<redacted>                                      0.0s
 => [web internal] load metadata for docker.io/library/nginx:stable-alpine                                                                                0.5s
 => [web internal] load metadata for do*****io/library/node:22*****ne                                                                                     0.5s
 => [web internal] load .dockerignore                                                                                                                     0.0s
 => => transferring context: 233B                                                                                                                         0.0s
 => [web builder 1/6] FROM do*****io/library/node:22************56:<redacted>                       0.0s
 => => resolve do*****io/library/node:22************56:<redacted>                                   0.0s
 => [web internal] load build context                                                                                                                     0.0s
 => => transferring context: 2.**kB                                                                                                                       0.0s
 => [web st***-1 1/3] FROM do*****io/library/nginx:st****************56:<redacted>                  0.0s
 => => resolve do*****io/library/nginx:st****************56:<redacted>                              0.0s
 => CACHED [web builder 2/6] WORKDIR /app                                                                                                                 0.0s
 => CACHED [web builder 3/6] COPY package.json package-lock.json* ./                                                                                      0.0s
 => CACHED [web builder 4/6] RUN if [ -n "ht**************************om" ]; then npm config set registry "ht**************************om"; fi  && (npm   0.0s
 => CACHED [web builder 5/6] COPY . .                                                                                                                     0.0s
 => CACHED [web builder 6/6] RUN npm run build                                                                                                            0.0s
 => CACHED [web st***-1 2/3] COPY --from=builder /app/dist /usr/share/nginx/html                                                                          0.0s
 => CACHED [web st***-1 3/3] COPY nginx.conf /etc/nginx/conf.d/default.conf                                                                               0.0s
 => [web] exporting to image                                                                                                                              0.0s
 => => exporting layers                                                                                                                                   0.0s
 => => exporting manifest sh**56:<redacted>                                                         0.0s
 => => exporting config sh**56:<redacted>                                                           0.0s
 => => exporting attestation manifest sh**56:<redacted>                                             0.0s
 => => exporting manifest list sh**56:<redacted>                                                    0.0s
 => => naming to docker.io/library/pchsandbox-web:latest                                                                                                  0.0s
 => => unpacking to docker.io/library/pchsandbox-web:latest                                                                                               0.0s
 => [web] resolving provenance for metadata file                                                                                                          0.0s
[+] Building 1/1
 ✔ web  t                                                                                                                                              
[23:51:38] [▶] 启动 postgres + backend + web
[+] Running 2/3
 ✔ Network pchsandbox_default       d                                                                                                                
[+] Running 4/5n*********ta         d                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                
[+] Running 4/5h****************-1  Starting                                    
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Starting                                    
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  Waiting                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  y                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  y                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 4/5h****************-1  y                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
[+] Running 5/5*****************-1  y                                     
 ✔ Network pchsandbox_default       d                                                                                                                 
 ✔ Volume pchsandbox_pgdata         d                                                                                                                 
 ✔ Container pc*****************-1  y                                                                                                                
 ✔ Container pc****************-1   d                                                                                                                
 ✔ Container pc************-1       d                                                                                                                
[23:51:44] [INFO] 等待 postgres 健康（超时 120s）...
[23:51:44] [INFO] 等待 ht*************************hz 返回 200（超时 180s）...
[23:51:47] [▶] Alembic 迁移（upgrade head）
[23:51:47] [INFO] 迁移前快照: backups/pr***************************ql
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
INFO  [alembic.runtime.migration] Running upgrade 00**********************id -> 00*********************hy, 子物品嵌套行（Option 2）
INFO  [alembic.runtime.migration] Running upgrade 00*********************hy -> 00*******************at, qty_per_unit 改为浮点（支持 0.5 等非整数单位用量）
[23:51:49] [INFO] 迁移完成，当前版本: 00*******************at (head)
[23:51:49] [INFO] web profile 启用：前端由 web 镜像构建，无需宿主 Node
[23:51:49] [▶] 部署 htcmc_auth 插件到 MCDR
[23:51:49] [WARN] 未找到依赖插件 uuid_api_remake（htcmc_auth 加载需要）→ ht********************************************ke
[23:51:49] [WARN] 未找到依赖插件 minecraft_data_api（htcmc_auth 加载需要）→ MCDR 插件市场 MinecraftDataAPI
[23:51:49] [INFO] 插件已拷贝: /tmp/fake-mcdr/plugins/htcmc_auth/
[23:51:49] [INFO] config.json 已生成: /tmp/fa*****dr/config/htcmc_auth/co*******on（ap*************************00）
[23:51:49] [WARN] 请在游戏内/MCDR 控制台执行热重载（脚本无法可靠注入）:

    !!MCDR plugin reload htcmc_auth
[23:51:49] [▶] 持久化部署状态

[23:51:49] [INFO] ====================================== 安装完成 ======================================
[23:51:49] [INFO] 后端健康:   curl ht*************************hz   (期望 {"status":"ok"})
[23:51:49] [INFO] 迁移版本:   00*******************at (head)
[23:51:49] [INFO] 前端 Web:    http://<本机IP>:6173（compose web 服务：托管 dist + 反代 /api 到 backend）
[23:51:49] [INFO] 插件已部署: /tmp/fake-mcdr/plugins/htcmc_auth/
[23:51:49] [INFO] 插件配置:   /tmp/fake-mcdr/config/htcmc_auth/config.json

[23:51:49] [WARN] 待办：
[23:51:49] [WARN]   - 在游戏内执行: !!MCDR plugin reload htcmc_auth
[23:51:49] [WARN]   - 确认依赖插件已装: uuid_api_remake + minecraft_data_api
[23:51:49] [WARN]   - 检查 .env：WEB_BASE_URL 需为玩家可访问的前端地址（单机 + web 默认 5173 已对齐；用域名/反代则改成真实 URL 后 docker compose restart backend）
[23:51:49] [INFO] ======================================================================================

```

> `--no-sync` 用当前工作树（=sandbox-test 快照）；`--yes` 无人值守；`--mcdr-root` 跳过 MCDR 交互；`--mcdr-api-url` 指向沙盒 backend `:8100`（默认拓扑推断返回 `:8000`=生产，必须覆盖）。

## I-2. install 验证（RUNBOOK 可执行清单，14 项）

```bash
cd /home/yushen/opt/pchsandbox
{
  echo "=== [1] .env 三密钥（非占位）==="
  grep -E '^(PO*************RD|JWT_SECRET|MCDR_SERVICE_TOKEN)=' .env
  echo "=== [2] override 含 healthcheck、无 --reload ==="
  grep -q healthcheck docker-compose.override.yml && echo "✓ healthcheck" || echo "✗ 缺 healthcheck"
  grep -q -- '--reload' docker-compose.override.yml && echo "✗ 残留 --reload" || echo "✓ 无 --reload"
  echo "=== [3] deploy.env ==="; cat .pchsystem.deploy.env
  echo "=== [4] 容器健康（project=pchsandbox）==="; docker compose -p pchsandbox ps
  echo "=== [5] he***hz（:8100）==="; curl -sS ht*************************hz; echo
  echo "=== [6] alembic current ==="; docker compose -p pchsandbox exec -T backend alembic current
  echo "=== [7] web 镜像内 dist（web 启用时镜像内构建，host dist 可空）==="; docker compose -p pchsandbox exec -T web ls /usr/share/nginx/html/index.html
  echo "=== [8] 插件目录（应无 __pycache__/tests）==="; ls /tmp/fake-mcdr/plugins/htcmc_auth/
  echo "=== [9] token 一致性 + api_url 指向 :8100 ==="
  env_tok=$(grep '^MCDR_SERVICE_TOKEN=' .env | cut -d= -f2-)
  cf*************n3 -c "import json;print(json.load(open('/tmp/fake-mcdr/config/htcmc_auth/config.json'))['service_token'])" 2>/dev/null)
  [ "$env_tok" = "$cfg_tok" ] && echo "✓ token 一致" || echo "✗ 不一致"
  grep -o '"api_url": *"[^"]*"' /tmp/fake-mcdr/config/htcmc_auth/config.json
  echo "=== [10] web 容器（COMPOSE_PROFILES=web 默认启用）==="; docker compose -p pchsandbox ps web 2>/dev/null | tail -1
  echo "=== [11] web / → 200（:6173）==="; curl -sS -o /dev/null -w '%{http_code}\n' ht*****************73/
  echo "=== [12] web /api/healthz 反代（去 /api 前缀 → 容器内 ba***nd:8000）==="; curl -sS ht*****************************hz; echo
  echo "=== [13] web SPA fallback /sheets/3 → 200（history 模式 try_files）==="; curl -sS -o /dev/null -w '%{http_code}\n' ht**************************/3
  echo "=== [14] .env COMPOSE_PROFILES / WEB_PORT ==="; grep -E '^(COMPOSE_PROFILES|WEB_PORT)=' .env
}

# Ran on 2026-07-11 23:52:21+08:00 for 1.419s exited with 0
=== [1] .env 三密钥（非占位）===
***************RD=<redacted>
********ET=<redacted>
****************EN=<redacted>
=== [2] override 含 healthcheck、无 --reload ===
✓ healthcheck
✗ 残留 --reload
=== [3] deploy.env ===
# PCHSystem 部署状态（install.sh/update.sh 自动生成，勿手改）
PC***************ht*****************00
PC*********************0c
PCH_MCDR_ROOT=/tmp/fake-mcdr
PCH_DEPLOY_STRATEGY=tag
PC**************************23:51:49
PC****************=0
PC***********************ed:cf***0c
=== [4] 容器健康（project=pchsandbox）===
NAME                    IMAGE                COMMAND                   SERVICE    CREATED          STATUS                    PORTS
pc****************-1    pchsandbox-backend   "uvicorn app.main:ap…"   backend    43 seconds ago   Up 37 seconds (healthy)   0.***.0:8100->8000/tcp, [::]:8100->8000/tcp
pc*****************-1   po****es:16          "docker-entrypoint.s…"   postgres   43 seconds ago   Up 43 seconds (healthy)   12*****.1:15433->5432/tcp
pc************-1        pchsandbox-web       "/docker-entrypoint.…"   web        43 seconds ago   Up 37 seconds             0.***.0:6173->80/tcp, [::]:6173->80/tcp
=== [5] he***hz（:8100）===
{"status":"ok"}
=== [6] alembic current ===
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
00*******************at (head)
=== [7] web 镜像内 dist（web 启用时镜像内构建，host dist 可空）===
/usr/share/nginx/html/index.html
=== [8] 插件目录（应无 __pycache__/tests）===
config.json.example  ********th  mcdreforged.plugin.json
=== [9] token 一致性 + api_url 指向 :8100 ===
✓ token 一致
"api_url": "ht*****************00"
=== [10] web 容器（COMPOSE_PROFILES=web 默认启用）===
pc************-1   pchsandbox-web   "/docker-entrypoint.…"   web       44 seconds ago   Up 38 seconds   0.***.0:6173->80/tcp, [::]:6173->80/tcp
=== [11] web / → 200（:6173）===
200
=== [12] web /api/healthz 反代（去 /api 前缀 → 容器内 ba***nd:8000）===
{"status":"ok"}
=== [13] web SPA fallback /sheets/3 → 200（history 模式 try_files）===
200
=== [14] .env COMPOSE_PROFILES / WEB_PORT ===
**************ES=web
******RT=**73

```

---

# ═══════════════════════ update 测试组 ═══════════════════════

> update 组默认用 `--no-sync`（用当前工作树，不 fetch 远端），便于离线/快速测重建矩阵与流程。

## U-1. update 已是最新（--no-sync）

```bash
cd /home/yushen/opt/pchsandbox
bash Scripts/update.sh --no-sync

# Ran on 2026-07-11 23:52:42+08:00 for 2.363s exited with 0
[23:52:42] [▶] 跳过远端拉取（--no-sync），用当前工作树
[23:52:43] [INFO] 当前工作树与部署记录一致（de****ed:cf***0c），--no-sync 仍执行更新流程（迁移/插件/健康）
[23:52:43] [INFO] 跳过 checkout（--no-sync）
[23:52:43] [INFO] 无 Backend / compose 变更，跳过后端容器操作
[23:52:43] [▶] Alembic 迁移（upgrade head，幂等）
[23:52:43] [INFO] 迁移前快照: backups/pr**************************ql
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
[23:52:44] [INFO] 迁移完成，当前版本: 00*******************at (head)
[23:52:44] [INFO] web profile 启用：前端由 web 镜像构建，跳过宿主 npm run build
[23:52:44] [INFO] 无 htcmc_auth 插件变更
[23:52:44] [▶] 健康验证
[23:52:44] [INFO] 等待 ht*************************hz 返回 200（超时 60s）...

[23:52:44] [INFO] ====================================== 更新完成 ======================================
[23:52:44] [INFO] 版本: de****ed:cf***0c → de****ed:cf***0c
[23:52:44] [INFO] 迁移: 00*******************at (head)
[23:52:44] [INFO] 健康: curl ht*************************hz → ok
[23:52:44] [INFO] ======================================================================================
```

**期望**：`当前工作树与部署记录一致...仍执行更新流程` → 幂等迁移 + 健康 ok，无容器 recreate。

## U-2. update 有 backend 变化（造 tag + --no-sync）

```bash
cd /home/yushen/opt/pchsandbox

# 造"未来版本"（本地 tag，不污染主仓）
git checkout -b test-new-version
echo "# e2e update test marker" >> Backend/app/main.py
git -c user.email=t@t -c user.name=test commit -am "test: backend tweak for update e2e"
git tag ba**********.0
git checkout ba**********.0

bash Scripts/update.sh --no-sync

# 验证新代码生效
docker compose -p pchsandbox exec -T backend grep "e2e update test marker" /app/app/main.py && echo "✓ 新代码生效"

# Ran on 2026-07-11 23:53:01+08:00 for 3.875s exited with 0
切换到一个新分支 'test-new-version'
[test-new-version 8c***4c] test: backend tweak for update e2e
 1 file changed, 1 insertion(+)
注意：正在切换到 'ba**********.0'。

您正处于分离头指针状态。您可以查看、做试验性的修改及提交，并且您可以在切换
回一个分支时，丢弃在此状态下所做的提交而不对分支造成影响。

如果您想要通过创建分支来保留在此状态下所做的提交，您可以通过在 switch 命令
中添加参数 -c 来实现（现在或稍后）。例如：

  git switch -c <新分支名>

或者撤销此操作：

  git switch -

通过将配置变量 advice.detachedHead 设置为 false 来关闭此建议

HEAD 目前位于 8c***4c test: backend tweak for update e2e
[23:53:01] [▶] 跳过远端拉取（--no-sync），用当前工作树
[23:53:01] [INFO] 本地变更: de****ed:cf***0c → tag:ba**********.0（--no-sync，未 fetch 远端）
[23:53:01] [INFO] 跳过 checkout（--no-sync）
[23:53:01] [▶] Backend 代码变更 → force-recreate（mount 策略，秒级，无需 rebuild）
[+] Running 1/2
 ✔ Container pc*****************-1  g                                     [+] Running 1/2                                                            
 ✔ Container pc*****************-1  g                                     [+] Running 1/2                                                            
 ✔ Container pc*****************-1  g                                     [+] Running 1/2                                                            
 ✔ Container pc*****************-1  g                                     [+] Running 1/2                                                            
 ✔ Container pc*****************-1  g                                     [+] Running 1/2                                                            
 ✔ Container pc*****************-1  g                                     [+] Running 1/2                                                            
 ⠙ Container pc*****************-1  Waiting                                     [+] Running 1/2                                                            
 ⠹ Container pc*****************-1  Waiting                                     [+] Running 1/2                                                            
 ⠸ Container pc*****************-1  Waiting                                     [+] Running 1/2                                                            
 ⠼ Container pc*****************-1  Waiting                                     [+] Running 1/2                                                            
 ⠴ Container pc*****************-1  Waiting                                     [+] Running 1/2                                                            
 ✔ Container pc*****************-1  y                                     [+] Running 1/2                                                            
 ✔ Container pc*****************-1  y                                     [+] Running 2/2                                                            
 ✔ Container pc*****************-1  y                                                                                                                
 ✔ Container pc****************-1   d                                                                                                                
[23:53:03] [▶] Alembic 迁移（upgrade head，幂等）
[23:53:03] [INFO] 迁移前快照: backups/pr**************************ql
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
[23:53:04] [INFO] 迁移完成，当前版本: 00*******************at (head)
[23:53:04] [INFO] web profile 启用：前端由 web 镜像构建，跳过宿主 npm run build
[23:53:04] [INFO] 无 htcmc_auth 插件变更
[23:53:04] [▶] 健康验证
[23:53:04] [INFO] 等待 ht*************************hz 返回 200（超时 60s）...

[23:53:04] [INFO] ====================================== 更新完成 ======================================
[23:53:04] [INFO] 版本: de****ed:cf***0c → tag:ba**********.0
[23:53:05] [INFO] 迁移: 00*******************at (head)
[23:53:05] [INFO] 健康: curl ht*************************hz → ok
[23:53:05] [INFO] ======================================================================================
# e2e update test marker
✓ 新代码生效

```

**期望日志**：`本地变更: ... → tag:ba**********.0` + `Backend 代码变更 → force-recreate`。

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

> 本组验证本 PR 新增的「compose `web` 服务（nginx 托管 dist + 反代 /api）」。默认 in***ll（I-1）已启用 web（`.env` `COMPOSE_PROFILES=web`），故 I-2 的 [10]~[14] 已覆盖「web 起来 + 全链路通」。W-2/W-3 补「禁用」与「更新重建」两条路径。

## W-2. install `--no-web`（禁用 web，走非容器路径）

```bash
cd /home/yushen/opt/pchsandbox
# 前置：先跑 §1 重置（确保干净 + 端口空闲），再带 --no-web 装一遍
BA*************00 PG*********33 WE*********73 NPM_REGISTRY=ht**************************om \
bash Scripts/install.sh --no-sync --yes \
  --mcdr-root /tmp/fake-mcdr --mcdr-api-url ht*****************00 --no-web
{
  echo "=== [1] web 应未启动 ==="
  docker compose -p pchsandbox ps web 2>/dev/null | tail -1 | grep -q 'web' && echo "✗ web 仍在" || echo "✓ web 未起"
  echo "=== [2] .env COMPOSE_PROFILES 应为空 ==="; grep '^COMPOSE_PROFILES=' .env
  echo "=== [3] Frontend/dist 仍应生成（web 禁用时 build_frontend 走宿主 npm）==="; ls Frontend/dist/index.html
  echo "=== [4] backend 仍健康（:8100）==="; curl -sS ht*************************hz; echo
}

# Ran on 2026-07-11 23:53:21+08:00 for 41.798s exited with 2
                                                                do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 19.8s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 20.0s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 20.2s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 20.3s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 20.5s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 20.6s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 20.7s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 20.9s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 21.0s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 21.2s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 21.4s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 21.5s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 21.6s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 21.8s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 21.9s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 22.1s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 22.3s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 22.4s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 22.6s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 22.7s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 22.9s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 23.0s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 23.2s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 23.2s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 23.4s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 23.5s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 23.6s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 23.7s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 23.9s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 24.0s (12/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 24.1s (13/13)                                                                                                                      do**er:de***lt  
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s  
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0sen
 => CACHED [backend 5/9] COPY pyproject.toml ./                                 [+] Building 24.2s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0s  
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.**en
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 24.4s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 24.5s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 24.7s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 24.8s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 25.0s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 25.1s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 25.3s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 25.4s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 25.6s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 25.7s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 25.9s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 26.0s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 26.2s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 26.3s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 26.5s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 26.6s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 26.8s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 26.9s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 27.1s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 27.2s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 27.4s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 27.5s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 27.7s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 27.8s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 28.0s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 28.1s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 28.3s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 28.4s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 28.6s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 28.7s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 28.9s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 29.0s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 29.2s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 29.3s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 29.5s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 29.6s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 29.8s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 29.9s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 30.1s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.**en
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s  
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                           [+] Building 30.2s (13/14)                                                                                                                      do**er:de***lt  
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.**en
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s  
 => [backend 7/9] COPY alembic.ini ./                                                                                                                     0.0s  
 => [backend 8/9] COPY alembic ./alembic                                        [+] Building 30.3s (13/14)                                                                                                                      do**er:de***lten
 => [backend] exporting to image                                                                                                                          6.3s  
 => => exporting layers                                                                                                                                   6.1s  
 => => exporting manifest sh**56:<redacted>                                                         0.0s44
 => => exporting config sh**56:<redacted>[+] Building 30.5s (13/14)                                                                                                                      do**er:de***lt15
 => [backend] exporting to image                                                                                                                          6.4s8f
 => => exporting layers                                                                                                                                   6.1s  
 => => exporting manifest sh**56:<redacted>                                                         0.0s  
 => => exporting config sh**56:<redacted>[+] Building 30.6s (13/14)                                                                                                                      do**er:de***lt15
 => [backend] exporting to image                                                                                                                          6.6s8f
 => => exporting layers                                                                                                                                   6.1s  
 => => exporting manifest sh**56:<redacted>                                                         0.0s  
 => => exporting config sh**56:<redacted>[+] Building 30.8s (13/14)                                                                                                                      do**er:de***lt15
 => [backend] exporting to image                                                                                                                          6.7s8f
 => => exporting layers                                                                                                                                   6.1s  
 => => exporting manifest sh**56:<redacted>                                                         0.0s  
 => => exporting config sh**56:<redacted>[+] Building 30.9s (13/14)                                                                                                                      do**er:de***lt15
 => [backend] exporting to image                                                                                                                          6.9s8f
 => => exporting layers                                                                                                                                   6.1s  
 => => exporting manifest sh**56:<redacted>                                                         0.0s  
 => => exporting config sh**56:<redacted>[+] Building 31.1s (13/14)                                                                                                                      do**er:de***lt15
 => [backend] exporting to image                                                                                                                          7.0s8f
 => => exporting layers                                                                                                                                   6.1s  
 => => exporting manifest sh**56:<redacted>                                                         0.0s  
 => => exporting config sh**56:<redacted>[+] Building 31.2s (13/14)                                                                                                                      do**er:de***lt15
 => [backend] exporting to image                                                                                                                          7.2s8f
 => => exporting layers                                                                                                                                   6.1s  
 => => exporting manifest sh**56:<redacted>                                                         0.0s  
 => => exporting config sh**56:<redacted>[+] Building 31.4s (13/14)                                                                                                                      do**er:de***lt15
 => [backend] exporting to image                                                                                                                          7.3s8f
 => => exporting layers                                                                                                                                   6.1s  
 => => exporting manifest sh**56:<redacted>                                                         0.0s  
 => => exporting config sh**56:<redacted>[+] Building 31.5s (13/14)                                                                                                                      do**er:de***lt15
 => [backend] exporting to image                                                                                                                          7.4s8f
 => => exporting layers                                                                                                                                   6.1s  
 => => exporting manifest sh**56:<redacted>                                                         0.0s  
 => => exporting config sh**56:<redacted>[+] Building 31.6s (15/15) FINISHED                                                                                                             do**er:de***lt15
 => [backend internal] load build definition from Dockerfile                                                                                              0.**8f
 => => transferring dockerfile: 2.**kB                                                                                                                    0.0s  
 => [backend internal] load metadata for do*****io/library/py**on:3.*****im                                                                               0.2s  
 => [backend internal] load .dockerignore                                                                                                                 0.0s
 => => transferring context: 135B                                                                                                                         0.0s
 => [backend 1/9] FROM do*****io/library/py**on:3.************56:<redacted>                         0.0s
 => => resolve do*****io/library/py**on:3.************56:<redacted>                                 0.0s
 => [backend internal] load build context                                                                                                                 0.0s
 => => transferring context: 35****kB                                                                                                                     0.0s
 => CACHED [backend 2/9] RUN apt-get update && apt-get install -y --no-install-recommends         git wget fontconfig ca-certificates     && rm -rf /var  0.0s
 => CACHED [backend 3/9] RUN mkdir -p /usr/share/fonts/truetype/noto-cjk     && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf     &&  0.0s
 => CACHED [backend 4/9] WORKDIR /app                                                                                                                     0.0s
 => CACHED [backend 5/9] COPY pyproject.toml ./                                                                                                           0.0s
 => [backend 6/9] COPY app ./app                                                                                                                          0.0s
 => [backend 7/9] COPY alembic.ini ./                                                                                                                     0.0s
 => [backend 8/9] COPY alembic ./alembic                                                                                                                  0.0s
 => [backend 9/9] RUN if [ -n "ht************************************le" ]; then pip config set global.index-url "ht*********************************im  23.8s
 => [backend] exporting to image                                                                                                                          7.4s
 => => exporting layers                                                                                                                                   6.1s
 => => exporting manifest sh**56:<redacted>                                                         0.0s
 => => exporting config sh**56:<redacted>                                                           0.0s
 => => exporting attestation manifest sh**56:<redacted>                                             0.0s
 => => exporting manifest list sh**56:<redacted>                                                    0.0s
 => => naming to docker.io/library/pchsandbox-backend:latest                                                                                              0.0s
 => => unpacking to docker.io/library/pchsandbox-backend:latest                                                                                           1.3s
 => [backend] resolving provenance for metadata file                                                                                                      0.0s
[+] Building 1/1
 ✔ backend  t                                                                                                                                          
[23:53:54] [▶] 构建 web 镜像（前端，容器内 npm build；NPM_REGISTRY/代理经 build-arg 透传）
N[0000] Docker Compose is configured to build using Bake, but buildx isn't installed 
[+] Building 0.0s (0/1)                                                         [+] Building 0.2s (1/2)                                                                                                                         docker:default
 => [web internal] load build definition from Dockerfile                                                                                                  0.0s
[+] Building 0.2s (2/3)                                                                                                                         docker:default
 => [web internal] load build definition from Dockerfile                                                                                                  0.0s
[+] Building 0.3s (3/5)                                                                                                                         docker:default
 => [web internal] load build definition from Dockerfile                                                                                                  0.0s
 => => transferring dockerfile: 1.**kB                                                                                                                    0.0s
 => [web] resolve image config for do********ge://do*****io/docker/do******le:1 ******************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************************:1@****56:<redacted>                     0.0s
 => => resolve do*****io/docker/do******le:1@****56:87************************ee[+] Building 0.8s (6/16)                                                                                                                        do**er:de***lt  
 => => transferring dockerfile: 1.**kB                                                                                                                    0.0s  
 => [web] resolve image config for do********ge://do*****io/docker/do******le:1                                                                           0.2s  
 => CACHED [web] do********ge://do*****io/docker/do******le:1@****56:<redacted>                     0.0s  
 => => resolve do*****io/docker/do******le:1@****56:87************************ee[+] Building 0.8s (18/18) FINISHED                                                                                                              do**er:de***lt  
 => [web internal] load build definition from Dockerfile                                                                                                  0.0s  
 => => transferring dockerfile: 1.**kB                                                                                                                    0.0s  
 => [web] resolve image config for do********ge://do*****io/docker/do******le:1                                                                           0.2s  
 => CACHED [web] do********ge://do*****io/docker/do******le:1@****56:<redacted>                     0.0s
 => => resolve do*****io/docker/do******le:1@****56:<redacted>                                      0.0s
 => [web internal] load metadata for docker.io/library/nginx:stable-alpine                                                                                0.2s
 => [web internal] load metadata for do*****io/library/node:22*****ne                                                                                     0.2s
 => [web internal] load .dockerignore                                                                                                                     0.0s
 => => transferring context: 233B                                                                                                                         0.0s
 => [web builder 1/6] FROM do*****io/library/node:22************56:<redacted>                       0.0s
 => => resolve do*****io/library/node:22************56:<redacted>                                   0.0s
 => [web internal] load build context                                                                                                                     0.0s
 => => transferring context: 2.**kB                                                                                                                       0.0s
 => [web st***-1 1/3] FROM do*****io/library/nginx:st****************56:<redacted>                  0.0s
 => => resolve do*****io/library/nginx:st****************56:<redacted>                              0.0s
 => CACHED [web builder 2/6] WORKDIR /app                                                                                                                 0.0s
 => CACHED [web builder 3/6] COPY package.json package-lock.json* ./                                                                                      0.0s
 => CACHED [web builder 4/6] RUN if [ -n "ht**************************om" ]; then npm config set registry "ht**************************om"; fi  && (npm   0.0s
 => CACHED [web builder 5/6] COPY . .                                                                                                                     0.0s
 => CACHED [web builder 6/6] RUN npm run build                                                                                                            0.0s
 => CACHED [web st***-1 2/3] COPY --from=builder /app/dist /usr/share/nginx/html                                                                          0.0s
 => CACHED [web st***-1 3/3] COPY nginx.conf /etc/nginx/conf.d/default.conf                                                                               0.0s
 => [web] exporting to image                                                                                                                              0.0s
 => => exporting layers                                                                                                                                   0.0s
 => => exporting manifest sh**56:<redacted>                                                         0.0s
 => => exporting config sh**56:<redacted>                                                           0.0s
 => => exporting attestation manifest sh**56:<redacted>                                             0.0s
 => => exporting manifest list sh**56:<redacted>                                                    0.0s
 => => naming to docker.io/library/pchsandbox-web:latest                                                                                                  0.0s
 => => unpacking to docker.io/library/pchsandbox-web:latest                                                                                               0.0s
 => [web] resolving provenance for metadata file                                                                                                          0.0s
[+] Building 1/1
 ✔ web  t                                                                                                                                              
[23:53:56] [▶] 启动 postgres + backend + web
[+] Running 1/2
 ✔ Container pc*****************-1  g                                     [+] Running 1/2                                                            
 ✔ Container pc*****************-1  g                                     [+] Running 1/2                                                            
 ✔ Container pc*****************-1  g                                     [+] Running 1/2                                                            
 ✔ Container pc*****************-1  g                                     [+] Running 1/2                                                            
 ✔ Container pc*****************-1  g                                     [+] Running 1/2                                                            
 ✔ Container pc*****************-1  g                                     [+] Running 1/2                                                            
 ✔ Container pc*****************-1  g                                     [+] Running 2/3                                                            
 ✔ Container pc*****************-1  g                                                                                                                
[+] Running 2/3h***************-1   d                                   
 ✔ Container pc*****************-1  g                                                                                                                 
[+] Running 2/3h***************-1   d                                   
 ✔ Container pc*****************-1  g                                                                                                                 
[+] Running 2/3h***************-1   d                                   
 ✔ Container pc*****************-1  g                                                                                                                 
[+] Running 2/3h***************-1   d                                   
 ⠙ Container pc*****************-1  Waiting                                                                                                                 
[+] Running 2/3h***************-1   d                                   
 ⠹ Container pc*****************-1  Waiting                                                                                                                 
[+] Running 2/3h***************-1   d                                   
 ⠸ Container pc*****************-1  Waiting                                                                                                                 
[+] Running 2/3h***************-1   d                                   
 ⠼ Container pc*****************-1  Waiting                                                                                                                 
[+] Running 2/3h***************-1   d                                   
 ⠴ Container pc*****************-1  Waiting                                                                                                                 
[+] Running 2/3h***************-1   d                                   
 ✔ Container pc*****************-1  y                                                                                                                 
[+] Running 2/3h***************-1   Starting                                    
 ✔ Container pc*****************-1  y                                                                                                                 
[+] Running 2/3h***************-1   Starting                                    
 ✔ Container pc*****************-1  y                                                                                                                 
[+] Running 2/3h***************-1   d                                     
 ✔ Container pc*****************-1  y                                                                                                                 
[+] Running 3/3****************-1   d                                     
 ✔ Container pc*****************-1  y                                                                                                                 
 ✔ Container pc****************-1   d                                                                                                                
 ✔ Container pc************-1       d                                                                                                                
[23:53:58] [INFO] 等待 postgres 健康（超时 120s）...
[23:53:58] [INFO] 等待 ht*************************hz 返回 200（超时 180s）...
[23:54:01] [▶] Alembic 迁移（upgrade head）
[23:54:01] [INFO] 迁移前快照: backups/pr***************************ql
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
[23:54:02] [INFO] 迁移完成，当前版本: 00*******************at (head)
[23:54:02] [INFO] web profile 启用：前端由 web 镜像构建，无需宿主 Node
[23:54:02] [▶] 部署 htcmc_auth 插件到 MCDR
[23:54:02] [WARN] 未找到依赖插件 uuid_api_remake（htcmc_auth 加载需要）→ ht********************************************ke
[23:54:02] [WARN] 未找到依赖插件 minecraft_data_api（htcmc_auth 加载需要）→ MCDR 插件市场 MinecraftDataAPI
[23:54:02] [INFO] 插件已拷贝: /tmp/fake-mcdr/plugins/htcmc_auth/
[23:54:02] [WARN] 已有 /tmp/fake-mcdr/config/htcmc_auth/config.json，保留（仅提示：请确认 service_token 与 .env MCDR_SERVICE_TOKEN 一致；强写用 --mcdr-overwrite-config）
[23:54:02] [WARN] 请在游戏内/MCDR 控制台执行热重载（脚本无法可靠注入）:

    !!MCDR plugin reload htcmc_auth
[23:54:02] [▶] 持久化部署状态

[23:54:02] [INFO] ====================================== 安装完成 ======================================
[23:54:02] [INFO] 后端健康:   curl ht*************************hz   (期望 {"status":"ok"})
[23:54:03] [INFO] 迁移版本:   00*******************at (head)
[23:54:03] [INFO] 前端 Web:    http://<本机IP>:6173（compose web 服务：托管 dist + 反代 /api 到 backend）
[23:54:03] [INFO] 插件已部署: /tmp/fake-mcdr/plugins/htcmc_auth/
[23:54:03] [INFO] 插件配置:   /tmp/fake-mcdr/config/htcmc_auth/config.json

[23:54:03] [WARN] 待办：
[23:54:03] [WARN]   - 在游戏内执行: !!MCDR plugin reload htcmc_auth
[23:54:03] [WARN]   - 确认依赖插件已装: uuid_api_remake + minecraft_data_api
[23:54:03] [WARN]   - 检查 .env：WEB_BASE_URL 需为玩家可访问的前端地址（单机 + web 默认 5173 已对齐；用域名/反代则改成真实 URL 后 docker compose restart backend）
[23:54:03] [INFO] ======================================================================================
=== [1] web 应未启动 ===
✗ web 仍在
=== [2] .env COMPOSE_PROFILES 应为空 ===
**************ES=web
=== [3] Frontend/dist 仍应生成（web 禁用时 build_frontend 走宿主 npm）===
ls: 无法访问 'Frontend/dist/index.html': 没有那个文件或目录

```

__期望__：web 容器不存在；`.env` `COMPOSE_PROFILES=`（空）；`Frontend/dist/index.html` 仍生成；backend 不受影响。

## W-3. update `Frontend/` 变更 → 重建 web 镜像（智能重建矩阵 web 分支）

```bash
cd /home/yushen/opt/pchsandbox
# 前提：处于 web 启用态（先跑过默认 I-1，而非 W-2 的 --no-web）
git checkout -b test-web-rebuild
echo "// e2e web rebuild marker" >> Frontend/src/main.ts
git -c user.email=t@t -c user.name=test commit -am "test: frontend tweak for web rebuild e2e"
bash Scripts/update.sh --no-sync 2>&1 | tee /tmp/w3**og
grep -q 'Frontend 变更（容器路径）→ 重建 web 镜像' /tmp/w3**og && echo "✓ web 镜像重建分支命中" || echo "✗ 未命中"
# 回到测试分支，清理造的分支
git checkout sandbox-test 2>/dev/null || true
git branch -D test-web-rebuild 2>/dev/null || true

# Ran on 2026-07-11 23:54:21+08:00 for 9.583s exited with 0
切换到一个新分支 'test-web-rebuild'
[test-web-rebuild 37***27] test: frontend tweak for web rebuild e2e
 1 file changed, 1 insertion(+)
[23:54:21] [▶] 跳过远端拉取（--no-sync），用当前工作树
[23:54:21] [INFO] 本地变更: tag:ba**********.0 → de****ed:37***27（--no-sync，未 fetch 远端）
[23:54:21] [INFO] 跳过 checkout（--no-sync）
[23:54:21] [INFO] 无 Backend / compose 变更，跳过后端容器操作
[23:54:21] [▶] Frontend 变更（容器路径）→ 重建 web 镜像
time="20*********23:54:22+08:00" level=warning msg="Docker Compose is configured to build using Bake, but buildx isn't installed"
#0 building with "default" instance using docker driver

#1 [web internal] load build definition from Dockerfile
#1 transferring dockerfile: 1.**kB done
#1 DONE 0.0s

#2 [web] resolve image config for do********ge://do*****io/docker/do******le:1
********************************************************************:1@****56:<redacted>
#3 resolve do*****io/docker/do******le:1@****56:<redacted> done
#3 CACHED

#4 [web internal] load metadata for do*****io/library/node:22*****ne
#4 DONE 0.0s

#5 [web internal] load metadata for docker.io/library/nginx:stable-alpine
#5 DONE 0.0s

#6 [web internal] load .dockerignore
#6 transferring context: 233B done
#6 DONE 0.0s

#7 [web internal] load build context
#7 transferring context: 2.**kB done
#7 DONE 0.0s

#8 [web st***-1 1/3] FROM do*****io/library/nginx:st****************56:<redacted>
#8 resolve do*****io/library/nginx:st****************56:<redacted> 0.0s done
#8 DONE 0.0s

#9 [web builder 1/6] FROM do*****io/library/node:22************56:<redacted>
#9 resolve do*****io/library/node:22************56:<redacted> 0.0s done
#9 DONE 0.0s

#10 [web builder 2/6] WORKDIR /app
#10 CACHED

#11 [web builder 3/6] COPY package.json package-lock.json* ./
#11 CACHED

#12 [web builder 4/6] RUN if [ -n "" ]; then npm config set registry ""; fi  && (npm ci || npm install)
#12 CACHED

#13 [web builder 5/6] COPY . .
#13 DONE 0.0s

#14 [web builder 6/6] RUN npm run build
#14 0.237 
#14 0.237 > fr**********.1 build
#14 0.237 > vue-tsc -b && vite build
#14 0.237 
#14 4.044 vite v8**.2 building client environment for production...
transforming...✓ 1664 modules transformed.
#14 4.504 rendering chunks...
#14 4.659 computing gzip size...
#14 4.669 dist/index.html                            0.45 kB │ gzip:   0.29 kB
#14 4.669 dist/assets/Sh********************ss       0.14 kB │ gzip:   0.12 kB
#14 4.669 dist/assets/in**************ss           356.37 kB │ gzip:  47.61 kB
#14 4.669 dist/assets/qty-DPtdJpob.js                0.15 kB │ gzip:   0.15 kB
#14 4.669 dist/assets/Me-hNHtthQX.js                 0.51 kB │ gzip:   0.36 kB
#14 4.669 dist/assets/us******************js         0.72 kB │ gzip:   0.44 kB
#14 4.669 dist/assets/AuthExchange-fyIJYdwD.js       0.81 kB │ gzip:   0.53 kB
#14 4.669 dist/assets/sh**************js             1.80 kB │ gzip:   0.53 kB
#14 4.669 dist/assets/Sh*****************js          2.80 kB │ gzip:   1.42 kB
#14 4.669 dist/assets/LitematicImport-QXAMHHJg.js    6.97 kB │ gzip:   2.67 kB
#14 4.669 dist/assets/Sh*******************js       23.44 kB │ gzip:   7.25 kB
#14 4.669 dist/assets/http-BzgzJGrm.js              45.00 kB │ gzip:  17.13 kB
#14 4.669 dist/assets/in*************js            997.40 kB │ gzip: 323.58 kB
#14 4.669 
#14 4.671 [INVALID_ANNOTATION] A comment "/* #__PURE__ */" in "node_modules/@vueuse/core/dist/index.js" contains an annotation that Rolldown cannot interpret due to the position of the comment.
#14 4.671       ╭─[ node_modules/@vueuse/core/dist/in****js:3362:1 ]
#14 4.671       │
#14 4.671   /* #__PURE__ */
#14 4.671       │ ───────┬───────  
#14 4.671       │        ╰───────── comment ignored due to position
#14 4.671       │ 
#14 4.671       │ p: For more information on how to use pure annotations correctly, check the documentation: ht***************************************************re
#14 4.671 ──────╯
#14 4.671 
#14 4.671 [plugin builtin:vite-reporter] 
#14 4.671 (!) Some chunks are larger than 500 kB after minification. Consider:
#14 4.671 - Using dynamic import() to code-split the application
#14 4.671 - Use build.rolldownOptions.output.codeSplitting to improve chunking: ht*****************************************************ng
#14 4.671 - Adjust chunk size limit for this warning via build.chunkSizeWarningLimit.
#14 4.671 [INVALID_ANNOTATION] A comment "/* #__PURE__ */" in "node_modules/@vueuse/core/dist/index.js" contains an annotation that Rolldown cannot interpret due to the position of the comment.
#14 4.671       ╭─[ node_modules/@vueuse/core/dist/in****js:5780:23 ]
#14 4.671       │
#14 4.671   t defaultState = (/* #__PURE__ */ {
#14 4.671       │                       ───────┬───────  
#14 4.671       │                              ╰───────── comment ignored due to position
#14 4.671       │ 
#14 4.671       │ p: For more information on how to use pure annotations correctly, check the documentation: ht***************************************************re
#14 4.671 ──────╯
#14 4.671 
#14 4.672 ✓ built in 627ms
#14 DONE 4.7s

#15 [web st***-1 2/3] COPY --from=builder /app/dist /usr/share/nginx/html
#15 CACHED

#16 [web st***-1 3/3] COPY nginx.conf /etc/nginx/conf.d/default.conf
#16 CACHED

#17 [web] exporting to image
#17 exporting layers done
#17 exporting manifest sh**56:<redacted> done
#17 exporting config sh**56:<redacted> done
#17 exporting attestation manifest sh**56:<redacted> done
#17 exporting manifest list sh**56:<redacted> done
#17 naming to docker.io/library/pchsandbox-web:latest done
#17 unpacking to docker.io/library/pchsandbox-web:latest done
#17 DONE 0.0s

#18 [web] resolving provenance for metadata file
#18 DONE 0.0s
 web  Built
 Container pc*****************-1  Running
 Container pc****************-1  Running
 Container pc************-1  Recreate
 Container pc************-1  Recreated
 Container pc*****************-1  Waiting
 Container pc*****************-1  Healthy
 Container pc************-1  Starting
 Container pc************-1  Started
[23:54:28] [▶] Alembic 迁移（upgrade head，幂等）
[23:54:28] [INFO] 迁移前快照: backups/pr**************************ql
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
[23:54:30] [INFO] 迁移完成，当前版本: 00*******************at (head)
[23:54:30] [INFO] web profile 启用：前端由 web 镜像构建，跳过宿主 npm run build
[23:54:30] [INFO] 无 htcmc_auth 插件变更
[23:54:30] [▶] 健康验证
[23:54:30] [INFO] 等待 ht*************************hz 返回 200（超时 60s）...

[23:54:30] [INFO] ====================================== 更新完成 ======================================
[23:54:30] [INFO] 版本: tag:ba**********.0 → de****ed:37***27
[23:54:30] [INFO] 迁移: 00*******************at (head)
[23:54:30] [INFO] 健康: curl ht*************************hz → ok
[23:54:30] [INFO] ======================================================================================
✓ web 镜像重建分支命中
已删除分支 test-web-rebuild（曾为 37***27）。

```

__期望日志__：`Frontend 变更（容器路径）→ 重建 web 镜像`（`update.sh::decide_rebuild` 新增的 web 分支；`compose_build web` + `up -d web`）。

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
  source <redacted>.sh
  printf 'COMPOSE_PROFILES=website\n' > .env; web_profile_active && echo "✗ website 被误判为 web" || echo "✓ website 不误判"
  printf 'COMPOSE_PROFILES=web,foo\n' > .env; web_profile_active && echo "✓ web,foo 命中" || echo "✗ 漏判"
  cd - >/dev/null && rm -rf "$tmp"
}

# Ran on 2026-07-11 23:55:40+08:00 for 512ms exited with 0
=== [1] 旧「需重启 MCDR」误报文案应 0 命中 ===
Scripts/e2e/TE**********md::  grep -rn '需【重启 MCDR】\|需\*\*重启 MCDR\*\*' Scripts/ McdrPlugin/ Docs/ && echo "✗ 仍有残留" || echo "✓ 已清除"
✗ 仍有残留
=== [2] update.sh 插件变更统一为 reload 文案（含 mcdreforged.plugin.json）===
✓ 统一 reload
=== [3] web_profile_active 整词不误判（website ≠ web）===
✓ website 不误判
✓ web,foo 命中
```

**期望**：[1] 0 命中；[2] 命中统一 re**ad；[3] `website` 不误判、`web,foo` 命中。

> __MCDR reload 语义依据__：`mcdreforged.plugin.json` 任何字段（version/dependencies/...）变更都随 `!!MCDR plugin reload` 重新读取并由 `DependencyWalker` 重校依赖，__无需重启 MCDR__。源码：[`plugin_manager.py`](ht***************************************************************************************py)（reload = unload→load→check dept）。详见 [`Docs/Reports/mcdr-release-prep.md`](../../Docs/Reports/mcdr-release-prep.md)。

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

1. **in***ll**：I-1 部署块 + I-2 验证块（14 项全 ✓，含 web [10]~[14]）
2. **web 禁用**：W-2 `--no-web`（web 未起 + dist 仍构建）
3. **web 重建**：W-3 `Frontend 变更（容器路径）→ 重建 web 镜像` 行
4. **MCDR 误报修复**：S-1 旧「需重启 MCDR」0 命中 + 统一 reload + `website` 不误判
5. **update U-1**：已是最新路径
6. **update U-2**：`本地变更` + `force-recreate` 行（证明智能重建矩阵 backend 分支）
7. 已知边界（端口偏移隔离 / tag 模式 checkout 旧 tag / `--no-sync` 语义 / Dockerfile pip+字体镜像）

---

## 已知边界

- __端口 8100/15433/6173 必须空闲__（前置 §1）；为与生产 `pc*****em`（8000/5433/5173）共存而偏移。compose 的 backend/pg/web 端口均 env 可配（`${BA********RT:-8000}`/`${PG***RT:-5433}`/`${WE****RT:-5173}`），install 块以__命令前缀内联 env__（`BA*************00 PG*********33 WE*********73 NPM_REGISTRY=... bash install.sh`）传入 → `install.sh::ensure_env` 写入 `.env` 持久化（__compose/脚本本身不变__，默认值 8000/5433/5173 对真实部署零影响；不用独立 `export` 行：RUNBOOK 插件会拦截/合并致变量丢失）。
- 沙盒 project 隔离：容器/卷/网络前缀 `pchsandbox-*`，与生产 `pchsystem-*` 互不干扰；镜像缓存共享（无害）。
- §1 重置必须先 `docker ... chown -R` 把工作树所有权改回宿主用户：backend 容器以 root 运行，bind mount 写出的 `__pycache__/*.pyc` 是 root 属主，宿主 `git clean -fdx`（`-x` 清 gitignored）会因 unlink 失败报"权限不够"。
- `--no-sync`（install/update 共有）：用当前工作树（=sandbox-test 快照），不拉取 / 不 checkout。开发与 e2e 用；生产部署用默认 tag 模式。
- update `--no-sync` 不 fetch：OLD=部署记录 commit，NEW=当前 HEAD；两者一致时仍跑迁移/健康（幂等确认），不 exit。
- 造的 tag/分支（`ba**********.0` / `test-new-version` / `test-web-rebuild`）只在 pchsandbox `.git`，删 worktree 即清除。
- __backend 镜像 pip 大包（matplotlib/numpy/Pillow…）__：`Backend/Dockerfile` 经 `ARG PIP_INDEX_URL` + `pip config set global.index-url` 消费 `install.sh/setup_mirrors` 透传的清华源（本 PR 修复，否则走 PyPI 直连国内极慢）。
- __CJK 字体 wget__：`Backend/Dockerfile` 多源链 ghfast.top → ghproxy → GitHub 直连 → apt 兜底（脚本层 `PCH_GH_MIRRORS` 仅重写 git clone/fetch，__不覆盖__ wget，故在 Dockerfile 显式列 GitHub 代理源；本机探测仅 ghfast.top 通）。
- __web 镜像 npm__：`NPM_REGISTRY` build-arg 控制源（install 块已设 npmmirror）；容器内 Node 为 `node:22*****ne`。
- `COMPOSE_PROFILES=web` 启停 web 服务；`--no-web` 仅在__首次生成 .env__ 时生效（已有 .env 则直接改 `.env` 的 `COMPOSE_PROFILES`）。
- `--mcdr-api-url ht*****************00` 必传：假 MCDR 路径 `/tmp/fake-mcdr` 非容器卷，`detect_mcdr_topology` 默认返回 `:8000`（生产），不覆盖会把插件 config 指向生产 backend。
