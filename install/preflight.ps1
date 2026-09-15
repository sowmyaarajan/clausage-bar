<#
.SYNOPSIS
    Check whether this machine can run clausage_bar. Changes nothing.

.DESCRIPTION
    Run this first on a machine that is not the one the app was built on.
    It separates the two blockers that actually stop the app from the three
    things that only cost you the fallback source, and it says which is which
    -- so a failed install is one line to read rather than a stack trace.

    Exit code 0 = installable. 1 = a hard blocker; the message says what.

    Written as a standalone script rather than inline in install.ps1 because
    the person receiving a copy of this app wants to know "will this work on
    my laptop?" before running an installer that creates shortcuts, patches
    their status line and writes to their registry.

.PARAMETER Quiet
    Print only failures. Used by install.ps1, which prints its own headings.
#>
[CmdletBinding()]
param([switch]$Quiet)

$claudeDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME '.claude' }

$script:blockers = @()
$script:warnings = @()

function Ok($msg)   { if (-not $Quiet) { Write-Host "  [ok]   $msg" -ForegroundColor Green } }
function Warn($msg) { $script:warnings += $msg; Write-Host "  [warn] $msg" -ForegroundColor Yellow }
function Bad($msg)  { $script:blockers += $msg; Write-Host "  [FAIL] $msg" -ForegroundColor Red }

if (-not $Quiet) { Write-Host "clausage_bar preflight" -ForegroundColor White }

# ------------------------------------------------------------- 1. Windows
# The whole app is Win32: the tray icon, the layered hover window, the DPI
# calls and the toasts have no equivalent elsewhere.
if ($env:OS -ne 'Windows_NT') {
    Bad 'This app is Windows-only (tray icon, toasts and DPI calls are all Win32).'
}

# ------------------------------------------------------------- 2. Python
# BLOCKER. `py` is the launcher shipped with python.org installs; the Microsoft
# Store build provides it too. A bare `python` on PATH is accepted as a
# fallback, but note that the Store's App Execution Alias is a stub that opens
# the Store instead of running Python -- hence checking the version, not just
# that the command resolves.
$python = $null
foreach ($candidate in @(@('py', '-3'), @('python'))) {
    $exe = $candidate[0]
    $args = @($candidate[1..($candidate.Count - 1)]) + @('--version')
    try {
        $out = & $exe @args 2>&1
        if ($LASTEXITCODE -eq 0 -and "$out" -match 'Python (\d+)\.(\d+)') {
            $major = [int]$Matches[1]; $minor = [int]$Matches[2]
            if ($major -eq 3 -and $minor -ge 10) {
                $python = $candidate
                Ok "Python $major.$minor via '$($candidate -join ' ')'"
                break
            }
            Warn "Python $major.$minor found via '$exe' but 3.10+ is needed"
        }
    } catch { }
}
if (-not $python) {
    Bad @'
No usable Python 3.10+ found.
           Install from https://www.python.org/downloads/ and tick
           "Add python.exe to PATH". The app needs 3.10+ for the
           `X | None` type syntax used throughout.
'@
}

# ------------------------------------------------------------- 3. Claude Code
# BLOCKER, and the one people are surprised by. This app has no login of its
# own by design -- it reads the OAuth token Claude Code already stores. Claude
# *Desktop* keeps its credentials in the Windows credential store, not in this
# file, so Desktop alone is not enough however signed-in it looks.
$cli = Get-Command claude -ErrorAction SilentlyContinue
if ($cli) {
    $ver = (& claude --version 2>&1) -join ' '
    Ok "Claude Code CLI: $ver"
} else {
    Warn @'
`claude` is not on PATH.
           Not fatal on its own -- the app falls back to a pinned User-Agent
           string -- but it is how the token gets refreshed roughly hourly,
           so without it the badge will go stale within the hour.
'@
}

$creds = Join-Path $claudeDir '.credentials.json'
if (Test-Path $creds) {
    # Read only the shape, never the value. A token must not reach a console
    # buffer, a transcript or a shell history.
    $hasToken = $false
    try {
        $json = Get-Content $creds -Raw | ConvertFrom-Json
        $hasToken = [bool]$json.claudeAiOauth.accessToken
    } catch { }
    if ($hasToken) {
        Ok 'Signed in to Claude Code (an OAuth token is present)'
    } else {
        Bad @'
.credentials.json exists but holds no claudeAiOauth.accessToken.
           Run `claude` and sign in with /login.
'@
    }
} else {
    Bad @"
Not signed in to Claude Code -- no $creds

           This app reads the token Claude Code stores; it has no login of
           its own. Install Claude Code, run ``claude``, sign in with /login,
           then run this again.

           Claude Desktop alone will NOT do: it keeps credentials in the
           Windows credential store, not in this file.
"@
}

# ------------------------------------------------------------- 4. Node
# OPTIONAL. Node powers the status-line collector, which is the free fallback
# source used when the HTTP endpoint is rate-limited or the token has gone
# stale. Losing it costs resilience, not function.
$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
    Ok "Node $((& node --version 2>&1) -join ' ') (status-line fallback available)"
    $statusline = Join-Path $claudeDir 'statusline.js'
    if (Test-Path $statusline) {
        Ok 'statusline.js found; the collector can be tapped into it'
    } else {
        Warn 'No statusline.js -- install with -NoStatusline, or the installer will skip that step.'
    }
} else {
    Warn 'Node not found. Install with -NoStatusline; you lose only the fallback source.'
}

# ------------------------------------------------------------- 5. network
# OPTIONAL to check, but a corporate proxy that intercepts api.anthropic.com is
# worth catching here rather than as a puzzling "?" in the tray.
#
# Sending the real User-Agent even for this one unauthenticated probe: a
# request without it is what triggers the endpoint's aggressive rate limiter,
# and there is no reason to add a strike to this machine's record just to ask
# whether DNS resolves.
$ua = 'claude-code/'
if ($cli) { $ua += (((& claude --version 2>&1) -join ' ') -split ' ')[0] } else { $ua += '2.1.263' }
try {
    Invoke-WebRequest -Uri 'https://api.anthropic.com/api/oauth/usage' `
                      -Method GET -TimeoutSec 10 -UseBasicParsing `
                      -Headers @{ 'User-Agent' = $ua } -ErrorAction Stop | Out-Null
    Ok 'api.anthropic.com is reachable'
} catch {
    # An HTTP *status* -- any status -- means we talked to the server, which is
    # the only thing this check is asking. Unauthenticated, 401 is the correct
    # answer and 429 is the endpoint's documented reflex. Neither is a problem.
    $code = 0
    if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
    if ($code -ge 400 -and $code -lt 500) {
        Ok "api.anthropic.com is reachable (HTTP $code unauthenticated, as expected)"
    } elseif ($code -ge 500) {
        Warn "api.anthropic.com answered HTTP $code. Transient; the app retries with backoff."
    } else {
        Warn "Could not reach api.anthropic.com ($($_.Exception.Message.Trim()))."
        Warn 'A proxy or VPN may be in the way. The app falls back to the status line.'
    }
}

# ------------------------------------------------------------- verdict
Write-Host ''
if ($script:blockers.Count) {
    Write-Host "Not installable yet: $($script:blockers.Count) blocker(s) above." -ForegroundColor Red
    exit 1
}
if ($script:warnings.Count) {
    Write-Host "Installable, with $($script:warnings.Count) reduced-capability warning(s)." -ForegroundColor Yellow
} else {
    Write-Host 'Installable. Everything checks out.' -ForegroundColor Green
}
exit 0
