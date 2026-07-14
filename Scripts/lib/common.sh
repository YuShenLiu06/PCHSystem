#!/usr/bin/env bash
# common.sh —— PCHSystem install.sh / update.sh 共享函数库
#
# 设计要点：
# - 仅被入口脚本 source，不单独执行；入口脚本自行 `set -Eeuo pipefail` 并安装 ERR trap。
# - 所有「可能失败但不该阻断」的步骤显式 `|| true` / `warn_continue`；数据/版本完整性步骤失败即 die。
# - 不在顶部 set -euo：交由入口脚本控制，避免 source 时污染调用者 shell。
# - 中国网络四类镜像（GitHub / Docker Hub / PyPI / npm）一律 best-effort，单一镜像不可用绝不 die。

# 防重复 source
[[ -n "${_PCH_COMMON_LOADED:-}" ]] && return 0
_PCH_COMMON_LOADED=1

# ---------- bash 版本守卫 ----------
# macOS 自带 bash 3.2 不支持关联数组（declare -gA/-A）、=~ 等语法。
# 要求 bash 4+：用户可 `brew install bash` 装 bash 5.x，然后用其完整路径运行。
if (( BASH_VERSINFO[0] < 4 )); then
    echo "错误：需要 bash 4.0+（当前 ${BASH_VERSION}）。macOS 自带 bash 3.2 不支持关联数组等语法。" >&2
    echo "请装新版 bash 后用其完整路径运行：" >&2
    echo "  brew install bash" >&2
    echo "  /opt/homebrew/bin/bash Scripts/install.sh   # Apple Silicon" >&2
    echo "  /usr/local/bin/bash Scripts/install.sh      # Intel Mac" >&2
    exit 1
fi

# ---------- 平台探测 ----------
# 被 ensure_docker / install_docker / ensure_docker_registry_mirrors 等的 macOS 分支使用。
PCH_OS=""
case "$(uname -s)" in
    Linux)  PCH_OS="linux" ;;
    Darwin) PCH_OS="macos" ;;
    *)      PCH_OS="unknown" ;;
esac

# ---------- 跨平台命令探测 ----------
# GNU timeout：macOS 无，装 coreutils 后 gtimeout 可用；都没有则降级为无超时直跑。
if   command -v timeout  >/dev/null 2>&1; then TIMEOUT_CMD=(timeout)
elif command -v gtimeout >/dev/null 2>&1; then TIMEOUT_CMD=(gtimeout)
else                                          TIMEOUT_CMD=()
fi
# sed -i：GNU 用 `-i`，BSD/macOS 用 `-i ''`（空 backup 后缀）。
if [[ "$PCH_OS" == "macos" ]]; then SED_I=(-i '')
else                                SED_I=(-i)
fi

# ---------- 全局状态 ----------
COMPOSE=""                 # "docker compose"(v2) 或 "docker-compose"(v1)，由 detect_compose 设置
declare -gA DEPLOY_CONFIG=()  # 部署配置内存映像（load_deploy_config 填充）
# shellcheck disable=SC2034  # 跨文件全局，install.sh 末尾读取
RELOGIN_REQUIRED=0         # 装 docker 加组后是否需要重新登录

# ---------- 常量 ----------
PCH_REPO_URL="https://github.com/YuShenLiu06/PCHSystem.git"
PCH_DEFAULT_BRANCH="main"

# GitHub 镜像候选：<rewrite-prefix>|<insteadOf>
# pick_github_mirror 用 git insteadOf 重写，返回命中的 entry（或空串=直连）
PCH_GH_MIRRORS=(
    "https://ghfast.top/https://github.com|https://github.com"       # ghfast 
    "https://ghproxy.com/https://github.com|https://github.com"      # ghproxy
    "https://kkgithub.com|https://github.com"                        # kkgithub 
    "https://gitclone.com/github.com|https://github.com"             # gitclone
    "https://gh.zwy.one/https://github.com|https://github.com"       # gh.zwy.one
)

PCH_DOCKER_MIRRORS=(
    "https://docker.nju.edu.cn"        # 南京大学
    "https://docker.1ms.run"           # 毫秒镜像
    "https://docker.m.daocloud.io"     # DaoCloud
    "https://mirror.baidubce.com"      # 百度云
)
# shellcheck disable=SC2034  # 跨文件常量，install.sh/update.sh 引用
PIP_INDEX_URL_TUNA="https://pypi.tuna.tsinghua.edu.cn/simple"
# shellcheck disable=SC2034
NPM_REGISTRY_MIRROR="https://registry.npmmirror.com"

# ============================================================
# 日志
# ============================================================
if [[ -t 1 ]]; then
    C_RED=$'\033[31m'; C_YELLOW=$'\033[33m'; C_GREEN=$'\033[32m'
    C_BLUE=$'\033[34m'; C_BOLD=$'\033[1m'; C_RESET=$'\033[0m'
else
    C_RED=""; C_YELLOW=""; C_GREEN=""; C_BLUE=""; C_BOLD=""; C_RESET=""
fi

_log() {
    local level=$1 color=$2; shift 2
    printf '%s[%s] [%s]%s %s\n' "$color" "$(date +%H:%M:%S)" "$level" "$C_RESET" "$*" >&2
}
log_info()  { _log INFO  "$C_GREEN"  "$@"; }
log_warn()  { _log WARN  "$C_YELLOW" "$@"; }
log_error() { _log ERROR "$C_RED"    "$@"; }
log_step()  { _log '▶'   "$C_BLUE$C_BOLD" "$@"; }
die()       { local code=${2:-1}; log_error "${1:-发生错误}"; exit "$code"; }

# ERR trap 处理器（入口脚本：trap 'pch_err_trap $LINENO' ERR）
pch_err_trap() {
    log_error "脚本在第 ${1:-?} 行异常退出（set -e 触发，详见上方日志）"
}

# ============================================================
# 提权与命令检查
# ============================================================
as_root() {
    # root 直接执行；否则 sudo 包装
    if [[ $EUID -eq 0 ]]; then "$@"; else sudo "$@"; fi
}

assert_root_or_sudo() {
    if [[ $EUID -eq 0 ]]; then return 0; fi
    if sudo -n true 2>/dev/null; then return 0; fi
    die "此操作需要 root 权限或免密 sudo，请用 root 运行或配置 sudoers 后重试。"
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "缺少必要命令: $1（请先安装）"
}

detect_os() {
    # stdout: macos | debian | rhel | alpine | arch | unknown
    # macOS 无 /etc/os-release，按平台探测短路返回。
    if [[ "$PCH_OS" == "macos" ]]; then echo "macos"; return 0; fi
    local id="" id_like=""
    if [[ -f /etc/os-release ]]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        id="${ID:-}"; id_like="${ID_LIKE:-}"
    fi
    case "$id" in
        debian|ubuntu) echo "debian" ;;
        centos|rhel|rocky|almalinux|fedora|ol) echo "rhel" ;;
        alpine) echo "alpine" ;;
        arch|manjaro) echo "arch" ;;
        *)
            if [[ " $id_like " == *" debian "* ]]; then echo "debian"
            elif [[ " $id_like " == *" rhel "* || " $id_like " == *" fedora "* ]]; then echo "rhel"
            else echo "unknown"; fi ;;
    esac
}

# ============================================================
# Docker / Compose 检测与安装
# ============================================================
detect_compose() {
    if docker compose version >/dev/null 2>&1; then
        COMPOSE="docker compose"
    elif command -v docker-compose >/dev/null 2>&1 && docker-compose version >/dev/null 2>&1; then
        COMPOSE="docker-compose"
    else
        COMPOSE=""
    fi
}

# dcc —— 统一 compose 调用（COMPOSE 含空格故需 word-split，SC2086 豁免）
dcc() {
    [[ -n "${COMPOSE:-}" ]] || die "docker compose 不可用（既无 v2 plugin 也无 v1 docker-compose）"
    # shellcheck disable=SC2086
    ${COMPOSE} "$@"
}

ensure_docker() {
    # 1) docker 已就绪
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        detect_compose
        [[ -n "$COMPOSE" ]] && return 0
        log_warn "检测到 docker 但缺少 compose，尝试补装 compose 插件..."
        _install_compose_plugin
        detect_compose
        [[ -n "$COMPOSE" ]] && return 0
        die "docker-compose-plugin 安装失败，请手动安装"
    fi
    # 1.5) macOS：docker 命令在但 info 失败（Docker Desktop 未启动），或 docker 缺失
    #      → 不调 get.docker.com（不支持 mac），直接指引装/启动 Docker Desktop。
    if [[ "$PCH_OS" == "macos" ]]; then
        if command -v docker >/dev/null 2>&1; then
            die "检测到 docker 命令但 'docker info' 失败。macOS 上请启动 Docker Desktop 后重跑本脚本。"
        fi
        die "macOS 未检测到 docker。请安装并启动 Docker Desktop：https://www.docker.com/products/docker-desktop/ ，启动后重跑本脚本。"
    fi
    # 2) docker 缺失 → 安装（仅 Linux 走此路径）
    install_docker
    detect_compose
    [[ -n "$COMPOSE" ]] || die "docker 已安装但仍检测不到 compose，请手动安装 docker-compose-plugin"
}

install_docker() {
    [[ "$PCH_OS" == "macos" ]] && die "install_docker 不支持 macOS，请手动安装 Docker Desktop。"
    log_step "安装 Docker"
    assert_root_or_sudo
    local os; os=$(detect_os)
    _ensure_curl
    log_info "使用 get.docker.com 官方脚本（--mirror Aliyun 加速国内拉取）"
    if curl -fsSL https://get.docker.com | as_root sh -s -- --mirror Aliyun; then
        _post_install_docker
        return 0
    fi
    log_warn "get.docker.com 失败，回退发行版原生包管理器（${os}）"
    _install_docker_native "$os"
    _post_install_docker
}

_ensure_curl() {
    command -v curl >/dev/null 2>&1 && return 0
    local os; os=$(detect_os)
    case "$os" in
        debian) as_root apt-get update -y >/dev/null 2>&1 && as_root apt-get install -y curl ;;
        rhel)   as_root dnf install -y curl ;;
        alpine) as_root apk add --no-cache curl ;;
        arch)   as_root pacman -S --noconfirm curl ;;
        *)      die "缺少 curl 且无法自动安装（未知发行版 ${os}），请手动安装 curl" ;;
    esac
}

_install_docker_native() {
    [[ "$PCH_OS" == "macos" ]] && die "_install_docker_native 不支持 macOS。"
    local os=$1
    case "$os" in
        debian) as_root apt-get update -y && as_root apt-get install -y docker.io docker-compose-plugin ;;
        rhel)   as_root dnf install -y docker docker-compose-plugin || as_root yum install -y docker docker-compose-plugin ;;
        alpine) as_root apk add --no-cache docker docker-cli-compose openrc ;;
        arch)   as_root pacman -S --noconfirm docker docker-compose ;;
        *)      die "不支持的发行版（${os}），请手动安装 docker + docker-compose-plugin 后重跑" ;;
    esac
}

_install_compose_plugin() {
    local os; os=$(detect_os)
    case "$os" in
        debian) as_root apt-get update -y && as_root apt-get install -y docker-compose-plugin ;;
        rhel)   as_root dnf install -y docker-compose-plugin ;;
        alpine) as_root apk add --no-cache docker-cli-compose ;;
        arch)   as_root pacman -S --noconfirm docker-compose ;;
        *)      log_warn "未知发行版（${os}），无法自动补装 compose 插件" ;;
    esac
}

_post_install_docker() {
    # macOS：Docker Desktop 自管理（无 systemctl / usermod / getent），整体跳过。
    [[ "$PCH_OS" == "macos" ]] && { log_info "macOS: Docker Desktop 自管理，跳过 systemd 配置"; return 0; }
    # 启动 docker 服务
    as_root systemctl enable --now docker 2>/dev/null \
        || as_root service docker start 2>/dev/null \
        || rc-update add docker default 2>/dev/null \
        || true
    # 当前用户加入 docker 组（非 root 时）
    if [[ $EUID -ne 0 ]] && getent group docker >/dev/null 2>&1; then
        if ! id -nG "$USER" 2>/dev/null | grep -qw docker; then
            as_root usermod -aG docker "$USER"
            log_warn "已将 $USER 加入 docker 组。请运行 newgrp docker 或重新登录后重跑本脚本，才能免 sudo 使用 docker。"
            # shellcheck disable=SC2034  # 全局标志，install.sh 末尾读取
            RELOGIN_REQUIRED=1
        fi
    fi
    sleep 2
    docker info >/dev/null 2>&1 || die "docker 安装后仍无法运行 'docker info'，请检查 docker 服务状态"
}

# ============================================================
# 网络探活与镜像源
# ============================================================
probe_url() {
    local url=$1 timeout=${2:-8}
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --max-time "$timeout" -o /dev/null "$url" 2>/dev/null
    else
        wget -q --spider "--timeout=$timeout" "$url" 2>/dev/null
    fi
}

# _run_with_timeout：有 timeout/gtimeout 则加超时，否则直跑（macOS 无 coreutils 时的降级）。
_run_with_timeout() {
    local secs=$1; shift
    if [[ ${#TIMEOUT_CMD[@]} -gt 0 ]]; then "${TIMEOUT_CMD[@]}" "$secs" "$@"
    else "$@"; fi
}

# pick_github_mirror: stdout 输出命中的 "<rewrite>|<insteadOf>" 或空串（=直连）
pick_github_mirror() {
    # 直连可用则直接走直连（最快）
    if _run_with_timeout 8 git ls-remote --exit-code "${PCH_REPO_URL}" HEAD >/dev/null 2>&1; then
        echo ""; return 0
    fi
    log_warn "GitHub 直连不通，尝试镜像源..."
    local entry rewrite insteadof test_url
    for entry in "${PCH_GH_MIRRORS[@]}"; do
        rewrite="${entry%|*}"
        insteadof="${entry#*|}"
        test_url="${rewrite}${PCH_REPO_URL#"${insteadof}"}"
        log_info "  探测镜像: $rewrite"
        if _run_with_timeout 12 git ls-remote --exit-code "$test_url" HEAD >/dev/null 2>&1; then
            log_info "  选用镜像: $rewrite"
            echo "$entry"; return 0
        fi
    done
    log_warn "所有镜像均不可达，回退直连（可能很慢或失败）"
    echo ""
}

# gh_git: 用镜像 entry 执行 git 命令（entry 为空则直连）
gh_git() {
    local entry=$1; shift
    if [[ -z "$entry" ]]; then
        git "$@"
    else
        local rewrite insteadof
        rewrite="${entry%|*}"
        insteadof="${entry#*|}"
        git -c "url.${rewrite}.insteadOf=${insteadof}" "$@"
    fi
}

# 配置 Docker registry 镜像加速（best-effort，失败不阻断）
ensure_docker_registry_mirrors() {
    # macOS：Docker Desktop 不读 /etc/docker/daemon.json（用 GUI Settings → Docker Engine）。
    if [[ "$PCH_OS" == "macos" ]]; then
        log_warn "macOS: Docker Desktop 用 GUI（Settings → Docker Engine）配 registry mirrors，脚本跳过自动配置。"
        return 0
    fi
    if [[ $EUID -ne 0 ]] && ! sudo -n true 2>/dev/null; then
        log_warn "无 root 权限，跳过 Docker registry 镜像加速配置"; return 0
    fi
    local daemonjson=/etc/docker/daemon.json
    if [[ -f "$daemonjson" ]] && grep -q registry-mirrors "$daemonjson" 2>/dev/null; then
        return 0
    fi
    log_info "配置 Docker registry 镜像加速（best-effort）..."
    as_root mkdir -p /etc/docker
    if [[ -f "$daemonjson" ]]; then
        # 已有 daemon.json 但无 registry-mirrors：用 jq 合并，无 jq 则跳过（避免覆盖玩家配置）
        if command -v jq >/dev/null 2>&1; then
            local tmp; tmp=$(mktemp)
            local mirrors_json
            mirrors_json=$(printf '%s\n' "${PCH_DOCKER_MIRRORS[@]}" | jq -R . | jq -s .)
            jq --argjson m "$mirrors_json" '. + {"registry-mirrors": ($m + (.registry-mirrors // []))}' \
                "$daemonjson" > "$tmp" && as_root cp "$tmp" "$daemonjson" && rm -f "$tmp"
        else
            log_warn "已有 daemon.json 但无 jq 合并，跳过 registry-mirrors（避免覆盖你的配置）"; return 0
        fi
    else
        local tmp; tmp=$(mktemp)
        {
            echo '{ "registry-mirrors": [ '
            local first=1
            for m in "${PCH_DOCKER_MIRRORS[@]}"; do
                [[ $first -eq 1 ]] || echo ","
                printf '    "%s"' "$m"
                first=0
            done
            echo; echo '  ] }'
        } > "$tmp"
        as_root cp "$tmp" "$daemonjson"; rm -f "$tmp"
    fi
    as_root systemctl restart docker 2>/dev/null \
        || as_root service docker restart 2>/dev/null \
        || log_warn "docker 重启失败（不阻断，mirrors 可能未生效）"
}

# ============================================================
# 密钥生成
# ============================================================
gen_secret() {
    local len=${1:-32}
    if command -v openssl >/dev/null 2>&1 && openssl rand -hex "$len" 2>/dev/null; then
        return 0
    fi
    if [[ -r /dev/urandom ]]; then
        head -c "$len" /dev/urandom | od -An -tx1 | tr -d ' \n'; return 0
    fi
    die "无法生成随机密钥（openssl 与 /dev/urandom 均不可用）"
}

# ============================================================
# 版本/tag 解析
# ============================================================
is_in_git_repo() { git rev-parse --is-inside-work-tree >/dev/null 2>&1; }

# latest_tag: stdout 最新发版 tag（三端 *-v* 过滤，creatordate 倒序），无则空
latest_tag() {
    git fetch --tags --quiet 2>/dev/null || true
    git tag --sort=-creatordate --list '*-v*' 2>/dev/null | head -1
}

# current_ref: stdout tag:<name> | edge | detached:<sha>
current_ref() {
    local t
    if t=$(git describe --tags --exact-match HEAD 2>/dev/null) && [[ "$t" == *-v* ]]; then
        echo "tag:$t"; return 0
    fi
    local branch; branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    if [[ "$branch" == "${PCH_DEFAULT_BRANCH}" ]]; then
        echo "edge"; return 0
    fi
    echo "detached:$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
}

# resolve_target_ref: strategy=tag|edge → tag:<name> | edge
resolve_target_ref() {
    local strategy=${1:-tag}
    if [[ "$strategy" == "edge" ]]; then echo "edge"; return 0; fi
    local t; t=$(latest_tag)
    if [[ -n "$t" ]]; then echo "tag:$t"
    else echo "edge"; log_warn "无发版 tag，回退 ${PCH_DEFAULT_BRANCH}（edge）"; fi
}

# ============================================================
# 健康轮询
# ============================================================
# wait_healthy: 等待 compose 服务 Health=healthy（postgres 等有 healthcheck 的服务）
wait_healthy() {
    local service=$1 timeout=${2:-120}
    local elapsed=0
    log_info "等待 $service 健康（超时 ${timeout}s）..."
    while (( elapsed < timeout )); do
        if dcc ps --format json "$service" 2>/dev/null | grep -q '"Health":"healthy"'; then
            return 0
        fi
        sleep 3; elapsed=$((elapsed + 3))
    done
    die "等待 $service 健康超时（${timeout}s）"
}

# wait_http_ok: 轮询 HTTP 端点期望状态码（backend /healthz 存活探针）
wait_http_ok() {
    local url=$1 timeout=${2:-60} expect=${3:-200}
    local elapsed=0 got="000"
    log_info "等待 $url 返回 ${expect}（超时 ${timeout}s）..."
    while (( elapsed < timeout )); do
        got=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null || echo "000")
        [[ "$got" == "$expect" ]] && return 0
        sleep 3; elapsed=$((elapsed + 3))
    done
    die "等待 $url 超时（${timeout}s，最后 HTTP ${got}）"
}

# ============================================================
# 迁移前快照（pg_dump 安全网）
# ============================================================
# MIGRATION_BAK：最近一次迁移前快照路径（dump_pre_migration 写入；run_migrations 的
# alembic 失败提示用它给恢复命令）。跨步骤全局，对齐 OLD_SHA/NEW_SHA/GH_MIRROR_ENTRY 模式。
MIGRATION_BAK=""

# dump_pre_migration <prefix> <pg_user> <pg_db>
# 迁移前 pg_dump —— R-2（score_ledger append-only）的唯一恢复路径，故失败必须【可见】：
# - 不再 2>/dev/null 吞 stderr：捕获到临时文件，失败时打出原因
# - 校验快照非空：pg_dump 返回 0 但 0 字节 = 被中断/信号杀掉（rc 未必非零），大声告警
# best-effort：任何失败都不阻断迁移（保持原「忽略继续」语义），只让"安全网失效"被看见。
# 快照路径写入全局 MIGRATION_BAK。
dump_pre_migration() {
    local prefix=$1 pg_user=$2 pg_db=$3
    mkdir -p backups
    ensure_gitignored backups/
    MIGRATION_BAK="backups/${prefix}-$(date +%Y%m%d-%H%M%S).sql"
    local err_tmp
    err_tmp=$(mktemp) || die "mktemp 失败（无法创建临时文件）"
    log_info "迁移前快照: $MIGRATION_BAK"
    if dcc exec -T postgres pg_dump -U "$pg_user" "$pg_db" > "$MIGRATION_BAK" 2>"$err_tmp"; then
        # rc=0 但 0 字节：> 先建空文件、pg_dump 没来得及写就被中断/杀掉
        if [[ ! -s "$MIGRATION_BAK" ]]; then
            log_error "迁移前快照为 0 字节（${MIGRATION_BAK}）——安全网失效！"
            log_error "  pg_dump 返回 0 但无输出（可能被中断/信号杀掉）。本次迁移无可恢复备份，请确认另有库备份。"
            [[ -s "$err_tmp" ]] && { log_warn "  pg_dump stderr:"; sed 's/^/    /' "$err_tmp" >&2; }
        fi
    else
        log_warn "pg_dump 失败（忽略，继续迁移）："
        if [[ -s "$err_tmp" ]]; then
            sed 's/^/    /' "$err_tmp" >&2
        else
            log_warn "  （无 stderr 输出）"
        fi
    fi
    rm -f "$err_tmp"
}

# ============================================================
# 交互封装
# ============================================================
# read_interactive <var> <prompt> <default>：PCH_YES=1 时采用 default，否则 read -p
read_interactive() {
    local var=$1 prompt=$2 default=$3
    if [[ "${PCH_YES:-0}" == "1" ]]; then
        printf -v "$var" '%s' "$default"
        log_info "$prompt → ${default}（--yes 自动采用）"
        return 0
    fi
    local answer=""
    read -r -p "$prompt [$default]: " answer || true
    printf -v "$var" '%s' "${answer:-$default}"
}

# confirm <prompt> [default(y/n)]：PCH_YES=1 自动 yes
confirm() {
    local prompt=$1 default=${2:-n}
    if [[ "${PCH_YES:-0}" == "1" ]]; then return 0; fi
    local yn default_hint
    [[ "$default" == "y" ]] && default_hint="Y/n" || default_hint="y/N"
    read -r -p "$prompt [$default_hint]: " yn || yn=""
    case "${yn:-$default}" in
        y|Y|yes|YES) return 0 ;;
        *) return 1 ;;
    esac
}

# ============================================================
# 部署配置读写（.pchsystem.deploy.env）
# ============================================================
deploy_config_path() { echo "${PCH_REPO_DIR:-$(pwd)}/.pchsystem.deploy.env"; }

load_deploy_config() {
    DEPLOY_CONFIG=()
    local f; f=$(deploy_config_path)
    [[ -f "$f" ]] || return 0
    local k v
    while IFS='=' read -r k v; do
        [[ -z "$k" || "$k" == \#* ]] && continue
        DEPLOY_CONFIG["$k"]="$v"
    done < "$f"
}

cfg_get() { printf '%s' "${DEPLOY_CONFIG[$1]:-}"; }

# save_deploy_config KEY VAL [KEY VAL ...]：合并写回（幂等）
save_deploy_config() {
    (( $# >= 2 )) || return 0
    local f; f=$(deploy_config_path)
    ensure_gitignored .pchsystem.deploy.env
    declare -A cur=()
    local k v
    if [[ -f "$f" ]]; then
        while IFS='=' read -r k v; do
            [[ -z "$k" || "$k" == \#* ]] && continue
            cur["$k"]="$v"
        done < "$f"
    fi
    while (( $# >= 2 )); do
        cur["$1"]="$2"; shift 2
    done
    {
        echo "# PCHSystem 部署状态（install.sh/update.sh 自动生成，勿手改）"
        for k in "${!cur[@]}"; do
            printf '%s=%s\n' "$k" "${cur[$k]}"
        done
    } > "$f"
}

# ============================================================
# .gitignore 幂等追加
# ============================================================
ensure_gitignored() {
    local pattern=$1
    local gi="${PCH_REPO_DIR:-$(pwd)}/.gitignore"
    [[ -f "$gi" ]] || { log_warn ".gitignore 不存在：$gi"; return 0; }
    grep -qxF "$pattern" "$gi" 2>/dev/null && return 0
    {
        printf '\n# by Scripts/install.sh or update.sh\n%s\n' "$pattern"
    } >> "$gi"
    log_info "已追加 .gitignore: $pattern"
}

# ============================================================
# 步骤封装（try/catch，按 on_fail 路由）
# ============================================================
# run_step [--on-fail die|warn] <name> <cmd...>
run_step() {
    local on_fail=die
    if [[ "${1:-}" == "--on-fail" ]]; then
        on_fail=$2; shift 2
    fi
    local name=$1; shift
    log_step "$name"
    # 注意：不在子 shell 里执行——子 shell 会丢失步骤内对全局变量的赋值
    # （如 ensure_docker 设置 COMPOSE）。改用 set +e/+e 在当前 shell 捕获退出码。
    local rc=0
    set +e
    "$@"
    rc=$?
    set -e
    if [[ $rc -eq 0 ]]; then return 0; fi
    if [[ "$on_fail" == "warn" ]]; then
        log_warn "步骤「${name}」失败（rc=${rc}），已跳过"; return 0
    fi
    die "步骤「${name}」失败（rc=${rc}）"
}

# ============================================================
# MCDR 拓扑推断
# ============================================================
# detect_mcdr_topology <mcdr_root>: 按 MCDR 路径形态推断后端 api_url 默认值
detect_mcdr_topology() {
    local root=$1
    if [[ "$root" == *"/var/lib/docker/volumes/"* || "$root" == *"/docker/volumes/"* ]]; then
        # MCDR 数据在 docker named volume 挂载点 → 假设与 backend 同网络，用服务名
        echo "http://pchsystem-backend-1:8000"
    else
        echo "http://127.0.0.1:8000"
    fi
}

# ============================================================
# compose build 封装（透传代理给 build，助 CJK 字体 wget）
# ============================================================
# compose_build <service>：自动透传 HTTP(S)_PROXY 与 PIP_INDEX_URL build-arg
compose_build() {
    local service=$1
    local -a args=(build)
    local hp="${HTTPS_PROXY:-${https_proxy:-}}"
    local htp="${HTTP_PROXY:-${http_proxy:-}}"
    [[ -n "$hp" ]]  && args+=(--build-arg "HTTPS_PROXY=$hp")
    [[ -n "$htp" ]] && args+=(--build-arg "HTTP_PROXY=$htp")
    [[ -n "${PIP_INDEX_URL:-}" ]] && args+=(--build-arg "PIP_INDEX_URL=$PIP_INDEX_URL")
    args+=("$service")
    dcc "${args[@]}"
}

# ============================================================
# Web 服务（前端 nginx）profile 判定
# ============================================================
# web_profile_active: .env 的 COMPOSE_PROFILES 是否激活 web profile。
# compose 读 .env 的 COMPOSE_PROFILES（值可空格/逗号分隔）；含 web → web 服务随 up -d 起。
# 被 install.sh / update.sh 复用，决定前端是「容器内镜像构建」还是「宿主 npm build」。
web_profile_active() {
    local profiles=""
    [[ -f .env ]] && profiles=$(grep -E '^COMPOSE_PROFILES=' .env 2>/dev/null | head -1 | cut -d= -f2-)
    # 同时容忍空格与逗号分隔：把空格统一成逗号，再前后加逗号做整词匹配
    [[ ",${profiles// /,}," == *",web,"* ]]
}

# ============================================================
# 重新安装：web 宿主端口冲突回收（绝不触碰 postgres / 数据卷）
# ============================================================
# port_listening <port>：宿主端口是否被监听（LISTEN）。ss 缺失/无权限→假（调用方兜底）。
port_listening() {
    ss -tlnp "sport = :$1" 2>/dev/null | grep -q 'LISTEN'
}

# _confirm_kill <prompt>：停掉端口占用者的显式确认。刻意不走 PCH_YES 自动 yes
# （杀进程 / 删他人容器须亲眼确认，--yes 不代表可擅自停宿主服务）。
_confirm_kill() {
    local yn=""
    read -r -p "$1 [y/N]: " yn || yn=""
    case "$yn" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

# reclaim_web_port <port>：web 容器宿主端口冲突回收（重新安装场景）。
#   1) 本项目 pchsystem-web-1 残留（Created/Exited/失败）→ 自动 docker rm -f（安全，无需问）
#   2) 仍被占 → 报告占用者（容器名 / 宿主进程 pid+comm）+ 询问是否停掉
# 返回 0=已空闲/已清理；1=仍被占且用户拒绝。红线：只动 web 容器与明确占用者，
#                          绝不 docker compose down / down -v / rm volume / 碰 postgres。
reclaim_web_port() {
    local port=$1
    [[ -n "$port" ]] || return 0
    port_listening "$port" || return 0
    log_warn "宿主端口 $port 已被占用 → 排查占用者（web 容器需绑此端口）"

    # 1) 本项目 pchsystem-web-1 残留（非 running）→ 自动清
    local webc st
    webc=$(docker ps -aq --filter 'name=^/pchsystem-web-1$' 2>/dev/null | head -1 || true)
    if [[ -n "$webc" ]]; then
        st=$(docker inspect -f '{{.State.Status}}' "$webc" 2>/dev/null || echo "")
        if [[ "$st" != "running" ]]; then
            log_info "  本项目残留 web 容器 pchsystem-web-1（状态=${st:-未知}）→ 自动移除"
            docker rm -f "$webc" >/dev/null 2>&1 || true
            port_listening "$port" || { log_info "  端口 $port 已释放"; return 0; }
        fi
    fi

    # 2) 某 docker 容器占的？
    local cname
    cname=$(docker ps -a --format '{{.Names}} {{.Ports}}' 2>/dev/null \
            | awk -v p=":$port->" 'index($0,p){print $1; exit}' || true)
    if [[ -n "$cname" ]]; then
        log_warn "  占用者：容器 $cname"
        if [[ "$cname" == "pchsystem-web-1" ]]; then
            docker rm -f "$cname" >/dev/null 2>&1 || true
            port_listening "$port" || { log_info "  端口 $port 已释放"; return 0; }
            return 1
        fi
        if _confirm_kill "  停掉并移除容器 $cname 以释放端口 $port？"; then
            docker rm -f "$cname" >/dev/null 2>&1 || true
            port_listening "$port" || { log_info "  端口 $port 已释放"; return 0; }
        fi
        return 1
    fi

    # 3) 宿主进程（非 docker）
    local line pid="" comm=""
    line=$(ss -tlnp "sport = :$port" 2>/dev/null | grep 'LISTEN' | head -1 || true)
    pid=$(printf '%s' "$line" | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2 || true)
    [[ -n "$pid" ]] && comm=$(ps -o comm= -p "$pid" 2>/dev/null | head -1 || true)
    log_warn "  占用者：宿主进程 ${comm:-unknown}（pid=${pid:-未知}）"
    log_warn "  提示：可能是你之前 'npm run dev' 起的前端开发服或其他服务；停掉前请确认无影响。"
    if [[ -z "$pid" ]]; then
        log_warn "  无法获取 pid（install.sh 需 sudo 运行才能读到进程）→ 手动停掉或改 .env 的 WEB_PORT"
        return 1
    fi
    if _confirm_kill "  停掉进程 ${comm}（pid=$pid）释放端口 $port？（SIGTERM，3s 未退则 SIGKILL）"; then
        kill "$pid" 2>/dev/null || true
        local i
        for i in 1 2 3; do port_listening "$port" || break; sleep 1; done
        if port_listening "$port"; then
            log_warn "  SIGTERM 未释放，发送 SIGKILL"
            kill -9 "$pid" 2>/dev/null || true
            sleep 1
        fi
        port_listening "$port" || { log_info "  端口 $port 已释放"; return 0; }
        log_error "  进程已停但端口仍被占（可能有子进程或被自动拉起）"
        return 1
    fi
    return 1
}

# ============================================================
# 插件 id 迁移（2026-07-12 起 plugin id 由 htcmc_auth 改为 pch_system）
# ============================================================
# migrate_legacy_plugin_name <mcdr_root>：把旧部署的 htcmc_auth 迁到 pch_system。
#   1) config 搬家：config/htcmc_auth/config.json → config/pch_system/config.json（保留玩家 api_url + service_token）
#   2) 删旧插件目录 plugins/htcmc_auth（id 已改，旧插件若残留会与新 pch_system 双注册 !!PCH 冲突）
#   幂等：无旧目录即跳过。install.sh / update.sh 在部署新 pch_system 之前调用。
migrate_legacy_plugin_name() {
    local mcdr_root=$1
    [[ -n "$mcdr_root" ]] || return 0
    local legacy_plugin="$mcdr_root/plugins/htcmc_auth"
    local legacy_cfg="$mcdr_root/config/htcmc_auth"
    local new_cfg_dir="$mcdr_root/config/pch_system"

    [[ -e "$legacy_plugin" || -e "$legacy_cfg" ]] || return 0

    log_step "迁移旧插件名 htcmc_auth → pch_system（plugin id 改名）"

    # 1) config 搬家（保留玩家手改的 api_url / service_token）
    if [[ -e "$legacy_cfg" ]]; then
        mkdir -p "$new_cfg_dir"
        if [[ -f "$legacy_cfg/config.json" ]]; then
            cp -f "$legacy_cfg/config.json" "$new_cfg_dir/config.json"
            log_info "已迁移配置: $legacy_cfg/config.json → $new_cfg_dir/config.json"
        fi
        rm -rf "$legacy_cfg"
        log_info "已移除旧配置目录: $legacy_cfg"
    fi

    # 2) 删旧插件目录（避免与新 pch_system 双注册 !!PCH）
    if [[ -e "$legacy_plugin" ]]; then
        rm -rf "$legacy_plugin"
        log_info "已移除旧插件目录: ${legacy_plugin}（否则与新 pch_system 双注册 !!PCH 冲突）"
    fi

    log_warn "迁移完成：MCDR 重启或游戏内 !!MCDR plugin reload pch_system 后生效"
}

# ============================================================
# .env 增量补全
# ============================================================
# ensure_env_keys：幂等补全 .env 相对 .env.example 缺失的键（已存在键一律不动，保留用户值）。
# 补全值优先让用户手动输入：每个非密钥缺失键用 read_interactive 提示确认/覆盖
# （--yes / PCH_YES=1 或输入空白 → 用默认值），与 ensure_env 现有 read_interactive 用法一致。
# stdout 打印 changed|unchanged，供 update.sh 决定是否 force-recreate backend。
# 密钥类（POSTGRES_PASSWORD / JWT_SECRET / MCDR_SERVICE_TOKEN）缺失只 warn，不补占位值（R-11）。
ensure_env_keys() {
    [[ -f .env.example && -f .env ]] || { echo unchanged; return 0; }

    # 先把 .env.example 的键读入数组再遍历——勿在 `while read < <(grep)` 体内直接 read_interactive：
    # 内层 read 会消费 grep 的重定向 stdin（而非终端），把 .env.example 内容误当用户输入。
    local -a keys=()
    local -A ex_val=()
    local k v
    while IFS='=' read -r k v; do
        [[ -n "$k" ]] || continue
        keys+=("$k"); ex_val["$k"]="$v"
    done < <(grep -E '^[A-Z_]+=' .env.example)

    # 处理顺序：COMPOSE_PROFILES 先于 WEB_PROBE_URL——后者据前者（web_profile_active）推断默认 http://web。
    local -a sorted=()
    for k in "${keys[@]}"; do [[ "$k" == "COMPOSE_PROFILES" ]] && sorted+=("$k"); done
    for k in "${keys[@]}"; do [[ "$k" != "COMPOSE_PROFILES" ]] && sorted+=("$k"); done

    local -a added=()
    local default _val
    for k in "${sorted[@]}"; do
        grep -qE "^${k}=" .env && continue   # 已存在，不动
        case "$k" in
            POSTGRES_PASSWORD|JWT_SECRET|MCDR_SERVICE_TOKEN)
                log_warn ".env 缺失密钥 $k——不自动补占位值（R-11），请手动设强随机值后重跑"
                continue
                ;;
            WEB_PROBE_URL)
                if web_profile_active; then
                    default="http://web"
                else
                    local base
                    base=$(grep -E '^WEB_BASE_URL=' .env 2>/dev/null | head -1 | cut -d= -f2-)
                    if [[ -n "$base" && ! "$base" =~ ^https?://(localhost|127\.0\.0\.1)(:|$) ]]; then
                        default="$base"
                        log_warn "WEB_PROBE_URL 默认回退为 WEB_BASE_URL（$base）——后端容器未必能探到该外部地址，若 /info.web_version 仍为 null 请手改 .env"
                    else
                        default=""
                        log_warn "WEB_PROBE_URL 默认留空：WEB_BASE_URL 是 localhost，后端容器内无法探前端 → /info.web_version=null（前端版本号不显示）"
                    fi
                fi
                ;;
            COMPOSE_PROFILES)
                default=""
                log_warn "COMPOSE_PROFILES 默认留空（避免与既有外部 nginx 端口冲突/重复托管）；若改用 compose 托管前端，可输入 web"
                ;;
            *)
                default="${ex_val[$k]}"
                ;;
        esac
        read_interactive _val "  补全 .env 缺失项 $k" "$default"
        printf '%s=%s\n' "$k" "$_val" >> .env
        added+=("$k")
    done

    if (( ${#added[@]} == 0 )); then
        echo unchanged
    else
        log_info "已补全 .env 缺失配置项: $(IFS=,; echo "${added[*]}")"
        log_info "各配置项含义与取值见 .env.example（同目录）行内注释；如需再改请编辑 .env 后 docker compose up -d --force-recreate backend"
        echo changed
    fi
}
