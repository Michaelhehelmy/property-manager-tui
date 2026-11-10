# Property Manager (TUI + Web UI)

Local-first property management system: reservations, front desk, POS/inventory, accounting ledger, invoices, reports/analytics. **Business logic lives in `src/app_core/`** and is shared by both UIs.

## Structure

```text
launcher.py             # prompts and launches TUI or Web UI
main.py                 # TUI entry point (default)
requirements*.txt        # runtime/web/dev Python dependencies
scripts/setup.sh        # dynamic setup/deploy (detects iSH/Alpine, Termux, Debian/Ubuntu, macOS)
scripts/setup_ish_alpine.sh  # same dynamic script (iSH-friendly name)
src/app_core/           # backend: db.py, services.py, paths.py, assistant.py
ui/tui/                 # Textual TUI (app, tabs, modals)
ui/web/                 # FastAPI + HTMX web UI
tests/                  # pytest tests
```

## Quick start (desktop / standard terminal)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt              # TUI/runtime
pip install -r requirements-web.txt          # optional, for Web UI

python launcher.py          # choose [1] TUI or [2] Web
python launcher.py --tui    # TUI
python launcher.py --web    # Web UI (opens browser, workers=1)
```

## Dynamic setup script (recommended for iSH / iOS terminals / servers)

`scripts/setup.sh` (alias: `scripts/setup_ish_alpine.sh`) detects the environment and package manager (`apk`/`apt`/`pkg`/`brew`), installs deps, clones/updates the repo, prepares a `.venv`, and can deploy/run.

```bash
# Setup (auto-detect; on iSH defaults to TUI-friendly install)
sh scripts/setup.sh

# Setup + run immediately (deploy)
MODE=tui ACTION=deploy sh scripts/setup.sh
MODE=web HOST=0.0.0.0 PORT=8000 ACTION=deploy sh scripts/setup.sh

# Run after setup
ACTION=run MODE=tui sh scripts/setup.sh
ACTION=run MODE=web HOST=0.0.0.0 PORT=8000 sh scripts/setup.sh
```

**Options (env vars):** `MODE=auto|tui|web|both`, `ACTION=setup|run|deploy`, `SKIP_WEB=auto|0|1`, `INSTALL_DIR`, `REPO_URL`, `BRANCH`, `HOST`, `PORT`, `INSTALL_DEV=1`, `RUN_TESTS=1`.

### Windows (PowerShell)

Use `scripts/setup_windows.ps1` (or `scripts/setup_windows.cmd` wrapper). Requires **Python 3.10+** in PATH and **Git for Windows** (for clone/update).

```powershell
# Setup (from repo checkout, or will clone/update into %USERPROFILE%\property-manager-tui)
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1

# Setup + run (TUI or Web)
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -Action deploy -Mode tui
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -Action deploy -Mode web -BindHost 0.0.0.0 -Port 8000

# Run later
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -Action run -Mode tui
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -Action run -Mode web
```

CMD wrapper examples: `scripts\setup_windows.cmd -Action deploy -Mode tui`

Parameters/env vars: `-Action`/`ACTION` (setup|run|deploy), `-Mode`/`MODE` (auto|tui|web|both), `-SkipWeb`/`SKIP_WEB`, `-InstallDir`/`INSTALL_DIR`, `-BindHost`/`HOST`, `-Port`/`PORT`, `-InstallDev`, `-RunTests`. For LAN access run web with `-BindHost 0.0.0.0` and set `$env:PM_WEB_SECRET_KEY` to a strong secret.

## Run on iSH or iOS terminal apps

Choose based on what your iOS terminal provides:

### 1) Full terminal on iOS (local TUI) — best for iSH
Use an app that provides a full terminal (e.g. **iSH** on iOS/iPadOS). The Textual TUI runs locally in the terminal.

```sh
apk update && apk add --no-cache git ca-certificates
git clone https://github.com/Michaelhehelmy/property-manager-tui.git ~/property-manager-tui
cd ~/property-manager-tui
SKIP_WEB=1 MODE=tui ACTION=deploy sh scripts/setup.sh
# or setup only, then:
# . .venv/bin/activate && python3 launcher.py --tui
```

Notes: iSH is emulated Alpine — TUI is the most reliable mode. Web dependency builds (e.g. `pydantic-core`/Rust) can be slow or unavailable, so `SKIP_WEB=1` is recommended. Re-run the script anytime to pull updates.

### 2) Full client + server (Web UI)
Run the **server** (FastAPI, `--workers 1`) on a capable host, and use an iOS device/browser as the **client**. This is recommended when you want iPhone/iPad Safari access (including **Add to Home Screen**).

- **Server on the same iOS terminal device** (if the environment can run Python web deps, e.g. some Linux-on-iOS setups/Termux-like environments or a remote host):  
  `HOST=0.0.0.0 ACTION=deploy MODE=web sh scripts/setup.sh` then open `http://<device-ip>:8000` in Safari.
- **Server on another machine** (Mac/Linux/VPS/Raspberry Pi/always-on host, accessible on LAN or via HTTPS/tunnel): run the web server there and open its URL from iOS Safari.
- **Same-device browser:** `HOST=127.0.0.1` → `http://127.0.0.1:8000`.

```bash
export PM_WEB_SECRET_KEY='change-this-to-a-long-random-secret'  # required for non-local/shared use
python3 -m uvicorn ui.web.server:app --host 0.0.0.0 --port 8000 --workers 1
# or: ACTION=run MODE=web HOST=0.0.0.0 PORT=8000 sh scripts/setup.sh
```

Security/ops: keep `--workers 1` (SQLite). Use a strong `PM_WEB_SECRET_KEY`. Prefer LAN/Tailscale/VPN or HTTPS reverse proxy for remote access; do not expose plain HTTP publicly. Health probes: `GET /health`, `GET /healthz`. Back up the DB/data regularly.

## Run (Web UI, manual)

```bash
pip install -r requirements-web.txt
PYTHONPATH=src uvicorn ui.web.server:app --reload --workers 1
# Open http://127.0.0.1:8000
```

Sensitive web actions use CSRF + super-password verification/unlock flow. The web UI is a localhost/single-worker MVP over the same `app_core` services/DB.

## Tests / checks

```bash
python -m py_compile src/app_core/*.py ui/tui/*.py ui/web/*.py main.py launcher.py
PYTHONPATH=src pytest -q
# coverage:
bash scripts/coverage.sh
# install deps + run tests helper:
bash scripts/install_deps_and_test.sh
```

## Data paths
`app_core.paths` anchors runtime data. By default it uses legacy root `property_manager.db`/`exports/`/`invoices/` if present; otherwise uses `data/`. Override with:
```bash
export PM_DATA_DIR=/path/to/data
```
