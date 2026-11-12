<# 
.SYNOPSIS
Dynamic setup/deploy script for property-manager-tui on Windows.

.DESCRIPTION
Auto-bootstraps prerequisites (installs Python/Git via winget if missing), clones/updates
repo, creates .venv, installs deps, and can run TUI or Web UI.

.NOTES
Git-based installs can be updated in-app via Settings → App updates (check/changelog/apply
fast-forward), which preserves runtime data (DB, exports, invoices, configured data dir).
Manual refresh: git pull --ff-only then re-run -Action setup. Web mode requires --workers 1.

.PARAMETER Action
setup | run | deploy  (deploy = setup + run). Default: setup

.PARAMETER Mode
auto | tui | web | both. Default: auto

.PARAMETER SkipWeb
auto | 0 | 1. Default: auto (0 on Windows)

.EXAMPLES
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -Action deploy -Mode tui
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -Action deploy -Mode web -BindHost 0.0.0.0 -Port 8000
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -Action run -Mode web

# One-liner bootstrap (no Python/Git preinstalled): download and run
irm https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install_windows.ps1 | iex

Environment variables also supported: ACTION, MODE, SKIP_WEB, INSTALL_DEV, RUN_TESTS,
REPO_URL, INSTALL_DIR, BRANCH, HOST, PORT, PYTHON, NO_BOOTSTRAP=1.
#>
[CmdletBinding()]
param(
  [string]$Action = $(if ($env:ACTION) { $env:ACTION } else { 'setup' }),
  [string]$Mode = $(if ($env:MODE) { $env:MODE } else { 'auto' }),
  [string]$SkipWeb = $(if ($env:SKIP_WEB) { $env:SKIP_WEB } else { 'auto' }),
  [string]$InstallDir = $(if ($env:INSTALL_DIR) { $env:INSTALL_DIR } else { (Join-Path $HOME 'property-manager-tui') }),
  [string]$RepoUrl = $(if ($env:REPO_URL) { $env:REPO_URL } else { 'https://github.com/Michaelhehelmy/property-manager-tui.git' }),
  [string]$Branch = $(if ($env:BRANCH) { $env:BRANCH } else { 'main' }),
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
  } catch { }
  # common install locations (fresh installs may not be on PATH in current session)
  $candidates = @(
    'C:\Program Files\Git\cmd',
    'C:\Program Files\Git\bin'
  )
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
  } catch { }
  foreach ($p in $candidates) {
    if ($p -and (Test-Path $p) -and ($env:Path -notlike "*$p*")) { $env:Path = "$p;$env:Path" }
  }
}

function Have-Cmd([string]$name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Ensure-Prerequisites {
  if ($NoBootstrap) { return }
  $needPy = -not (Test-PythonCmd 'py -3') -and -not (Test-PythonCmd 'py') -and -not (Test-PythonCmd 'python') -and -not (Test-PythonCmd 'python3')
  $needGit = -not (Have-Cmd 'git')
  # git is required to clone when not running from an existing repo checkout
  $scriptRoot = if ($PSScriptRoot) { Split-Path -Parent $PSScriptRoot } else { '' }
  $inCheckout = $scriptRoot -and (Test-Path (Join-Path $scriptRoot 'launcher.py'))
  $installHasRepo = (Test-Path (Join-Path $InstallDir '.git')) -or (Test-Path (Join-Path $InstallDir 'launcher.py'))
  if (-not $inCheckout -and -not $installHasRepo -and $Action -ne 'run') { $needGit = $needGit -or $false } # clone path needs git
  if (-not $needPy -and -not $needGit) { return }

  Log "Missing prerequisites detected (python=$needPy git=$needGit). Attempting automatic install..."
  if (-not (Have-Cmd 'winget')) {
    Warn "winget (App Installer) not found; cannot auto-install prerequisites."
    if ($needPy) { Warn "Install Python 3.10+ from https://www.python.org/downloads/ (check 'Add python.exe to PATH')." }
    if ($needGit) { Warn "Install Git for Windows from https://git-scm.com/download/win." }
    Warn "After installing, close & reopen PowerShell and re-run this script."
    return
  }
  try {
    if ($needPy) {
      Log "Installing Python 3.12 via winget (this may take a minute)..."
      & winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent 2>$null
      if ($LASTEXITCODE -ne 0) { & winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements }
    }
  } catch { Warn "Python install attempt failed: $_" }
  try {
    if ($needGit) {
      Log "Installing Git for Windows via winget..."
      & winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements --silent 2>$null
      if ($LASTEXITCODE -ne 0) { & winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements }
    }
  } catch { Warn "Git install attempt failed: $_" }
  Refresh-PathEnv
}

function Test-PythonCmd([string]$cand) {
  try {
    if ($cand -eq 'py -3') { & py -3 -c "import sys; sys.exit(0)" *> $null; return ($LASTEXITCODE -eq 0) }
    elseif ($cand -eq 'py') { & py -c "import sys; sys.exit(0)" *> $null; return ($LASTEXITCODE -eq 0) }
    elseif (Get-Command $cand -ErrorAction SilentlyContinue) {
      & $cand -c "import sys; sys.exit(0)" *> $null
      if ($LASTEXITCODE -eq 0) { return $true }
      # App Execution Alias stub may "exist" but fail to run real Python
      & $cand --version *> $null
      return ($LASTEXITCODE -eq 0)
    }
  } catch {}
  return $false
}

function Resolve-Python {
  Refresh-PathEnv
  if ($PythonExe) {
    if ($PythonExe -eq 'py -3' -or $PythonExe -eq 'py') { if (Test-PythonCmd $PythonExe) { return $PythonExe } }
    elseif (Get-Command $PythonExe -ErrorAction SilentlyContinue) { if (Test-PythonCmd $PythonExe) { return $PythonExe } }
  }
  foreach ($cand in @('py -3', 'py', 'python', 'python3')) {
    if (Test-PythonCmd $cand) { return $cand }
  }
  # probe common install dirs (PATH may not be refreshed in-session)
  try {
    $roots = @('C:\Program Files', 'C:\Program Files (x86)', (Join-Path $env:LOCALAPPDATA 'Programs\Python'))
    foreach ($r in $roots) {
      if (-not (Test-Path $r)) { continue }
      Get-ChildItem -Path $r -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending | ForEach-Object {
        $exe = Join-Path $_.FullName 'python.exe'
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

function Clone-Or-Update([string]$git) {
  if (-not $git) { throw "Git not found and required to clone/update. Install Git for Windows (https://git-scm.com/download/win) or run from an existing repo checkout, then re-run." }
  if (Test-Path (Join-Path $InstallDir '.git')) {
    Log "Updating existing repo in $InstallDir"
    try { & git -C $InstallDir fetch --all --prune } catch { Warn "git fetch failed: $_" }
    try { & git -C $InstallDir checkout $Branch } catch { Warn "git checkout failed: $_" }
    try { & git -C $InstallDir pull --ff-only origin $Branch } catch { try { & git -C $InstallDir pull } catch { Warn "git pull failed: $_" } }
  } else {
    Log "Cloning repo into $InstallDir"
    $parent = Split-Path -Parent $InstallDir
    if ($parent -and -not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    & git clone --branch $Branch $RepoUrl $InstallDir
    if ($LASTEXITCODE -ne 0) { throw "git clone failed" }
  }
}

function Ensure-Venv-And-Deps([string]$py) {
  Set-Location $InstallDir
  $venvDir = Join-Path $InstallDir '.venv'
  $venvPy = Join-Path $venvDir 'Scripts\python.exe'
  $venvPip = Join-Path $venvDir 'Scripts\pip.exe'

  if (-not (Test-Path $venvPy)) {
    Log "Creating virtual environment (.venv)..."
    Invoke-Py $py @('-m', 'venv', '.venv')
  }
  Log "Upgrading pip/setuptools/wheel..."
  try { & $venvPy -m pip install --upgrade pip setuptools wheel } catch { Warn "pip upgrade failed: $_" }

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

  foreach ($d in @('data','exports','invoices')) {
    $p = Join-Path $InstallDir $d
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
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
  Log "Ready. (dir: $InstallDir, mode: $Mode, web_skipped: $SkipWeb)"
  @"

Run (PowerShell):
  Set-Location "$InstallDir"
  .\.venv\Scripts\Activate.ps1

  # TUI (local terminal)
  python launcher.py --tui

  # Web server (client/server - access from browser)
  `$env:PM_WEB_SECRET_KEY = 'change-this-to-a-long-random-secret'
  python -m uvicorn ui.web.server:app --host $BindHost --port $Port --workers 1

Tips:
  - Same device browser: HOST=127.0.0.1 -> http://127.0.0.1:$Port
  - LAN/other devices: use -BindHost 0.0.0.0 and a strong `$env:PM_WEB_SECRET_KEY` (keep --workers 1).
  - Updates: in-app Settings → App updates checks/applies git fast-forward updates and preserves DB/exports/invoices/data dir (restart after apply); or git pull --ff-only + re-run -Action setup.
  - LAN access (other devices): HOST=0.0.0.0 -> http://<this-pc-ip>:$Port (set strong PM_WEB_SECRET_KEY)
  - If script execution is blocked: powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
"@ | Write-Host
}

function Run-Deploy {
  Set-Location $InstallDir
  $venvPy = Join-Path $InstallDir '.venv\Scripts\python.exe'
  if (-not (Test-Path $venvPy)) { throw "Install not found at $InstallDir. Run with -Action setup first." }
  $env:PYTHONPATH = "$(Join-Path $InstallDir 'src');$InstallDir$([IO.Path]::PathSeparator)$($env:PYTHONPATH)"
  switch ($Mode) {
    'tui'  { Log "Launching TUI..."; & $venvPy launcher.py --tui; exit $LASTEXITCODE }
    'web'  {
      if ($SkipWeb -eq '1') { throw "Web deps were skipped; cannot start web mode. Re-run with -SkipWeb 0 -Mode web." }
      Log "Launching Web server on ${BindHost}:${Port} (workers=1)..."
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

  Log "Windows setup for property-manager-tui"
  Log "Action=$Action Mode=$Mode SkipWeb=$SkipWeb Host=$BindHost Port=$Port"
  Log "InstallDir=$InstallDir"

  # Auto-bootstrap Python/Git when missing (winget)
  if ($Action -in @('setup','deploy')) { Ensure-Prerequisites }

  $git = Resolve-Git
  $py = Resolve-Python
  if ($py) { Log "Python: $py" } else { Log "Python: not found" }
  if ($git) { Log "Git: $git" } else { Warn "Git not found (clone/update disabled unless already in a repo)." }

  switch ($Action) {
    'setup' {
      $scriptRoot = if ($PSScriptRoot) { Split-Path -Parent $PSScriptRoot } else { '' }
      if ($scriptRoot -and (Test-Path (Join-Path $scriptRoot '.git')) -and ($InstallDir -eq (Join-Path $HOME 'property-manager-tui')) -and (Test-Path (Join-Path $scriptRoot 'launcher.py'))) {
        $InstallDir = $scriptRoot
        Log "Detected existing repo checkout; using InstallDir=$InstallDir"
      } elseif ($scriptRoot -and -not (Test-Path (Join-Path $InstallDir '.git')) -and (Test-Path (Join-Path $scriptRoot 'launcher.py'))) {
        $InstallDir = $scriptRoot
      }
      $script:InstallDir = $InstallDir
      if (-not $py) { throw "Python not found (auto-install may require reopening PowerShell). Install Python 3.10+ from https://www.python.org/downloads/ or run scripts\\install_windows.ps1, then re-run." }
      if (-not (Test-Path (Join-Path $InstallDir '.git')) -or -not (Test-Path (Join-Path $InstallDir 'launcher.py'))) {
        Clone-Or-Update $git
      } else {
        if ($git) { try { Clone-Or-Update $git } catch { Warn "Repo update skipped: $_" } }
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
      }
      $script:InstallDir = $InstallDir
      if (-not $py) { throw "Python not found (auto-install may require reopening PowerShell). Install Python 3.10+ from https://www.python.org/downloads/ or run scripts\\install_windows.ps1, then re-run." }
      if (-not (Test-Path (Join-Path $InstallDir '.git')) -or -not (Test-Path (Join-Path $InstallDir 'launcher.py'))) {
        Clone-Or-Update $git
      } else {
        if ($git) { try { Clone-Or-Update $git } catch { Warn "Repo update skipped: $_" } }
      }
      Ensure-Venv-And-Deps $py
      Print-Hints
      Run-Deploy
    }
    default { throw "Unknown Action '$Action' (use setup|run|deploy)" }
  }
} catch {
  Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
  exit 1
}
