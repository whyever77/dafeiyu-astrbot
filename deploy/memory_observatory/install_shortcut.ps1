param([string]$Pythonw = "")
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
if (-not $Pythonw) {
    $Pythonw = (Get-Command pythonw.exe -ErrorAction Stop).Source
}
$Pythonw = (Resolve-Path -LiteralPath $Pythonw).Path
if ([IO.Path]::GetFileName($Pythonw) -ne "pythonw.exe") {
    throw "Use pythonw.exe so the desktop shortcut opens without a console."
}
$icon = Join-Path $PSScriptRoot "static\memory-whale.ico"
if (-not (Test-Path -LiteralPath $icon -PathType Leaf)) { throw "Whale icon is missing." }
$desktop = [Environment]::GetFolderPath("Desktop")
$destination = Join-Path $desktop "记忆星海.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($destination)
if ((Test-Path -LiteralPath $destination) -and
    ($shortcut.Arguments -ne "-B -m deploy.memory_observatory.launcher" -or
     $shortcut.WorkingDirectory -ne $repoRoot)) {
    throw "An unrelated shortcut already exists at the destination; it was not overwritten."
}
$shortcut.TargetPath = $Pythonw
$shortcut.Arguments = "-B -m deploy.memory_observatory.launcher"
$shortcut.WorkingDirectory = $repoRoot
$shortcut.IconLocation = "$icon,0"
$shortcut.Description = "记忆星海 · 大肥鱼的 LivingMemory 只读观测台"
$shortcut.WindowStyle = 7
$shortcut.Save()
# Notify Explorer of this one shortcut, without restarting it or clearing caches.
if (-not ("MemoryShortcutNotify" -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class MemoryShortcutNotify {
    [DllImport("shell32.dll", CharSet=CharSet.Unicode)]
    public static extern void SHChangeNotify(uint e, uint flags, string path, IntPtr unused);
}
'@
}
[MemoryShortcutNotify]::SHChangeNotify(0x00002000, 0x0005, $destination, [IntPtr]::Zero)
Write-Output $destination
