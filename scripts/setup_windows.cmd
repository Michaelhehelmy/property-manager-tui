@echo off
setlocal
REM Convenience wrapper for Windows setup script (data-preserving, idempotent).
REM Usage examples:
REM   scripts\setup_windows.cmd
REM   scripts\setup_windows.cmd -Action deploy -Mode tui
REM   scripts\setup_windows.cmd -Action deploy -Mode web -BindHost 0.0.0.0 -Port 8000 -GitRef v1.2.3
REM Env: ACTION, MODE, SKIP_WEB, HOST, PORT, GIT_REF/TAG/COMMIT, PM_WEB_SECRET_KEY, PM_DATA_DIR

set "SCRIPT_DIR=%~dp0"
if not exist "%SCRIPT_DIR%setup_windows.ps1" (
  echo ERROR: Missing script: "%SCRIPT_DIR%setup_windows.ps1" 1>&2
  endlocal & exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%setup_windows.ps1" %*
set "EXITCODE=%ERRORLEVEL%"
endlocal & exit /b %EXITCODE%