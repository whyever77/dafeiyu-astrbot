param(
    [Parameter(Mandatory = $true)]
    [string]$Server,
    [string]$User = "root",
    [string]$Key = "$env:USERPROFILE\.ssh\ecs_dafeiyu",
    [string]$PhoneMcpDir = "D:\AI\PhoneMCP",
    [string]$AdbDir = "D:\leidian\LDPlayer14",
    [string]$LdPlayerExe = "D:\leidian\LDPlayer14\dnplayer.exe",
    [int]$Port = 8009,
    [switch]$NoEmulator,
    [switch]$NoCloudProxy,
    [switch]$RestartBot
)

$ErrorActionPreference = "Stop"
$logDir = Join-Path $PhoneMcpDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir "service.log"

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
    Write-Host $line
}

function Test-LocalPort {
    param([int]$Port)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect("127.0.0.1", $Port)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

function Find-PhoneMcpProcesses {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessId -ne $PID -and
        $_.CommandLine -and
        $_.CommandLine -match "main\.py\s+serve" -and
        $_.CommandLine -match "--port\s+$Port"
    }
}

function Find-TunnelProcesses {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessId -ne $PID -and
        $_.CommandLine -and
        $_.CommandLine -match "start_reverse_tunnel\.ps1" -and
        $_.CommandLine -match "-Port\s+$Port"
    }
}

Write-Log "=== start_phone_service begin (server=$Server port=$Port) ==="

# 1. LDPlayer emulator
if (-not $NoEmulator) {
    # PS 5.1: with EAP=Stop, redirecting a native command's stderr (2>$null)
    # raises NativeCommandError. adb prints "daemon not running..." on first
    # start after boot, which killed this script before the readiness loop.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    if (Get-Process -Name dnplayer -ErrorAction SilentlyContinue) {
        Write-Log "LDPlayer already running"
    } else {
        Write-Log "Starting LDPlayer: $LdPlayerExe"
        Start-Process -FilePath $LdPlayerExe | Out-Null
    }

    $adb = Join-Path $AdbDir "adb.exe"
    if (-not (Test-Path -LiteralPath $adb)) {
        throw "adb not found: $adb"
    }
    $deadline = (Get-Date).AddSeconds(120)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        $devices = & $adb devices 2>$null
        if ($devices -match "emulator-5554\s+device") {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 3
    }
    if ($ready) {
        Write-Log "emulator-5554 ready"
        $packages = & $adb -s emulator-5554 shell pm list packages 2>$null
        if ($packages -match "com\.android\.adbkeyboard") {
            & $adb -s emulator-5554 shell settings put secure default_input_method com.android.adbkeyboard/.AdbIME 2>$null | Out-Null
            Write-Log "ADBKeyboard IME ensured"
        } else {
            Write-Log "WARN: ADBKeyboard not installed; Chinese input falls back to clipboard helper"
        }
    } else {
        Write-Log "WARN: emulator-5554 not ready after 120s; PhoneMCP may see no device"
    }
    $ErrorActionPreference = $prevEap
}

# 2. PhoneMCP
if (Test-LocalPort -Port $Port) {
    Write-Log "PhoneMCP already listening on 127.0.0.1:$Port"
} else {
    foreach ($p in (Find-PhoneMcpProcesses)) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Write-Log "Starting PhoneMCP"
    $startMcp = Join-Path $PSScriptRoot "start_phone_mcp.ps1"
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$startMcp`"",
        "-PhoneMcpDir", "`"$PhoneMcpDir`"", "-AdbDir", "`"$AdbDir`"", "-Port", "$Port"
    ) -WindowStyle Hidden `
      -RedirectStandardOutput (Join-Path $logDir "phonemcp.out.log") `
      -RedirectStandardError (Join-Path $logDir "phonemcp.err.log") | Out-Null

    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline -and -not (Test-LocalPort -Port $Port)) {
        Start-Sleep -Seconds 1
    }
    if (Test-LocalPort -Port $Port) {
        Write-Log "PhoneMCP is up"
    } else {
        Write-Log "ERROR: PhoneMCP did not open port $Port; check $logDir"
    }
}

# 3. SSH reverse tunnel (the script retries forever)
# The phone tunnel also carries -L 8012 for the cloud ASR relay. If an old
# tunnel is running without it, restart the tunnel launcher once.
if ($Port -eq 8009 -and -not (Test-LocalPort -Port 8012)) {
    $stale = Find-TunnelProcesses
    if ($stale) {
        Write-Log "Reverse tunnel missing ASR forward; restarting tunnel launcher"
        foreach ($p in $stale) {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 1
    }
}
if (Find-TunnelProcesses) {
    Write-Log "Reverse tunnel script already running"
} else {
    Write-Log "Starting reverse tunnel"
    $tunnel = Join-Path $PSScriptRoot "start_reverse_tunnel.ps1"
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$tunnel`"",
        "-Server", "`"$Server`"", "-User", "`"$User`"", "-Key", "`"$Key`"", "-Port", "$Port"
    ) -WindowStyle Hidden `
      -RedirectStandardOutput (Join-Path $logDir "tunnel.out.log") `
      -RedirectStandardError (Join-Path $logDir "tunnel.err.log") | Out-Null
    Start-Sleep -Seconds 3
    Write-Log "Tunnel launcher started (reconnects every 3s)"
}

# 4. Cloud proxy + ASR relay (safe to call repeatedly)
#    以前用 pgrep -f 判断：ssh 远端 shell 自己的命令行里就有 proxy.py，
#    服务没跑也会匹配成功、脚本照样报 OK。改成按监听地址判断，启动后等端口，
#    失败返回非零；再用 curl 探一次隧道后面的 MCP（仅提示，不影响退出码）。
if (-not $NoCloudProxy) {
    Write-Log "Ensuring cloud phone_mcp_proxy.py and asr_relay.py"
    # 注意：PowerShell 5.1 传参给原生 exe 会吞掉双引号，远端脚本里一律不用 "
    $remote = @'
cd /opt/dafeiyu/tools || exit 1
port_up() { ss -ltn | grep -q $1; }
wait_up() { i=0; while [ $i -lt 10 ]; do sleep 1; port_up $1 && return 0; i=$((i+1)); done; return 1; }
rc=0
if port_up 172.18.0.1:8009; then
  echo phone_proxy: listening
else
  nohup python3 phone_mcp_proxy.py >> phone_mcp_proxy.log 2>&1 &
  if wait_up 172.18.0.1:8009; then
    echo phone_proxy: started
  else
    echo phone_proxy: FAILED to listen on 172.18.0.1:8009
    tail -n 5 phone_mcp_proxy.log
    rc=1
  fi
fi
if port_up 127.0.0.1:8012; then
  echo asr_relay: listening
else
  setsid nohup python3 asr_relay.py >> asr_relay.log 2>&1 < /dev/null &
  if wait_up 127.0.0.1:8012; then
    echo asr_relay: started
  else
    echo asr_relay: FAILED to listen on 127.0.0.1:8012
    tail -n 5 asr_relay.log
    rc=1
  fi
fi
if port_up 127.0.0.1:8009; then
  echo phone_tunnel: listening
  if command -v curl >/dev/null 2>&1; then
    code=$(curl -s -o /dev/null -m 3 -w %{http_code} http://172.18.0.1:8009/Phone)
    if [ x$code = x000 ]; then
      echo phone_mcp: GET probe inconclusive
    else
      echo phone_mcp: reachable http $code
    fi
  fi
else
  echo phone_tunnel: not attached yet
fi
exit $rc
'@
    try {
        $out = & ssh.exe -i $Key -o BatchMode=yes -o ConnectTimeout=10 "$User@$Server" $remote 2>$null
        $detail = (($out -join "; ") -replace "\r?\n", "; ")
        if ($LASTEXITCODE -eq 0) {
            Write-Log "Cloud proxy check OK: $detail"
        } else {
            Write-Log "WARN: cloud proxy check failed (exit $LASTEXITCODE): $detail"
        }
    } catch {
        Write-Log "WARN: cloud proxy check failed: $($_.Exception.Message)"
    }
}

# 5. Optional: refresh AstrBot's MCP session after a reboot.
#    AstrBot keeps one SSE session per MCP server; a restarted PhoneMCP
#    needs a fresh connection on the AstrBot side.
if ($RestartBot) {
    Write-Log "Restarting cloud AstrBot to refresh the MCP session"
    $remote = "cd /opt/dafeiyu && docker restart astrbot >/dev/null && sleep 30 && python3 heal_ws.py"
    try {
        & ssh.exe -i $Key -o BatchMode=yes -o ConnectTimeout=15 "$User@$Server" $remote 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Log "Cloud AstrBot restarted and WebSocket healed"
        } else {
            Write-Log "WARN: cloud AstrBot restart failed (exit $LASTEXITCODE)"
        }
    } catch {
        Write-Log "WARN: cloud AstrBot restart failed: $($_.Exception.Message)"
    }
}

Write-Log "=== start_phone_service done ==="
