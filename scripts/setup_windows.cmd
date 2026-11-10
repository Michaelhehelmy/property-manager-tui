@echo off
setlocal
REM Convenience wrapper for Windows setup script.
REM Usage examples:
REM   scripts\setup_windows.cmd
REM   scripts\setup_windows.cmd -Action deploy -Mode tui
REM   scripts\setup_windows.cmd -Action deploy -Mode web -Host 0.0.0.0 -Port 8000

set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%setup_windows.ps1" %*
set "EXITCODE=%ERRORLEVEL%"
endlocal & exit /b %EXITCODE%
