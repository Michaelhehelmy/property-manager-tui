@echo off
setlocal
REM One-command Windows bootstrap wrapper (idempotent, data-preserving).
REM Forwards args/env to install_windows.ps1. Web mode is SQLite-safe only with --workers 1.
REM Usage:
REM   scripts\install_windows.cmd
REM   scripts\install_windows.cmd -Action deploy -Mode tui
REM   scripts\install_windows.cmd -Action deploy -Mode web -BindHost 0.0.0.0 -Port 8000 -GitRef v1.2.3
REM Env: ACTION, MODE, SKIP_WEB, HOST, PORT, GIT_REF/TAG/COMMIT, PM_WEB_SECRET_KEY, PM_DATA_DIR, REPO_URL, INSTALL_DIR, BRANCH

set "SCRIPT_DIR=%~dp0"
if not exist "%SCRIPT_DIR%install_windows.ps1" (
  echo ERROR: Missing script: "%SCRIPT_DIR%install_windows.ps1" 1>&2
  endlocal & exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%install_windows.ps1" %*
set "EXITCODE=%ERRORLEVEL%"
endlocal & exit /b %EXITCODE%