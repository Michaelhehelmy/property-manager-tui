# Safe backup/verify helper wrapper. Preserves DB/exports/invoices/PM_DATA_DIR (never deletes runtime data).
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Py = if ($env:PYTHON) { $env:PYTHON } else { 'python' }
$env:PYTHONPATH = "$(Join-Path $Root 'src');$Root$([IO.Path]::PathSeparator)$($env:PYTHONPATH)"
& $Py (Join-Path $PSScriptRoot 'backup_runtime.py') @args
exit $LASTEXITCODE
