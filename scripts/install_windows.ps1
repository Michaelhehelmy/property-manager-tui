<#
One-command Windows installer/bootstrap for property-manager-tui.
- Best-effort installs Python 3.12 and Git via winget if missing
- Idempotent clone/update with optional pinned ref (branch/tag/commit)
- Preserves runtime data (DB/exports/invoices/PM_DATA_DIR)
- Delegates to setup_windows.ps1 for venv/deps/run (Web: --workers 1)

Usage (PowerShell):
  irm https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install_windows.ps1 | iex

Examples:
  $env:ACTION='deploy'; $env:MODE='tui'; irm https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install_windows.ps1 | iex
  $env:ACTION='deploy'; $env:MODE='web'; $env:HOST='0.0.0.0'; $env:PM_WEB_SECRET_KEY='...'; irm ... | iex
  $env:GIT_REF='v1.2.3'; irm ... | iex

Env vars: ACTION, MODE, SKIP_WEB, INSTALL_DIR, REPO_URL, BRANCH, GIT_REF/TAG/COMMIT, HOST, PORT,
          INSTALL_DEV, RUN_TESTS, PYTHON, PM_DATA_DIR, PM_WEB_SECRET_KEY
#>
[CmdletBinding()]
param(
  [string]$Action = $(if ($env:ACTION) { $env:ACTION } else { 'deploy' }),
  [string]$Mode = $(if ($env:MODE) { $env:MODE } else { 'auto' }),
  [string]$SkipWeb = $(if ($env:SKIP_WEB) { $env:SKIP_WEB } else { 'auto' }),
  [string]$InstallDir = $(if ($env:INSTALL_DIR) { $env:INSTALL_DIR } else { (Join-Path $HOME 'property-manager-tui') }),
  [string]$RepoUrl = $(if ($env:REPO_URL) { $env:REPO_URL } else { 'https://github.com/Michaelhehelmy/property-manager-tui.git' }),
  [string]$Branch = $(if ($env:BRANCH) { $env:BRANCH } else { 'main' }),
  [string]$GitRef = $(if ($env:GIT_REF) { $env:GIT_REF } elseif ($env:TAG) { $env:TAG } elseif ($env:COMMIT) { $env:COMMIT } else { $env:BRANCH ? $env:BRANCH : 'main' }),
  [string]$BindHost = $(if ($env:HOST) { $env:HOST } else { '127.0.0.1' }),
  [int]$Port = $(if ($env:PORT) { [int]$env:PORT } else { 8000 })
)

$ErrorActionPreference = 'Stop'
function Log([string]$m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn([string]$m) { Write-Warning $m }
function Have($n) { [bool](Get-Command $n -ErrorAction SilentlyContinue) }

function Refresh-PathEnv {
  try {
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path','User') + ';' + $env:Path
  } catch {}
  foreach ($p in @('C:\Program Files\Git\cmd','C:\Program Files\Git\bin')) {
    if ((Test-Path $p) -and ($env:Path -notlike "*$p*")) { $env:Path = "$p;$env:Path" }
  }
  try {
    foreach ($r in @('C:\Program Files','C:\Program Files (x86)',(Join-Path $env:LOCALAPPDATA 'Programs\Python'))) {
      if (Test-Path $r) {
        Get-ChildItem $r -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue | ForEach-Object {
          $env:Path = "$($_.FullName);$($_.FullName)\Scripts;$env:Path"
        }
      }
    }
  } catch {}
}

function Test-PythonCmd([string]$cand) {
  try {
    if ($cand -eq 'py -3') { & py -3 -c "import sys; sys.exit(0)" *> $null; return ($LASTEXITCODE -eq 0) }
    elseif ($cand -eq 'py') { & py -c "import sys; sys.exit(0)" *> $null; return ($LASTEXITCODE -eq 0) }
    elseif (Get-Command $cand -ErrorAction SilentlyContinue) {
      & $cand -c "import sys; sys.exit(0)" *> $null
      if ($LASTEXITCODE -eq 0) { return $true }
      & $cand --version *> $null
      return ($LASTEXITCODE -eq 0)
    }
  } catch {}
  return $false
}
function Has-WorkingPython { return (Test-PythonCmd 'py -3') -or (Test-PythonCmd 'py') -or (Test-PythonCmd 'python') -or (Test-PythonCmd 'python3') }

function Checkout-RequestedRef {
  param([string]$Dir, [string]$Ref)
  if (-not $Ref) { return }
  try { git -C $Dir fetch --all --tags --prune *> $null } catch {}
  $remoteRef = "refs/remotes/origin/$Ref"
  $hasRemote = $false
  try { git -C $Dir rev-parse --verify --quiet $remoteRef *> $null; if ($LASTEXITCODE -eq 0) { $hasRemote = $true } } catch {}
  if ($hasRemote) {
    git -C $Dir checkout -B $Ref "origin/$Ref" *> $null
    if ($LASTEXITCODE -ne 0) { git -C $Dir checkout $Ref }
  } else {
    git -C $Dir checkout $Ref
  }
  if ($LASTEXITCODE -ne 0) { throw "Failed to checkout ref '$Ref'. Verify branch/tag/commit exists." }
}

try {
  Log "property-manager-tui Windows bootstrap (Action=$Action Mode=$Mode Ref=$GitRef InstallDir=$InstallDir)"
  if (($BindHost -eq '0.0.0.0' -or $BindHost -eq '::') -and -not $env:PM_WEB_SECRET_KEY) {
    Warn "Non-local bind requested without PM_WEB_SECRET_KEY. Set a strong random secret for shared/LAN deployments."
  }
  if ($BindHost -eq '0.0.0.0' -or $BindHost -eq '::') {
    Warn "Use HTTPS via a reverse proxy (Caddy/Nginx) for shared/network deployments. SQLite web mode requires --workers 1."
  }

  Refresh-PathEnv
  if (-not (Have winget)) {
    Warn "winget (App Installer) not found. Install Python 3.10+ and Git manually, then run setup_windows.ps1."
  } else {
    if (-not (Has-WorkingPython)) {
      Log "Installing Python 3.12 via winget (best-effort)..."
      try { winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent } catch { winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements }
      Refresh-PathEnv
      if (-not (Has-WorkingPython)) {
        Warn "Python still not detected in this session (or Store alias may be active). Disable python.exe/python3.exe App Execution Aliases or reopen PowerShell after install."
      }
    }
    if (-not (Have git)) {
      Log "Installing Git via winget (best-effort)..."
      try { winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements --silent } catch { winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements }
      Refresh-PathEnv
    }
  }

  if (-not (Have git)) { throw "Git is not available. Install Git for Windows, reopen PowerShell, and re-run." }
  if (Test-Path (Join-Path $InstallDir '.git')) {
    Log "Updating repo in $InstallDir (data-preserving)"
    try { git -C $InstallDir fetch --all --tags --prune } catch { Warn "git fetch failed: $($_.Exception.Message)" }
    if ($GitRef) { Checkout-RequestedRef -Dir $InstallDir -Ref $GitRef }
    elseif ($Branch) { try { git -C $InstallDir checkout $Branch } catch {} }
    # ff-only only when on a branch
    try {
      git -C $InstallDir symbolic-ref -q HEAD *> $null
      if ($LASTEXITCODE -eq 0) { git -C $InstallDir pull --ff-only *> $null }
    } catch {}
  } else {
    Log "Cloning repo to $InstallDir"
    $parent = Split-Path -Parent $InstallDir
    if ($parent -and -not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $cloned = $false
    try { git clone --branch $Branch $RepoUrl $InstallDir; if ($LASTEXITCODE -eq 0) { $cloned = $true } } catch {}
    if (-not $cloned) { git clone $RepoUrl $InstallDir; if ($LASTEXITCODE -ne 0) { throw "git clone failed" } }
    if ($GitRef) { Checkout-RequestedRef -Dir $InstallDir -Ref $GitRef }
  }

  $setup = Join-Path $InstallDir 'scripts\setup_windows.ps1'
  if (-not (Test-Path $setup)) { throw "Setup script not found at $setup" }

  # Preserve runtime data directories (never delete existing contents)
  foreach ($d in @('data','exports','invoices')) {
    $p = Join-Path $InstallDir $d
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
  }

  Log "Running setup script..."
  $env:REPO_URL = $RepoUrl; $env:INSTALL_DIR = $InstallDir; $env:BRANCH = $Branch
  $env:GIT_REF = $GitRef; $env:TAG = ''; $env:COMMIT = ''
  & powershell -NoProfile -ExecutionPolicy Bypass -File $setup -Action $Action -Mode $Mode -SkipWeb $SkipWeb -InstallDir $InstallDir -RepoUrl $RepoUrl -Branch $Branch -GitRef $GitRef -BindHost $BindHost -Port $Port
  exit $LASTEXITCODE
} catch {
  Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
  Write-Host "If Python/Git were just installed, close & reopen PowerShell and re-run the installer." -ForegroundColor Yellow
  exit 1
}