# Safe backup/verify/restore-precheck helper wrapper. Preserves runtime data (DB/exports/invoices/PM_DATA_DIR); never deletes.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\backup_runtime.ps1 --backup
$ErrorActionPreference = 'Stop'
try {
  $Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
  $Py = if ($env:PYTHON) { $env:PYTHON } else { 'python' }
  $BackupPy = Join-Path $PSScriptRoot 'backup_runtime.py'
  if (-not (Test-Path $BackupPy)) { throw "Missing backup helper: $BackupPy" }
  $pyCmd = Get-Command $Py -ErrorAction SilentlyContinue
  if (-not $pyCmd) { throw "Python interpreter not found: $Py. Set `$env:PYTHON and retry." }
  $env:PYTHONPATH = "$(Join-Path $Root 'src');$Root$([IO.Path]::PathSeparator)$($env:PYTHONPATH)"
  & $Py $BackupPy @args
  exit $LASTEXITCODE
} catch {
  Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
  exit 1
}