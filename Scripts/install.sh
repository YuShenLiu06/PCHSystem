#!/usr/bin/env bash
# install.sh —— PCHSystem 一键首次安装
#
# 用法（在仓库内执行）：
#   git clone https://github.com/YuShenLiu06/PCHSystem.git
#   cd PCHSystem
#   bash Scripts/install.sh [--edge] [--yes] [--mcdr-root DIR] [--no-frontend] [--no-mcdr] ...
#
# 流程：docker 检测/安装 → 网络镜像自适应 → 同步到目标版本 → 生成 .env → 生产 override
#       → 起容器 → alembic 迁移 → 前端构建 → 拷 pch_system 插件 → 持久化 + 摘要
#
# 红线遵守：.env 已存在绝不覆盖；alembic 失败绝不自动 downgrade（score_ledger append-only）；
#          插件 service_token 与 .env MCDR_SERVICE_TOKEN 强制同值。

set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
# macOS sudo env_reset 可能清空 locale，保底设 C.UTF-8（不覆盖用户已设值）。
export LC_ALL="${LC_ALL:-C.UTF-8}"
export LANG="${LANG:-C.UTF-8}"
trap 'pch_err_trap $LINENO' ERR

PCH_REPO_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$PCH_REPO_DIR"

# ---------- 参数 ----------
STRATEGY="tag"          # tag | edge
NO_FRONTEND=0
NO_WEB=0
NO_MCDR=0
NO_SYNC=0
OVERWRITE_MCDR_CONFIG=0
MCDR_ROOT_OVERRIDE=""
MCDR_API_URL_OVERRIDE=""

usage() {
    cat <<'EOF'
PCHSystem install.sh —— 一键首次安装

用法: bash Scripts/install.sh [选项]

选项:
  --edge                 拉 main 最新提交（默认拉最新发版 tag）
  --yes                  无人值守，全部用默认值（等价 PCH_YES=1）
  --mcdr-root DIR        MCDR 根目录（含 plugins/ 和 config/），等价 PCH_MCDR_ROOT
  --mcdr-api-url URL     插件访问后端的 URL，等价 PCH_MCDR_API_URL
  --mcdr-overwrite-config  强制覆盖玩家已有的 pch_system config.json
  --no-frontend          跳过前端构建
  --no-web               不启用 web 服务（默认启用 nginx 托管前端；禁用后走非容器路径自管 nginx）
  --no-mcdr              跳过 MCDR 插件拷贝
  --no-sync              跳过版本同步（用当前工作树，开发/测试用）
  -h, --help             显示本帮助

环境变量: PCH_YES / PCH_MCDR_ROOT / PCH_MCDR_API_URL / WEB_BASE_URL
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --edge) STRATEGY="edge"; shift ;;
            --yes) export PCH_YES=1; shift ;;
            --mcdr-root) MCDR_ROOT_OVERRIDE=$2; shift 2 ;;
            --mcdr-api-url) MCDR_API_URL_OVERRIDE=$2; shift 2 ;;
            --mcdr-overwrite-config) OVERWRITE_MCDR_CONFIG=1; shift ;;
            --no-frontend) NO_FRONTEND=1; shift ;;
            --no-web) NO_WEB=1; shift ;;
            --no-mcdr) NO_MCDR=1; shift ;;
            --no-sync) NO_SYNC=1; shift ;;
            -h|--help) usage; exit 0 ;;
            *) die "未知参数: $1（用 --help 查看用法）" ;;
        esac
    done
}

# .env 读字段（仅本仓库根 .env）
env_get() {
    grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2-
}

# ---------- 步骤 ----------
check_in_repo() {
    is_in_git_repo || {
        cat >&2 <<EOF
未在 PCHSystem 仓库内。请先 clone：
  git clone ${PCH_REPO_URL}
  cd PCHSystem
  bash Scripts/install.sh
（若 GitHub 直连不通，见 Scripts/README.md 的镜像 clone 命令）
EOF
        die "需在仓库内执行"
    }
}

setup_mirrors() {
    local entry; entry=$(pick_github_mirror)
    GH_MIRROR_ENTRY=$entry
    # PyPI 加速（build-arg 透传；当前 Dockerfile 未消费，留作未来兼容，无害）
    export PIP_INDEX_URL="${PIP_INDEX_URL:-$PIP_INDEX_URL_TUNA}"
    # Docker registry 镜像加速（best-effort，失败不阻断）
    run_step --on-fail warn "配置 Docker registry 加速" ensure_docker_registry_mirrors
}

sync_repo() {
    if [[ $NO_SYNC -eq 1 ]]; then
        log_info "跳过版本同步（--no-sync），使用当前工作树 ($(current_ref))"
        return 0
    fi
    log_step "同步仓库到目标版本（strategy=${STRATEGY}）"
    # dirty 保护（跟踪文件被改）
    if ! git diff --quiet HEAD -- 2>/dev/null || [[ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]]; then
        if ! confirm "检测到本地改动，继续将强制切换版本（可能丢弃改动）？ [y/N]" "n"; then
            die "已取消。请先 git stash 或 git commit 本地改动。"
        fi
        log_warn "强制切换（本地改动将被 checkout 覆盖，gitignored 文件不受影响）"
    fi
    if [[ "$STRATEGY" == "edge" ]]; then
        gh_git "$GH_MIRROR_ENTRY" fetch origin "$PCH_DEFAULT_BRANCH"
        git checkout "$PCH_DEFAULT_BRANCH"
        gh_git "$GH_MIRROR_ENTRY" pull --ff-only origin "$PCH_DEFAULT_BRANCH"
    else
        gh_git "$GH_MIRROR_ENTRY" fetch --all --tags --prune
        local tag; tag=$(latest_tag)
        [[ -n "$tag" ]] || die "未找到任何发版 tag（*-v*），可用 --edge 拉 main"
        git checkout "$tag"
        log_info "已切换到最新发版: $tag"
    fi
}

ensure_env() {
    if [[ -f .env ]]; then
        log_warn ".env 已存在，保留不覆盖（如需重新生成请先备份并删除）"
        return 0
    fi
    log_step "生成 .env"
    cp .env.example .env
    local pg_pwd jwt svc
    pg_pwd=$(gen_secret 24)
    jwt=$(gen_secret 32)
    svc=$(gen_secret 24)
    sed "${SED_I[@]}" "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$pg_pwd|" .env
    sed "${SED_I[@]}" "s|^JWT_SECRET=.*|JWT_SECRET=$jwt|" .env
    sed "${SED_I[@]}" "s|^MCDR_SERVICE_TOKEN=.*|MCDR_SERVICE_TOKEN=$svc|" .env
    local web_default="http://localhost:5173"
    read_interactive WEB_BASE_URL "  WEB_BASE_URL（!!PCH login 回链前缀，默认本机前端）" "${WEB_BASE_URL:-$web_default}"
    sed "${SED_I[@]}" "s|^WEB_BASE_URL=.*|WEB_BASE_URL=$WEB_BASE_URL|" .env
    if [[ $NO_WEB -eq 1 ]]; then
        sed "${SED_I[@]}" 's|^COMPOSE_PROFILES=.*|COMPOSE_PROFILES=|' .env
        log_info "已禁用 web 服务（--no-web）：.env COMPOSE_PROFILES 置空（web 服务不随 compose 起）"
    fi
    # 端口可配：若导出了 BACKEND_PORT/PG_PORT/WEB_PORT（如沙盒避让生产），写入 .env 持久化（供后续 update.sh 复用）
    local _bp="${BACKEND_PORT:-}" _gp="${PG_PORT:-}" _wp="${WEB_PORT:-}"
    [[ -n "$_bp" ]] && sed "${SED_I[@]}" "s|^BACKEND_PORT=.*|BACKEND_PORT=$_bp|" .env
    [[ -n "$_gp" ]] && sed "${SED_I[@]}" "s|^PG_PORT=.*|PG_PORT=$_gp|" .env
    [[ -n "$_wp" ]] && sed "${SED_I[@]}" "s|^WEB_PORT=.*|WEB_PORT=$_wp|" .env
    chmod 600 .env
    log_info ".env 已生成（POSTGRES_PASSWORD / JWT_SECRET / MCDR_SERVICE_TOKEN 已填强随机值）"
}

ensure_override() {
    log_step "生成生产 override（docker-compose.override.yml）"
    ensure_gitignored docker-compose.override.yml
    if [[ -f docker-compose.override.yml ]] && ! confirm "  override.yml 已存在，覆盖？" "n"; then
        log_warn "保留已有 override.yml"; return 0
    fi
    cat > docker-compose.override.yml <<'EOF'
# 生产模式 override（由 Scripts/install.sh 生成，已 .gitignore）
# - 覆盖 backend.command 为无 --reload 的生产 CMD（与 Backend/Dockerfile CMD 一致）
# - 加 backend healthcheck（/healthz 存活探针；slim 镜像无 curl 故用 python）
# - 保留源码 volume 挂载：更新只需 git pull + force-recreate，无需 rebuild
services:
  backend:
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz',timeout=3).status==200 else 1)\""]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 20s
EOF
    log_info "override.yml 已生成（去 --reload + 加 healthcheck）"
}

start_stack() {
    log_step "构建 backend 镜像（自动透传 HTTPS_PROXY 加速 CJK 字体下载）"
    compose_build backend
    if web_profile_active; then
        log_step "构建 web 镜像（前端，容器内 npm build；NPM_REGISTRY/代理经 build-arg 透传）"
        compose_build web
    fi
    log_step "启动 postgres + backend$(web_profile_active && echo ' + web')"
    dcc up -d
    wait_healthy postgres 120
    local _bp="${BACKEND_PORT:-$(env_get BACKEND_PORT)}"; _bp="${_bp:-8000}"
    wait_http_ok "http://127.0.0.1:${_bp}/healthz" 180 200
}

run_migrations() {
    log_step "Alembic 迁移（upgrade head）"
    local pg_user pg_db
    pg_user=$(env_get POSTGRES_USER); pg_db=$(env_get POSTGRES_DB)
    dump_pre_migration pre-install "$pg_user" "$pg_db"
    if ! dcc exec -T backend alembic upgrade head; then
        log_error "alembic upgrade head 失败"
        dcc exec -T backend alembic current 2>/dev/null || true
        cat >&2 <<EOF
迁移失败处理（绝不自动 downgrade，score_ledger append-only）：
  1. 查看迁移文件: ls Backend/alembic/versions/
  2. 排查后重试:   bash Scripts/install.sh
  3. 如需回滚库:  psql 恢复 $MIGRATION_BAK
EOF
        die "alembic 迁移失败"
    fi
    log_info "迁移完成，当前版本: $(dcc exec -T backend alembic current 2>/dev/null || echo unknown)"
}

check_node() {
    command -v node >/dev/null 2>&1 || return 1
    local v; v=$(node -v 2>/dev/null | sed 's/v//')
    local major=${v%%.*}
    [[ "$major" =~ ^[0-9]+$ ]] || return 1
    (( major >= 18 ))
}

build_frontend() {
    [[ $NO_FRONTEND -eq 1 ]] && { log_info "跳过前端构建（--no-frontend）"; return 0; }
    # web 服务启用 → 前端在镜像内构建（start_stack 已 compose_build web），跳过宿主 npm
    if web_profile_active; then
        log_info "web profile 启用：前端由 web 镜像构建，无需宿主 Node"
        return 0
    fi
    log_step "构建前端（best-effort，非容器路径：宿主 npm run build 出 Frontend/dist）"
    if ! check_node; then
        log_warn "未检测到 Node 18+，跳过前端构建。装好后可运行: bash Scripts/update.sh --frontend"
        return 0
    fi
    ( cd Frontend \
        && npm config set registry "$NPM_REGISTRY_MIRROR" \
        && ( npm ci || npm install ) \
        && npm run build ) \
        || { log_warn "前端构建失败（不阻断后端，详见 Frontend/ 排错）"; return 0; }
    log_info "前端构建完成: Frontend/dist/"
}

check_mcdr_dep_plugins() {
    # 补丁发现 A：pch_system 依赖 uuid_api_remake 与 minecraft_data_api
    local plugins_dir=$1
    local found_uuid=0 found_mda=0
    local f
    for f in "$plugins_dir"/*; do
        [[ -e "$f" ]] || continue
        local base; base=$(basename "$f")
        case "$base" in
            *uuid_api_remake*) found_uuid=1 ;;
            *MinecraftDataAPI*|*minecraft_data_api*) found_mda=1 ;;
        esac
    done
    if [[ $found_uuid -eq 0 ]]; then
        log_warn "未找到依赖插件 uuid_api_remake（pch_system 加载需要）→ https://github.com/gubaiovo/MCDR_uuid_api_remake"
    fi
    if [[ $found_mda -eq 0 ]]; then
        log_warn "未找到依赖插件 minecraft_data_api（pch_system 加载需要）→ MCDR 插件市场 MinecraftDataAPI"
    fi
}

deploy_mcdr_plugin() {
    [[ $NO_MCDR -eq 1 ]] && { log_info "跳过 MCDR 插件拷贝（--no-mcdr）"; return 0; }
    log_step "部署 pch_system 插件到 MCDR"
    local mcdr_root="${MCDR_ROOT_OVERRIDE:-${PCH_MCDR_ROOT:-}}"
    if [[ -z "$mcdr_root" ]]; then
        read_interactive mcdr_root "  MCDR 根目录绝对路径（含 plugins/ 和 config/）" "/opt/mcdr"
    fi
    [[ -d "$mcdr_root/plugins" && -d "$mcdr_root/config" ]] \
        || die "MCDR 根目录无效（需含 plugins/ 与 config/ 子目录）: $mcdr_root"
    check_mcdr_dep_plugins "$mcdr_root/plugins"

    # 旧版插件 id 为 htcmc_auth → 先迁移（搬 config + 删旧目录，避免与新 pch_system 双注册 !!PCH）
    migrate_legacy_plugin_name "$mcdr_root"

    # 拷贝插件（install 用 --delete 清旧残留）
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete \
            --exclude='__pycache__' --exclude='*.pyc' --exclude='tests' --exclude='.pytest_cache' \
            --exclude='CLAUDE.md' --exclude='docs' \
            McdrPlugin/ "$mcdr_root/plugins/pch_system/"
    else
        rm -rf "$mcdr_root/plugins/pch_system"
        cp -r McdrPlugin "$mcdr_root/plugins/pch_system"
        find "$mcdr_root/plugins/pch_system" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
        find "$mcdr_root/plugins/pch_system" -type d -name tests -prune -exec rm -rf {} + 2>/dev/null || true
        find "$mcdr_root/plugins/pch_system" -type d -name docs -prune -exec rm -rf {} + 2>/dev/null || true
        rm -f "$mcdr_root/plugins/pch_system/CLAUDE.md" 2>/dev/null || true
        log_warn "无 rsync，已用 cp -r（可能残留 __pycache__）"
    fi
    log_info "插件已拷贝: $mcdr_root/plugins/pch_system/"

    # 写 config/pch_system/config.json（api_url 按拓扑推断，token 强制复用 .env）
    local cfg_dir="$mcdr_root/config/pch_system"
    local cfg="$cfg_dir/config.json"
    mkdir -p "$cfg_dir"
    local api_url svc_token
    svc_token=$(env_get MCDR_SERVICE_TOKEN)
    [[ -n "$svc_token" ]] || { svc_token="change_me_service_token"; log_warn ".env 未读到 MCDR_SERVICE_TOKEN，请手动同步"; }
    api_url="${MCDR_API_URL_OVERRIDE:-${PCH_MCDR_API_URL:-}}"
    if [[ -z "$api_url" ]]; then
        local default_url; default_url=$(detect_mcdr_topology "$mcdr_root")
        read_interactive api_url "  插件访问后端的 URL（MCDR 与 backend 同机=127.0.0.1；同 docker 网络=服务名）" "$default_url"
    fi
    if [[ -f "$cfg" ]] && [[ $OVERWRITE_MCDR_CONFIG -eq 0 ]]; then
        log_warn "已有 ${cfg}，保留（仅提示：请确认 service_token 与 .env MCDR_SERVICE_TOKEN 一致；强写用 --mcdr-overwrite-config）"
    else
        if command -v jq >/dev/null 2>&1; then
            jq --arg api "$api_url" --arg tok "$svc_token" \
                '. + {api_url:$api, service_token:$tok}' \
                McdrPlugin/config.json.example > "$cfg"
        else
            python3 -c "
import json,sys
d=json.load(open('McdrPlugin/config.json.example'))
d['api_url']=sys.argv[1]; d['service_token']=sys.argv[2]
json.dump(d,open('$cfg','w'),ensure_ascii=False,indent=2)
" "$api_url" "$svc_token"
        fi
        log_info "config.json 已生成: ${cfg}（api_url=${api_url}）"
    fi

    cat >&2 <<EOF
$(log_warn "请在游戏内/MCDR 控制台执行热重载（脚本无法可靠注入）:")
    !!MCDR plugin reload pch_system
EOF
    MCDR_DEPLOYED_ROOT=$mcdr_root
    MCDR_DEPLOYED_API_URL=$api_url
}

save_state_and_summary() {
    log_step "持久化部署状态"
    local version; version=$(current_ref)
    local commit; commit=$(git rev-parse --short HEAD)
    save_deploy_config \
        PCH_DEPLOY_VERSION "$version" \
        PCH_DEPLOY_COMMIT "$commit" \
        PCH_DEPLOY_STRATEGY "$STRATEGY" \
        PCH_MCDR_ROOT "${MCDR_DEPLOYED_ROOT:-${MCDR_ROOT_OVERRIDE:-${PCH_MCDR_ROOT:-}}}" \
        PCH_MCDR_API_URL "${MCDR_DEPLOYED_API_URL:-${MCDR_API_URL_OVERRIDE:-${PCH_MCDR_API_URL:-}}}" \
        PCH_FRONTEND_BUILT "$( [[ -d Frontend/dist ]] && echo 1 || echo 0 )" \
        PCH_INSTALL_DATE "$(date +%Y-%m-%dT%H:%M:%S)"

    echo
    log_info "====================================== 安装完成 ======================================"
    log_info "后端健康:   curl http://127.0.0.1:8000/healthz   (期望 {\"status\":\"ok\"})"
    log_info "迁移版本:   $(dcc exec -T backend alembic current 2>/dev/null || echo unknown)"
    local web_port; web_port=$(env_get WEB_PORT); web_port=${web_port:-5173}
    if web_profile_active; then
        log_info "前端 Web:    http://<本机IP>:${web_port}（compose web 服务：托管 dist + 反代 /api 到 backend）"
    elif [[ -d Frontend/dist ]]; then
        log_info "前端产物:   Frontend/dist/（web 未启用，自管 nginx 托管 + 反代 /api 到 :8000，见 Scripts/README.md §10）"
    fi
    [[ -n "${MCDR_DEPLOYED_ROOT:-}" ]] && {
        log_info "插件已部署: ${MCDR_DEPLOYED_ROOT}/plugins/pch_system/"
        log_info "插件配置:   ${MCDR_DEPLOYED_ROOT}/config/pch_system/config.json"
    }
    echo
    log_warn "待办："
    [[ $RELOGIN_REQUIRED -eq 1 ]] && log_warn "  - 运行 newgrp docker 或重新登录，才能免 sudo 使用 docker"
    [[ -z "${MCDR_DEPLOYED_ROOT:-}" && $NO_MCDR -eq 0 ]] && log_warn "  - 部署 pch_system 到你的 MCDR（或重跑 install.sh 带 --mcdr-root）"
    log_warn "  - 在游戏内执行: !!MCDR plugin reload pch_system"
    log_warn "  - 确认依赖插件已装: uuid_api_remake + minecraft_data_api"
    [[ -f .env ]] && log_warn "  - 检查 .env：WEB_BASE_URL 需为玩家可访问的前端地址（单机 + web 默认 5173 已对齐；用域名/反代则改成真实 URL 后 docker compose restart backend）"
    log_info "======================================================================================"
}

# ---------- main ----------
main() {
    parse_args "$@"
    check_in_repo
    run_step "OS / 权限探测" detect_os
    run_step "检测/安装 Docker + Compose" ensure_docker
    setup_mirrors
    sync_repo
    ensure_env
    ensure_override
    start_stack
    run_migrations
    build_frontend
    deploy_mcdr_plugin
    save_state_and_summary
}

main "$@"
