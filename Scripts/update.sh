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
# macOS sudo env_reset 可能清空 locale，保底设 C.UTF-8（不覆盖用户已设值）。
export LC_ALL="${LC_ALL:-C.UTF-8}"
export LANG="${LANG:-C.UTF-8}"
trap 'pch_err_trap $LINENO' ERR

PCH_REPO_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$PCH_REPO_DIR"

# ---------- 参数 ----------
FORCE=0
FORCE_FRONTEND=0
NO_MCDR=0
NO_SYNC=0
UPGRADE_PLUGINS=0
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
  --upgrade-plugins      经 MCDR pim 升级已装依赖插件到 latest（缺失依赖每次 update 自动补装，无需本 flag）
  --no-sync              跳过远端拉取（用当前工作树，不 fetch / 不 checkout；开发/测试用）
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
            --upgrade-plugins) UPGRADE_PLUGINS=1; shift ;;
            --no-sync) NO_SYNC=1; shift ;;
            --mcdr-root) MCDR_ROOT_OVERRIDE=$2; shift 2 ;;
            -h|--help) usage; exit 0 ;;
            *) die "未知参数: $1（用 --help 查看用法）" ;;
        esac
    done
}

# .env 读字段（键缺失 / .env 缺失 → 空串 + 退出码 0；调用方用 ${var:-default} / [[ -n ]] 判空）。
# 末尾 || true 防 grep 无匹配在 pipefail 下令 env_get 非零 → 裸赋值 set -e 静默退出（a022d73 同类）。
env_get() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }

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
    # --no-sync：不 fetch 远端、不 checkout，用当前工作树。OLD=部署记录，NEW=当前 HEAD。
    if [[ $NO_SYNC -eq 1 ]]; then
        log_step "跳过远端拉取（--no-sync），用当前工作树"
        OLD_SHA=$(cfg_get PCH_DEPLOY_COMMIT)
        OLD_REF=$(cfg_get PCH_DEPLOY_VERSION)
        NEW_SHA=$(git rev-parse HEAD)
        NEW_REF=$(current_ref)
        if [[ -z "$OLD_SHA" ]]; then
            log_warn "部署配置无 PCH_DEPLOY_COMMIT，--no-sync 无法计算 diff，视为无变更"
            OLD_SHA=$NEW_SHA; OLD_REF=$NEW_REF
        else
            # 部署记录存的是短 hash（install 时 git rev-parse --short），规范化为完整 hash 再比较，
            # 否则短/长 hash 字符串永不相等 → 即便 OLD/NEW 是同一 commit 也误报"本地变更"。
            OLD_SHA=$(git rev-parse "$OLD_SHA^{commit}" 2>/dev/null || echo "$OLD_SHA")
        fi
        if [[ "$OLD_SHA" == "$NEW_SHA" ]]; then
            log_info "当前工作树与部署记录一致（${OLD_REF}），--no-sync 仍执行更新流程（迁移/插件/健康）"
        else
            log_info "本地变更: $OLD_REF → ${NEW_REF}（--no-sync，未 fetch 远端）"
        fi
        return 0
    fi

    local strategy; strategy=$(resolve_strategy)
    [[ -n "$strategy" ]] || strategy="tag"
    GH_MIRROR_ENTRY=$(pick_github_mirror)

    OLD_SHA=$(git rev-parse HEAD)
    OLD_REF=$(current_ref)

    log_step "拉取更新（strategy=${strategy}）"
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
        log_info "已是最新（${OLD_REF}），无需更新"
        # 依赖插件补装已由 main 的 ensure_dep_plugins 先行完成；此处放行 --upgrade-plugins
        # （主动升 latest 与代码版本无关，否则该 flag 在最新状态下永不可达）
        [[ $UPGRADE_PLUGINS -eq 1 ]] && upgrade_dep_plugins
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
    [[ $NO_SYNC -eq 1 ]] && { log_info "跳过 checkout（--no-sync）"; return 0; }
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

    local rebuild=0 backend_changed=0 compose_changed=0 web_changed=0
    if printf '%s\n' "$changes" | grep -qE '^Backend/(Dockerfile|pyproject\.toml)$'; then
        rebuild=1; backend_changed=1
    fi
    if printf '%s\n' "$changes" | grep -qE '^Backend/(app|alembic)/'; then
        backend_changed=1
    fi
    if printf '%s\n' "$changes" | grep -qE '^(docker-compose\.yml|docker-compose\.override\.yml)$'; then
        compose_changed=1
    fi
    if printf '%s\n' "$changes" | grep -qE '^Frontend/'; then
        web_changed=1
    fi

    if (( rebuild )); then
        log_step "Dockerfile / pyproject.toml 变更 → 重建 backend 镜像"
        compose_build backend
        dcc up -d backend || die "docker compose up -d backend 失败（postgres 与数据卷未受影响，未执行 down -v；docker compose logs backend 排查）"
    elif (( backend_changed )); then
        log_step "Backend 代码变更 → force-recreate（mount 策略，秒级，无需 rebuild）"
        dcc up -d --force-recreate backend || die "docker compose up -d --force-recreate backend 失败（postgres 与数据卷未受影响；docker compose logs backend 排查）"
    elif (( compose_changed )); then
        log_step "compose 配置变更 → up -d（自动 recreate）"
        # compose 全量 up 会连带起 web（若 profile 激活）→ 先回收宿主端口，避免 "address already in use" 裸退出。
        # recreate 只换容器不删 volume，postgres 数据卷不受影响（绝非 down -v）。
        if web_profile_active; then
            local _wp="${WEB_PORT:-$(env_get WEB_PORT)}"; _wp="${_wp:-5173}"
            reclaim_web_port "$_wp" || die "web 宿主端口 ${_wp} 被占且未释放（详见上方；postgres 与数据卷未受影响）"
        fi
        dcc up -d || die "docker compose up -d 失败（端口冲突见上方；postgres 与数据卷未受影响，未执行 down -v）"
    elif (( ENV_CHANGED )); then
        log_step ".env 已补全 → force-recreate backend（注入新 env）"
        dcc up -d --force-recreate backend || die "docker compose up -d --force-recreate backend 失败（postgres 与数据卷未受影响；docker compose logs backend 排查）"
    else
        log_info "无 Backend / compose 变更，跳过后端容器操作"
    fi

    # web 镜像：前端 dist 烘焙进镜像（非 bind-mount），任何 Frontend/ 变更都需重建。
    # 仅 web profile 激活时；--frontend 强制重建。web 未激活则由 update_frontend() 走宿主 npm build。
    if web_profile_active && { (( web_changed )) || [[ $FORCE_FRONTEND -eq 1 ]]; }; then
        log_step "Frontend 变更（容器路径）→ 重建 web 镜像"
        compose_build web
        # 启动 web 前回收宿主端口（清本项目残留 web 容器 / 询问停掉占用者），避免裸 set -e 退出。
        local _wp="${WEB_PORT:-$(env_get WEB_PORT)}"; _wp="${_wp:-5173}"
        reclaim_web_port "$_wp" || die "web 宿主端口 ${_wp} 被占且未释放（详见上方；postgres 与数据卷未受影响）"
        dcc up -d web || die "docker compose up -d web 失败（端口冲突见上方；其他错误 docker compose logs web）"
    fi
}

run_migrations() {
    log_step "Alembic 迁移（upgrade head，幂等）"
    local pg_user pg_db
    pg_user=$(env_get POSTGRES_USER); pg_db=$(env_get POSTGRES_DB)
    dump_pre_migration pre-update "$pg_user" "$pg_db"

    if ! dcc exec -T backend alembic upgrade head; then
        log_error "alembic upgrade head 失败"
        dcc exec -T backend alembic current 2>/dev/null || true
        cat >&2 <<EOF
迁移失败处理（绝不自动 downgrade，score_ledger append-only）：
  1. 排查迁移:   ls Backend/alembic/versions/
  2. 手动恢复:   dcc exec -T postgres psql -U $pg_user -d $pg_db < $MIGRATION_BAK
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
    # web 服务启用：前端构建在镜像内（decide_rebuild 的 web 分支已处理），跳过宿主 build
    if web_profile_active; then
        log_info "web profile 启用：前端由 web 镜像构建，跳过宿主 npm run build"
        return 0
    fi
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
    [[ -d "$mcdr_root/plugins" ]] || { log_warn "MCDR 根目录无效: ${mcdr_root}（跳过插件更新）"; return 0; }

    local changes
    changes=$(git diff --name-only "$OLD_SHA" "$NEW_SHA")
    if printf '%s\n' "$changes" | grep -qE '^McdrPlugin/(pch_system/|mcdreforged\.plugin\.json|requirements\.txt|config\.json\.example)'; then
        log_step "增量更新 pch_system 插件（保守，不删玩家手改）"
        # 旧版插件 id 为 htcmc_auth → 先迁移（搬 config + 删旧目录，避免与新 pch_system 双注册 !!PCH）
        migrate_legacy_plugin_name "$mcdr_root"
        if command -v rsync >/dev/null 2>&1; then
            rsync -a \
                --exclude='__pycache__' --exclude='*.pyc' --exclude='tests' --exclude='.pytest_cache' \
                --exclude='CLAUDE.md' --exclude='docs' \
                McdrPlugin/ "$mcdr_root/plugins/pch_system/"
        else
            cp -r McdrPlugin/* "$mcdr_root/plugins/pch_system/" 2>/dev/null || true
            find "$mcdr_root/plugins/pch_system" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
            find "$mcdr_root/plugins/pch_system" -type d -name tests -prune -exec rm -rf {} + 2>/dev/null || true
            find "$mcdr_root/plugins/pch_system" -type d -name docs -prune -exec rm -rf {} + 2>/dev/null || true
            rm -f "$mcdr_root/plugins/pch_system/CLAUDE.md" 2>/dev/null || true
        fi
        log_info "插件已增量同步: $mcdr_root/plugins/pch_system/"
        # mcdreforged.plugin.json 的任何字段（version/dependencies/name/description/author/link/entrypoint）
        # 都随 !!MCDR plugin reload 一并重新读取，并由 DependencyWalker 重校 dependencies
        # —— 无需重启 MCDR（reload = unload→load→重校依赖）。仅当 dependencies 收紧到不满足时，
        # reload 会自动卸载插件（重启也救不了，需先满足依赖）。源码（.py）变更同理只需 reload。
        log_warn "请在游戏内执行: !!MCDR plugin reload pch_system"
    else
        log_info "无 pch_system 插件变更"
    fi

    # token 双写一致性校验（补丁 B：不照抄容器服务名 + token 必须与 .env 同值）
    local env_tok cfg_tok cfg_path
    env_tok=$(env_get MCDR_SERVICE_TOKEN)
    cfg_path="$mcdr_root/config/pch_system/config.json"
    if [[ -n "$env_tok" && -f "$cfg_path" ]]; then
        cfg_tok=$(jq -r .service_token "$cfg_path" 2>/dev/null || echo "")
        if [[ -z "$cfg_tok" ]]; then
            cfg_tok=$(python3 -c "import json;print(json.load(open('$cfg_path')).get('service_token',''))" 2>/dev/null || echo "")
        fi
        if [[ -n "$cfg_tok" && "$env_tok" != "$cfg_tok" ]]; then
            log_warn "token 不一致：.env MCDR_SERVICE_TOKEN ≠ 插件 config.service_token。请手动同步（脚本不擅改你的 config）："
            log_warn "  编辑 $cfg_path 的 service_token，改为与 .env 一致，然后 !!MCDR plugin reload pch_system"
        fi
    fi
}

verify_and_summary() {
    log_step "健康验证"
    local _bp="${BACKEND_PORT:-$(env_get BACKEND_PORT)}"; _bp="${_bp:-8000}"
    if ! wait_http_ok "http://127.0.0.1:${_bp}/healthz" 60 200; then
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

check_compose() {
    # update.sh 假设 install.sh 已装好 Docker；此处只检测、不安装。
    # 必须在任何 dcc 调用前设置 ${COMPOSE}，否则 dcc() 会 die（且 pg_dump 那行的
    # 2>/dev/null 会吞掉 die 的错误信息，表现为"迁移前快照后静默 exit 1"）。
    detect_compose
    [[ -n "$COMPOSE" ]] \
        || die "docker compose 不可用（既无 v2 plugin 也无 v1 docker-compose）——请先运行 bash Scripts/install.sh（或安装 Docker + compose 插件）"
}

# .env 增量补全 wrapper：补全缺失键，changed 则置 ENV_CHANGED=1 供 decide_rebuild 决定是否 force-recreate。
ENV_CHANGED=0
ensure_env_keys_update() {
    local status
    status=$(ensure_env_keys)
    # 用 if 而非 [[ ]] && VAR= ：后者在 status=unchanged 时退出码 1，作为函数末行会被
    # set -Eeuo pipefail 捕获，导致脚本在 main 的裸调用处中断（曾使更新半途中止）。
    if [[ "$status" == "changed" ]]; then
        ENV_CHANGED=1
    fi
}

# ---------- main ----------
# 依赖插件缺失自动补装（幂等、无交互）。置于 fetch_and_compare 之前是关键：
# 老部署升级断裂恢复场景下，旧 update.sh 已把 PCH_DEPLOY_COMMIT 写为新 sha，
# 二次运行会在 fetch_and_compare 的「已是最新」分支 early-exit——
# update_mcdr 及其后所有步骤都不可达，必须在此先兜底（CHANGELOG 恢复指引依赖此行为）。
ensure_dep_plugins() {
    [[ $NO_MCDR -eq 1 ]] && return 0
    local mcdr_root; mcdr_root="${MCDR_ROOT_OVERRIDE:-$(cfg_get PCH_MCDR_ROOT)}"
    [[ -n "$mcdr_root" && -d "$mcdr_root/plugins" ]] || { log_info "未配置 MCDR 根目录，跳过依赖插件检查"; return 0; }
    mcdr_install_dep_plugins "$mcdr_root/plugins" \
        || log_error "依赖插件补装失败，请手动执行: mcdreforged pim download <缺失插件id> -o ${mcdr_root}/plugins && mcdreforged pim pipi <对应 .mcdr>"
}

upgrade_dep_plugins() {
    # 经 MCDR 原生 pim 升级 pch_system 的依赖插件（--upgrade-plugins）：
    # 临时目录下载 latest → 文件名比对（资产名内嵌版本）→ 删旧换新 → pipi 装 Python 依赖
    [[ $NO_MCDR -eq 1 ]] && { log_info "跳过依赖插件升级（--no-mcdr）"; return 0; }
    local mcdr_root; mcdr_root="${MCDR_ROOT_OVERRIDE:-$(cfg_get PCH_MCDR_ROOT)}"
    [[ -n "$mcdr_root" ]] || { log_info "未配置 MCDR 根目录，跳过依赖插件升级"; return 0; }
    local plugins_dir="$mcdr_root/plugins"
    [[ -d "$plugins_dir" ]] || { log_warn "MCDR 根目录无效: ${mcdr_root}（跳过依赖插件升级）"; return 0; }

    local pim_cmd; pim_cmd=$(mcdr_pim_cmd) || { mcdr_pim_missing; return 1; }
    local tmp; tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' RETURN

    log_step "升级 MCDR 依赖插件（pim latest）"
    # shellcheck disable=SC2046  # 故意分词：id 列表逐个传参
    "$pim_cmd" pim download $(mcdr_dep_plugin_ids) -o "$tmp" \
        || { log_error "pim download 失败（网络 / 插件目录不可达？）"; return 1; }
    # pim 对部分失败静默 exit 0（如目录无效 / 网络断），退出码拦不住，须校验产物存在
    local -a downloaded=("$tmp"/*)
    if (( ${#downloaded[@]} == 0 )) || [[ ! -e "${downloaded[0]}" ]]; then
        log_error "pim download 未产出任何文件（pim 部分失败静默 exit 0，详见上方输出）"
        return 1
    fi

    local -a changed=()
    local f base id old
    for f in "$tmp"/*; do
        [[ -e "$f" ]] || continue
        base=$(basename "$f")
        if [[ -e "$plugins_dir/$base" ]]; then
            log_info "已最新: $base"
            continue
        fi
        id=$(mcdr_plugin_id_of "$base") || { log_warn "跳过非依赖插件文件: $base"; continue; }
        # 先删同插件旧文件再换入，防同 id 双 .mcdr 加载冲突
        for old in "$plugins_dir"/*; do
            [[ -e "$old" ]] || continue
            if mcdr_file_matches_plugin "$(basename "$old")" "$id"; then
                log_info "移除旧版: $(basename "$old")"
                rm -f "$old"
            fi
        done
        # /tmp 与 plugins 常跨文件系统（mv=copy+rename），失败显式 return 走 RETURN trap 清理，
        # 避免旧已删新未进的缺失窗口静默延续
        mv "$f" "$plugins_dir/$base" || { log_error "mv 失败: $f → $plugins_dir/$base"; return 1; }
        changed+=("$plugins_dir/$base")
    done

    ((${#changed[@]})) || { log_info "依赖插件均为最新，无需变更"; return 0; }
    mcdr_pim_pipi "${changed[@]}" || { log_error "pim pipi 失败"; return 1; }
    # pim 对部分失败静默 exit 0：按 id 逐个复检兜底
    local missing_after; missing_after=$(mcdr_missing_dep_plugins "$plugins_dir")
    [[ -z "$missing_after" ]] || { log_error "升级后仍缺失依赖插件（pim 部分失败）: ${missing_after}。请手动补装或稍后重跑"; return 1; }
    log_info "已升级: ${changed[*]}（游戏内执行 !!MCDR reload plugin 生效）"
}

main() {
    parse_args "$@"
    check_compose
    check_managed
    ensure_dep_plugins
    fetch_and_compare
    guard_dirty
    do_checkout
    ensure_env_keys_update   # checkout 后补全 .env 缺失键（读新版 .env.example）；changed → decide_rebuild 兜底 force-recreate
    decide_rebuild
    run_migrations
    update_frontend
    update_mcdr
    [[ $UPGRADE_PLUGINS -eq 1 ]] && upgrade_dep_plugins
    verify_and_summary
}

main "$@"
