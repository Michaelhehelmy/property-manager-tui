@echo off
setlocal
REM One-command Windows bootstrap: installs Python/Git via winget if missing, clones, and sets up.
REM Usage:
REM   scripts\install_windows.cmd
REM   scripts\install_windows.cmd -Action deploy -Mode tui
REM   scripts\install_windows.cmd -Action deploy -Mode web -BindHost 0.0.0.0 -Port 8000
REM Or run the PowerShell one-liner:
REM   powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install_windows.ps1 | iex"

set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%install_windows.ps1" %*
set "EXITCODE=%ERRORLEVEL%"
endlocal & exit /b %EXITCODE%
