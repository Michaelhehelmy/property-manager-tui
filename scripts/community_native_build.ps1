<# 
Best-effort: build a native app for the current Windows arch, package it, generate a
sanitized report + prefilled GitHub issue URL, and optionally auto-file via gh.
Privacy-first: no secrets/DB/runtime data included. Run with explicit user consent.
#>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Continue'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
function Log($m){ Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m){ Write-Warning $m }
function Have($n){ [bool](Get-Command $n -ErrorAction SilentlyContinue) }

$Py = $null
foreach ($cand in @((Join-Path $Root '.venv\Scripts\python.exe'), (Join-Path $Root '.venv\bin\python'), 'py -3','py','python','python3')) {
  try {
    if ($cand -eq 'py -3') { & py -3 -V *> $null; if ($LASTEXITCODE -eq 0) { $Py='py -3'; break } }
    elseif ($cand -eq 'py') { & py -V *> $null; if ($LASTEXITCODE -eq 0) { $Py='py'; break } }
    elseif (Get-Command $cand -ErrorAction SilentlyContinue) { $Py=$cand; break }
  } catch {}
}
if (-not $Py) { Warn 'Python not found. Install Python 3.10+ and retry.'; exit 1 }

$arch = if ($env:PROCESSOR_ARCHITECTURE -match 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -match 'ARM64') { 'arm64' } else { 'x64' }
$artifactBase = "property-manager-windows-$arch"
$distDir = Join-Path $Root 'dist\community-native'
$nuitkaOut = Join-Path $Root 'dist\nuitka-community'
$logFile = Join-Path $distDir 'build.log'
$reportFile = Join-Path $distDir 'community-native-build-report.md'
New-Item -ItemType Directory -Force -Path $distDir | Out-Null

try { $commit = (git rev-parse --short HEAD 2>$null) } catch { $commit = '' }
$appRef = if ($env:GIT_REF) { $env:GIT_REF } elseif ($env:TAG) { $env:TAG } elseif ($env:COMMIT) { $env:COMMIT } else { try { git rev-parse --abbrev-ref HEAD 2>$null } catch { '' } }

Log "Community native build (windows-$arch)"
Log 'This may take a long time and requires MSVC Build Tools/toolchain. Report will be sanitized (no secrets/DB/runtime data).'
Log 'Installing/ensuring build deps (nuitka, ordered-set, zstandard)...'
try { if ($Py -eq 'py -3') { py -3 -m pip install --upgrade pip nuitka ordered-set zstandard } elseif ($Py -eq 'py') { py -m pip install --upgrade pip nuitka ordered-set zstandard } else { & $Py -m pip install --upgrade pip nuitka ordered-set zstandard } } catch { Warn 'Failed to install Nuitka/build deps.'; exit 1 }
try { if ($Py -eq 'py -3') { $nuitkaVer = (py -3 -m nuitka --version | Select-Object -First 1) } elseif ($Py -eq 'py') { $nuitkaVer = (py -m nuitka --version | Select-Object -First 1) } else { $nuitkaVer = (& $Py -m nuitka --version | Select-Object -First 1) } } catch { $nuitkaVer='' }

$buildStatus = 'failed'
$artifactPath = ''
$checksum = ''
$buildCmd = "python -m nuitka --standalone --assume-yes-for-downloads --enable-plugin=anti-bloat --include-package=app_core --include-package=ui --include-package=uvicorn --include-package=fastapi --include-package=starlette --include-package=anyio --include-package=jinja2 --include-package=itsdangerous --include-package=python_multipart --include-package=multipart --include-package=pydantic --include-package=pydantic_core --include-data-dir=ui/web/templates=ui/web/templates --include-data-dir=ui/web/static=ui/web/static --include-data-files=pyproject.toml=pyproject.toml --output-dir=dist/nuitka-community launcher.py"

if (Test-Path $nuitkaOut) { Remove-Item -Recurse -Force $nuitkaOut -ErrorAction SilentlyContinue }
Log 'Building with Nuitka (standalone)...'
try {
  $nuitkaArgs = @('-m','nuitka','--standalone','--assume-yes-for-downloads','--enable-plugin=anti-bloat','--include-package=app_core','--include-package=ui','--include-package=uvicorn','--include-package=fastapi','--include-package=starlette','--include-package=anyio','--include-package=jinja2','--include-package=itsdangerous','--include-package=python_multipart','--include-package=multipart','--include-package=pydantic','--include-package=pydantic_core','--include-data-dir=ui/web/templates=ui/web/templates','--include-data-dir=ui/web/static=ui/web/static','--include-data-files=pyproject.toml=pyproject.toml',"--output-dir=$nuitkaOut",'launcher.py')
  if ($Py -eq 'py -3') { & py -3 @nuitkaArgs *>&1 | Tee-Object -FilePath $logFile }
  elseif ($Py -eq 'py') { & py @nuitkaArgs *>&1 | Tee-Object -FilePath $logFile }
  else { & $Py @nuitkaArgs *>&1 | Tee-Object -FilePath $logFile }
  $rc = $LASTEXITCODE
} catch { $rc = 1; $_ | Out-File -FilePath $logFile -Append }

if ($rc -ne 0) {
  Warn "Nuitka build failed (exit $rc). See log: $logFile"
} else {
  $distOut = Join-Path $nuitkaOut 'launcher.dist'
  $exeOut = Join-Path $nuitkaOut 'launcher.exe'
  $artifactPath = Join-Path $distDir ("$artifactBase.zip")
  try {
    if (Test-Path $distOut) {
      if (Test-Path $artifactPath) { Remove-Item $artifactPath -Force }
      Compress-Archive -Path (Join-Path $distOut '*') -DestinationPath $artifactPath -Force
    } elseif (Test-Path $exeOut) {
      if (Test-Path $artifactPath) { Remove-Item $artifactPath -Force }
      Compress-Archive -Path $exeOut -DestinationPath $artifactPath -Force
    } else {
      $artifactPath = ''
    }
  } catch { $artifactPath = '' }
  if ($artifactPath -and (Test-Path $artifactPath)) {
    try { $checksum = (Get-FileHash -Algorithm SHA256 -Path $artifactPath).Hash.ToLower() } catch { $checksum='' }
    $buildStatus = 'success'
    Log "Built: $artifactPath"
    if ($checksum) { Log "SHA256: $checksum" }
  } else {
    Warn 'Build finished but packaged artifact was not found.'
  }
}

$title = "Community native build: windows-$arch ($buildStatus)"
$notes = "Automated community native build attempt from install/build helper. Status=$buildStatus. Review sanitized report; attach/share artifact and checksum via a trusted channel if comfortable."
$issueUrl = ''
try {
  $reportArgs = @('scripts/community_native_build_report.py','--title',$title,'--artifact',$artifactPath,'--checksum',$checksum,'--build-status',$buildStatus,'--app-ref',$appRef,'--commit',$commit,'--build-cmd',$buildCmd,'--nuitka-version',"$nuitkaVer",'--notes',$notes,'--log-file',$logFile,'--out',$reportFile)
  if ($Py -eq 'py -3') { $issueUrl = (py -3 @reportArgs 2>$null | Select-Object -Last 1) }
  elseif ($Py -eq 'py') { $issueUrl = (py @reportArgs 2>$null | Select-Object -Last 1) }
  else { $issueUrl = (& $Py @reportArgs 2>$null | Select-Object -Last 1) }
} catch { $issueUrl='' }

Log "Sanitized report: $reportFile"
if ($issueUrl) {
  Log 'Prefilled GitHub issue URL (review before submitting):'
  Write-Host $issueUrl
  try { Start-Process $issueUrl | Out-Null } catch {}
  if (Have gh) {
    $ans = Read-Host 'GitHub CLI (gh) detected. Create an issue automatically now? [y/N]'
    if ($ans -match '^(y|yes)$') {
      try { gh issue create --repo "Michaelhehelmy/property-manager-tui" --title $title --body-file $reportFile --label 'community-build' } catch { Warn 'gh issue create failed (auth/permissions?). You can still use the prefilled URL above.' }
    }
  } else {
    Log 'Tip: install and authenticate GitHub CLI (gh) to auto-file; otherwise submit via the prefilled URL and attach/share the artifact+checksum if you wish.'
  }
} else {
  Warn "Could not generate prefilled issue URL. Share $reportFile manually (review for sensitive info first)."
}

Log "Done. Artifact (if built): $($artifactPath -or 'none'). No runtime data or secrets were included in the report."
exit 0
