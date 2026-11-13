@echo off
setlocal
REM Safe backup/verify/restore-precheck helper wrapper. Preserves runtime data (DB/exports/invoices/PM_DATA_DIR); never deletes.
set "SCRIPT_DIR=%~dp0"
set "ROOT=%SCRIPT_DIR%.."
if defined PYTHON (set "PY=%PYTHON%") else (set "PY=python")
set "BACKUP_PY=%SCRIPT_DIR%backup_runtime.py"
if not exist "%BACKUP_PY%" (
  echo ERROR: Missing backup helper: "%BACKUP_PY%" 1>&2
  endlocal & exit /b 1
)
where "%PY%" >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python interpreter not found: %PY% 1>&2
  echo Set PYTHON=path\to\python.exe and retry. 1>&2
  endlocal & exit /b 1
)
set "PYTHONPATH=%ROOT%\src;%ROOT%;%PYTHONPATH%"
"%PY%" "%BACKUP_PY%" %*
set "EXITCODE=%ERRORLEVEL%"
endlocal & exit /b %EXITCODE%