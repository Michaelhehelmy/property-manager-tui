@echo off
setlocal
REM Safe backup/verify helper wrapper. Preserves DB/exports/invoices/PM_DATA_DIR (never deletes runtime data).
set "SCRIPT_DIR=%~dp0"
set "ROOT=%SCRIPT_DIR%.."
if defined PYTHON (set "PY=%PYTHON%") else (set "PY=python")
set "PYTHONPATH=%ROOT%\src;%ROOT%;%PYTHONPATH%"
"%PY%" "%SCRIPT_DIR%backup_runtime.py" %*
set "EXITCODE=%ERRORLEVEL%"
endlocal & exit /b %EXITCODE%
