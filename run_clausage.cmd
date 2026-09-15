@echo off
REM Dev launcher: runs with a console so you can see logs and --diagnose output.
setlocal
cd /d "%~dp0"
set PYTHONPATH=%~dp0src
".venv\Scripts\python.exe" -m clausage_bar %*
