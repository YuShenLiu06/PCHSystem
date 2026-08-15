#!/usr/bin/env bash
# bootstrap.sh —— PCHSystem 一行安装引导（无需先 clone 仓库，clone 后委托 Scripts/install.sh）
#
# 用法（参数原样透传给 Scripts/install.sh）：
#   bash <(curl -fsSL https://raw.githubusercontent.com/YuShenLiu06/PCHSystem/main/Scripts/bootstrap.sh)
#   curl -fsSL <同上 URL> | bash -s -- --yes   # 管道形态：本脚本不读 stdin，委托时自动接 /dev/tty
#   大陆第一跳可换 Gitee raw：https://gitee.com/yushenliu03/PCHSystem/raw/main/Scripts/bootstrap.sh
#   （镜像前缀变体：把 ghfast.top/ 等前缀拼在 raw.githubusercontent URL 前即可）
#
# 环境变量：
#   PCH_CLONE_DIR    clone 目标目录（默认 ./PCHSystem）；已是本仓库则复用跳过 clone（幂等）
#   PCH_REPO_URL     仓库源覆盖（file:// 可作离线测试）
#   PCH_GITEE_URL    Gitee 自动同步镜像覆盖
#   PCH_GH_MIRRORS   GitHub 镜像前缀链覆盖（空格分隔 "<rewrite>|<insteadOf>"，同 lib/common.sh）
#
# 兼容性约束：本脚本必须兼容 macOS 自带 bash 3.2（管道场景用户无法选择解释器）——
#   禁用关联数组 / declare -gA / ${var,,} / mapfile。bash4 仅 install.sh 需要，本脚本
#   开头即定位 bash4（找不到提前 die，不浪费 clone 流量），末尾用它委托执行。
# 镜像链与 Scripts/lib/common.sh 的 PCH_GH_MIRRORS 对应维护。

BOOT_REPO_URL="${PCH_REPO_URL:-https://github.com/YuShenLiu06/PCHSystem.git}"
BOOT_GITEE_URL="${PCH_GITEE_URL:-https://gitee.com/yushenliu03/PCHSystem.git}"
BOOT_CLONE_DIR="${PCH_CLONE_DIR:-./PCHSystem}"
BOOT_BASH4=""

# 日志一律走 stderr：pick_source 的 stdout 被 $( ) 命令替换捕获作 clone 参数，绝不能混入日志
_boot_log()  { printf '[bootstrap] %s\n' "$*" >&2; }
_boot_warn() { printf '[bootstrap][警告] %s\n' "$*" >&2; }
_boot_die()  { printf '[bootstrap][错误] %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
PCHSystem bootstrap.sh —— 一行安装引导（clone + 委托 Scripts/install.sh）

用法:
  bash <(curl -fsSL <bootstrap-url>) [install.sh 参数...]      # 推荐（stdin 干净）
  curl -fsSL <bootstrap-url> | bash -s -- [install.sh 参数...] # 管道形态（委托时接 /dev/tty）

环境变量:
  PCH_CLONE_DIR   clone 目标目录（默认 ./PCHSystem，已是本仓库则复用）
  PCH_REPO_URL / PCH_GITEE_URL / PCH_GH_MIRRORS   源与镜像链覆盖（同 Scripts/lib/common.sh）

install.sh 参数见其 --help（--edge / --yes / --mcdr-root / --no-web / --no-mcdr ...）。
EOF
}

# ---------- 步骤 ----------
# find_bash4：定位 bash4+（install.sh 的关联数组等语法需要；macOS 自带 3.2）。
#   找到则 stdout 输出路径；找不到 die（在 clone 之前，快失败省流量）。
find_bash4() {
    if [ "${BASH_VERSINFO[0]:-0}" -ge 4 ] 2>/dev/null; then
        BOOT_BASH4="bash"        # 当前解释器已是 bash4
        _boot_log "bash ${BASH_VERSION} 满足 install.sh 要求"
        return 0
    fi
    local cand
    for cand in /opt/homebrew/bin/bash /usr/local/bin/bash; do   # Apple Silicon / Intel
        if [ -x "$cand" ] && "$cand" -c '(( BASH_VERSINFO[0] >= 4 ))' 2>/dev/null; then
            BOOT_BASH4="$cand"
            _boot_log "当前 bash ${BASH_VERSION} 过旧，自动改用 $cand"
            return 0
        fi
    done
    _boot_die "install.sh 需要 bash 4.0+（当前 ${BASH_VERSION}）。请安装后重跑：
  brew install bash
（Apple Silicon: /opt/homebrew/bin/bash；Intel: /usr/local/bin/bash 均会被自动识别）"
}

# ensure_cmds：git / curl 强检（clone 与镜像探测的最小依赖）。
ensure_cmds() {
    if ! command -v git >/dev/null 2>&1; then
        case "$(uname -s)" in
            Darwin) _boot_die "缺少 git。安装任选其一：
  xcode-select --install   # 命令行工具（弹窗安装）
  brew install git" ;;
            MINGW*|MSYS*|CYGWIN*) _boot_die "缺少 git。Windows 请用 PowerShell 运行 bootstrap.ps1（其会引导安装 Git），或自行安装 Git for Windows: https://git-scm.com/download/win" ;;
            *) _boot_die "缺少 git。请用发行版包管理器安装（apt/dnf/apk/pacman install git）" ;;
        esac
    fi
    command -v curl >/dev/null 2>&1 || _boot_die "缺少 curl。请先安装 curl（macOS: xcode-select --install；Linux: 发行版包管理器）"
}

# _probe <url>：git ls-remote 探活（有 timeout/gtimeout 则限时，防大陆半连接挂死）。
_probe() {
    if   command -v timeout  >/dev/null 2>&1; then timeout 10 git ls-remote --exit-code "$1" HEAD >/dev/null 2>&1
    elif command -v gtimeout >/dev/null 2>&1; then gtimeout 10 git ls-remote --exit-code "$1" HEAD >/dev/null 2>&1
    else git ls-remote --exit-code "$1" HEAD >/dev/null 2>&1
    fi
}

# pick_source：选 clone 源。stdout 输出传给 git clone 的完整参数串（可能含 -c 重写）。
#   探测顺序：GitHub 直连 → Gitee（自动同步）→ gh 镜像前缀链 → 全失败 die。
pick_source() {
    _boot_log "探测 clone 源（直连 → Gitee → GitHub 镜像链）..."
    if _probe "$BOOT_REPO_URL"; then
        _boot_log "直连源可用: $BOOT_REPO_URL"
        printf '%s' "$BOOT_REPO_URL"
        return 0
    fi
    _boot_warn "GitHub 直连不通，尝试 Gitee 自动同步镜像..."
    if _probe "$BOOT_GITEE_URL"; then
        _boot_log "选用 Gitee: $BOOT_GITEE_URL（后续 fetch 也走 Gitee，大陆更快）"
        printf '%s' "$BOOT_GITEE_URL"
        return 0
    fi
    _boot_warn "Gitee 也不通，尝试 GitHub 镜像前缀链..."
    local mirrors="" entry rewrite insteadof
    if [ -n "${PCH_GH_MIRRORS:-}" ]; then
        mirrors=${PCH_GH_MIRRORS}
    else
        mirrors="https://ghfast.top/https://github.com|https://github.com \
https://ghproxy.com/https://github.com|https://github.com \
https://kkgithub.com|https://github.com \
https://gh.zwy.one/https://github.com|https://github.com"
    fi
    local -a list
    # shellcheck disable=SC2206  # 空格分隔（entry 内只含 |，无空格）
    list=($mirrors)
    for entry in "${list[@]}"; do
        rewrite="${entry%|*}"
        insteadof="${entry#*|}"
        local test_url="${BOOT_REPO_URL#$insteadof}"
        _boot_log "  探测镜像: $rewrite"
        if _probe "${rewrite}${test_url}"; then
            _boot_log "  选用镜像: $rewrite"
            # 用 insteadOf 重写 clone（只影响本次命令，不改全局 git 配置）
            printf '%s' "-c url.${rewrite}.insteadOf=${insteadof}|${BOOT_REPO_URL}"
            return 0
        fi
    done
    _boot_die "所有 clone 源均不可达。手动兜底（任选其一后进入目录跑 bash Scripts/install.sh）：
  git clone ${BOOT_GITEE_URL}
  git clone ${BOOT_REPO_URL}
（镜像前缀 clone 用法见 Scripts/README.md §1/§5）"
}

# clone_repo：幂等 clone（已是本仓库则复用，由 install.sh 的 sync_repo 负责同步版本）。
clone_repo() {
    local src=$1
    if [ -d "$BOOT_CLONE_DIR/.git" ] && [ -f "$BOOT_CLONE_DIR/Scripts/install.sh" ]; then
        _boot_log "目标目录已是 PCHSystem 仓库，复用（版本同步交给 install.sh）: $BOOT_CLONE_DIR"
        return 0
    fi
    if [ -d "$BOOT_CLONE_DIR" ] && [ -n "$(ls -A "$BOOT_CLONE_DIR" 2>/dev/null)" ]; then
        _boot_die "目标目录存在且非空、也不是 PCHSystem 仓库: $BOOT_CLONE_DIR
请换目录（PCH_CLONE_DIR=/path/to/dir）或清空后重跑"
    fi
    _boot_log "clone 到 $BOOT_CLONE_DIR ..."
    local -a git_args=()
    case "$src" in
        *"|"*) # 镜像形态 "-c url.<rewrite>.insteadOf=<orig>|<repo-url>"
            # shellcheck disable=SC2206
            git_args=(${src%%|*} ${src#*|}) ;;
        *) git_args=("$src") ;;
    esac
    if git clone "${git_args[@]}" "$BOOT_CLONE_DIR"; then
        return 0
    fi
    _boot_die "git clone 失败（源: ${src%%|*}）。可重跑本命令（会换源重试）或见上方手动兜底命令"
}

# ---------- main ----------
for arg in "$@"; do
    case "$arg" in
        -h|--help) usage; exit 0 ;;
    esac
done

find_bash4
ensure_cmds
clone_repo "$(pick_source)"

cd "$BOOT_CLONE_DIR" || _boot_die "无法进入 $BOOT_CLONE_DIR"
_boot_log "委托 install.sh 执行（参数原样透传）..."
# 管道形态（curl | bash）下 stdin 是耗尽的脚本管道——重新接到终端保住 install.sh 的交互；
# 无终端（CI）则 /dev/null 并提示 --yes（install.sh 的 read 会取默认值）。
# 注意 [ -r /dev/tty ] 只查权限位不查能否 open（无控制终端的 CI/沙盒下为真、open 报 ENXIO），
# 故用 `: < /dev/tty` 真实试探打开。
if : 2>/dev/null < /dev/tty; then
    exec "$BOOT_BASH4" Scripts/install.sh "$@" < /dev/tty
else
    _boot_warn "无终端可用，install.sh 交互将不可用（read 一律取默认值）——无人值守建议追加 --yes"
    exec "$BOOT_BASH4" Scripts/install.sh "$@" < /dev/null
fi
