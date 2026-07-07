#!/usr/bin/env bash
# update.sh —— PCHSystem 一键更新
#
# 用法（在仓库内执行）：
#   bash Scripts/update.sh                 # 沿用 install 时策略（默认拉最新发版 tag）
#   bash Scripts/update.sh --edge          # 临时切到 main 最新
#   bash Scripts/update.sh --force         # 接管非脚本安装的部署 / 跳过 dirty 保护
#   bash Scripts/update.sh --frontend      # 强制重建前端
#
# 流程：读部署配置 → 网络镜像自适应 → fetch+比较版本(dirty 保护) → 智能重建判断
#       → alembic 迁移 → 前端增量 → 插件增量(不带 --delete) + token 校验 → 健康验证 + 摘要
#
# 红线：迁移前 pg_dump；迁移失败绝不自动 downgrade（score_ledger append-only）；不自动回滚。

set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
trap 'pch_err_trap $LINENO' ERR

PCH_REPO_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$PCH_REPO_DIR"

# ---------- 参数 ----------
FORCE=0
FORCE_FRONTEND=0
NO_MCDR=0
MCDR_ROOT_OVERRIDE=""
STRATEGY_OVERRIDE=""

usage() {
    cat <<'EOF'
PCHSystem update.sh —— 一键更新

用法: bash Scripts/update.sh [选项]

选项:
  --edge                 本次临时拉 main 最新（不改部署策略）
  --yes                  无人值守（等价 PCH_YES=1）
  --force                接管非脚本安装的部署 / 跳过本地改动保护
  --frontend             强制重建前端（即使无 Frontend/ 变更）
  --no-mcdr              跳过 MCDR 插件增量更新
  --mcdr-root DIR        覆盖部署配置里的 MCDR 根目录
  -h, --help             显示本帮助
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --edge) STRATEGY_OVERRIDE="edge"; shift ;;
            --yes) export PCH_YES=1; shift ;;
            --force) FORCE=1; shift ;;
            --frontend) FORCE_FRONTEND=1; shift ;;
            --no-mcdr) NO_MCDR=1; shift ;;
            --mcdr-root) MCDR_ROOT_OVERRIDE=$2; shift 2 ;;
            -h|--help) usage; exit 0 ;;
            *) die "未知参数: $1（用 --help 查看用法）" ;;
        esac
    done
}

env_get() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2-; }

# ---------- 步骤 ----------
check_managed() {
    load_deploy_config
    if [[ ! -f "$(deploy_config_path)" ]]; then
        [[ $FORCE -eq 1 ]] || die "未找到部署配置 $(deploy_config_path)。若为手动部署，可用 --force 接管。"
        log_warn "--force 接管：把当前 HEAD 写入部署配置"
        local cur strat
        cur=$(current_ref)
        strat=$([[ $cur == edge ]] && echo edge || echo tag)
        save_deploy_config \
            PCH_DEPLOY_VERSION "$cur" \
            PCH_DEPLOY_COMMIT "$(git rev-parse --short HEAD)" \
            PCH_DEPLOY_STRATEGY "$strat" \
            PCH_INSTALL_DATE "$(date +%Y-%m-%dT%H:%M:%S)"
        load_deploy_config
    fi
}

resolve_strategy() {
    if [[ -n "$STRATEGY_OVERRIDE" ]]; then echo "$STRATEGY_OVERRIDE"
    else cfg_get PCH_DEPLOY_STRATEGY; fi
}

# fetch + 计算 OLD/NEW；返回 OLD_SHA NEW_SHA（全局）；一致则 exit 0
fetch_and_compare() {
    local strategy; strategy=$(resolve_strategy)
    [[ -n "$strategy" ]] || strategy="tag"
    GH_MIRROR_ENTRY=$(pick_github_mirror)

    OLD_SHA=$(git rev-parse HEAD)
    OLD_REF=$(current_ref)

    log_step "拉取更新（strategy=$strategy）"
    if [[ "$strategy" == "edge" ]]; then
        gh_git "$GH_MIRROR_ENTRY" fetch origin "$PCH_DEFAULT_BRANCH"
        NEW_SHA=$(git rev-parse "origin/${PCH_DEFAULT_BRANCH}")
        NEW_REF="edge"
    else
        gh_git "$GH_MIRROR_ENTRY" fetch --all --tags --prune
        local tag; tag=$(latest_tag)
        [[ -n "$tag" ]] || die "未找到发版 tag（*-v*）"
        NEW_SHA=$(git rev-list -n 1 "$tag")
        NEW_REF="tag:$tag"
    fi

    if [[ "$OLD_SHA" == "$NEW_SHA" ]]; then
        log_info "已是最新（$OLD_REF），无需更新"
        exit 0
    fi
    log_info "版本变更: $OLD_REF → $NEW_REF"
}

guard_dirty() {
    local dirty
    dirty=$(git status --porcelain --untracked-files=no 2>/dev/null)
    if [[ -n "$dirty" ]]; then
        if [[ $FORCE -eq 1 ]] || confirm "检测到本地跟踪文件改动，继续将覆盖？ [y/N]" "n"; then
            log_warn "强制更新（--force，本地改动将被 checkout 覆盖；gitignored 文件不受影响）"
        else
            die "已取消。请先 git stash / git commit 本地改动，或用 --force。"
        fi
    fi
}

do_checkout() {
    if [[ "$NEW_REF" == "edge" ]]; then
        git checkout "$PCH_DEFAULT_BRANCH"
        gh_git "$GH_MIRROR_ENTRY" pull --ff-only origin "$PCH_DEFAULT_BRANCH"
    else
        git checkout "${NEW_REF#tag:}"
    fi
}

decide_rebuild() {
    local changes
    changes=$(git diff --name-only "$OLD_SHA" "$NEW_SHA")

    local rebuild=0 backend_changed=0 compose_changed=0
    if printf '%s\n' "$changes" | grep -qE '^Backend/(Dockerfile|pyproject\.toml)$'; then
        rebuild=1; backend_changed=1
    fi
    if printf '%s\n' "$changes" | grep -qE '^Backend/(app|alembic)/'; then
        backend_changed=1
    fi
    if printf '%s\n' "$changes" | grep -qE '^(docker-compose\.yml|docker-compose\.override\.yml)$'; then
        compose_changed=1
    fi

    if (( rebuild )); then
        log_step "Dockerfile / pyproject.toml 变更 → 重建 backend 镜像"
        compose_build backend
        dcc up -d backend
    elif (( backend_changed )); then
        log_step "Backend 代码变更 → force-recreate（mount 策略，秒级，无需 rebuild）"
        dcc up -d --force-recreate backend
    elif (( compose_changed )); then
        log_step "compose 配置变更 → up -d（自动 recreate）"
        dcc up -d
    else
        log_info "无 Backend / compose 变更，跳过容器操作"
    fi
}

run_migrations() {
    log_step "Alembic 迁移（upgrade head，幂等）"
    mkdir -p backups
    ensure_gitignored backups/
    local pg_user pg_db bak
    pg_user=$(env_get POSTGRES_USER); pg_db=$(env_get POSTGRES_DB)
    bak="backups/pre-update-$(date +%Y%m%d-%H%M%S).sql"
    log_info "迁移前快照: $bak"
    dcc exec -T postgres pg_dump -U "$pg_user" "$pg_db" > "$bak" 2>/dev/null \
        || log_warn "pg_dump 失败（忽略，继续迁移）"

    if ! dcc exec -T backend alembic upgrade head; then
        log_error "alembic upgrade head 失败"
        dcc exec -T backend alembic current 2>/dev/null || true
        cat >&2 <<EOF
迁移失败处理（绝不自动 downgrade，score_ledger append-only）：
  1. 排查迁移:   ls Backend/alembic/versions/
  2. 手动恢复:   dcc exec -T postgres psql -U $pg_user -d $pg_db < $bak
  3. 回滚代码:   git checkout ${OLD_REF#tag:}  然后 dcc up -d --build backend
EOF
        die "alembic 迁移失败"
    fi
    log_info "迁移完成，当前版本: $(dcc exec -T backend alembic current 2>/dev/null || echo unknown)"
}

check_node() {
    command -v node >/dev/null 2>&1 || return 1
    local v; v=$(node -v 2>/dev/null | sed 's/v//'); local major=${v%%.*}
    [[ "$major" =~ ^[0-9]+$ ]] || return 1
    (( major >= 18 ))
}

update_frontend() {
    local changes
    changes=$(git diff --name-only "$OLD_SHA" "$NEW_SHA")
    if [[ $FORCE_FRONTEND -eq 1 ]] || printf '%s\n' "$changes" | grep -qE '^Frontend/(package\.json|package-lock\.json)$'; then
        if check_node; then
            log_step "前端依赖变更 → npm install"
            ( cd Frontend && npm config set registry "$NPM_REGISTRY_MIRROR" && ( npm ci || npm install ) ) \
                || log_warn "npm install 失败（不阻断）"
        else
            log_warn "无 Node 18+，跳过前端依赖更新"
        fi
    fi
    if [[ $FORCE_FRONTEND -eq 1 ]] || printf '%s\n' "$changes" | grep -qE '^Frontend/'; then
        if check_node; then
            log_step "前端代码变更 → npm run build"
            ( cd Frontend && npm run build ) \
                || log_warn "前端构建失败（不阻断后端，详见 Frontend/ 排错）"
        else
            log_warn "无 Node 18+，跳过前端构建"
        fi
    fi
}

update_mcdr() {
    [[ $NO_MCDR -eq 1 ]] && { log_info "跳过 MCDR 插件更新（--no-mcdr）"; return 0; }
    local mcdr_root; mcdr_root="${MCDR_ROOT_OVERRIDE:-$(cfg_get PCH_MCDR_ROOT)}"
    [[ -n "$mcdr_root" ]] || { log_info "未配置 MCDR 根目录，跳过插件更新"; return 0; }
    [[ -d "$mcdr_root/plugins" ]] || { log_warn "MCDR 根目录无效: $mcdr_root（跳过插件更新）"; return 0; }

    local changes
    changes=$(git diff --name-only "$OLD_SHA" "$NEW_SHA")
    if printf '%s\n' "$changes" | grep -qE '^McdrPlugin/htcmc_auth/'; then
        log_step "增量更新 htcmc_auth 插件（保守，不删玩家手改）"
        if command -v rsync >/dev/null 2>&1; then
            rsync -a \
                --exclude='__pycache__' --exclude='*.pyc' --exclude='tests' --exclude='.pytest_cache' \
                McdrPlugin/htcmc_auth/ "$mcdr_root/plugins/htcmc_auth/"
        else
            cp -r McdrPlugin/htcmc_auth/* "$mcdr_root/plugins/htcmc_auth/" 2>/dev/null || true
            find "$mcdr_root/plugins/htcmc_auth" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
        fi
        log_info "插件已增量同步: $mcdr_root/plugins/htcmc_auth/"
        if printf '%s\n' "$changes" | grep -q '^McdrPlugin/htcmc_auth/mcdreforged\.plugin\.json$'; then
            log_warn "mcdreforged.plugin.json 变更（version/dependencies）→ 需【重启 MCDR】而非仅 reload；并复核依赖插件 uuid_api_remake / minecraft_data_api 版本"
        else
            log_warn "请在游戏内执行: !!MCDR plugin reload htcmc_auth"
        fi
    else
        log_info "无 htcmc_auth 插件变更"
    fi

    # token 双写一致性校验（补丁 B：不照抄容器服务名 + token 必须与 .env 同值）
    local env_tok cfg_tok cfg_path
    env_tok=$(env_get MCDR_SERVICE_TOKEN)
    cfg_path="$mcdr_root/config/htcmc_auth/config.json"
    if [[ -n "$env_tok" && -f "$cfg_path" ]]; then
        cfg_tok=$(jq -r .service_token "$cfg_path" 2>/dev/null || echo "")
        if [[ -z "$cfg_tok" ]]; then
            cfg_tok=$(python3 -c "import json;print(json.load(open('$cfg_path')).get('service_token',''))" 2>/dev/null || echo "")
        fi
        if [[ -n "$cfg_tok" && "$env_tok" != "$cfg_tok" ]]; then
            log_warn "token 不一致：.env MCDR_SERVICE_TOKEN ≠ 插件 config.service_token。请手动同步（脚本不擅改你的 config）："
            log_warn "  编辑 $cfg_path 的 service_token，改为与 .env 一致，然后 !!MCDR plugin reload htcmc_auth"
        fi
    fi
}

verify_and_summary() {
    log_step "健康验证"
    if ! wait_http_ok http://127.0.0.1:8000/healthz 60 200; then
        cat >&2 <<EOF
$(log_error "健康检查失败")。手动回滚步骤（脚本不自动回滚，避免迁移数据风险）：
  git checkout ${OLD_REF#tag:}
  dcc up -d --build backend
  dcc exec -T backend alembic upgrade head
  # 若新迁移已应用且不向后兼容：dcc exec -T postgres psql -U \$(env_get POSTGRES_USER) -d \$(env_get POSTGRES_DB) < backups/<最近>.sql
EOF
        die "更新后健康检查失败"
    fi

    save_deploy_config \
        PCH_DEPLOY_VERSION "$NEW_REF" \
        PCH_DEPLOY_COMMIT "$(git rev-parse --short HEAD)" \
        PCH_DEPLOY_STRATEGY "$(resolve_strategy)" \
        PCH_LAST_UPDATE_DATE "$(date +%Y-%m-%dT%H:%M:%S)"

    echo
    log_info "====================================== 更新完成 ======================================"
    log_info "版本: $OLD_REF → $NEW_REF"
    log_info "迁移: $(dcc exec -T backend alembic current 2>/dev/null || echo unknown)"
    log_info "健康: curl http://127.0.0.1:8000/healthz → ok"
    log_info "======================================================================================"
}

# ---------- main ----------
main() {
    parse_args "$@"
    check_managed
    fetch_and_compare
    guard_dirty
    do_checkout
    decide_rebuild
    run_migrations
    update_frontend
    update_mcdr
    verify_and_summary
}

main "$@"
