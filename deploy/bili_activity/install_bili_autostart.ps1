param(
    [string]$Server = "",
    [string]$User = "root",
    [string]$Key = "$env:USERPROFILE\.ssh\ecs_dafeiyu",
    [int]$SshPort = 22,
    [string]$RuntimeDir = "D:\AI\BiliActivity",
    [string]$Adb = "D:\leidian\LDPlayer14\adb.exe",
    [switch]$Remove
)
$ErrorActionPreference = "Stop"
if (-not $Server) {
    $SshHost = $env:DAFEIYU_SSH_HOST
    $HostFile = Join-Path $HOME ".dafeiyu-admin\host"
    if (-not $SshHost -and (Test-Path $HostFile)) {
        $SshHost = (Get-Content $HostFile -TotalCount 1).Trim()
    }
    if ($SshHost) { $Server = $SshHost -replace '^[^@]+@', '' }
}
if (-not $Server) {
    throw "Specify -Server <host>, or set DAFEIYU_SSH_HOST / ~/.dafeiyu-admin/host"
}
$startup = [Environment]::GetFolderPath("Startup")
$target = Join-Path $startup "dafeiyu-bili.cmd"
if ($Remove) {
    Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
    Write-Output "Removed $target"
    exit 0
}
$script = Join-Path $RuntimeDir "src\start_bili_service.ps1"
if (-not (Test-Path -LiteralPath $script)) { throw "Start script missing: $script" }
# Wait for emulator-5554 first: at logon this entry may run before the phone
# service has finished booting LDPlayer, and start_bili_service.ps1 aborts when
# the device is offline. Poll adb for up to 5 minutes, then launch the service.
$line = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$adb=''{5}''; $deadline=(Get-Date).AddMinutes(5); while ((Get-Date) -lt $deadline) {{ $d = & $adb devices 2>$null; if ($d -match ''emulator-5554\s+device'') {{ break }}; Start-Sleep -Seconds 5 }}; & ''{0}'' -Server ''{1}'' -User ''{2}'' -Key ''{3}'' -SshPort {4} -Adb ''{5}'' -SkipInstall"' -f `
    $script, $Server, $User, $Key, $SshPort, $Adb
Set-Content -LiteralPath $target -Value $line -Encoding Ascii
Write-Output "Installed $target"
Write-Output "It waits up to 5 minutes for emulator-5554 at logon, then starts the Bili service."
