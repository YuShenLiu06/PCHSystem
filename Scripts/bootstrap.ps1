# bootstrap.ps1 —— PCHSystem 一行安装引导（Windows；clone 后经 Git Bash 委托 Scripts/install.sh）
#
# 用法（内存执行，不受 ExecutionPolicy 限制；参数透传给 install.sh）：
#   & ([scriptblock]::Create((irm https://raw.githubusercontent.com/YuShenLiu06/PCHSystem/main/Scripts/bootstrap.ps1))) --yes
#   大陆第一跳可换 Gitee raw：irm https://gitee.com/yushenliu03/PCHSystem/raw/main/Scripts/bootstrap.ps1
#   irm <url> | iex 也可，但该形态无法传参——参数走环境变量：
#     $env:PCH_INSTALL_ARGS = "--yes --no-mcdr";  irm <url> | iex
#
# 环境变量：
#   PCH_CLONE_DIR     clone 目标目录（默认 <当前目录>\PCHSystem）；已是本仓库则复用（幂等）
#   PCH_REPO_URL / PCH_GITEE_URL / PCH_GH_MIRRORS   源与镜像链覆盖（同 Scripts/lib/common.sh）
#   PCH_INSTALL_ARGS  仅 irm|iex 形态的参数透传通道（空格分隔）
#
# 前提：必须已安装 Git（含 Git Bash）——缺失直接报错并给指引，不做 zip 兜底。
# 镜像链与 Scripts/lib/common.sh 的 PCH_GH_MIRRORS 对应维护。

# 注意：不设 $ErrorActionPreference='Stop' —— PS 5.1 下原生命令（git/docker）stderr 重
# 向 + EAP=Stop 会把 stderr 行包装成 ErrorRecord 抛异常（NativeCommandError 坑）。
# 本脚本对所有关键调用显式检查 $LASTEXITCODE，不依赖 EAP 兜底。

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$InstallArgs
)

if (-not $InstallArgs) { $InstallArgs = @() }
if ($env:PCH_INSTALL_ARGS) {
    # irm|iex 形态拿不到 $args，经环境变量透传
    $InstallArgs = @($InstallArgs) + @($env:PCH_INSTALL_ARGS -split ' +')
}

function Write-BootInfo { param([string]$Msg) Write-Host "[bootstrap] $Msg" }
function Write-BootWarn { param([string]$Msg) Write-Warning "[bootstrap] $Msg" }
function Stop-Boot {
    param([string]$Msg)
    Write-Error "[bootstrap] $Msg"
    exit 1
}

# ---------- 步骤 ----------
function Test-Git {
    if (Get-Command git -ErrorAction SilentlyContinue) { return }
    Stop-Boot @"
缺少 git（必须）。安装任选其一：
  winget install -e --id Git.Git
  官网安装包：https://git-scm.com/download/win
装好后重跑本命令（已 clone 的目录会自动复用）。不做 zip 兜底。
"@
}

# Find-GitBash：从 git.exe 位置上溯定位 Git Bash（委托 install.sh 用）。
function Find-GitBash {
    $gitExe = (Get-Command git -ErrorAction Stop).Source        # 常见 .../Git/cmd/git.exe
    $gitRoot = Split-Path (Split-Path $gitExe -Parent) -Parent
    $candidates = @(
        (Join-Path $gitRoot 'bin\bash.exe'),
        (Join-Path $gitRoot 'usr\bin\bash.exe'),
        'C:\Program Files\Git\bin\bash.exe',
        'C:\Program Files (x86)\Git\bin\bash.exe'
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    Stop-Boot "已装 git 但找不到 Git Bash（bash.exe）。可能是精简安装，请重装完整版 Git for Windows：https://git-scm.com/download/win"
}

# Ensure-DockerDesktop：检测 / winget 引导安装（首启协议需手动，轮询 docker info ≤3 分钟）。
function Ensure-DockerDesktop {
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        docker info *> $null
        if ($LASTEXITCODE -eq 0) { return }
        Stop-Boot "检测到 docker 命令但 'docker info' 失败。请启动 Docker Desktop 后重跑本命令（已 clone 的目录会自动复用）。"
    }
    Write-BootWarn "未检测到 Docker。Docker Desktop 是运行本系统的前提。"
    $ans = Read-Host "现在用 winget 安装 Docker Desktop？(y/N)"
    if ($ans -match '^[yY]') {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Write-BootWarn "无 winget（LTSC/Server 常见）。请手动安装：https://www.docker.com/products/docker-desktop/"
            exit 1
        }
        winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -ne 0) {
            Write-BootWarn "winget 安装失败。请手动安装：https://www.docker.com/products/docker-desktop/"
            exit 1
        }
    } else {
        Write-BootWarn "跳过自动安装。请自行安装并启动 Docker Desktop 后重跑：https://www.docker.com/products/docker-desktop/"
        exit 1
    }

    # 装完当前会话 PATH 无 docker.exe + 首次启动需手动接受协议 → 绝对路径轮询
    $dockerExe = "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe"
    Write-BootInfo "等待 Docker Desktop 就绪（首次启动需在弹窗中手动接受协议，≤3 分钟）..."
    $deadline = (Get-Date).AddSeconds(180)
    $dockerReady = $false
    while (-not $dockerReady -and (Get-Date) -lt $deadline) {
        if (Test-Path $dockerExe) {
            & $dockerExe info *> $null
            $dockerReady = ($LASTEXITCODE -eq 0)
        }
        if (-not $dockerReady) { Start-Sleep -Seconds 5 }
    }
    if ($dockerReady) { return }
    Stop-Boot "Docker Desktop 3 分钟内未就绪。请手动启动它（首次需接受协议）后重跑本命令（已 clone 的目录会自动复用）。"
}

# Test-LsRemote：git ls-remote 探活（http.lowSpeed* 防"半连接"无限挂起）。
function Test-LsRemote {
    param([string]$Url)
    & git -c http.lowSpeedLimit=1 -c http.lowSpeedTime=10 ls-remote --exit-code $Url 'HEAD' *> $null
    return ($LASTEXITCODE -eq 0)
}

# Pick-CloneSource：直连 → Gitee（自动同步）→ gh 镜像前缀链；返回 clone 参数数组。
function Pick-CloneSource {
    $repoUrl = if ($env:PCH_REPO_URL) { $env:PCH_REPO_URL } else { 'https://github.com/YuShenLiu06/PCHSystem.git' }
    $giteeUrl = if ($env:PCH_GITEE_URL) { $env:PCH_GITEE_URL } else { 'https://gitee.com/yushenliu03/PCHSystem.git' }

    Write-BootInfo "探测 clone 源（直连 → Gitee → GitHub 镜像链）..."
    if (Test-LsRemote $repoUrl) {
        Write-BootInfo "直连源可用: $repoUrl"
        return @($repoUrl)
    }
    Write-BootWarn "GitHub 直连不通，尝试 Gitee 自动同步镜像..."
    if (Test-LsRemote $giteeUrl) {
        Write-BootInfo "选用 Gitee: $giteeUrl"
        return @($giteeUrl)
    }
    Write-BootWarn "Gitee 也不通，尝试 GitHub 镜像前缀链..."
    $mirrors = if ($env:PCH_GH_MIRRORS) {
        ,@($env:PCH_GH_MIRRORS -split ' +')
    } else {
        ,@(
            'https://ghfast.top/https://github.com|https://github.com',
            'https://ghproxy.com/https://github.com|https://github.com',
            'https://kkgithub.com|https://github.com',
            'https://gh.zwy.one/https://github.com|https://github.com'
        )
    }
    foreach ($entry in $mirrors) {
        $parts = $entry -split '\|', 2
        $rewrite, $insteadOf = $parts[0], $parts[1]
        $testUrl = $rewrite + ($repoUrl -replace [regex]::Escape($insteadOf), '')
        Write-BootInfo "  探测镜像: $rewrite"
        if (Test-LsRemote $testUrl) {
            Write-BootInfo "  选用镜像: $rewrite"
            return @('-c', "url.$rewrite.insteadOf=$insteadOf", $repoUrl)
        }
    }
    Stop-Boot @"
所有 clone 源均不可达。手动兜底（任选其一后进入目录跑 bash Scripts/install.sh）：
  git clone $giteeUrl
  git clone $repoUrl
（镜像前缀 clone 用法见 Scripts/README.md §1/§5）
"@
}

# Clone-Repo：幂等 clone（已是本仓库则复用，版本同步交给 install.sh）。
function Clone-Repo {
    $dir = if ($env:PCH_CLONE_DIR) { $env:PCH_CLONE_DIR } else { Join-Path (Get-Location) 'PCHSystem' }
    if ((Test-Path (Join-Path $dir '.git')) -and (Test-Path (Join-Path $dir 'Scripts\install.sh'))) {
        Write-BootInfo "目标目录已是 PCHSystem 仓库，复用: $dir"
        return $dir
    }
    if ((Test-Path $dir) -and (Get-ChildItem $dir -Force | Select-Object -First 1)) {
        Stop-Boot "目标目录存在且非空、也不是 PCHSystem 仓库: $dir`n请换目录（`$env:PCH_CLONE_DIR）或清空后重跑"
    }
    Write-BootInfo "clone 到 $dir ..."
    # @( ) 强制数组：函数输出的单元素数组会被 PowerShell 解 rolling 成裸字符串，
    # 之后 'url' + @($dir) 会变字符串拼接（而非数组合并）；数组 splatting 也要求集合类型。
    $cloneArgs = @(Pick-CloneSource)
    & git clone @cloneArgs "$dir"
    if ($LASTEXITCODE -ne 0) { Stop-Boot "git clone 失败。可重跑本命令（会换源重试）或见上方手动兜底命令" }
    return $dir
}

# ---------- main ----------
if ($InstallArgs -contains '-h' -or $InstallArgs -contains '--help') {
    Write-Host "PCHSystem bootstrap.ps1 —— 参数原样透传给 Scripts/install.sh（其 --help 见详情）"
    exit 0
}

Test-Git
Ensure-DockerDesktop
$cloneDir = Clone-Repo
$bash = Find-GitBash

Write-BootInfo "委托 Git Bash 执行 install.sh（参数原样透传）..."
# Git Bash 处理 C:\ 反斜杠路径不可靠 → 统一正斜杠；stdin 继承控制台保住 read -p 交互
$shPath = ($cloneDir -replace '\\', '/') + '/Scripts/install.sh'
& $bash $shPath @InstallArgs
exit $LASTEXITCODE
