# 大肥鱼一键引导（Windows PowerShell）
#
#   powershell -ExecutionPolicy Bypass -File deploy-kit\bootstrap.ps1
#       → 就在本仓库里跑：装依赖 + 组装 payload + 离线测试 + 体检
#   powershell -ExecutionPolicy Bypass -File deploy-kit\bootstrap.ps1 -WithBili
#       → 连同 B 站自主活动插件（Pillow / mcp）一起安装与组装
#   powershell -ExecutionPolicy Bypass -File deploy-kit\bootstrap.ps1 -Offline
#       → 跳过联网 pip 安装，只组装 payload 并跑标准库离线测试
#   powershell -ExecutionPolicy Bypass -File deploy-kit\bootstrap.ps1 -Target D:\bots\dfy
#       → 在仓库外跑：先克隆到该目录，再引导
#
# 它不会、也无法替你做的事：装 AstrBot / NapCat、扫码登录 QQ、填 API 密钥。

param(
    [string]$Target = "",
    [string]$Upstream = "https://github.com/whyever77/dafeiyu-astrbot.git",
    [switch]$WithBili = $false,
    [switch]$Offline = $false
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Say($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

# --- 1. 代码：就地用 vs 克隆 ---
if ((-not $Target) -and (Test-Path (Join-Path $Root "plugin")) -and (Test-Path (Join-Path $Root "persona"))) {
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

$ReqFile = Join-Path $Src "deploy-kit\requirements-plugin.txt"
$BiliReq = Join-Path $Src "plugin\astrbot_plugin_bili_activity\requirements.txt"
$Preflight = Join-Path $Src "deploy-kit\preflight.py"

# --- 2. 虚拟环境 + 依赖 ---
$venv = Join-Path $Src ".venv"
if (-not $Offline) {
    if (-not (Test-Path $venv)) {
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            Say "用 uv 创建虚拟环境"
            uv venv --quiet $venv
        } else {
            Say "创建虚拟环境"
            python -m venv $venv
        }
    }
}
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) { $py = Join-Path $venv "bin\python" }
if (-not (Test-Path $py)) { $py = "python" }

function Install-Req($reqPath) {
    & $py -m pip --version *> $null
    if ($LASTEXITCODE -eq 0) {
        & $py -m pip install -q -r $reqPath
        if ($LASTEXITCODE -ne 0) {
            & $py -m pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r $reqPath
        }
    } elseif (Get-Command uv -ErrorAction SilentlyContinue) {
        uv pip install --python $py -q -r $reqPath
        if ($LASTEXITCODE -ne 0) {
            uv pip install --python $py -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r $reqPath
        }
    } else {
        & $py -m ensurepip --upgrade
        & $py -m pip install -q -r $reqPath
    }
}

if (-not $Offline) {
    Say "安装核心插件依赖（dashscope / imageio-ffmpeg / aiohttp）"
    Install-Req $ReqFile
    if ($WithBili -and (Test-Path $BiliReq)) {
        Say "安装 B 站自主活动插件依赖（Pillow / mcp）"
        Install-Req $BiliReq
    }
} else {
    Say "已跳过联网装包（-Offline）"
}

# --- 3. 干净组装 payload + 前置检查 ---
$payload = Join-Path $Src "payload"
$pfArgs = @($Preflight, "--stage-payload", $payload)
if ($WithBili) { $pfArgs += "--with-bili" }
Say "干净组装 payload 并执行前置检查"
& $py @pfArgs

# --- 4. 离线回归测试 ---
Say "跑核心离线回归测试（不花钱、不连 QQ）"
Push-Location $Src
try {
    & $py tests\test_face_rules.py
    if ($LASTEXITCODE -ne 0) { throw "test_face_rules 失败" }
    & $py tests\test_schedule_awareness.py
    if ($LASTEXITCODE -ne 0) { throw "test_schedule_awareness 失败" }
    & $py tests\test_heal_ws.py
    if ($LASTEXITCODE -ne 0) { throw "test_heal_ws 失败" }
    & $py tests\test_chat_layout.py
    if ($LASTEXITCODE -ne 0) { throw "test_chat_layout 失败" }
    & $py tests\test_deploy_kit.py
    if ($LASTEXITCODE -ne 0) { throw "test_deploy_kit 失败" }
} finally {
    Pop-Location
}

Write-Host @"

=== 接下来只剩三件手工活 ===
 1. 装 AstrBot（Launcher 版）+ NapCat（Shell.Node 版，双击根目录 napcat.bat），用小号扫码
 2. NapCat 加客户端 ws://127.0.0.1:6199/ws；AstrBot 配 DeepSeek / 百炼提供商，并在该环境装好依赖
 3. 把 payload\plugins\* 拷进 AstrBot 的 data\plugins\；把 payload\persona\大肥鱼-system-prompt.txt 整段粘进「人格」

全部启动后可运行：python deploy-kit\preflight.py --stage post --env .env 做最终复查。
详见 QUICKSTART.md 与 README.md。
"@ -ForegroundColor Yellow
