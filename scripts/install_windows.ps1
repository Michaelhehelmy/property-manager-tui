<# 
One-command Windows installer/bootstrap for property-manager-tui.
- Installs Python 3.12 and Git via winget if missing (requires App Installer/winget)
- Clones or updates the repo
- Runs setup_windows.ps1 (setup/deploy)

Usage (PowerShell, run as Administrator recommended for winget installs):
  irm https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install_windows.ps1 | iex

Customize via environment variables before running, e.g.:
  $env:ACTION='deploy'; $env:MODE='web'; $env:HOST='0.0.0.0'; $env:PORT='8000'; irm https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install_windows.ps1 | iex

Or download and run:
  powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1 -Action deploy -Mode tui
#>
[CmdletBinding()]
param(
  [string]$Action = $(if ($env:ACTION) { $env:ACTION } else { 'deploy' }),
  [string]$Mode = $(if ($env:MODE) { $env:MODE } else { 'tui' }),
  [string]$SkipWeb = $(if ($env:SKIP_WEB) { $env:SKIP_WEB } else { 'auto' }),
  [string]$InstallDir = $(if ($env:INSTALL_DIR) { $env:INSTALL_DIR } else { (Join-Path $HOME 'property-manager-tui') }),
  [string]$RepoUrl = $(if ($env:REPO_URL) { $env:REPO_URL } else { 'https://github.com/Michaelhehelmy/property-manager-tui.git' }),
  [string]$Branch = $(if ($env:BRANCH) { $env:BRANCH } else { 'main' }),
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
  foreach ($p in @('C:\Program Files\Git\cmd','C:\Program Files\Git\bin')) { if ((Test-Path $p) -and ($env:Path -notlike "*$p*")) { $env:Path = "$p;$env:Path" } }
  try {
    foreach ($r in @('C:\Program Files',(Join-Path $env:LOCALAPPDATA 'Programs\Python'))) {
      if (Test-Path $r) { Get-ChildItem $r -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue | ForEach-Object { $env:Path = "$($_.FullName);$($_.FullName)\Scripts;$env:Path" } }
    }
  } catch {}
}

try {
  Log "property-manager-tui Windows bootstrap (Action=$Action Mode=$Mode InstallDir=$InstallDir)"
  if (-not (Have winget)) {
    Warn "winget (App Installer) not found. Install Python 3.10+ and Git manually, then run:"
    Write-Host "  git clone $RepoUrl `"$InstallDir`""
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$InstallDir\scripts\setup_windows.ps1`" -Action $Action -Mode $Mode"
    exit 1
  }
  Refresh-PathEnv
  if (-not (Have py) -and -not (Have python) -and -not (Have python3)) {
    Log "Installing Python 3.12 via winget..."
    try { winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent } catch { winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements }
  }
  if (-not (Have git)) {
    Log "Installing Git via winget..."
    try { winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements --silent } catch { winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements }
  }
  Refresh-PathEnv

  # clone/update
  if (-not (Have git)) { throw "Git is not available in this session yet. Close & reopen PowerShell, then re-run this installer." }
  if (Test-Path (Join-Path $InstallDir '.git')) {
    Log "Updating repo in $InstallDir"
    try { git -C $InstallDir fetch --all --prune } catch {}
    try { git -C $InstallDir checkout $Branch } catch {}
    try { git -C $InstallDir pull --ff-only origin $Branch } catch { try { git -C $InstallDir pull } catch {} }
  } else {
    Log "Cloning repo to $InstallDir"
    $parent = Split-Path -Parent $InstallDir
    if ($parent -and -not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    git clone --branch $Branch $RepoUrl $InstallDir
    if ($LASTEXITCODE -ne 0) { throw "git clone failed" }
  }

  $setup = Join-Path $InstallDir 'scripts\setup_windows.ps1'
  if (-not (Test-Path $setup)) { throw "Setup script not found at $setup" }
  Log "Running setup script..."
  & powershell -NoProfile -ExecutionPolicy Bypass -File $setup -Action $Action -Mode $Mode -SkipWeb $SkipWeb -InstallDir $InstallDir -RepoUrl $RepoUrl -Branch $Branch -BindHost $BindHost -Port $Port
  exit $LASTEXITCODE
} catch {
  Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
  Write-Host "If Python/Git were just installed, close & reopen PowerShell and re-run the installer." -ForegroundColor Yellow
  exit 1
}
