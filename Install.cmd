@echo off
REM ---------------------------------------------------------------------------
REM Double-clickable installer for clausage_bar.
REM
REM This wrapper exists because a .ps1 file cannot be double-clicked: Windows
REM associates it with Notepad, and even from a prompt the default
REM ExecutionPolicy (RemoteSigned) blocks a script that arrived from the
REM internet or a network share -- which is exactly how this folder travels.
REM A .cmd has no such restriction and can set the policy for its own child
REM process only, changing nothing about the machine.
REM
REM Pass any install.ps1 switch straight through, e.g.
REM     Install.cmd -NoStatusline -NoPin
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install\install.ps1" %*
set RC=%ERRORLEVEL%

echo.
if %RC% NEQ 0 (
    echo Install failed with exit code %RC%. The messages above say why.
)

REM Double-clicked from Explorer the window would vanish with the result in
REM it, so hold it open. From a prompt this is a needless pause, but a lost
REM error message costs more than one keypress.
echo Press any key to close...
pause >nul
exit /b %RC%
