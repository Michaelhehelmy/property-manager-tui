<#
.SYNOPSIS
Dynamic setup/deploy script for property-manager-tui on Windows.

.DESCRIPTION
Auto-bootstraps prerequisites (best-effort winget), clones/updates repo (optional pinned ref),
creates .venv, installs deps, and can run TUI or Web UI.

Idempotent and data-preserving: never deletes DB/exports/invoices/PM_DATA_DIR.
Web mode is SQLite-safe only with --workers 1.

.PARAMETER Action
auto | setup | run | deploy  (deploy = setup + run). Default: setup

.PARAMETER Mode
auto | tui | web | both. Default: auto

.PARAMETER SkipWeb
auto | 0 | 1. Default: auto (0 on Windows)

.EXAMPLES
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -Action deploy -Mode tui
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -Action deploy -Mode web -BindHost 0.0.0.0 -Port 8000 -GitRef v1.2.3

Environment variables also supported: ACTION, MODE, SKIP_WEB, INSTALL_DEV, RUN_TESTS,
REPO_URL, INSTALL_DIR, BRANCH, GIT_REF/TAG/COMMIT, HOST, PORT, PYTHON, PM_DATA_DIR,
PM_WEB_SECRET_KEY, NO_BOOTSTRAP=1.
#>
[CmdletBinding()]
param(
  [string]$Action = $(if ($env:ACTION) { $env:ACTION } else { 'setup' }),
  [string]$Mode = $(if ($env:MODE) { $env:MODE } else { 'auto' }),
  [string]$SkipWeb = $(if ($env:SKIP_WEB) { $env:SKIP_WEB } else { 'auto' }),
  [string]$InstallDir = $(if ($env:INSTALL_DIR) { $env:INSTALL_DIR } else { (Join-Path $HOME 'property-manager-tui') }),
  [string]$RepoUrl = $(if ($env:REPO_URL) { $env:REPO_URL } else { 'https://github.com/Michaelhehelmy/property-manager-tui.git' }),
  [string]$Branch = $(if ($env:BRANCH) { $env:BRANCH } else { 'main' }),
  [string]$GitRef = $(if ($env:GIT_REF) { $env:GIT_REF } elseif ($env:TAG) { $env:TAG } elseif ($env:COMMIT) { $env:COMMIT } else { $env:BRANCH ? $env:BRANCH : 'main' }),
  [string]$BindHost = $(if ($env:HOST) { $env:HOST } else { '127.0.0.1' }),
  [int]$Port = $(if ($env:PORT) { [int]$env:PORT } else { 8000 }),
  [string]$PythonExe = $(if ($env:PYTHON) { $env:PYTHON } else { '' }),
  [switch]$InstallDev = $(if ($env:INSTALL_DEV -eq '1') { $true } else { $false }),
  [switch]$RunTests = $(if ($env:RUN_TESTS -eq '1') { $true } else { $false }),
  [switch]$NoBootstrap = $(if ($env:NO_BOOTSTRAP -eq '1') { $true } else { $false })
)

$ErrorActionPreference = 'Stop'

function Log([string]$msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Warn([string]$msg) { Write-Warning $msg }

function Refresh-PathEnv {
  try {
    $machine = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [System.Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = ($machine, $user, $env:Path | Where-Object { $_ -and $_.Trim() }) -join ';'
  } catch {}
  $candidates = @('C:\Program Files\Git\cmd','C:\Program Files\Git\bin')
  try {
    $pyRoots = @('C:\Program Files', 'C:\Program Files (x86)', (Join-Path $env:LOCALAPPDATA 'Programs\Python'))
    foreach ($r in $pyRoots) {
      if (Test-Path $r) {
        Get-ChildItem -Path $r -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue | ForEach-Object {
          $candidates += $_.FullName
          $candidates += (Join-Path $_.FullName 'Scripts')
        }
      }
    }
  } catch {}
  foreach ($p in $candidates) {
    if ($p -and (Test-Path $p) -and ($env:Path -notlike "*$p*")) { $env:Path = "$p;$env:Path" }
  }
}

function Have-Cmd([string]$name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

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

function Resolve-Python {
  Refresh-PathEnv
  if ($PythonExe) {
    if (($PythonExe -eq 'py -3' -or $PythonExe -eq 'py') -and (Test-PythonCmd $PythonExe)) { return $PythonExe }
    if ((Get-Command $PythonExe -ErrorAction SilentlyContinue) -and (Test-PythonCmd $PythonExe)) { return $PythonExe }
  }
  foreach ($cand in @('py -3', 'py', 'python', 'python3')) {
    if (Test-PythonCmd $cand) { return $cand }
  }
  try {
    $roots = @('C:\Program Files', 'C:\Program Files (x86)', (Join-Path $env:LOCALAPPDATA 'Programs\Python'))
    foreach ($r in $roots) {
      if (-not (Test-Path $r)) { continue }
      $dirs = Get-ChildItem -Path $r -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending
      foreach ($d in $dirs) {
        $exe = Join-Path $d.FullName 'python.exe'
        if ((Test-Path $exe) -and (Test-PythonCmd $exe)) { return $exe }
      }
    }
  } catch {}
  return $null
}

function Invoke-Py([string]$py, [string[]]$pyArgs) {
  if ($py -eq 'py -3') { & py -3 @pyArgs }
  elseif ($py -eq 'py') { & py @pyArgs }
  else { & $py @pyArgs }
  if ($LASTEXITCODE -ne 0) { throw "Python command failed: $py $($pyArgs -join ' ')" }
}

function Resolve-Git {
  Refresh-PathEnv
  if (Get-Command git -ErrorAction SilentlyContinue) { return 'git' }
  return $null
}

function Ensure-Prerequisites {
  if ($NoBootstrap) { return }
  $needPy = -not (Resolve-Python)
  $needGit = -not (Resolve-Git)
  if (-not $needPy -and -not $needGit) { return }
  Log "Missing prerequisites detected (python=$needPy git=$needGit). Attempting automatic install..."
  if (-not (Have-Cmd 'winget')) {
    Warn "winget not found; cannot auto-install prerequisites. Install Python 3.10+ and Git, then re-run."
    return
  }
  try {
    if ($needPy) {
      Log "Installing Python 3.12 via winget (best-effort)..."
      & winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent 2>$null
      if ($LASTEXITCODE -ne 0) { & winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements }
    }
  } catch { Warn "Python install attempt failed: $($_.Exception.Message)" }
  try {
    if ($needGit) {
      Log "Installing Git for Windows via winget (best-effort)..."
      & winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements --silent 2>$null
      if ($LASTEXITCODE -ne 0) { & winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements }
    }
  } catch { Warn "Git install attempt failed: $($_.Exception.Message)" }
  Refresh-PathEnv
}

function Validate-WebTarget {
  if (-not $BindHost -or ($BindHost -notmatch '^[A-Za-z0-9._:\[\]-]+$')) { throw "Invalid host: $BindHost" }
  if ($Port -lt 1 -or $Port -gt 65535) { throw "Invalid port: $Port" }
}

function Print-SecurityGuidance {
  $isNonLocal = ($BindHost -eq '0.0.0.0' -or $BindHost -eq '::' -or ($BindHost -ne '127.0.0.1' -and $BindHost -ne 'localhost' -and $BindHost -ne '::1'))
  if ($isNonLocal -and -not $env:PM_WEB_SECRET_KEY) {
    Warn "PM_WEB_SECRET_KEY is not set for non-local host '$BindHost'. Set a strong random secret (e.g. 32+ bytes)."
  }
  if ($BindHost -eq '0.0.0.0' -or $BindHost -eq '::' -or $isNonLocal) {
    Warn "For shared/network deployments, terminate HTTPS at a reverse proxy (Caddy/Nginx/Traefik). SQLite web mode requires --workers 1."
  }
}

function Checkout-RequestedRef([string]$dir, [string]$ref) {
  if (-not $ref) { return }
  try { git -C $dir fetch --all --tags --prune *> $null } catch {}
  $remoteRef = "refs/remotes/origin/$ref"
  $hasRemote = $false
  try { git -C $dir rev-parse --verify --quiet $remoteRef *> $null; if ($LASTEXITCODE -eq 0) { $hasRemote = $true } } catch {}
  if ($hasRemote) {
    git -C $dir checkout -B $ref "origin/$ref" *> $null
    if ($LASTEXITCODE -ne 0) { git -C $dir checkout $ref }
  } else {
    git -C $dir checkout $ref
  }
  if ($LASTEXITCODE -ne 0) { throw "Failed to checkout ref '$ref'. Verify branch/tag/commit exists." }
}

function Clone-Or-Update([string]$git) {
  if (-not $git) { throw "Git not found and required to clone/update. Install Git for Windows or run from an existing checkout." }
  if (Test-Path (Join-Path $InstallDir '.git')) {
    Log "Updating existing repo in $InstallDir (data-preserving)"
    try { & git -C $InstallDir fetch --all --tags --prune } catch { Warn "git fetch failed: $($_.Exception.Message)" }
    if ($GitRef) {
      Checkout-RequestedRef $InstallDir $GitRef
    } elseif ($Branch) {
      try { & git -C $InstallDir checkout $Branch } catch {}
    }
    try {
      & git -C $InstallDir symbolic-ref -q HEAD *> $null
      if ($LASTEXITCODE -eq 0) { & git -C $InstallDir pull --ff-only }
    } catch { Warn "git pull --ff-only skipped/failed (continuing)." }
  } else {
    Log "Cloning repo into $InstallDir"
    $parent = Split-Path -Parent $InstallDir
    if ($parent -and -not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $cloned = $false
    try { & git clone --branch $Branch $RepoUrl $InstallDir; if ($LASTEXITCODE -eq 0) { $cloned = $true } } catch {}
    if (-not $cloned) {
      & git clone $RepoUrl $InstallDir
      if ($LASTEXITCODE -ne 0) { throw "git clone failed" }
    }
    if ($GitRef) { Checkout-RequestedRef $InstallDir $GitRef }
  }
}

function Ensure-Venv-And-Deps([string]$py) {
  Set-Location $InstallDir
  $venvPy = Join-Path $InstallDir '.venv\Scripts\python.exe'

  if (-not (Test-Path $venvPy)) {
    Log "Creating virtual environment (.venv)..."
    Invoke-Py $py @('-m', 'venv', '.venv')
  }

  Log "Upgrading pip/setuptools/wheel (best-effort)..."
  try { & $venvPy -m pip install --upgrade pip setuptools wheel } catch { Warn "pip upgrade failed: $($_.Exception.Message)" }

  if (Test-Path (Join-Path $InstallDir 'requirements.txt')) {
    Log "Installing runtime dependencies (requirements.txt)..."
    & $venvPy -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Failed to install requirements.txt" }
  } else {
    Log "Installing core dependency (textual)..."
    & $venvPy -m pip install 'textual>=0.47'
  }

  $doWeb = $true
  if ($SkipWeb -eq '1' -or $Mode -eq 'tui') { $doWeb = $false }
  if ($SkipWeb -eq '0') { $doWeb = $true }

  if ($doWeb -and (Test-Path (Join-Path $InstallDir 'requirements-web.txt'))) {
    Log "Installing web dependencies (requirements-web.txt)..."
    & $venvPy -m pip install -r requirements-web.txt
    if ($LASTEXITCODE -ne 0) {
      Warn "Web dependency installation failed. TUI/core can still be used. Retry with -SkipWeb 1 -Mode tui"
      $script:SkipWeb = '1'
      if ($script:Mode -eq 'both') { $script:Mode = 'tui' }
    }
  } elseif (-not $doWeb) {
    Log "Skipping web dependencies (Mode=$Mode SkipWeb=$SkipWeb)."
  }

  if ($InstallDev) {
    Log "Installing dev/test dependencies..."
    if (Test-Path (Join-Path $InstallDir 'requirements-dev.txt')) {
      & $venvPy -m pip install -r requirements-dev.txt
    } else {
      & $venvPy -m pip install 'pytest>=7.0' 'httpx>=0.24'
    }
  }

  # Preserve runtime data across setup/updates (never delete DB/exports/invoices/data dir).
  foreach ($d in @('data','exports','invoices')) {
    $p = Join-Path $InstallDir $d
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
  }
  if ($env:PM_DATA_DIR) {
    foreach ($d in @($env:PM_DATA_DIR, (Join-Path $env:PM_DATA_DIR 'exports'), (Join-Path $env:PM_DATA_DIR 'invoices'))) {
      if ($d -and -not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
    }
  }

  if ($RunTests) {
    Log "Running tests..."
    $env:PYTHONPATH = "$(Join-Path $InstallDir 'src');$InstallDir$([IO.Path]::PathSeparator)$($env:PYTHONPATH)"
    & $venvPy -m pytest -c tests/pytest.ini
    if ($LASTEXITCODE -ne 0) { Warn "Tests failed." }
  }
}

function Print-Hints {
  Write-Host ""
  Log "Ready. (dir: $InstallDir, mode: $Mode, web_skipped: $SkipWeb, ref: $GitRef)"
  @"

Run (PowerShell):
  Set-Location "$InstallDir"
  .\.venv\Scripts\Activate.ps1

  # TUI (local terminal)
  python launcher.py --tui

  # Web server (client/server; SQLite-safe: ALWAYS --workers 1)
  `$env:PM_WEB_SECRET_KEY = 'replace-with-a-long-random-secret'
  python -m uvicorn ui.web.server:app --host $BindHost --port $Port --workers 1

Tips / Ops:
  - Same device browser: HOST=127.0.0.1 -> http://127.0.0.1:$Port
  - LAN/other devices: use -BindHost 0.0.0.0, set a strong `$env:PM_WEB_SECRET_KEY`, and terminate HTTPS via reverse proxy (Caddy/Nginx/Traefik).
  - Runtime data is preserved: DB, exports, invoices, and PM_DATA_DIR are never deleted by setup/update scripts.
  - Updates (git): Settings → App updates (fast-forward) or git pull --ff-only + re-run -Action setup; restart after updates.
  - Health: python -m app_core.doctor --json (probes: /health, /healthz)
  - Backup/verify: python scripts/backup_runtime.py --backup  |  --verify <backup.db>
  - Deployment examples: deploy\ (systemd/Caddy/Nginx), Dockerfile, docker-compose.yml
  - If script execution is blocked: powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
"@ | Write-Host
  Print-SecurityGuidance
}

function Run-Deploy {
  Set-Location $InstallDir
  $venvPy = Join-Path $InstallDir '.venv\Scripts\python.exe'
  if (-not (Test-Path $venvPy)) { throw "Install not found at $InstallDir. Run with -Action setup or -Action auto first." }
  $env:PYTHONPATH = "$(Join-Path $InstallDir 'src');$InstallDir$([IO.Path]::PathSeparator)$($env:PYTHONPATH)"
  Validate-WebTarget
  switch ($Mode) {
    'tui'  { Log "Launching TUI..."; & $venvPy launcher.py --tui; exit $LASTEXITCODE }
    'web'  {
      if ($SkipWeb -eq '1') { throw "Web deps were skipped; cannot start web mode. Re-run with -SkipWeb 0 -Mode web." }
      Print-SecurityGuidance
      Log "Launching Web server on ${BindHost}:${Port} (workers=1)..."
      Log "Runtime data (DB/exports/invoices/PM_DATA_DIR) is preserved. Health: python -m app_core.doctor"
      & $venvPy -m uvicorn ui.web.server:app --host $BindHost --port $Port --workers 1
      exit $LASTEXITCODE
    }
    'both' { Log "Launching launcher (choose TUI/Web)..."; & $venvPy launcher.py; exit $LASTEXITCODE }
    'auto' { Log "Launching launcher (choose TUI/Web)..."; & $venvPy launcher.py; exit $LASTEXITCODE }
    default { throw "Unknown Mode '$Mode' (use auto|tui|web|both)" }
  }
}

# --- main ---
try {
  if ($SkipWeb -eq 'auto') { $SkipWeb = '0' }
  if ($Mode -eq 'auto') { $Mode = 'both' }
  $script:SkipWeb = $SkipWeb
  $script:Mode = $Mode
  $script:InstallDir = $InstallDir

  Log "Windows setup for property-manager-tui"
  Log "Action=$Action Mode=$Mode SkipWeb=$SkipWeb Host=$BindHost Port=$Port Ref=$GitRef"
  Log "InstallDir=$InstallDir"
  Validate-WebTarget
  Print-SecurityGuidance

  if ($Action -in @('setup','deploy','auto')) { Ensure-Prerequisites }

  $git = Resolve-Git
  $py = Resolve-Python
  if ($py) { Log "Python: $py" } else { Warn "Python not found." }
  if ($git) { Log "Git: $git" } else { Warn "Git not found (clone/update disabled unless already in a repo)." }

  switch ($Action) {
    'auto' {
      $hasInstall = (Test-Path (Join-Path $InstallDir '.git')) -and (Test-Path (Join-Path $InstallDir '.venv\Scripts\python.exe'))
      if ($hasInstall) {
        Log "Existing install detected: checking for updates and launching..."
        if (-not $py) { throw "Python not found. Install Python 3.10+ and re-run." }
        if ($git) { try { Clone-Or-Update $git } catch { Warn "Repo update skipped: $($_.Exception.Message)" } }
        Ensure-Venv-And-Deps $py
        Run-Deploy
      } else {
        Log "No existing install detected: running full setup and launch..."
        if (-not $py) { throw "Python not found. Install Python 3.10+ and re-run (or allow winget bootstrap)." }
        if (-not (Test-Path (Join-Path $InstallDir 'launcher.py'))) { Clone-Or-Update $git } elseif ($git) { try { Clone-Or-Update $git } catch { Warn "Repo update skipped: $($_.Exception.Message)" } }
        Ensure-Venv-And-Deps $py
        Print-Hints
        Run-Deploy
      }
    }
    'setup' {
      $scriptRoot = if ($PSScriptRoot) { Split-Path -Parent $PSScriptRoot } else { '' }
      if ($scriptRoot -and (Test-Path (Join-Path $scriptRoot 'launcher.py')) -and ($InstallDir -eq (Join-Path $HOME 'property-manager-tui'))) {
        $InstallDir = $scriptRoot
        $script:InstallDir = $InstallDir
        Log "Detected repo checkout; using InstallDir=$InstallDir"
      }
      if (-not $py) { throw "Python not found. Install Python 3.10+ (and reopen PowerShell) or run scripts\install_windows.ps1." }
      if (-not (Test-Path (Join-Path $InstallDir '.git')) -or -not (Test-Path (Join-Path $InstallDir 'launcher.py'))) {
        Clone-Or-Update $git
      } elseif ($git) {
        try { Clone-Or-Update $git } catch { Warn "Repo update skipped: $($_.Exception.Message)" }
      }
      Ensure-Venv-And-Deps $py
      Print-Hints
    }
    'run' {
      if (-not (Test-Path (Join-Path $InstallDir '.venv'))) { throw "Install not found at $InstallDir. Run with -Action setup first." }
      Run-Deploy
    }
    'deploy' {
      $scriptRoot = if ($PSScriptRoot) { Split-Path -Parent $PSScriptRoot } else { '' }
      if ($scriptRoot -and (Test-Path (Join-Path $scriptRoot 'launcher.py')) -and (-not (Test-Path (Join-Path $InstallDir '.git')))) {
        $InstallDir = $scriptRoot
        $script:InstallDir = $InstallDir
      }
      if (-not $py) { throw "Python not found. Install Python 3.10+ (and reopen PowerShell) or run scripts\install_windows.ps1." }
      if (-not (Test-Path (Join-Path $InstallDir '.git')) -or -not (Test-Path (Join-Path $InstallDir 'launcher.py'))) {
        Clone-Or-Update $git
      } elseif ($git) {
        try { Clone-Or-Update $git } catch { Warn "Repo update skipped: $($_.Exception.Message)" }
      }
      Ensure-Venv-And-Deps $py
      Print-Hints
      Run-Deploy
    }
    default { throw "Unknown Action '$Action' (use auto|setup|run|deploy)" }
  }
} catch {
  Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
  exit 1
}