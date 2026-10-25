# 大肥鱼一键引导（Windows PowerShell）
#
#   powershell -ExecutionPolicy Bypass -File deploy-kit\bootstrap.ps1
#       → 就在本仓库里跑：装依赖 + 组装 payload + 自检
#   powershell -ExecutionPolicy Bypass -File deploy-kit\bootstrap.ps1 -Target D:\bots\dfy
#       → 在仓库外跑：先克隆到该目录，再引导
#
# 它不会、也无法替你做的事：装 AstrBot / NapCat、扫码登录 QQ、填 API 密钥。

param(
    [string]$Target = "",
    [string]$Upstream = "https://github.com/whyever77/dafeiyu-astrbot.git"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Say($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

# --- 1. 代码：就地用 vs 克隆 ---
if ((Test-Path (Join-Path $Root "plugin")) -and (Test-Path (Join-Path $Root "persona"))) {
    $Src = $Root
    Say "检测到当前目录就是项目仓库，跳过克隆: $Src"
} else {
    if (-not $Target) { $Target = "dafeiyu-astrbot" }
    if (Test-Path (Join-Path $Target ".git")) {
        Say "更新已有仓库: $Target"
        git -C $Target pull --ff-only
    } else {
        Say "克隆上游: $Upstream -> $Target"
        git clone --depth 1 $Upstream $Target
    }
    $Src = (Resolve-Path $Target).Path
    Say "工作目录: $Src"
}

# --- 2. 虚拟环境 + 依赖 ---
$venv = Join-Path $Src ".venv"
if (-not (Test-Path $venv)) {
    Say "创建虚拟环境"
    python -m venv $venv
}
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) { $py = Join-Path $venv "bin\python" }
Say "安装插件依赖"
& $py -m pip install -q --upgrade pip
& $py -m pip install -q -r (Join-Path $Root "deploy-kit\requirements-plugin.txt")

# --- 3. 组装 payload ---
$payload = Join-Path $Src "payload"
if (Test-Path $payload) { Remove-Item $payload -Recurse -Force }
New-Item -ItemType Directory -Force -Path (Join-Path $payload "plugins"), (Join-Path $payload "persona") | Out-Null
Copy-Item (Join-Path $Src "plugin\*") (Join-Path $payload "plugins") -Recurse -Force
Copy-Item (Join-Path $Src "persona\*.md") (Join-Path $payload "persona") -Force
Say "已组装: $payload"
Say "  plugins\ → 拷进 AstrBot 数据目录的 data\plugins\"
Say "  persona\ → 内容粘进 AstrBot 面板「人格」"

# --- 4. 自检 ---
Say "跑离线回归测试（不花钱、不连 QQ）"
Push-Location $Src
& $py tests\test_face_rules.py
& $py tests\test_schedule_awareness.py
Pop-Location

Say "跑前置检查"
& $py (Join-Path $Root "deploy-kit\preflight.py")

Write-Host @"

=== 接下来只剩三件手工活 ===
 1. 装 AstrBot（Launcher 版）+ NapCat（Shell.Node 版），NapCat 用小号扫码
 2. AstrBot 里加 ws://127.0.0.1:6199/ws，配 DeepSeek / 百炼 提供商
 3. 把 payload\plugins\* 拷进 AstrBot 的 data\plugins\，重启

详见 QUICKSTART.md 与 README.md。
"@ -ForegroundColor Yellow
