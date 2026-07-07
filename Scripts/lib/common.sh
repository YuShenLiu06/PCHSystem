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
    # stdout: debian | rhel | alpine | arch | unknown
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
    # 2) docker 缺失 → 安装
    install_docker
    detect_compose
    [[ -n "$COMPOSE" ]] || die "docker 已安装但仍检测不到 compose，请手动安装 docker-compose-plugin"
}

install_docker() {
    log_step "安装 Docker"
    assert_root_or_sudo
    local os; os=$(detect_os)
    _ensure_curl
    log_info "使用 get.docker.com 官方脚本（--mirror Aliyun 加速国内拉取）"
    if curl -fsSL https://get.docker.com | as_root sh -s -- --mirror Aliyun; then
        _post_install_docker
        return 0
    fi
    log_warn "get.docker.com 失败，回退发行版原生包管理器（$os）"
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
        *)      die "缺少 curl 且无法自动安装（未知发行版 $os），请手动安装 curl" ;;
    esac
}

_install_docker_native() {
    local os=$1
    case "$os" in
        debian) as_root apt-get update -y && as_root apt-get install -y docker.io docker-compose-plugin ;;
        rhel)   as_root dnf install -y docker docker-compose-plugin || as_root yum install -y docker docker-compose-plugin ;;
        alpine) as_root apk add --no-cache docker docker-cli-compose openrc ;;
        arch)   as_root pacman -S --noconfirm docker docker-compose ;;
        *)      die "不支持的发行版（$os），请手动安装 docker + docker-compose-plugin 后重跑" ;;
    esac
}

_install_compose_plugin() {
    local os; os=$(detect_os)
    case "$os" in
        debian) as_root apt-get update -y && as_root apt-get install -y docker-compose-plugin ;;
        rhel)   as_root dnf install -y docker-compose-plugin ;;
        alpine) as_root apk add --no-cache docker-cli-compose ;;
        arch)   as_root pacman -S --noconfirm docker-compose ;;
        *)      log_warn "未知发行版（$os），无法自动补装 compose 插件" ;;
    esac
}

_post_install_docker() {
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

# pick_github_mirror: stdout 输出命中的 "<rewrite>|<insteadOf>" 或空串（=直连）
pick_github_mirror() {
    # 直连可用则直接走直连（最快）
    if timeout 8 git ls-remote --exit-code "${PCH_REPO_URL}" HEAD >/dev/null 2>&1; then
        echo ""; return 0
    fi
    log_warn "GitHub 直连不通，尝试镜像源..."
    local entry rewrite insteadof test_url
    for entry in "${PCH_GH_MIRRORS[@]}"; do
        rewrite="${entry%|*}"
        insteadof="${entry#*|}"
        test_url="${rewrite}${PCH_REPO_URL#"${insteadof}"}"
        log_info "  探测镜像: $rewrite"
        if timeout 12 git ls-remote --exit-code "$test_url" HEAD >/dev/null 2>&1; then
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
    log_info "等待 $url 返回 $expect（超时 ${timeout}s）..."
    while (( elapsed < timeout )); do
        got=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null || echo "000")
        [[ "$got" == "$expect" ]] && return 0
        sleep 3; elapsed=$((elapsed + 3))
    done
    die "等待 $url 超时（${timeout}s，最后 HTTP $got）"
}

# ============================================================
# 交互封装
# ============================================================
# read_interactive <var> <prompt> <default>：PCH_YES=1 时采用 default，否则 read -p
read_interactive() {
    local var=$1 prompt=$2 default=$3
    if [[ "${PCH_YES:-0}" == "1" ]]; then
        printf -v "$var" '%s' "$default"
        log_info "$prompt → $default（--yes 自动采用）"
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
        log_warn "步骤「$name」失败（rc=$rc），已跳过"; return 0
    fi
    die "步骤「$name」失败（rc=$rc）"
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
