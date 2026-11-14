# Updates, Releases & Rollback

This guide covers how to update a git-based Property Manager installation safely, what is automated, and how to roll back operationally.

## 1) Update model (current implementation)

- **Primary mechanism (git installs):** in-app **Settings → App updates** (Web & TUI)
  - **Check for updates** (fetch/compare as implemented)
  - View **changelog/commit details** before applying
  - **Apply/Update** via **git fast-forward** (ff-only)
- **Manual mechanism:** `git pull --ff-only` in the install directory, then refresh dependencies/setup if needed.
- **Pinned/reproducible updates:** to move to a specific tag/commit/branch, use `GIT_REF=<tag|commit|branch>` with setup/install scripts (Unix) or `-GitRef` / `$env:GIT_REF` (Windows), and prefer fetching bootstrap scripts from the same raw ref for reproducibility (e.g. `.../raw/<ref>/scripts/install.sh`).
- **Runtime data preserved:** DB, exports, invoices, and configured data directory (`PM_DATA_DIR`/defaults) are **not modified** by source updates.
- **Migrations:** DB migrations run on application startup and are additive where applicable.
- **Restart required:** after applying an update, **restart the TUI/app/web server** to load the new code.
- **Web constraint:** always run with **`--workers 1`**. Set `PM_WEB_SECRET_KEY` and use HTTPS reverse proxy/VPN for shared/non-local deployments.

## 2) Pre-update checklist (operational)

- [ ] Confirm installation is a **git checkout** on the intended branch/remote.
- [ ] Ensure working tree is clean (no local source changes) if using in-app apply.
- [ ] Create and **verify** a backup before the update/migration (`Settings → Backup now` or `scripts/backup_runtime.py --backup` / `--verify`).
- [ ] Review changelog/commit details and assess impact (schema/startup behavior, config/env, operational procedures).
- [ ] Plan a maintenance window, user notification if needed, and **restart after apply** (required to load new code).
- [ ] Ensure you can roll back (known prior commit/tag and verified backup) and that manual offline restore procedures/drills are understood. Keep web on `--workers 1`, set strong `PM_WEB_SECRET_KEY`, and use HTTPS reverse proxy/private networking for shared/non-local access.

## 3) In-app updates (automated for clean ff-only cases)

### Web & TUI
1. Open **Settings → Updates & Health → App updates**.
2. Click **Check for updates** (fetch/compare and show changelog/commit details).
3. If an update is available and conditions allow, click **Update now / Apply**. (The apply action is shown only when an update is available; local changes or non-ff states may block apply.)
4. **Restart** the TUI/app/web server to load the new version.

### Auto-check and update notifications (polling-based)
- **Built-in polling checks:** the app performs **best-effort automatic update checks when online** (startup/periodic, cached/rate-limited) and shows notifications when a new update/release is detected:
  - **Web UI:** banner/notice when an update is available (with links to **App updates** and/or release/details).
  - **TUI:** startup notification (toast) when an update is available.
  - **Git installs:** checks remote commits (fast-forward availability). **Native/packaged installs:** checks latest GitHub Release/tag (best-effort version comparison).
- **Settings:** `auto_check_updates` (default enabled) and `auto_update_check_interval_hours` control polling behavior. Checks are read-only and use network access to GitHub; failures are handled gracefully (no crash).
- **Important:** notifications are **not push notifications**; apps detect updates when they run and can reach GitHub. Native/packaged apps still require downloading/installing the new release artifact (or a native-aware updater) — `git push` alone does not update an installed native binary.
- **Auto-apply:** not enabled by default (risky). Prefer semi-auto (notify, manual apply with backup/maintenance window). For git installs, apply via **Settings → App updates** (ff-only, clean tree) then restart; for native builds, install the new release asset. If automating apply, always include verified backup, dependency refresh if needed, controlled restart, and health/doctor verification with rollback plan.

### Requirements and limits
- **Clean working tree required:** commit, stash, or discard local changes before apply.
- **Fast-forward only:** diverged/local-ahead/non-ff histories cannot be auto-applied safely; resolve manually with git.
- **Dependency changes:** in-app apply updates source; if Python dependencies changed, re-run setup to refresh the venv (see below).
- **Non-git installs:** in-app git update flow is not applicable; update by replacing source per your packaging/deployment method, then refresh deps and restart.

## 4) First-time update / install-and-update by platform (then use UI)

These flows are for **git-based installs** (recommended). Runtime data (**DB, exports, invoices, `PM_DATA_DIR`**) is preserved across source updates. Always restart after apply/update and keep web on **`--workers 1`**.

### 4.1 Windows (10/11, Server) — first time, then UI
**Prereqs:** Python 3.10+ and Git (bootstrap scripts can try `winget` if available), PowerShell, network access.

1. **First-time install/update (recommended, idempotent):** rerun anytime to update.
   ```powershell
   irm https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install_windows.ps1 | iex
   ```
   Examples:
   ```powershell
   # Setup/update and launch
   $env:ACTION='deploy'; $env:MODE='auto'; irm https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install_windows.ps1 | iex

   # Web UI (localhost, SQLite-safe) — set strong secret for non-local/shared use
   $env:ACTION='deploy'; $env:MODE='web'; $env:HOST='127.0.0.1'; $env:PORT='8000'
   $env:PM_WEB_SECRET_KEY='(long-random-secret)'
   irm https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install_windows.ps1 | iex

   # Pinned ref for reproducibility
   $env:GIT_REF='vX.Y.Z'; irm https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install_windows.ps1 | iex

   # Built-in recovery for dirty/stuck git checkout (e.g. reset/checkout/pull errors)
   $env:REPAIR='1'; irm https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install_windows.ps1 | iex
   ```
   Defaults: installs/updates to `%USERPROFILE%\property-manager-tui` (override `INSTALL_DIR`), refreshes `.venv`/deps, and runs per mode. CMD bootstrap: download/run `scripts/install_windows.cmd`.
2. **From existing local checkout (manual):**
   ```powershell
   # recommended: backup first (Settings -> Backup now or scripts/backup_runtime.py --backup)
   git fetch --all --tags --prune
   git pull --ff-only   # commit/stash/discard local changes first
   powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1 -Action setup -Mode auto
   ```
3. **Restart** app/server. Verify: `PYTHONPATH=src python -m app_core.doctor` and web `/healthz`, `/readyz` (web always `--workers 1`; use strong `PM_WEB_SECRET_KEY` and HTTPS reverse proxy/private network for LAN/remote/shared access).
4. **Later updates from UI:** **Settings → Updates & Health → App updates** → **Check for updates** → **Update now** appears **only when an update is available** → apply (clean ff-only) → **restart**. Refresh deps via setup if dependencies changed.

### 4.2 Termux (Android) — TUI and full Web (first time, then UI)
**Prereqs:** Termux packages, network access. On constrained devices/ABIs web deps may be unavailable (`pydantic-core`/Rust wheels) — use TUI-only fallback.
```bash
pkg update -y && pkg upgrade -y
pkg install -y git python curl ca-certificates
```

1. **TUI (most reliable):**
   ```bash
   curl -fsSL https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install.sh | SKIP_WEB=1 MODE=tui sh
   ```
2. **Full Web UI (SQLite-safe, on-device or LAN):**
   ```bash
   # Localhost
   curl -fsSL https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install.sh \
     | MODE=web HOST=127.0.0.1 PORT=8000 PM_WEB_SECRET_KEY="$(openssl rand -hex 32)" sh

   # LAN (trusted/private network only)
   curl -fsSL https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install.sh \
     | MODE=web HOST=0.0.0.0 PORT=8000 PM_WEB_SECRET_KEY="$(openssl rand -hex 32)" sh
   ```
   Always **`--workers 1`**. Set strong `PM_WEB_SECRET_KEY` for non-local/shared use and prefer **HTTPS** (reverse proxy/VPN/tunnel) for shared/remote access.
3. **Run (if not auto-launched):**
   ```bash
   cd ~/property-manager-tui
   # TUI
   PYTHONPATH=src .venv/bin/python launcher.py --tui
   # Web
   export PM_WEB_SECRET_KEY='(long-random-secret)'
   PYTHONPATH=src .venv/bin/python -m uvicorn ui.web.server:app --host 127.0.0.1 --port 8000 --workers 1
   ```
4. **Manual update from existing checkout:** backup → `git fetch --all --tags --prune && git pull --ff-only` → `ACTION=setup MODE=auto sh scripts/setup.sh` (or `SKIP_WEB=1 MODE=tui`) → restart.
5. **Later updates from UI:** same as Windows/Web/TUI — **Settings → Updates & Health → App updates** → **Check for updates** → **Update now** only when available → apply → **restart**; refresh deps if needed. Re-running the install one-liner is also a safe data-preserving way to update/refresh on Termux.

### 4.3 Manual update (CLI, any git install)
```bash
cd <install-dir>
# optional but recommended: backup first
python3 scripts/backup_runtime.py --backup

# update source (ff-only)
git status --porcelain   # should be empty for safe ff-only workflow
git pull --ff-only

# refresh dependencies/environment if needed
# Unix/Termux:
MODE=<auto|tui|web|both> ACTION=setup sh scripts/setup.sh
# Windows:
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -Action setup

# restart app/server (web must use --workers 1)
```

Alternative helpers:
- Unix/Termux: re-run `scripts/install.sh` (idempotent install/update/launch, preserves runtime data).
- Web convenience: `scripts/update_and_run_web.sh` (ff-only pull if git repo, then runs uvicorn with `--workers 1`; ensure venv/deps are healthy). Use on a schedule/supervision only with backup/restart/health safeguards.

## 5) Dependency and environment refresh

Source updates may add/change Python packages or system requirements. If startup fails after update or changelog indicates dependency changes:
- **Automated-ish:** re-run setup scripts (`ACTION=setup` / `-Action setup`) to recreate/refresh venv deps.
- **Manual:** activate venv and `pip install -r requirements.txt` (and `requirements-web.txt` for web), or use `pip install -e '.[web,dev]'` if using packaging workflow.
- Re-verify with doctor/health checks and basic smoke tests.

## 6) Post-update verification

- Restart completed (new process running updated code).
- `PYTHONPATH=src python -m app_core.doctor --json` (optionally `--check-updates`, `--strict`).
- Web: `GET /health`, `GET /healthz` return healthy responses.
- Functional smoke tests: login/unlock as applicable, core workflows (reservations/POS/inventory/reports) relevant to your operation.
- Confirm runtime data intact (expected records, exports/invoices accessible).

## 7) Rollback guidance

Rollback may involve **code**, **dependencies**, and potentially **data/state** considerations. Handle carefully.

### 7.1 Code rollback (git)
1. **Stop** the app/server.
2. Ensure you have a **verified backup** of data (and keep a copy of current state).
3. Identify last known-good revision (commit/tag): e.g. `git log --oneline`, release notes.
4. Restore code to that revision (example): `git checkout <commit-or-tag>` or `git reset --hard <commit>` on a controlled deployment branch as appropriate for your workflow.
   - Prefer deterministic, reviewed rollback steps in production; avoid ad-hoc force operations without understanding impact.
5. Refresh dependencies to match that revision (`ACTION=setup` / reinstall requirements) because newer deps may be incompatible with older code.
6. **Start** the app/server and verify (doctor, health, smoke tests).

### 7.2 Data/schema considerations
- **Startup migrations are additive where applicable.** If an update applied schema/data migrations, rolling back **code only** may leave the DB at a newer schema level; older code may or may not be compatible.
- If incompatibility is suspected or observed, restore a **verified pre-update backup** taken before the failed update (manual offline restore per `docs/BACKUP_RESTORE.md`) to align data with the rolled-back code version.
- Always validate integrity and business-critical data after rollback/restore.

### 7.3 When rollback is not straightforward
- Diverged git history, local patches, or hotfixes: preserve changes, coordinate with maintainers, and create a controlled recovery plan.
- If data corruption or security incident is involved: prioritize containment, verified restore, secret rotation, and incident procedures over rapid in-place rollback.

## 8) Release, version, and changelog conventions (guidance)

- **Version source:** package/runtime version is tracked in `pyproject.toml` (`[project].version`). Bump it for releasable changes according to your chosen scheme (e.g. SemVer-like `MAJOR.MINOR.PATCH`) and keep release tags/commits consistent with that version.
- **Changelog / release notes:** record user/operator-visible changes (features, fixes, migrations, security, operational impacts, required env/dependency changes) in release notes or a changelog artifact maintained with the release/tag. In-app update flow surfaces git changelog/commit details when available; ensure commits/tags are meaningful.
- **Tagging for reproducibility:** create annotated tags for releases (e.g. `vX.Y.Z`) from a verified commit on the intended branch. Prefer installing/updating production or reproducible environments via pinned `GIT_REF=<tag-or-commit>` and raw bootstrap scripts at the same ref.
- **Pre-release checklist (recommended):** run tests/checks appropriate to scope, verify `--workers 1` web behavior and migrations on a copy, update docs if operational behavior changes, create/verify backup guidance as needed, bump version, publish tag/release notes, and document any manual upgrade/rollback steps.
- **Compatibility notes:** call out dependency/toolchain constraints (including constrained-environment web build issues such as `pydantic-core`/Rust wheels), TUI-only fallback guidance, and any schema/startup considerations relevant to rollback.

## 9) Automated vs operational/manual

- **Automated/tooling:** in-app update check/changelog, ff-only apply, startup migrations, backup/verify helpers, setup scripts for dependency refresh and pinned-ref checkout support.
- **Operational/manual:** pre-update backup/verification, maintenance window and restart, resolving local changes or non-ff histories, dependency/system remediation (including constrained-environment fallbacks), post-update validation, rollback decisions/execution (including DB restore when required), release tagging/version/changelog publication, and release communication/sign-off. Keep `--workers 1`, strong `PM_WEB_SECRET_KEY`, and HTTPS/private networking for shared web access. Runtime data (DB/exports/invoices/`PM_DATA_DIR`) remains preserved.
