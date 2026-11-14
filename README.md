# Property Manager (TUI + Web UI)

Local-first property management system with a shared backend and two frontends:

- **TUI:** Textual terminal app (`main.py`, `launcher.py --tui`) for local/SSH/constrained terminals.
- **Web UI:** FastAPI + HTMX server (`ui.web.server:app`, `launcher.py --web`) for browser access on desktop/mobile, LAN, or behind a reverse proxy.

**Architecture:** all business logic lives in `src/app_core/` (DB, services, paths, assistant, integrity/doctor, updater, barcode/batch logic) and is shared by `ui/tui/` and `ui/web/`. SQLite is the datastore.

## Current capabilities

- **Front desk & reservations:** guests (including 360° portfolio views), rooms/rate plans, availability, create/check-in/checkout flows, folio/balances, payments/refunds where applicable, invoices/receipts.
- **POS & inventory:** catalog, walk-in and reservation-linked sales, recipes/meals/ingredients with auto-deduction, expenses, and **traceable barcodes/SKUs/lots**.
- **Batch/lot traceability & FEFO:** inventory supports batch/lot tracking with received date/time, expiry date, remaining/received quantity, unit cost, supplier/reference/notes, status/expiry state, and movements. Supports **FEFO-aware** (First Expired, First Out) workflows, expiring/expired batch review, batch/lot lists, and barcode/SKU/lot trace (resolved item or lot with received/expiry/remaining and recent movements when available).
- **Activities/trips:** register/book activities/excursions, track bookings/stats, and post charges to folios/invoices.
- **Accounting, reports & analytics:** ledger, operational/financial reports and analytics/exports (as available in the app).
- **Grounded assistant (read-only):** Web & TUI assistant answers from real app data/tools (ops, reports, guests/portfolios, reservations/balances, inventory including expiring batches, batch lists, barcode/lot trace, activities/bookings, integrity/health, and app version/update status). Assistant follows configured unlock/allowlist policy and remains read-only.
- **Integrity & health:** read-only ledger/data integrity checks in-app, CLI **doctor** (`python -m app_core.doctor`), safe backup/verify helpers, and web liveness/readiness probes `GET /health`, `GET /healthz`, `GET /readyz`.
- **In-app updates (git installs, Web & TUI):** **Settings → App updates** supports **Check for updates**, changelog/commit details, and **Apply/Update** via **git fast-forward**. Local source changes must be committed/stashed/discarded before apply.
- **Backups & data safety:** create verified DB backups from Settings (**Backup now**, subject to unlock/CSRF as applicable) or CLI/scripts. Runtime data is preserved across source updates; **restore is manual and offline**.

## Operational requirements (important)

- **SQLite / single worker:** always run the web server with **`--workers 1`**. Do not increase worker count.
- **Web secret key:** set **`PM_WEB_SECRET_KEY`** to a strong random value for any non-local/shared/LAN/remote deployment.
- **HTTPS / transport security:** bind to `127.0.0.1` for local-only use. For LAN/remote/browser access, use **HTTPS** (reverse proxy that terminates TLS) or a private network (VPN/Tailscale/SSH tunnel). Do not expose plain HTTP publicly.
- **Data directory & permissions:** runtime data lives in `PM_DATA_DIR` or app defaults/legacy locations (DB, exports, invoices). Ensure the process user can read/write the data directory and keep a single writer.
- **Data preserved across updates:** source/app updates (in-app fast-forward or `git pull --ff-only`) do **not** modify runtime data (DB, exports, invoices, configured data dir). DB migrations run on startup and are additive where applicable.
- **Restart after apply:** after applying an in-app update, **restart the TUI/app/server** to load the new version.
- **Local source changes:** in-app apply requires a clean working tree for fast-forward; commit, stash, or discard local changes first.
- **Backups before risky operations:** take and verify backups before upgrades/migrations, restores, or major operational changes.
- **No destructive one-click restore:** restore is deliberate/manual/offline (stop app, verify backup, replace files, start, verify integrity).
- **Security model:** web state-changing requests are CSRF-protected; sensitive/destructive/admin mutations require **super-password unlock** (and CSRF) per policy, while some routine operational/front-desk flows remain usable with CSRF only. See `docs/web_authz_matrix.md`.

## Repository layout

```text
launcher.py                  # unified launcher/menu (TUI or Web)
main.py                      # TUI entry point
pyproject.toml               # packaging metadata and extras
requirements*.txt            # runtime / web / dev Python deps
scripts/                     # install/setup/update/backup/coverage helpers
src/app_core/                # shared backend (db, services, paths, assistant, doctor, updater, ...)
ui/tui/                      # Textual TUI
ui/web/                      # FastAPI + HTMX web UI
docs/                        # operations, security, deployment, runbooks
assistant.md                 # assistant capabilities/evolution/plan
tests/                       # pytest suite
```

## Quick decision: how to run

| Goal | Recommended mode |
|---|---|
| Local use on a PC/server terminal | **TUI** (`python launcher.py --tui`) or Web on `127.0.0.1` |
| Access from phone/tablet browser on same LAN | **Web** bound to `0.0.0.0` behind trusted LAN/VPN and with `PM_WEB_SECRET_KEY`; prefer HTTPS reverse proxy if feasible |
| Remote access over internet | **Web** behind **HTTPS** reverse proxy / VPN / Tailscale (never plain HTTP public exposure) |
| iSH (iOS/Alpine, constrained) | **TUI** with web deps skipped (`SKIP_WEB=1`) |
| Android (Termux) | **TUI** or **Web** (`--workers 1`) |
| Windows desktop/server | `scripts/install_windows.ps1` / `scripts/setup_windows.ps1` or manual venv |

## One-line install / update (all supported OS families)

These commands are intended for **first-time install and later re-runs** on git-based installs: they bootstrap prerequisites where possible, clone/update (fast-forward), refresh dependencies, preserve runtime data, and launch/run per selected mode. **Always keep web on `--workers 1`**. For shared/non-local/LAN/remote web access, set a strong `PM_WEB_SECRET_KEY` and use **HTTPS** via reverse proxy/VPN/private networking (do not expose plain HTTP publicly). **Built-in recovery:** if an install/update gets stuck due to dirty tree, stale `.git/index.lock`, stuck merge/rebase, or ff-only/checkout errors (e.g. `Reset branch 'main'`), rerun with `REPAIR=1` (Unix/Termux) or `-Repair` / `$env:REPAIR='1'` (Windows) to auto-recover by resetting local source to the target branch (runtime data in `PM_DATA_DIR`/external data dirs is not removed by git).

| OS / Environment | One-line command | Notes |
|---|---|---|
| **Linux / macOS / WSL / generic Unix** (curl) | `curl -fsSL https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install.sh \| sh` | Detects environment/package manager, installs/updates, launches launcher (choose TUI or Web). Re-run anytime. |
| **Linux / macOS / WSL** (wget) | `wget -qO- https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install.sh \| sh` | Fallback if `curl` is unavailable. |
| **TUI only (minimal / headless / iSH)** | `curl -fsSL https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install.sh \| SKIP_WEB=1 MODE=tui sh` | Most reliable for constrained environments. |
| **Web server for LAN (trusted network)** | `curl -fsSL https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install.sh \| MODE=web HOST=0.0.0.0 PORT=8000 PM_WEB_SECRET_KEY="$(openssl rand -hex 32)" sh` | `--workers 1` required (SQLite). Use HTTPS reverse proxy/VPN for shared/remote access. |
| **Android (Termux)** | `pkg update -y && pkg install -y curl && curl -fsSL https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install.sh \| sh` | Launcher (TUI/Web). For LAN web add `MODE=web HOST=0.0.0.0 PORT=8000 PM_WEB_SECRET_KEY=...`; if web deps fail (e.g. pydantic-core/Rust wheel builds), use `SKIP_WEB=1 MODE=tui`. |
| **iOS (iSH / Alpine)** | `apk add --no-cache curl && curl -fsSL https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install.sh \| SKIP_WEB=1 MODE=tui sh` | Prefer TUI on iSH. |
| **Windows (PowerShell, bootstrap)** | `irm https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install_windows.ps1 \| iex` | Uses winget when available to install Python/Git if missing, then clones/updates and runs setup/deploy. Customize via `$env:ACTION/$env:MODE/$env:HOST/$env:PORT/$env:PM_WEB_SECRET_KEY`. |
| **Windows (CMD bootstrap)** | `curl -fsSL -o install_windows.cmd https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install_windows.cmd && install_windows.cmd -Action deploy -Mode auto` | Downloads/runs bootstrap wrapper (uses PowerShell script). |

**Common env vars / parameters (Unix installer/setup):** `INSTALL_DIR`, `REPO_URL`, `BRANCH`, `GIT_REF`/`TAG`/`COMMIT` (pinned ref), `MODE` (`auto|tui|web|both`), `ACTION` (`auto|setup|run|deploy`), `SKIP_WEB` (`auto|0|1`), `HOST`, `PORT`, `PYTHON`, `INSTALL_DEV`, `RUN_TESTS`, `PM_DATA_DIR`, `PM_WEB_SECRET_KEY`. **Windows:** pass `-GitRef`/`-Action`/`-Mode`/`-BindHost`/`-Port` or set corresponding env vars (`GIT_REF`/`TAG`/`COMMIT`, etc.).

- `ACTION=auto` (default): full install+launch if missing, else update deps/code and launch.
- `ACTION=setup`: install/update/prepare only. `ACTION=run`: run existing install. `ACTION=deploy`: setup + run.
- Runtime data (DB/exports/invoices/`PM_DATA_DIR`) is preserved by scripts/updates.

### Pinned/reproducible installs

- Install/update to a specific tag/commit/branch: `GIT_REF=<tag-or-commit-or-branch> sh` (Unix) or `-GitRef <...>` / `$env:GIT_REF=<...>` (Windows). Aliases: `TAG`, `COMMIT`.
- **Pinned bootstrap script URL:** use the raw script at the same ref to ensure installer logic is reproducible, e.g. `https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/<tag-or-commit>/scripts/install.sh` (Unix) or `.../<tag-or-commit>/scripts/install_windows.ps1` (Windows).
- Existing checkouts: `GIT_REF=<tag> ACTION=setup sh scripts/setup.sh` (or Windows setup script) checks out the requested ref; when on a branch, scripts may fast-forward as applicable. Runtime data is preserved.
- For immutable production-like deployments, prefer pinning to a release tag/commit and recording the resolved commit (`git -C <install-dir> rev-parse HEAD`) alongside backups/change records.

### Dependency/install robustness (constrained environments)

- **TUI-only fallback:** if web dependency installation/build fails, re-run with `SKIP_WEB=1 MODE=tui` (Unix) or `-SkipWeb 1 -Mode tui` (Windows). Core/TUI remains usable.
- **`pydantic-core` / Rust wheel issues:** on iSH/Termux/older CPU architectures or limited toolchains, some web deps may require Rust/cargo/build tools or a prebuilt wheel matching your Python version/architecture. If unavailable, use TUI-only, upgrade Python/toolchain, or run the web UI on a fuller host.
- **SQLite web safety:** always run web with `--workers 1`. Use `PM_WEB_SECRET_KEY` and HTTPS reverse proxy/VPN for non-local/shared access. Runtime data (`PM_DATA_DIR`, DB, exports, invoices) is preserved across setup/update runs.

## Platform-specific setup

### Linux / macOS / WSL / Unix-like
- Use `scripts/install.sh` (bootstrap) or `scripts/setup.sh` from a checkout.
- Examples: `sh scripts/setup.sh` (setup), `MODE=tui ACTION=deploy sh scripts/setup.sh`, `MODE=web HOST=127.0.0.1 PORT=8000 ACTION=deploy sh scripts/setup.sh`.
- WSL: treat as Linux; bind carefully for LAN access and account for Windows firewall/port forwarding.

### Android (Termux)
```sh
pkg update && pkg install -y git python curl
curl -fsSL https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install.sh | sh
# or web on LAN (trusted network): add MODE=web HOST=0.0.0.0 PORT=8000 and PM_WEB_SECRET_KEY
```

### iOS (iSH / Alpine)
```sh
apk add --no-cache curl
curl -fsSL https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install.sh | SKIP_WEB=1 MODE=tui sh
```
TUI is recommended on iSH. Web dependency builds may be slow/unavailable.

### iOS/iPadOS as web client
Run the server on a capable host (Mac/Linux/Windows/Termux/RPi/VPS) and open from Safari (`http://<host>:8000` on LAN or `https://...` via reverse proxy/Tailscale/VPN). Add to Home Screen for app-like use.

### Windows (10/11, Server)
- **Bootstrap (recommended):** PowerShell (Admin recommended for winget):
  ```powershell
  irm https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install_windows.ps1 | iex
  ```
  Customize: `$env:ACTION='deploy'; $env:MODE='web'; $env:HOST='127.0.0.1'; $env:PORT='8000'; $env:PM_WEB_SECRET_KEY='...'; irm ... | iex`
- **From existing checkout:**
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1 -Action deploy -Mode tui
  powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -Action deploy -Mode web -BindHost 127.0.0.1 -Port 8000
  scripts\install_windows.cmd -Action deploy -Mode auto
  ```
- If winget is unavailable, install Python 3.10+ (PATH) and Git, then re-run. Scripts attempt to refresh PATH/bootstrap prerequisites.

### Manual install (any OS with Python)

```bash
git clone https://github.com/Michaelhehelmy/property-manager-tui.git
cd property-manager-tui
python3 -m venv .venv
# activate: Unix `. .venv/bin/activate` | PowerShell `.\\.venv\\Scripts\\Activate.ps1` | cmd `.venv\\Scripts\\activate.bat`
pip install -r requirements.txt
pip install -r requirements-web.txt  # optional, for web
python launcher.py          # menu
python launcher.py --tui
python launcher.py --web --host 127.0.0.1 --port 8000
# direct web server (always --workers 1):
PYTHONPATH=src python -m uvicorn ui.web.server:app --host 127.0.0.1 --port 8000 --workers 1
```

`pyproject.toml` also defines extras/scripts (`.[web]`, `.[dev]`, `property-manager`, `pm`, etc.) for packaging workflows.

## Web deployment essentials

- **Workers:** `--workers 1` only (SQLite/shared state). Never increase workers.
- **Bind:** `127.0.0.1` local-only; `0.0.0.0` reachable on interfaces (LAN). Manage firewall exposure explicitly.
- **Secret:** set `PM_WEB_SECRET_KEY` to a strong random value for any non-local/shared deployment (required for non-loopback use unless an explicit local/dev insecure override is set).
- **TLS/remote access:** use reverse proxy with HTTPS or a private network (VPN/Tailscale/SSH tunnel). Do not expose plain HTTP publicly.
- **Data directory & permissions:** set `PM_DATA_DIR` if needed; ensure the service user can read/write the data directory and run with a single writer.
- **Health probes:** `GET /health` and `GET /healthz` (liveness/basic health with readiness summary), `GET /readyz` (readiness/runtime checks including data-dir writability). Keep probes and app on `--workers 1`.
- **Service/autostart:** use systemd/launchd/NSSM/Task Scheduler/supervisor/Docker restart policy to run venv Python/uvicorn with `--workers 1`.
- **Reverse proxy:** proxy to `127.0.0.1:8000`, terminate TLS, and forward standard proxy headers as needed.
- **Backups, restore, updates:** take and verify backups before upgrades/migrations; restore is manual/offline (with restore drill validation recommended); restart the app/server after applying updates.
- **App icon / launcher:** no native installer icons are bundled. For browser/mobile install use "Add to Home Screen" (add PWA assets like `manifest.webmanifest` and icons under `ui/web/static/` and link from `ui/web/templates/base.html` if needed); for Windows/Linux create OS shortcuts (`.lnk`/`.desktop`) pointing to the venv launcher with a custom icon; on Termux prefer browser home-screen shortcut for web or Termux:Widget for TUI scripts.
- See `docs/DEPLOYMENT.md` and `docs/SECURITY.md` for examples and hardening.

## Security model (Web) — summary

- **CSRF:** all state-changing web requests (POST/PUT/PATCH/DELETE) are CSRF-protected (form tokens and/or header/middleware enforcement per request type).
- **Super-password unlock:** sensitive/destructive/admin mutations require active unlock (super password) in addition to CSRF (financial/accounting operations, inventory/stock/batch/recipe/meal/activity changes, voids/refunds/cancels/deletes/modifications, exports/backups/seed/demo/settings changes, labels, protected downloads where applicable, etc.).
- **Routine operational flows:** some front-desk/POS operations may remain usable with CSRF where appropriate, but policy-sensitive overrides/destructive actions remain gated by unlock.
- **Assistant:** follows configured unlock/allowlist policy while remaining read-only and grounded.
- **Secrets & transport:** strong `PM_WEB_SECRET_KEY`, HTTPS/private networking for shared/remote access.
- **Reference:** `docs/web_authz_matrix.md` is the authoritative route-group matrix for `ui/web`. See `docs/SECURITY.md` for hardening checklist.

## Updates

**In-app (recommended for git installs, Web & TUI):** **Settings → Updates & Health → App updates** → **Check for updates** → **Update now / Apply** appears **only when an update is available** (git fast-forward for clean checkouts).

- **Automated:** check/fetch metadata, show changelog/commits, apply ff-only when eligible.
- **Requirements/limits:** clean working tree required; non-fast-forward/diverged histories need manual git resolution.
- **Data preserved:** DB, exports, invoices, and configured data directory (`PM_DATA_DIR`/defaults) are not modified by source updates.
- **After apply:** **restart the app/server** to load the new version. If dependencies changed, refresh the venv via setup scripts.
- **Auto-check when online:** not automatic by default (checks are manual). Use Settings/doctor checks, external schedulers, or add in-app periodic/startup checking if desired; avoid unattended auto-apply without backup/restart/health safeguards.
- **First-time update / platform guides:** Windows: rerun `scripts/install_windows.ps1` (or `git pull --ff-only` + `scripts\setup_windows.ps1 -Action setup`) then restart. Termux (TUI/Web): rerun `scripts/install.sh` with `SKIP_WEB=1 MODE=tui` or `MODE=web ... --workers 1` (or manual ff-only + setup) then restart. Details in `docs/UPDATES.md`.
- **Manual CLI:** `git pull --ff-only` then re-run setup to refresh deps if needed (Unix/Termux: `MODE=<...> ACTION=setup sh scripts/setup.sh`, Windows: `scripts\setup_windows.ps1 -Action setup`).

See `docs/UPDATES.md` for release/update/rollback guidance.

## Integrity, health checks & doctor

- **In-app (read-only):** ledger/data integrity checks available in Web/TUI and via grounded assistant queries.
- **CLI doctor:** `PYTHONPATH=src python -m app_core.doctor` (or `scripts/backup_runtime.* --doctor`) for operational health checks. Supports `--json`, `--check-updates`, `--strict`, and safe backup/verify helpers: `--backup [--backup-to <path>]`, `--verify-backup <path>` / `--verify`.
- **Web probes:** `GET /health`, `GET /healthz`, `GET /readyz` for liveness/readiness (and detailed readiness checks).

## Backups & restore

- **Create backups (automated/helpers):**
  - Web/TUI: **Settings → Backup now** (subject to unlock/CSRF as applicable).
  - CLI: `python3 scripts/backup_runtime.py --backup [--to <path>]`, `python3 scripts/backup_runtime.py --verify <backup.db>`, `python3 scripts/backup_runtime.py --doctor [--json] [--check-updates]`, or `PYTHONPATH=src python -m app_core.doctor --backup/--verify-backup`.
  - Wrappers: `scripts/backup_runtime.sh`, `scripts/backup_runtime.cmd`, `scripts/backup_runtime.ps1`.
- **Preserve:** DB, exports, invoices, and data dir (`PM_DATA_DIR` or defaults). Take backups regularly and before upgrades/migrations.
- **Restore (manual, offline):** no destructive one-click in-app restore.
  1. Stop app/server.
  2. Verify candidate backup (`--verify` / `--verify-backup`).
  3. Copy current DB/files as safety backup.
  4. Replace active DB (and related exports/invoices if needed) with verified backup in the correct data directory.
  5. Start app/server (`--workers 1` for web); migrations run on startup.
  6. Verify with doctor/integrity checks and app smoke tests.

See `docs/BACKUP_RESTORE.md` for DR guidance.

## Tests & checks

```bash
python -m py_compile src/app_core/*.py ui/tui/*.py ui/web/*.py main.py launcher.py
PYTHONPATH=src pytest -q
bash scripts/coverage.sh
bash scripts/install_deps_and_test.sh
```

## Troubleshooting (quick)

- **Web deps/build issues (iSH/limited toolchains):** use `SKIP_WEB=1 MODE=tui`.
- **`venv` missing:** install distro Python venv package (e.g. `python3-venv`) or re-run setup scripts.
- **Port in use / unreachable:** change `PORT`, bind correct `HOST`, allow firewall, verify LAN IP/proxy.
- **DB/permission issues:** ensure writable data dir (`PM_DATA_DIR`/defaults), single writer, `--workers 1` for web.
- **Update apply blocked:** commit/stash/discard local changes or resolve non-fast-forward state manually.

See `docs/TROUBLESHOOTING.md`.

## More documentation

- `assistant.md` — assistant capabilities, grounded/read-only behavior, evolution and plan.
- `docs/PRODUCTION_ROADMAP.md` — phased P0–P3 production hardening/operations roadmap and checklist.
- `docs/DEPLOYMENT.md` — local/LAN/remote deployment, reverse proxy + HTTPS, systemd/Docker guidance.
- `docs/SECURITY.md` — hardening checklist, secrets/cookies, authz/unlock/CSRF, printer/file/data handling.
- `docs/BACKUP_RESTORE.md` — backup, verify, restore, DR, data preservation.
- `docs/UPDATES.md` — release/update/rollback guidance.
- `docs/TROUBLESHOOTING.md` — common issues and diagnostics.
- `docs/GO_LIVE_CHECKLIST.md` — operational go-live steps (monitoring, domain/TLS, backups, pilot, etc.).
- `docs/web_authz_matrix.md` — web route-group authz/CSRF/unlock reference (authoritative for `ui/web`).
