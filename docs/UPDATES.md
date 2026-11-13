# Updates, Releases & Rollback

This guide covers how to update a git-based Property Manager installation safely, what is automated, and how to roll back operationally.

## 1) Update model (current implementation)

- **Primary mechanism (git installs):** in-app **Settings → App updates** (Web & TUI)
  - **Check for updates** (fetch/compare as implemented)
  - View **changelog/commit details** before applying
  - **Apply/Update** via **git fast-forward** (ff-only)
- **Manual mechanism:** `git pull --ff-only` in the install directory, then refresh dependencies/setup if needed.
- **Runtime data preserved:** DB, exports, invoices, and configured data directory (`PM_DATA_DIR`/defaults) are **not modified** by source updates.
- **Migrations:** DB migrations run on application startup and are additive where applicable.
- **Restart required:** after applying an update, **restart the TUI/app/web server** to load the new code.
- **Web constraint:** always run with **`--workers 1`**.

## 2) Pre-update checklist (operational)

- [ ] Confirm installation is a **git checkout** on the intended branch/remote.
- [ ] Ensure working tree is clean (no local source changes) if using in-app apply.
- [ ] Create and **verify** a backup (`Settings → Backup now` or `scripts/backup_runtime.py --backup` / `--verify`).
- [ ] Review changelog/commit details and assess impact (schema/startup behavior, config/env, operational procedures).
- [ ] Plan a maintenance window/restart and user notification if needed.
- [ ] Ensure you can roll back (known prior commit/tag and verified backup).

## 3) In-app updates (automated for clean ff-only cases)

### Web & TUI
1. Open **Settings → App updates**.
2. Click **Check for updates** and review changelog/commit details.
3. If available and applicable, click **Apply/Update**.
4. **Restart** the app/server.

### Requirements and limits
- **Clean working tree required:** commit, stash, or discard local changes before apply.
- **Fast-forward only:** diverged/local-ahead/non-ff histories cannot be auto-applied safely; resolve manually with git.
- **Dependency changes:** in-app apply updates source; if Python dependencies changed, re-run setup to refresh the venv (see below).
- **Non-git installs:** in-app git update flow is not applicable; update by replacing source per your packaging/deployment method, then refresh deps and restart.

## 4) Manual update (CLI)

```bash
cd <install-dir>
# optional but recommended: backup first
python3 scripts/backup_runtime.py --backup

# update source (ff-only)
git status --porcelain   # should be empty for safe ff-only workflow
git pull --ff-only

# refresh dependencies/environment if needed
# Unix:
MODE=<auto|tui|web|both> ACTION=setup sh scripts/setup.sh
# Windows:
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -Action setup

# restart app/server (web must use --workers 1)
```

Alternative helpers:
- Unix: re-run `scripts/install.sh` (designed to install/update/launch while preserving runtime data).
- Web convenience: `scripts/update_and_run_web.sh` (ff-only pull if git repo, then runs uvicorn with `--workers 1`; ensure venv/deps are healthy).

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

## 8) Automated vs operational/manual

- **Automated/tooling:** in-app update check/changelog, ff-only apply, startup migrations, backup/verify helpers, setup scripts for dependency refresh.
- **Operational/manual:** pre-update backup/verification, maintenance window and restart, resolving local changes or non-ff histories, dependency/system remediation, post-update validation, rollback decisions/execution (including DB restore when required), and release communication/sign-off.
