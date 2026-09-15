<#
.SYNOPSIS
    Install clausage_bar: virtualenv, status-line collector, startup shortcut.

.DESCRIPTION
    Idempotent. Backs up statusline.js before touching it and refuses to patch
    twice. Run uninstall.ps1 to reverse everything.

.PARAMETER NoAutostart
    Skip creating the Startup-folder shortcut.

.PARAMETER NoStatusline
    Skip the status-line collector. The tray still works from the HTTP
    endpoint; you lose only the free fallback source.

.PARAMETER NoToastIdentity
    Skip registering the AppUserModelId. Toasts then show as "PowerShell".

.PARAMETER NoPin
    Skip making the tray icon always-visible on the taskbar. Windows 11 hides
    new tray icons in the overflow flyout by default; without this flag the
    installer promotes ours once it has registered.

.PARAMETER SkipPreflight
    Skip the environment check. The check is advisory for most things but hard
    on two: a usable Python, and a signed-in Claude Code. Skipping it turns a
    one-line explanation into a stack trace, so it exists for automation
    rather than for people.

.PARAMETER ReplaceStatusline
    Replace statusline.js wholesale with collector/statusline.reference.js,
    which shows real rate_limits in the 5h and weekly bars instead of the
    original approximations. Without this, only the two-line collector tap is
    added and the existing bars are left alone. See install/statusline_patch.md
#>
[CmdletBinding()]
param(
    [switch]$NoAutostart,
    [switch]$NoStatusline,
    [switch]$NoToastIdentity,
    [switch]$NoPin,
    [switch]$SkipPreflight,
    [switch]$ReplaceStatusline
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$claudeDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME '.claude' }

function Say($msg)  { Write-Host "  $msg" }
function Step($msg) { Write-Host "`n[*] $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }

Write-Host "clausage_bar installer" -ForegroundColor White
Say "project:   $root"
Say "claude dir: $claudeDir"

# ---------------------------------------------------------------- 0. preflight
if (-not $SkipPreflight) {
    Step 'Checking this machine'
    & powershell -NoProfile -ExecutionPolicy Bypass `
                 -File (Join-Path $PSScriptRoot 'preflight.ps1') |
        ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) {
        # A clean exit rather than `throw`: this is the most likely message a
        # recipient ever sees, and a PowerShell stack trace under it buries
        # the one line that tells them what to do.
        Write-Host ''
        Write-Host 'Stopping here. Nothing on this machine has been changed.' -ForegroundColor Red
        Write-Host 'Fix the item(s) marked [FAIL] above, then run this again.'
        exit 1
    }
}

# ---------------------------------------------------------------- 1. venv
Step 'Creating the virtual environment'
$venv = Join-Path $root '.venv'
$python = Join-Path $venv 'Scripts\python.exe'
if (Test-Path $python) {
    Say 'already present'
} else {
    # `py` is the usual launcher but is absent from some installs (notably a
    # Microsoft Store Python, and any install where the launcher was
    # deselected), so fall back to a bare `python` rather than dying on a
    # missing command the user has no reason to connect to the real problem.
    $bootstrap = if (Get-Command py -ErrorAction SilentlyContinue) { @('py', '-3') }
                 else { @('python') }
    Say "bootstrapping with '$($bootstrap -join ' ')'"
    & $bootstrap[0] @($bootstrap[1..($bootstrap.Count - 1)]) -m venv $venv
    if (-not (Test-Path $python)) {
        throw @"
venv creation failed: $python was not created.

  Check that Python 3.10+ is installed and on PATH:
      python --version

  If that opens the Microsoft Store instead of printing a version, the
  App Execution Alias stub is shadowing a real install. Turn it off under
  Settings > Apps > Advanced app settings > App execution aliases.
"@
    }
    Say 'created'
}

Step 'Installing dependencies'
& $python -m pip install --disable-pip-version-check -q -r (Join-Path $root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
Say 'pystray, pillow, winotify, requests'

# ---------------------------------------------------------------- 2. probe
Step 'Verifying the usage endpoint'
& $python (Join-Path $root 'tools\probe_endpoint.py') | Select-Object -Last 3
if ($LASTEXITCODE -ne 0) {
    Warn 'The usage endpoint did not return data. The tray will fall back to'
    Warn 'the status line, which only updates while Claude Code is open.'
    Warn 'Run tools\probe_endpoint.py yourself to see the full error.'
}

# ---------------------------------------------------------------- 3. collector
if (-not $NoStatusline -and -not (Get-Command node -ErrorAction SilentlyContinue)) {
    Step 'Skipping the status-line collector'
    # Without Node the collector cannot run and the smoke test below cannot
    # even be attempted, so there is no point copying it in. The tray is
    # unaffected: it loses the free fallback source, not the primary one.
    Warn 'Node is not installed, so the status-line fallback is unavailable.'
    Warn 'The tray still reads the HTTP endpoint. Install Node later and re-run'
    Warn 'this installer to add the fallback.'
    $NoStatusline = $true
}

if (-not $NoStatusline) {
    Step 'Installing the status-line collector'
    Copy-Item (Join-Path $root 'collector\clausage_collector.js') `
              (Join-Path $claudeDir 'clausage_collector.js') -Force
    Say 'clausage_collector.js -> .claude\'

    $statusline = Join-Path $claudeDir 'statusline.js'
    $backup = "$statusline.clausage-bak"

    if (-not (Test-Path $statusline)) {
        Warn 'no statusline.js found; skipping the patch'
    }
    elseif ($ReplaceStatusline) {
        if (-not (Test-Path $backup)) {
            Copy-Item $statusline $backup
            Say "backed up -> $(Split-Path -Leaf $backup)"
        }
        Copy-Item (Join-Path $root 'collector\statusline.reference.js') $statusline -Force
        Say 'replaced statusline.js (real rate_limits in the 5h and weekly bars)'
        Say 'the old .session_start and .weekly_usage.json are now unused'
    }
    elseif (Select-String -Path $statusline -Pattern 'clausage_collector' -Quiet) {
        Say 'statusline.js already taps the collector'
    }
    else {
        if (-not (Test-Path $backup)) {
            Copy-Item $statusline $backup
            Say "backed up -> $(Split-Path -Leaf $backup)"
        }
        # Insert the tap right after the payload is parsed.
        $lines = Get-Content $statusline
        $anchor = ($lines | Select-String -Pattern 'JSON\.parse' | Select-Object -First 1)
        if (-not $anchor) {
            Warn 'could not find a JSON.parse anchor; patch not applied'
        } else {
            $at = $anchor.LineNumber
            $indent = [regex]::Match($lines[$at - 1], '^\s*').Value
            $patch = @(
                "$indent// clausage_bar collector (fail-open; must never break the statusline)"
                "$indent" + "try { require('./clausage_collector.js').capture(data); } catch (_) {}"
            )
            $updated = @($lines[0..($at - 1)]) + $patch + @($lines[$at..($lines.Count - 1)])
            Set-Content -Path $statusline -Value $updated -Encoding utf8
            Say 'patched statusline.js (2 lines added)'
            Say 'tip: -ReplaceStatusline also fixes the 5h/weekly bars to use real data'
        }
    }

    # A patched status line must still render with no rate_limits present.
    #
    # Guarded on the file existing: statusline.js is a user's own script, not
    # something Claude Code ships, so a recipient machine very often has none.
    # Unguarded, this ran `node` against a missing path and threw -- aborting
    # the install after the venv had already been built, over an optional
    # fallback source that was correctly skipped moments earlier.
    if (Test-Path $statusline) {
        $probe = '{"model":{"display_name":"Test"}}' | & node $statusline
        if ($LASTEXITCODE -ne 0) {
            if (Test-Path $backup) {
                Copy-Item $backup $statusline -Force
                Warn 'statusline.js exited non-zero after patching -- backup restored'
            } else {
                Warn 'statusline.js exits non-zero, and there is no backup to restore'
            }
            throw 'status line smoke test failed'
        }
        Say 'status line smoke test passed'
    }
}

# ---------------------------------------------------------------- 4. toasts
if (-not $NoToastIdentity) {
    Step 'Registering the toast identity'
    $key = 'HKCU:\SOFTWARE\Classes\AppUserModelId\clausage_bar'
    New-Item -Path $key -Force | Out-Null
    New-ItemProperty -Path $key -Name 'DisplayName' -Value 'clausage_bar' `
                     -PropertyType String -Force | Out-Null
    Say 'toasts will be attributed to clausage_bar'
}

# ---------------------------------------------------------------- 5. autostart
if (-not $NoAutostart) {
    Step 'Enabling start with Windows'
    $env:PYTHONPATH = Join-Path $root 'src'
    & $python -c "import sys; sys.path.insert(0, r'$root\src'); from clausage_bar import autostart; print('ok' if autostart.enable() else 'failed')"
    Say "shortcut: $env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\clausage_bar.lnk"
}

# ---------------------------------------------------------------- 6. taskbar
if (-not $NoPin) {
    Step 'Making the tray icon always visible'
    # Windows only creates the registry entry once an icon has registered, so
    # the tray must have run at least once before we can promote it.
    $running = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
               Where-Object { $_.CommandLine -match 'run_tray' }
    if (-not $running) {
        Say 'starting the tray so Windows registers its icon'
        Start-Process -FilePath (Join-Path $venv 'Scripts\pythonw.exe') `
                      -ArgumentList "`"$(Join-Path $root 'run_tray.pyw')`"" `
                      -WorkingDirectory $root
        $sig = '^(\[STALE\] )?(5h |7d |Claude usage)'
        for ($i = 0; $i -lt 20; $i++) {
            Start-Sleep -Milliseconds 500
            $hit = Get-ChildItem 'HKCU:\Control Panel\NotifyIconSettings' -ErrorAction SilentlyContinue |
                   Where-Object { (Get-ItemProperty $_.PSPath).InitialTooltip -match $sig }
            if ($hit) { break }
        }
    }
    & powershell -NoProfile -ExecutionPolicy Bypass `
                 -File (Join-Path $PSScriptRoot 'pin_to_taskbar.ps1') |
        ForEach-Object { Say $_ }
}

# ---------------------------------------------------------------- done
Write-Host "`nInstalled." -ForegroundColor Green
Write-Host @"

  Start it now:      .\run_tray.pyw          (or double-click it)
  See what it reads:  .\run_clausage.cmd --diagnose
  Logs:               $claudeDir\clausage\clausage.log
  Remove everything:  .\install\uninstall.ps1

  Note: the usage endpoint this reads is undocumented and unsupported by
  Anthropic. If it stops working, the tray falls back to the status line and
  says so in the tooltip.
"@
