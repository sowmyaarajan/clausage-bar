<#
.SYNOPSIS
    Make the clausage_bar tray icon always visible in the taskbar.

.DESCRIPTION
    Windows 11 hides newly registered tray icons in the overflow flyout (the
    "^" chevron) by default. Visibility is stored per icon under
    HKCU\Control Panel\NotifyIconSettings\<id> as the DWORD "IsPromoted":
    1 = always visible on the taskbar, absent/0 = hidden in the overflow.

    The tray must have run at least once, because Windows only creates the
    registry entry when an icon first registers via Shell_NotifyIcon.

    Note the entry's ExecutablePath is the *base* interpreter, not the venv
    one: a venv's pythonw.exe is a small redirector that re-executes the base
    interpreter, and Windows records the process that actually owns the icon.
    So matching on path alone is not enough to identify our icon, and this
    script also requires our tooltip signature.

.PARAMETER Unpin
    Move the icon back into the overflow flyout.

.PARAMETER RestartExplorer
    Restart Explorer afterwards. Not normally needed -- restarting the tray is
    enough, since the icon re-registers and Windows re-reads the setting.
#>
[CmdletBinding()]
param(
    [switch]$Unpin,
    [switch]$RestartExplorer
)

$ErrorActionPreference = 'Stop'
$base = 'HKCU:\Control Panel\NotifyIconSettings'
$value = if ($Unpin) { 0 } else { 1 }

# Our tooltip always starts with a window percentage, "Claude usage", or the
# stale marker. Requiring this avoids promoting an unrelated Python tray app.
$signature = '^(\[STALE\] )?(5h |7d |Claude usage)'

if (-not (Test-Path $base)) {
    Write-Host 'No NotifyIconSettings key yet. Start the tray first:' -ForegroundColor Yellow
    Write-Host '  .\run_tray.pyw'
    exit 1
}

$found = @()
Get-ChildItem $base | ForEach-Object {
    $props = Get-ItemProperty $_.PSPath
    if ($props.ExecutablePath -match 'pythonw\.exe$' -and
        $props.InitialTooltip -match $signature) {
        New-ItemProperty -Path $_.PSPath -Name 'IsPromoted' -Value $value `
                         -PropertyType DWord -Force | Out-Null
        $found += [pscustomobject]@{
            Id      = $_.PSChildName
            Exe     = $props.ExecutablePath
            Tooltip = $props.InitialTooltip
        }
    }
}

if ($found.Count -eq 0) {
    Write-Host 'No clausage_bar tray icon found in the registry.' -ForegroundColor Yellow
    Write-Host 'Start the tray, wait for the badge to show a number, then re-run this.'
    Write-Host ''
    Write-Host 'Python tray icons currently registered:'
    Get-ChildItem $base | ForEach-Object {
        $p = Get-ItemProperty $_.PSPath
        if ($p.ExecutablePath -match 'python') {
            Write-Host "  $($p.ExecutablePath)  |  $($p.InitialTooltip)"
        }
    }
    exit 1
}

$state = if ($Unpin) { 'hidden in the overflow flyout' } else { 'always visible' }
Write-Host "Set $($found.Count) icon(s) to $state." -ForegroundColor Green
$found | ForEach-Object { Write-Host "  $($_.Id)  $($_.Exe)" }

if ($RestartExplorer) {
    Write-Host 'Restarting Explorer...'
    Stop-Process -Name explorer -Force
    Write-Host 'Explorer restarted.'
} else {
    Write-Host ''
    Write-Host 'Restart the tray for this to take effect:' -ForegroundColor Cyan
    Write-Host '  Right-click the icon -> Quit, then run .\run_tray.pyw'
    Write-Host ''
    Write-Host 'You can also set this by hand at any time:'
    Write-Host '  Settings > Personalization > Taskbar > Other system tray icons'
}
