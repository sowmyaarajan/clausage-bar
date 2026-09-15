<#
.SYNOPSIS
    Build a distributable clausage_bar zip for another machine.

.DESCRIPTION
    Copies the project to a staging folder, drops everything that is either
    machine-specific or regenerable, adds a plain-text INSTALL.txt, and zips
    the result.

    What gets excluded, and why each one matters:

      .venv/               Not portable at all. A venv hardcodes absolute
                           interpreter paths in pyvenv.cfg and in every
                           Scripts/*.exe shim, so a copied venv points at a
                           Python that does not exist on the target machine
                           and fails in a way that looks like a broken app.
                           install.ps1 builds a fresh one in seconds.
      __pycache__/         Bytecode is stamped with the source path and the
                           exact interpreter version. Harmless but pointless.
      .pytest_cache/       Local test run state.
      *.log, clausage/     Runtime state. Shipping a snapshot.json would show
                           the recipient YOUR usage numbers on first launch,
                           which is both wrong and a small privacy leak.
      .claude.json*        Never. Account identity and project history.
      .credentials.json    Never, under any circumstance. This is the OAuth
                           token. The recipient supplies their own by being
                           signed in to Claude Code.

    The last two are not in the project folder and could not be picked up by
    accident -- they are listed because an exclusion list is also a statement
    of intent, and the next person to edit this script should know the token
    must never travel with the app.

.PARAMETER OutDir
    Where to write the zip. Defaults to the project's parent directory, so the
    artifact lands beside the project rather than inside it (a zip written
    inside the folder it is zipping is a classic way to produce a corrupt or
    self-including archive).

.PARAMETER Version
    Stamped into the filename and INSTALL.txt. Defaults to today's date.
#>
[CmdletBinding()]
param(
    [string]$OutDir,
    [string]$Version = (Get-Date -Format 'yyyy.MM.dd')
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $OutDir) { $OutDir = Split-Path -Parent $root }

function Say($msg)  { Write-Host "  $msg" }
function Step($msg) { Write-Host "`n[*] $msg" -ForegroundColor Cyan }

Write-Host 'clausage_bar packager' -ForegroundColor White
Say "source:  $root"
Say "version: $Version"

# ------------------------------------------------------------- 1. stage
Step 'Staging a clean copy'
$stage = Join-Path ([System.IO.Path]::GetTempPath()) "clausage_pkg_$(Get-Random)"
$inner = Join-Path $stage 'clausage_bar'
New-Item -ItemType Directory -Path $inner -Force | Out-Null

# Directory names to prune wholesale, matched at any depth.
# '.venv-build', 'dist' and 'build' are the standalone-build artifacts. They
# are ~40MB of bundled interpreter that install.ps1 would rebuild anyway, and
# shipping a prebuilt exe inside the *source* package would make it ambiguous
# which of the two things the recipient is being given.
$dropDirs = @('.venv', '.venv-build', 'dist', 'build', '__pycache__',
              '.pytest_cache', '.git', 'clausage', 'raw', '.idea', '.vscode')
# Leaf patterns to drop.
$dropFiles = @('*.log', '*.pyc', '*.pyo', 'snapshot.json', 'notif_state.json', '*.spec',
               'state.json', '.credentials.json', '.claude.json*',
               '*.clausage-damaged-*', 'desktop.ini', 'Thumbs.db')

$copied = 0
Get-ChildItem $root -Recurse -File -Force | ForEach-Object {
    $rel = $_.FullName.Substring($root.Length).TrimStart('\')
    $parts = $rel -split '\\'

    # Prune by directory segment, so src/foo/__pycache__/x.pyc goes even
    # though the leaf name itself matches nothing.
    foreach ($seg in $parts[0..($parts.Count - 2)]) {
        if ($dropDirs -contains $seg) { return }
    }
    foreach ($pattern in $dropFiles) {
        if ($_.Name -like $pattern) { return }
    }

    $dest = Join-Path $inner $rel
    $destDir = Split-Path -Parent $dest
    if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
    Copy-Item $_.FullName $dest -Force
    $script:copied++
}
Say "$copied file(s) staged"

# A packaged copy that carries a stale frozen-requirements file pointing at
# another machine's venv is confusing; the pinned versions are still useful,
# so keep the file but say where it came from.
#
# The pins themselves are worth shipping; the header comment above them is
# not -- it records the absolute path and hostname of the machine the freeze
# was taken on, which means a username and a computer name travelling to
# whoever receives the zip. Small, but there is no reason for it to go.
$frozen = Join-Path $inner 'requirements-frozen-.venv.txt'
if (Test-Path $frozen) {
    $pins = Get-Content $frozen | Where-Object { $_ -notmatch '^\s*#' -and $_.Trim() }
    @(
        '# Pinned versions of the set this app was developed and tested against.'
        '# Recreate with:  python -m venv .venv'
        '#                 .\.venv\Scripts\Activate.ps1'
        '#                 pip install -r requirements-frozen-.venv.txt'
        ''
    ) + $pins | Set-Content -Path $frozen -Encoding utf8
    Say "requirements-frozen-.venv.txt: $($pins.Count) pins kept, host details stripped"
}

# ------------------------------------------------------------- 2. sanity
# An exclusion list is only as good as its test. If a token or an account file
# ever reaches the staging folder, fail loudly rather than ship it.
Step 'Checking the staged copy carries nothing personal'
$leaked = Get-ChildItem $inner -Recurse -File -Force |
          Where-Object { $_.Name -in '.credentials.json', '.claude.json' -or
                         $_.Name -like '*.clausage-damaged-*' }
if ($leaked) {
    Remove-Item $stage -Recurse -Force
    throw "refusing to package: $($leaked.Name -join ', ')"
}
# Grep the staged text for anything that looks like an OAuth token. Cheap, and
# it is the check that would have caught a token accidentally pasted into a
# comment or a test fixture.
$suspect = Get-ChildItem $inner -Recurse -File -Include *.py,*.js,*.ps1,*.md,*.txt,*.cmd,*.pyw |
           Select-String -Pattern 'sk-ant-[A-Za-z0-9_-]{20,}' -List
if ($suspect) {
    Remove-Item $stage -Recurse -Force
    throw "refusing to package: token-shaped string in $($suspect.Path -join ', ')"
}
Say 'no credentials, account files or token-shaped strings'

# ------------------------------------------------------------- 3. readme
Step 'Writing INSTALL.txt'
@"
clausage_bar $Version
=====================

A Windows tray icon showing how much of your Claude quota you have used --
current session, weekly window, and monthly usage credits -- so you never
have to open Claude to check. It alerts you at 50%, 75% and 100%.


WHAT YOU NEED FIRST
-------------------

1. Python 3.10 or newer.
   https://www.python.org/downloads/  -- tick "Add python.exe to PATH".

2. Claude Code CLI, installed AND signed in.
   Open a terminal, run `claude`, then `/login`.

   This matters and is easy to get wrong: the app has no login of its own.
   It reads the OAuth token Claude Code already stores on your machine, and
   never sees your password. Claude DESKTOP alone is not enough -- Desktop
   keeps its credentials in the Windows credential store, not in the file
   this app reads.

3. Optional: Node.js, only for the status-line fallback source.
   Without it, install with:  Install.cmd -NoStatusline


INSTALL
-------

Double-click  Install.cmd

Or, to see what it would do first, without changing anything:

    powershell -ExecutionPolicy Bypass -File install\preflight.ps1

The installer creates a virtual environment, verifies it can read your usage,
adds a Startup shortcut so the tray comes back at every logon, makes the icon
always-visible instead of hidden in the overflow tray, and registers a name so
toasts say "clausage_bar" rather than "PowerShell".


USING IT
--------

  Hover the icon    the session, weekly and credit numbers
  Click the icon    a panel with your team name and each window in detail
  Right-click       Refresh now, Details, Open usage page, Quit

The number on the icon is your CURRENT SESSION percentage. Its background
colour shifts from calm through amber to red as that fills. A dimmed, greyed
icon means the reading is stale -- usually because Claude Code has been closed
long enough for its token to expire; open Claude Code once and it recovers.


REMOVING IT
-----------

    powershell -ExecutionPolicy Bypass -File install\uninstall.ps1

That reverses everything: the shortcut, the registry entries, the collector,
and your original statusline.js from the backup it took. Add -PurgeState to
delete the saved snapshots and logs too.


ONE HONEST LIMITATION
---------------------

The usage figures come from the same endpoint Claude Code's own /usage command
calls. It is undocumented and unsupported by Anthropic, and could change or
disappear without notice. If it does, the tray falls back to reading the
Claude Code status line -- which is accurate but only updates while Claude
Code is open -- and the tooltip tells you that is what happened. It will not
show you a stale number while pretending it is current.

Packaged $(Get-Date -Format 'yyyy-MM-dd HH:mm').
"@ | Set-Content -Path (Join-Path $inner 'INSTALL.txt') -Encoding utf8
Say 'INSTALL.txt'

# ------------------------------------------------------------- 4. zip
Step 'Compressing'
$zip = Join-Path $OutDir "clausage_bar-$Version.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $inner -DestinationPath $zip -CompressionLevel Optimal
Remove-Item $stage -Recurse -Force

$size = [math]::Round((Get-Item $zip).Length / 1KB, 1)
Write-Host "`nPackaged." -ForegroundColor Green
Say "$zip  ($size KB)"
Say ''
Say 'Send that file. The recipient unzips it and double-clicks Install.cmd.'
