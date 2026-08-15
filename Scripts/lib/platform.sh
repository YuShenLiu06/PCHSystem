#!/usr/bin/env bash
# platform.sh —— 跨平台探测与工具降级链（被 lib/common.sh source，不单独执行）
#
# 职责：OS 探测（linux / macos / windows[Git Bash] / unknown）、平台命令降级
#       （sed -i、timeout）、端口探测三态降级、端口占用者定位、python3 探测。
# windows = Git Bash（MINGW/MSYS/CYGWIN）——bootstrap.ps1 定位 Git Bash 后委托执行本套 bash 脚本，
# 故所有"宿主"操作须同时兼容 Linux / macOS / Git Bash 三形态。

# ---------- 平台探测 ----------
PCH_OS=""
case "$(uname -s)" in
    Linux)                PCH_OS="linux" ;;
    Darwin)               PCH_OS="macos" ;;
    MINGW*|MSYS*|CYGWIN*) PCH_OS="windows" ;;
    *)                    PCH_OS="unknown" ;;
esac

# ---------- 跨平台命令探测 ----------
# GNU timeout：macOS 无，装 coreutils 后 gtimeout 可用；都没有则降级为无超时直跑。
# shellcheck disable=SC2034  # 跨文件全局，common.sh 的 _run_with_timeout 引用
if   command -v timeout  >/dev/null 2>&1; then TIMEOUT_CMD=(timeout)
elif command -v gtimeout >/dev/null 2>&1; then TIMEOUT_CMD=(gtimeout)
else                                          TIMEOUT_CMD=()
fi
# sed -i：GNU 用 `-i`，BSD/macOS 用 `-i ''`（空 backup 后缀）。
# windows（Git Bash/MSYS）自带 GNU sed，走 else 分支 `-i` 恰好正确。
# shellcheck disable=SC2034  # 跨文件全局，install.sh/update.sh 引用
if [[ "$PCH_OS" == "macos" ]]; then SED_I=(-i '')
else                                SED_I=(-i)
fi

# ---------- 端口探测（三态） ----------
# port_listening <port>：宿主端口是否被监听（LISTEN）。
#   返回 0=占用 / 1=空闲 / 2=未知（探测工具全缺——调用方必须显式处理 2，勿当空闲，
#   否则 up -d 会因 "address already in use" 裸失败，恰好是本函数要防住的场景）。
# 降级链：linux ss→netstat；macos lsof→netstat(BSD)；windows netstat（Git Bash 调 Windows 自带）。
port_listening() {
    local port=$1 out
    if command -v ss >/dev/null 2>&1; then
        out=$(ss -tln "sport = :$port" 2>/dev/null || true)
        [[ "$out" == *LISTEN* ]] && return 0 || return 1
    fi
    case "$PCH_OS" in
        macos)
            if command -v lsof >/dev/null 2>&1; then
                lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 && return 0 || return 1
            elif command -v netstat >/dev/null 2>&1; then
                # BSD netstat：`tcp4  0  0  *.5173  *.*  LISTEN`（本地地址用 . 分隔端口）
                out=$(netstat -an -p tcp 2>/dev/null | grep LISTEN || true)
                [[ "$out" =~ [.:]${port}[[:space:]] ]] && return 0 || return 1
            fi
            ;;
        windows)
            if command -v netstat >/dev/null 2>&1; then
                # Windows netstat：`TCP  0.0.0.0:5173  0.0.0.0:0  LISTENING`
                out=$(netstat -an 2>/dev/null | grep LISTENING || true)
                [[ "$out" =~ [.:]${port}[[:space:]] ]] && return 0 || return 1
            fi
            ;;
        *)
            if command -v netstat >/dev/null 2>&1; then
                out=$(netstat -tln 2>/dev/null | grep LISTEN || true)
                [[ "$out" =~ [.:]${port}[[:space:]] ]] && return 0 || return 1
            fi
            ;;
    esac
    return 2
}

# listening_owner <port>：定位端口占用者（宿主进程，非 docker）。
#   stdout：`<pid>|<comm>`；空输出 = 无法定位（工具缺/无权限）。
#   Linux: ss pid=；macOS: lsof；windows(Git Bash): netstat -ano 尾列 PID + tasklist 进程名
#   （MSYS 的 ps 看不到 Windows 原生进程，不能沿用 Linux 路径）。
listening_owner() {
    local port=$1 out pid="" comm=""
    case "$PCH_OS" in
        macos)
            if command -v lsof >/dev/null 2>&1; then
                out=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -Fp 2>/dev/null | head -1 || true)
                pid="${out#p}"
                [[ "$pid" =~ ^[0-9]+$ ]] || pid=""
            fi
            ;;
        windows)
            out=$(netstat -ano 2>/dev/null | grep LISTENING | grep -E "[.:]${port}[[:space:]]" | head -1 || true)
            pid=$(printf '%s' "$out" | awk '{print $NF}')
            [[ "$pid" =~ ^[0-9]+$ ]] || pid=""
            if [[ -n "$pid" ]]; then
                # //FI：Git Bash 会把 /FI 误转成路径，双斜杠转义回单斜杠
                comm=$(tasklist //FI "PID eq $pid" //FO CSV //NH 2>/dev/null | head -1 | cut -d'"' -f2 || true)
            fi
            ;;
        *)
            if command -v ss >/dev/null 2>&1; then
                out=$(ss -tlnp "sport = :$port" 2>/dev/null | grep LISTEN | head -1 || true)
                pid=$(printf '%s' "$out" | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2 || true)
            fi
            ;;
    esac
    # windows 分支的 comm 已由 tasklist 取得（MSYS ps 看不到 Windows 进程，勿覆盖）
    [[ -n "$pid" && -z "$comm" ]] && comm=$(ps -o comm= -p "$pid" 2>/dev/null | head -1 || true)
    [[ -n "$pid" ]] && printf '%s|%s' "$pid" "${comm:-unknown}"
    return 0
}

# ---------- python3 探测 ----------
# find_python3：stdout 输出可用的 python 命令（可能是 "py -3"），无则 rc=1。
#   探测顺序 py -3 → python → python3；每个候选必须 -c 实跑验证——
#   Windows 无 Python 时 python3 可能命中 WindowsApps 的 Store stub（命令存在但一跑就弹商店）。
find_python3() {
    local c
    for c in "py -3" python python3; do
        # shellcheck disable=SC2086  # "py -3" 需按词拆分
        if command -v ${c%% *} >/dev/null 2>&1 && $c -c "import sys" >/dev/null 2>&1; then
            printf '%s' "$c"; return 0
        fi
    done
    return 1
}
