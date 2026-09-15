<#
.SYNOPSIS
    Remove clausage_bar and restore statusline.js.

.PARAMETER PurgeState
    Also delete ~/.claude/clausage (snapshots, notification state, logs).

.PARAMETER KeepStatusline
    Leave statusline.js as it is instead of restoring the backup.
#>
[CmdletBinding()]
param(
    [switch]$PurgeState,
    [switch]$KeepStatusline
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$claudeDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME '.claude' }

function Say($msg)  { Write-Host "  $msg" }
function Step($msg) { Write-Host "`n[*] $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }

Write-Host 'clausage_bar uninstaller' -ForegroundColor White

# ---------------------------------------------------------------- 1. process
Step 'Stopping a running tray, if any'
$stopped = 0
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'clausage' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        $stopped++
    }
Say "stopped $stopped process(es)"

# ---------------------------------------------------------------- 2. autostart
Step 'Removing the startup shortcut'
$link = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\clausage_bar.lnk'
if (Test-Path $link) {
    Remove-Item $link -Force
    Say 'removed'
} else {
    Say 'not present'
}

# ---------------------------------------------------------------- 3. statusline
if (-not $KeepStatusline) {
    Step 'Restoring statusline.js'
    $statusline = Join-Path $claudeDir 'statusline.js'
    $backup = "$statusline.clausage-bak"
    if (Test-Path $backup) {
        Copy-Item $backup $statusline -Force
        Remove-Item $backup -Force
        Say 'restored from backup and removed the backup'

        $same = '{"model":{"display_name":"Test"}}' | & node $statusline
        if ($LASTEXITCODE -eq 0) {
            Say 'restored status line renders correctly'
        } else {
            Warn 'the restored status line exited non-zero -- check it manually'
        }
    }
    elseif (Test-Path $statusline) {
        # No backup: strip only our two lines rather than touch anything else.
        if (Select-String -Path $statusline -Pattern 'clausage_collector' -Quiet) {
            $kept = Get-Content $statusline |
                    Where-Object { $_ -notmatch 'clausage_collector' -and
                                   $_ -notmatch 'clausage_bar collector' }
            Set-Content -Path $statusline -Value $kept -Encoding utf8
            Say 'no backup found; removed the collector lines in place'
        } else {
            Say 'statusline.js carries no clausage patch'
        }
    }
}

Step 'Removing the collector'
$collector = Join-Path $claudeDir 'clausage_collector.js'
if (Test-Path $collector) { Remove-Item $collector -Force; Say 'removed' }
else { Say 'not present' }

# ---------------------------------------------------------------- 4. registry
Step 'Removing the toast identity'
$key = 'HKCU:\SOFTWARE\Classes\AppUserModelId\clausage_bar'
if (Test-Path $key) { Remove-Item $key -Recurse -Force; Say 'removed' }
else { Say 'not present' }

# ---------------------------------------------------------------- 4b. taskbar
Step 'Removing the taskbar visibility setting'
$base = 'HKCU:\Control Panel\NotifyIconSettings'
$removed = 0
if (Test-Path $base) {
    Get-ChildItem $base | ForEach-Object {
        $p = Get-ItemProperty $_.PSPath
        if ($p.ExecutablePath -match 'pythonw\.exe$' -and
            $p.InitialTooltip -match '^(\[STALE\] )?(5h |7d |Claude usage)') {
            Remove-Item $_.PSPath -Recurse -Force
            $removed++
        }
    }
}
Say "removed $removed tray icon entry/entries"

# ---------------------------------------------------------------- 5. state
Step 'Application state'
$stateDir = Join-Path $claudeDir 'clausage'
if ($PurgeState) {
    if (Test-Path $stateDir) { Remove-Item $stateDir -Recurse -Force; Say "deleted $stateDir" }
    else { Say 'nothing to delete' }
} else {
    Say "kept $stateDir  (pass -PurgeState to delete)"
}

Write-Host "`nUninstalled." -ForegroundColor Green
Say "The project folder and .venv are untouched: $root"
