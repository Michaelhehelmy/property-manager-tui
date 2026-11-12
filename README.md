# Property Manager (TUI + Web UI)

Local-first property management system (reservations/front desk, POS & inventory with barcodes/batches, meals/recipes & activities, accounting ledger, invoices, reports/analytics, grounded assistant). **All business logic lives in `src/app_core/`** and is shared by the Textual TUI and the FastAPI/HTMX web UI.

- **TUI:** runs locally in a terminal (best for constrained/mobile terminals like iSH).
- **Web UI:** FastAPI server (`--workers 1`, SQLite) accessed from a browser (desktop/mobile, LAN or remote). Health probes: `GET /health`, `GET /healthz`.

## Key features (current)

- **Front desk & reservations:** guests, rooms/rate plans, availability, check-in/out flows, folio/balances, invoices.
- **POS & inventory:** catalog, sales, recipes/meals, expenses, and **traceable barcodes/SKUs/lots**. Inventory supports **batch/lot tracking** with received date, expiry, remaining quantity/cost, and movements; expiring/expired batches can be reviewed and items/lots traced by barcode/SKU/lot code.
- **Activities/trips:** register and book activities with stats and folio/invoice posting.
- **Accounting, reports & analytics:** ledger, P&L/cash flow/VAT/payment methods/occupancy/ops reports and decision support.
- **Integrity & health checks:** read-only ledger/data integrity checks in-app (Web/TUI/services), CLI **doctor** checks, and web runtime health probes (`GET /health`, `GET /healthz`).
- **Grounded assistant:** read-only, data-backed help in Web & TUI for ops, reports, guests/portfolios, reservations/balances, inventory (including expiring batches, batch lists, barcode/lot trace with received/expiry/movements), integrity/health checks, and app version/update status.
- **In-app updates (Web & TUI):** Settings provide **Check for updates**, changelog/details, and an **Apply/Update** action for git-based installs (fast-forward). Runtime data is preserved during updates (DB, exports, invoices, configured data dir). Restart after applying an update.
- **Backups & data safety:** in-app/manual database backups to the exports/data area; runtime data (DB/exports/invoices/data dir) is preserved across source updates. Restore is a manual, offline file operation (see **Backups & restore**).

## Inventory, batches & traceability

- **Barcodes/SKUs/lots:** inventory items can carry SKU/barcode identifiers; stock can be tracked per **batch/lot** (traceable lots).
- **Batch fields:** each batch/lot records received date/time, expiry date, remaining (and received) quantity, unit cost, supplier/reference/notes, and status/expiry state.
- **Expiring batches:** review batches expiring within a window (and expired batches) to manage shelf life and reduce waste.
- **Batch lists:** list inventory batches/lots (per item or overall) with received/expiry/remaining/cost for traceability.
- **Barcode/lot trace:** resolve a barcode/SKU/lot code to the inventory item or specific batch/lot and inspect **when received, when it expires, remaining quantity, and recent movements/logs** (grounded in real records).
- **Grounded assistant:** ask for expiring batches, batch lists, or to trace a barcode/SKU/lot code; answers are read-only and based on real app data.

## Repository layout

```text
launcher.py                  # unified entry point: menu to choose/switch TUI or Web UI
main.py                      # TUI entry point
requirements*.txt            # runtime / web / dev Python deps
scripts/setup.sh             # dynamic setup/deploy (Linux/macOS/Termux/iSH, apk/apt/pkg/brew)
scripts/setup_ish_alpine.sh  # same script (iSH-friendly name)
scripts/setup_windows.ps1    # dynamic setup/deploy for Windows (PowerShell, auto-bootstraps prereqs)
scripts/setup_windows.cmd    # Windows CMD wrapper
scripts/install_windows.ps1  # one-command Windows bootstrap (winget + clone + setup)
scripts/install_windows.cmd  # Windows bootstrap CMD wrapper
src/app_core/                # backend: db.py, services.py, paths.py, assistant.py, barcodes.py
ui/tui/                      # Textual TUI (app, tabs, modals)
ui/web/                      # FastAPI + HTMX web UI
data/assistant_nlu/          # assistant seeds/feedback
tests/                       # pytest tests
assistant.md                 # assistant evolution & enhancement plan
```

## Prerequisites (all platforms)

- **Python 3.10+** (3.11+ recommended) with `pip` and `venv`.
- **Git** (to clone/update; optional if you copy an existing checkout).
- **SQLite** is bundled with Python; a C build toolchain is only needed if a wheel is unavailable for your platform/arch (common on iSH/Alpine or some ARM setups) — the setup scripts install best-effort build deps.
- **Web UI extra:** dependencies in `requirements-web.txt`. Use a single worker (`--workers 1`) because the app uses SQLite.

> Data/DB location is managed by `app_core.paths` (legacy `property_manager.db`/`exports/`/`invoices/` if present, else `data/`). Override with `PM_DATA_DIR=/path/to/data`.

## Quick decision: how to deploy

| Goal | Recommended mode |
|---|---|
| Local use on a PC/server terminal | **TUI** (`python launcher.py --tui`) or Web on `127.0.0.1` |
| Access from phone/tablet browser on the same LAN | **Web** server bound to `0.0.0.0`, open `http://<host-ip>:8000` (Add to Home Screen) |
| Remote access over the internet | **Web** on a host you control behind **HTTPS** (reverse proxy) / VPN / Tailscale |
| iSH (iOS, emulated Alpine) | **TUI** with web deps skipped (`SKIP_WEB=1`) — most reliable |
| Android (Termux) | **TUI** or **Web** (Termux supports both) |
| Windows desktop/server | PowerShell setup script or manual `venv` + `uvicorn`/launcher |

---

## Deploy on any OS (recommended: dynamic setup scripts)

The repo ships dynamic scripts that detect the OS/package manager, install prerequisites, clone/update, create a `.venv`, install deps, and can run/deploy.

- **Unix-like (Linux, macOS, Android/Termux, iSH/Alpine):** `scripts/setup.sh` (alias `scripts/setup_ish_alpine.sh`)
- **Windows:** `scripts/setup_windows.ps1` (or `scripts/setup_windows.cmd`)

**Common parameters (env vars / PS params):**
- `MODE=auto|tui|web|both` — what to install/run (`auto` prefers TUI on iSH, else both).
- `ACTION=setup|run|deploy` — `setup` = install/prepare, `run` = start existing install, `deploy` = setup + run.
- `SKIP_WEB=auto|0|1` — skip web deps (auto-skips on iSH; recommended there).
- `HOST` / `PORT` — web bind/port (default `127.0.0.1:8000`). Use `0.0.0.0` for LAN access.
- `INSTALL_DIR`, `REPO_URL`, `BRANCH` — install location/source.
- `INSTALL_DEV=1`, `RUN_TESTS=1` — optional dev deps/tests.

### Linux (Debian/Ubuntu, Fedora/RHEL*, Arch*, SUSE*, generic)

> The script auto-detects `apt`/`apk`/`pkg`/`brew`. On distros without these, install Python 3, pip, venv, git (and a compiler for fallback builds) via your package manager, then run the script or the manual steps below.

**Debian/Ubuntu (apt):**
```bash
sudo apt-get update && sudo apt-get install -y git python3 python3-venv python3-pip
git clone https://github.com/Michaelhehelmy/property-manager-tui.git
cd property-manager-tui
sh scripts/setup.sh                 # setup (installs deps, creates .venv)
# or setup + run:
MODE=tui ACTION=deploy sh scripts/setup.sh
MODE=web ACTION=deploy HOST=127.0.0.1 PORT=8000 sh scripts/setup.sh
```
**Alpine (apk):**
```sh
apk update && apk add --no-cache git ca-certificates
git clone https://github.com/Michaelhehelmy/property-manager-tui.git
cd property-manager-tui
sh scripts/setup.sh                 # web builds may need cargo/rust (script attempts); use SKIP_WEB=1 for TUI-only
```
**Fedora/RHEL/CentOS (dnf/yum), Arch (pacman), openSUSE (zypper):** install `git python3 python3-pip` (+ venv/build tools per distro), then:
```bash
git clone https://github.com/Michaelhehelmy/property-manager-tui.git && cd property-manager-tui
# run the script (it will detect env; install any missing system deps if prompted/warned)
MODE=tui ACTION=deploy sh scripts/setup.sh
# or use manual venv steps below
```

### macOS

```bash
# Xcode CLT recommended: xcode-select --install   (Homebrew optional)
git clone https://github.com/Michaelhehelmy/property-manager-tui.git
cd property-manager-tui
sh scripts/setup.sh                 # uses brew if available; otherwise system python3
MODE=web ACTION=deploy HOST=127.0.0.1 PORT=8000 sh scripts/setup.sh
# TUI: MODE=tui ACTION=deploy sh scripts/setup.sh
```

### Windows (10/11, Server)

**One-command install (even if Python/Git are not installed)** — PowerShell as Administrator recommended (uses `winget` to auto-install prerequisites), then:

```powershell
irm https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install_windows.ps1 | iex
```

Customize before running (optional):

```powershell
$env:ACTION='deploy'; $env:MODE='web'; $env:HOST='127.0.0.1'; $env:PORT='8000'
irm https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install_windows.ps1 | iex
```
(`MODE=tui|web|both`, `HOST=0.0.0.0` for LAN; set `$env:PM_WEB_SECRET_KEY` for non-local/shared web.)

**If you already have the repo / prefer local scripts:**

```powershell
# auto-bootstraps Python/Git if missing (winget), clones/updates, sets up & runs
powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1 -Action deploy -Mode tui
# or double-click / run:
scripts\install_windows.cmd -Action deploy -Mode tui

# lower-level setup script (same actions/modes, also bootstraps prereqs)
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -Action deploy -Mode web -BindHost 127.0.0.1 -Port 8000
scripts\setup_windows.cmd -Action run -Mode tui
```

Parameters/env vars: `-Action`/`ACTION` (setup|run|deploy), `-Mode`/`MODE` (auto|tui|web|both), `-SkipWeb`/`SKIP_WEB`, `-InstallDir`/`INSTALL_DIR`, `-BindHost`/`HOST`, `-Port`/`PORT`, `-InstallDev`, `-RunTests`. Installs to `%USERPROFILE%\property-manager-tui` by default. If `winget` is unavailable, install [Python 3.10+](https://www.python.org/downloads/) (add to PATH) and [Git for Windows](https://git-scm.com/download/win), then re-run.

### Windows Subsystem for Linux (WSL / WSL2)

Treat as Linux inside WSL:
```bash
git clone https://github.com/Michaelhehelmy/property-manager-tui.git && cd property-manager-tui
MODE=web ACTION=deploy HOST=0.0.0.0 PORT=8000 sh scripts/setup.sh
```
Open from Windows browser via `http://localhost:8000` (or the WSL IP for other LAN devices, subject to Windows firewall/port forwarding).

### Android — Termux

```sh
pkg update && pkg install -y git python
git clone https://github.com/Michaelhehelmy/property-manager-tui.git
cd property-manager-tui
# TUI or Web both supported
MODE=tui ACTION=deploy sh scripts/setup.sh
# or web (accessible from other devices on LAN):
MODE=web HOST=0.0.0.0 PORT=8000 ACTION=deploy sh scripts/setup.sh
```
Then browse `http://<phone-ip>:8000` from another device (same Wi‑Fi). Set `PM_WEB_SECRET_KEY` for non-local use.

### iOS — iSH (Alpine, emulated)

iSH is constrained/emulated: **TUI is the most reliable mode**. Web dependency builds (e.g. `pydantic-core`/Rust) can be slow or unavailable — prefer `SKIP_WEB=1`.

```sh
apk update && apk add --no-cache git ca-certificates
git clone https://github.com/Michaelhehelmy/property-manager-tui.git ~/property-manager-tui
cd ~/property-manager-tui
SKIP_WEB=1 MODE=tui ACTION=deploy sh scripts/setup.sh
# later runs:
ACTION=run MODE=tui sh scripts/setup.sh
# or: . .venv/bin/activate && python3 launcher.py --tui
```

### iOS/iPadOS — Web client (Safari) against a server

Run the **server** on a capable host and use iOS Safari as the **client** (works with “Add to Home Screen”):
- **Server on Mac/Linux/Windows/Termux/Raspberry Pi/VPS** (LAN or reachable host): `MODE=web HOST=0.0.0.0 PORT=8000 ACTION=deploy ...` then open `http://<server-ip>:8000` in Safari.
- **Same-device localhost** (if the environment can run the web deps): bind `127.0.0.1`.
- For remote/public access, put the server behind **HTTPS** (reverse proxy) or a private network (Tailscale/VPN). Do not expose plain HTTP directly.

### Other Unix / NAS / Raspberry Pi / ARM / minimal environments

On any POSIX-like system with Python 3.10+, git, and a working compiler toolchain (when wheels aren’t available):
```bash
git clone https://github.com/Michaelhehelmy/property-manager-tui.git && cd property-manager-tui
# try the dynamic script first
sh scripts/setup.sh
# if your package manager isn’t detected, install python3/pip/venv/git/build deps manually, then:
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt        # TUI
pip install -r requirements-web.txt    # Web (optional)
python3 launcher.py --tui
```
The app is local-first/SQLite; on very low-resource devices prefer TUI (`SKIP_WEB=1`).

### Containers / Docker (generic)

No official image is bundled; a minimal example (Debian-based) for the **Web UI**:
```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -r requirements.txt -r requirements-web.txt
ENV PM_WEB_SECRET_KEY=change-me PM_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "ui.web.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```
Build/run: `docker build -t property-manager . && docker run -p 8000:8000 -v pmdata:/data -e PM_WEB_SECRET_KEY=... property-manager`. Mount a volume for persistent data/backups and keep `--workers 1`.

---

## Manual install & run (any OS with Python)

```bash
# 1) Get source
git clone https://github.com/Michaelhehelmy/property-manager-tui.git
cd property-manager-tui

# 2) Create & activate venv
python3 -m venv .venv
# Linux/macOS/Termux/iSH/WSL:  . .venv/bin/activate
# Windows (PowerShell):         .\.venv\Scripts\Activate.ps1
# Windows (cmd):                .venv\Scripts\activate.bat

# 3) Install deps
pip install -r requirements.txt          # TUI/core
pip install -r requirements-web.txt      # Web UI (optional)

# 4) Run
python launcher.py            # interactive [1] TUI / [2] Web
python launcher.py --tui
python launcher.py --web      # web launcher
# or directly:
PYTHONPATH=src python -m uvicorn ui.web.server:app --host 127.0.0.1 --port 8000 --workers 1
```

## Web deployment notes (important)

- **Single worker:** always run with `--workers 1` (SQLite/shared state). Do not increase workers for the built-in server.
- **Secret:** set `PM_WEB_SECRET_KEY` to a strong random value for any non-local/shared deployment.
- **Bind:** `127.0.0.1` = local only; `0.0.0.0` = reachable on network interfaces (LAN). Open firewall/port as needed.
- **Transport security:** prefer **HTTPS** (reverse proxy like Caddy/Nginx/Traefik) or a private network (VPN/Tailscale) for remote/LAN access; avoid exposing plain HTTP publicly.
- **Reverse proxy (example):** proxy to `127.0.0.1:8000`, terminate TLS, forward headers as usual.
- **Service/autostart:** run via a process manager (systemd, launchd, NSSM/Task Scheduler on Windows, `supervisord`, Docker restart policy) executing the venv’s Python/uvicorn with `--workers 1`.
- **Health probes:** `GET /health` and `GET /healthz` are available for liveness/readiness checks.
- **Backups:** back up the DB and data/exports/invoices regularly (and before upgrades/migrations). Runtime data lives in the configured data dir (`PM_DATA_DIR` or default) and should be preserved across updates.

## Security model (Web: CSRF & unlock)

- **CSRF:** all state-changing web requests (POST/PUT/PATCH/DELETE) are CSRF-protected. Form posts verify a CSRF token; JSON/header requests are enforced by server middleware/handlers (see `docs/web_authz_matrix.md`).
- **Super-password unlock:** sensitive/destructive/admin mutations (financial/accounting operations, inventory/stock/batch/recipe/meal/activity changes, voids/refunds/cancels/deletes/modifications, exports/backups/seed/demo/settings changes, labels, protected downloads, etc.) require an active unlock (super password) in addition to CSRF.
- **Operational front-desk flows:** some routine operational actions (e.g. create/check-in/checkout/pay, POS cart operations, walk-in sale) remain usable where appropriate but still require CSRF; policy-sensitive overrides and destructive actions remain gated by unlock.
- **Assistant unlock policy:** assistant ask/feedback follows configured unlock/allowlist policy while remaining read-only and grounded.
- **Reference:** `docs/web_authz_matrix.md` documents the route-group matrix (CSRF/unlock/conditional) for `ui/web`. Keep deployments on trusted networks or behind HTTPS/VPN.

## Updates

**In-app (recommended for git installs, Web & TUI):** open **Settings → App updates** to **Check for updates**, review the **changelog/commit details**, and **Apply/Update** (git fast-forward apply).

- **Changelog/details:** available updates show commit/changelog details before applying.
- **Data preserved:** runtime data is **not** modified by source updates (database, exports, invoices, and the configured data directory such as `PM_DATA_DIR` / `data/` are preserved).
- **After apply:** restart the app/server to load the new version.
- **Local changes:** if the working tree has local source changes, commit/stash/discard them before applying an in-app update.
- **Assistant:** you can ask “check for updates / what app version?” for grounded local version/update status and guidance.

**Manual (CLI):**
```bash
cd <install-dir>
git pull --ff-only
# re-run setup to refresh deps:
#   Unix: MODE=<tui|web|both> ACTION=setup sh scripts/setup.sh
#   Win:  powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -Action setup
# DB migrations run on startup (additive). Runtime data (DB/exports/invoices/data dir) is preserved.
```

## Integrity, health checks & doctor

- **In-app (read-only):** ledger/data integrity checks are available in the app (and surfaced by the grounded assistant as “health check / ledger integrity”). Use them to detect inconsistencies before relying on reports.
- **CLI doctor:** run operational health checks (safe/read-oriented where possible) with:
  ```bash
  PYTHONPATH=src python -m app_core.doctor
  ```
  Supports `--json`, `--check-updates`, and `--strict`. It also provides safe backup/verify helpers: `--backup [--backup-to <path>]` to create a verified SQLite backup copy (no runtime data deletion), and `--verify-backup <path>` to read-only integrity/schema-verify a candidate backup. Run `--help` for all flags.
- **Web runtime probes:** `GET /health` and `GET /healthz` for liveness/readiness of the web server.

## Backups & restore

- **Create backups:** use **Settings → Backup now** in Web/TUI (when unlocked where required) to create a database backup (stored under the app’s exports/data area). You can also create/verify safe SQLite backups via CLI doctor: `PYTHONPATH=src python -m app_core.doctor --backup` (or `--backup-to <path>`) and `--verify-backup <path>`. Keep regular backups and take one before upgrades/migrations.
- **What to preserve:** runtime data lives in the configured data directory (`PM_DATA_DIR` or the default `data/`/legacy locations), including the database, exports, and invoices. Source/app updates (in-app or `git pull`) do **not** modify this runtime data.
- **Restore (manual):** stop the app/server, copy the desired backup DB/file back to the active database path for your data directory (and restore related exports/invoices if needed), then start the app. DB migrations run on startup (additive). Verify with a backup/health/integrity check after restore. (There is no destructive one-click in-app restore; perform restores deliberately offline.)

## Tests & checks

```bash
python -m py_compile src/app_core/*.py ui/tui/*.py ui/web/*.py main.py launcher.py
PYTHONPATH=src pytest -q
bash scripts/coverage.sh
bash scripts/install_deps_and_test.sh
```

## Troubleshooting

- **Web deps fail to build (iSH/slow or no Rust toolchain):** use `SKIP_WEB=1 MODE=tui` and run the TUI.
- **`python3 -m venv` missing:** install your distro’s venv package (e.g. `python3-venv` on Debian/Ubuntu) or let the script fall back where possible.
- **Port in use:** change `PORT`/`--port`.
- **Can’t reach web from another device:** bind `HOST=0.0.0.0`, allow the port through firewall, and use the host’s LAN IP.
- **Permission/DB issues:** ensure the process can read/write the data directory (`PM_DATA_DIR` or default) and keep a single writer/worker.

## More

- `assistant.md` — assistant capabilities, local-only architecture, and enhancement plan.
- In-app help/tooltips in Web & TUI for flows (POS/inventory/barcodes & batches and lot traceability, activities, settings, updates, health checks, etc.).
