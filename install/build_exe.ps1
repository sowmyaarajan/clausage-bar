<#
.SYNOPSIS
    Build the standalone Windows package -- one folder, no Python required.

.DESCRIPTION
    Produces dist\clausage_bar\clausage_bar.exe, carrying its own Python and
    all four dependencies, then zips it. The recipient needs nothing installed
    except Claude Code (for the token this app reads).

    Two decisions worth knowing:

    ONEDIR, NOT ONEFILE. A --onefile build is a single pretty .exe that
    unpacks its entire contents to a temp directory on every launch. For an
    app that starts at every logon and then sits in the tray for days, that
    buys a nicer-looking download in exchange for a slower start, a temp
    directory that antivirus watches with suspicion, and a whole extra failure
    mode when the unpack is blocked. A folder starts instantly and is what the
    app actually wants to be.

    BUILT IN ITS OWN VENV. PyInstaller bundles whatever it finds in the
    environment it runs in, so building from the dev venv would sweep pytest,
    responses and their transitive dependencies into the shipped package.
    A clean venv with only requirements.txt keeps the output honest.

.PARAMETER OutDir
    Where to write the zip. Defaults to the project's parent directory.

.PARAMETER Version
    Stamped into the zip name. Defaults to today's date.

.PARAMETER OneFile
    Build a single self-contained clausage_bar.exe instead of a folder.

    One file to hand someone, at a measured cost: the exe unpacks its whole
    contents to a temp directory on every launch, so startup is slower and
    each panel window pays it again. See the timings the script prints.

.PARAMETER KeepBuildDir
    Leave build\ and the .spec in place for debugging a failed build.
#>
[CmdletBinding()]
param(
    [string]$OutDir,
    [string]$Version = (Get-Date -Format 'yyyy.MM.dd'),
    [switch]$OneFile,
    [switch]$KeepBuildDir
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $OutDir) { $OutDir = Split-Path -Parent $root }

function Say($msg)  { Write-Host "  $msg" }
function Step($msg) { Write-Host "`n[*] $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }

Write-Host 'clausage_bar standalone build' -ForegroundColor White
Say "source: $root"

# ------------------------------------------------------------- 1. build venv
Step 'Preparing an isolated build environment'
$bvenv = Join-Path $root '.venv-build'
$bpy = Join-Path $bvenv 'Scripts\python.exe'
if (-not (Test-Path $bpy)) {
    $boot = if (Get-Command py -ErrorAction SilentlyContinue) { @('py', '-3') } else { @('python') }
    & $boot[0] @($boot[1..($boot.Count - 1)]) -m venv $bvenv
    if (-not (Test-Path $bpy)) { throw "could not create $bvenv" }
    Say 'created .venv-build'
} else {
    Say '.venv-build already present'
}

& $bpy -m pip install --disable-pip-version-check -q --upgrade pip
& $bpy -m pip install --disable-pip-version-check -q -r (Join-Path $root 'requirements.txt')
& $bpy -m pip install --disable-pip-version-check -q pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
$piv = (& $bpy -m PyInstaller --version) -join ''
Say "pyinstaller $piv + runtime deps only (no pytest)"

# ------------------------------------------------------------- 2. build
Step 'Building clausage_bar.exe'
Push-Location $root
try {
    $distDir = Join-Path $root 'dist'
    if (Test-Path $distDir) { Remove-Item $distDir -Recurse -Force }

    # --windowed: no console window, ever. This is a tray app; a console
    #   flashing at every logon would be the most visible thing about it.
    # --hidden-import pystray._win32: pystray picks its backend at runtime by
    #   importing a name it builds from the platform, which static analysis
    #   cannot see. Without this the exe builds fine and then fails at launch
    #   with "no available backend" -- the single most likely way this build
    #   breaks, so it is pinned explicitly rather than left to discovery.
    # --exclude-module pytest/responses: belt and braces on the clean venv.
    $mode = if ($OneFile) { '--onefile' } else { '--onedir' }
    Say "mode: $mode"
    & $bpy -m PyInstaller `
        --noconfirm --clean --windowed $mode `
        --name clausage_bar `
        --paths (Join-Path $root 'src') `
        --hidden-import pystray._win32 `
        --exclude-module pytest --exclude-module responses `
        --exclude-module _pytest --exclude-module pluggy `
        (Join-Path $root 'run_tray.pyw')
    if ($LASTEXITCODE -ne 0) { throw 'pyinstaller failed' }
} finally {
    Pop-Location
}

$exe = if ($OneFile) { Join-Path $root 'dist\clausage_bar.exe' }
       else { Join-Path $root 'dist\clausage_bar\clausage_bar.exe' }
if (-not (Test-Path $exe)) { throw "expected $exe" }
$exeSize = [math]::Round((Get-Item $exe).Length / 1MB, 1)
if ($OneFile) {
    Say "clausage_bar.exe ($exeSize MB) -- single file, everything inside"
} else {
    $dirSize = [math]::Round(((Get-ChildItem (Split-Path $exe) -Recurse -File |
                               Measure-Object Length -Sum).Sum / 1MB), 1)
    Say "clausage_bar.exe ($exeSize MB), package total $dirSize MB"
}

# ------------------------------------------------------------- 3. smoke test
# A build that imports cleanly but cannot reach its own dependencies is the
# failure this catches -- and it is invisible until launch, because a windowed
# exe with a broken import shows nothing at all: no console, no error, no
# tray icon. --once runs the whole pipeline and exits.
Step 'Smoke-testing the built executable'
$log = Join-Path $env:TEMP "clausage_smoke_$PID.txt"
$clock = [System.Diagnostics.Stopwatch]::StartNew()
$proc = Start-Process -FilePath $exe -ArgumentList '--once', '--diagnose', '--no-toast' `
                      -Wait -PassThru -NoNewWindow `
                      -RedirectStandardOutput $log -RedirectStandardError "$log.err"
$clock.Stop()
# This figure includes one full network poll, so it is not pure startup -- but
# the *difference* between the two build modes is, and that is the number the
# onefile trade-off actually turns on.
Say ("end-to-end run: {0:N1}s" -f $clock.Elapsed.TotalSeconds)
$out = ''
if (Test-Path $log) { $out = Get-Content $log -Raw }
$err = ''
if (Test-Path "$log.err") { $err = Get-Content "$log.err" -Raw }

if ($proc.ExitCode -ne 0) {
    Warn "exit code $($proc.ExitCode)"
    if ($err) { Write-Host $err -ForegroundColor Red }
    throw 'the built executable failed its smoke test'
}
if ($out -match 'badge:') {
    Say 'it runs, reads usage and renders a badge'
    ($out -split "`n" | Select-String -Pattern 'badge:|style=|source ' |
        Select-Object -First 3) | ForEach-Object { Say "  $($_.ToString().Trim())" }
} else {
    Warn 'ran without error but produced no badge line; check it by hand'
}
Remove-Item $log, "$log.err" -Force -ErrorAction SilentlyContinue

# ------------------------------------------------------------- 4. bundle
Step 'Assembling the package'
$stage = Join-Path ([System.IO.Path]::GetTempPath()) "clausage_exe_$(Get-Random)"
$inner = Join-Path $stage 'clausage_bar'
New-Item -ItemType Directory -Path $inner -Force | Out-Null
if ($OneFile) {
    Copy-Item $exe $inner -Force
} else {
    Copy-Item (Join-Path $root 'dist\clausage_bar\*') $inner -Recurse -Force
}

# The optional Node collector, for a recipient who does have a status line.
$col = Join-Path $root 'collector\clausage_collector.js'
if (Test-Path $col) { Copy-Item $col $inner -Force }

@'
@echo off
REM Turn on "start with Windows" and launch the tray, without a console.
REM Equivalent to right-clicking the tray icon and ticking Start with Windows.
setlocal
cd /d "%~dp0"
start "" "%~dp0clausage_bar.exe"
echo clausage_bar is starting in the system tray.
echo.
echo If you do not see it, click the ^^ chevron next to the clock -- Windows
echo hides new tray icons there. Drag it onto the taskbar to keep it visible.
echo.
echo Right-click the icon for Refresh, Details and Start with Windows.
timeout /t 8 >nul
'@ | Set-Content (Join-Path $inner 'Start clausage_bar.cmd') -Encoding ascii

@"
clausage_bar $Version  --  standalone Windows build
===================================================

A tray icon showing how much of your Claude quota you have used: current
session, weekly window, and monthly usage credits. It alerts you at 50%, 75%
and 100% so you never have to open Claude to check.

This build carries its own Python. You do NOT need to install Python.


THE ONE REQUIREMENT
-------------------

Claude Code CLI, installed and signed in.

    claude          then  /login

This app has no login of its own, by design: it reads the OAuth token Claude
Code already stores on your machine and never sees your password.

Claude DESKTOP alone is not enough, however signed-in it looks -- Desktop
keeps its credentials in the Windows credential store, not in the file this
app reads.


RUN IT
------

Double-click  "Start clausage_bar.cmd"     (or clausage_bar.exe directly)

Nothing installs. Keep this folder wherever you like -- but do not move it
after enabling "Start with Windows", because the shortcut points here.

Do not delete or rearrange the files beside the .exe: the bundled Python
lives in _internal and the app will not start without it.


USING IT
--------

  Hover the icon    session, weekly and credit numbers
  Click the icon    a panel with your team name and each window in detail
  Right-click       Refresh now, Details, Open usage page,
                    Start with Windows, Quit

The number on the icon is your CURRENT SESSION percentage. Its background
shifts from calm through amber to red as that fills. A dimmed grey icon means
the reading is stale -- usually Claude Code has been closed long enough for
its token to expire. Open Claude Code once and it recovers.

Can't see the icon? Windows 11 hides new tray icons in the overflow flyout.
Click the ^ chevron by the clock and drag it onto the taskbar.


START WITH WINDOWS
------------------

Right-click the icon and tick "Start with Windows". That writes one shortcut
to your Startup folder, pointing at this exe. Untick to remove it.


REMOVING IT
-----------

Untick "Start with Windows", Quit, and delete this folder. Optionally delete
the saved state at  %USERPROFILE%\.claude\clausage

Nothing is written to Program Files, and the only registry value is the
optional toast name.


ONE HONEST LIMITATION
---------------------

The figures come from the same endpoint Claude Code's own /usage command
calls. It is undocumented and unsupported by Anthropic and could change
without notice. If it does, the tray falls back to reading the Claude Code
status line -- accurate, but only updating while Claude Code is open -- and
the tooltip tells you so. It will not show a stale number as though it were
current.

Built $(Get-Date -Format 'yyyy-MM-dd HH:mm').
"@ | Set-Content (Join-Path $inner 'README.txt') -Encoding utf8

Say 'README.txt, Start clausage_bar.cmd, collector'

# ------------------------------------------------------------- 5. leak check
Step 'Checking the package carries nothing personal'
$bad = Get-ChildItem $inner -Recurse -File -Force |
       Where-Object { $_.Name -in '.credentials.json', '.claude.json',
                                  'snapshot.json', 'state.json' }
if ($bad) {
    Remove-Item $stage -Recurse -Force
    throw "refusing to package: $($bad.Name -join ', ')"
}
$suspect = Get-ChildItem $inner -Recurse -File -Include *.txt,*.cmd,*.js,*.json |
           Select-String -Pattern 'sk-ant-[A-Za-z0-9_-]{20,}' -List
if ($suspect) {
    Remove-Item $stage -Recurse -Force
    throw "refusing to package: token-shaped string in $($suspect.Path -join ', ')"
}
Say 'no credentials or token-shaped strings'

# ------------------------------------------------------------- 6. zip
Step 'Compressing'
$suffix = if ($OneFile) { 'single-exe' } else { 'standalone' }
$zip = Join-Path $OutDir "clausage_bar-$suffix-$Version.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $inner -DestinationPath $zip -CompressionLevel Optimal

# For a onefile build the point is the single file, so put the bare exe beside
# the zip too: that is the thing the user actually wants to hand over, and
# making them open an archive to retrieve it would defeat the build mode.
$bareExe = $null
if ($OneFile) {
    $bareExe = Join-Path $OutDir 'clausage_bar.exe'
    Copy-Item $exe $bareExe -Force
}
Remove-Item $stage -Recurse -Force
if (-not $KeepBuildDir) {
    Remove-Item (Join-Path $root 'build') -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $root 'clausage_bar.spec') -Force -ErrorAction SilentlyContinue
}

$size = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "`nBuilt." -ForegroundColor Green
if ($bareExe) {
    $bs = [math]::Round((Get-Item $bareExe).Length / 1MB, 1)
    Say "$bareExe  ($bs MB)   <-- send this single file"
    Say "$zip  ($size MB)   (same exe plus README.txt)"
    Say ''
    Say 'The recipient just double-clicks it. Nothing to unzip, no Python.'
    Say 'Note: mail and chat systems often strip .exe attachments -- send the'
    Say 'zip, or a file-share link, if it does not arrive.'
} else {
    Say "$zip  ($size MB)"
    Say ''
    Say 'The recipient unzips it and double-clicks "Start clausage_bar.cmd".'
    Say 'No Python needed.'
}
