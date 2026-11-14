# Troubleshooting Guide

Practical diagnostics for common installation, runtime, web, update, backup, and data issues. Start with **doctor/health checks** before making destructive changes.

## 1) First-response diagnostics (safe/read-oriented)

```bash
# App/DB/integrity/backup/update hints (read-oriented; --backup creates a safe copy)
PYTHONPATH=src python -m app_core.doctor --json
PYTHONPATH=src python -m app_core.doctor --check-updates
python3 scripts/backup_runtime.py --doctor --json

# Web liveness/readiness (if server running)
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz

# Git/install state (if git-based)
git -C <install-dir> status --porcelain --branch
git -C <install-dir> rev-parse --short HEAD
```

Collect: OS/platform, install method, command used, full error output, whether TUI/web, bind host/port, and `PM_DATA_DIR` if customized.

## 2) Install & environment issues

### Python/venv/pip problems
- **`python3 -m venv` fails or missing:** install venv package for distro (e.g. Debian/Ubuntu `python3-venv`), or re-run setup scripts.
- **Wrong Python version:** requires **Python 3.10+**. Ensure venv uses correct interpreter (`python3 --version`).
- **Permission errors installing packages:** use project `.venv`; avoid system-wide installs unless intentional.
- **Windows PATH / Store alias issues:** disable App Execution Aliases for `python.exe`/`python3.exe` if they shadow real Python; reopen shell; re-run installer/setup. Use `py -3` where available.

### Web dependency build failures
- **Symptoms:** `pip install -r requirements-web.txt` fails (common on constrained envs like iSH or missing compilers/rust for some deps).
- **Mitigations:** use **TUI mode** (`SKIP_WEB=1 MODE=tui`), install system build deps if supported, or run web on a more capable host and access via browser.
- **Check installed deps:** `python -c "import fastapi, uvicorn, jinja2; import multipart"`.

### Git/bootstrap issues
- **`git` missing or clone fails:** install git/CA certificates and check network/proxy/firewall; bootstrap scripts attempt install on supported platforms.
- **SSL/cert issues on minimal images:** install/update CA certificates package.

## 3) TUI issues

- **TUI fails to start:** ensure runtime deps installed (`requirements.txt` / textual), run from install root, and `PYTHONPATH` includes `src` when running directly (launcher/main handle typical cases).
- **Terminal rendering/input issues (constrained terminals/iSH):** prefer TUI mode there; resize terminal, use compatible font/encoding, or run via SSH from a fuller terminal.

## 4) Web server issues

### Server won't start
- **Port in use:** change `PORT`/`--port` or stop conflicting process.
- **`uvicorn`/module import errors:** install web deps, activate correct venv, set `PYTHONPATH=src:<repo>` if needed.
- **Invalid host/port:** use valid bind address and port range (1-65535).
- **Missing/weak secret in non-local use:** set strong `PM_WEB_SECRET_KEY` for non-local/shared deployments (required for non-loopback unless explicit local/dev insecure override).

### Cannot access from browser/other devices
- **Local only works:** `127.0.0.1` is loopback-only; bind `0.0.0.0` for LAN (trusted networks only) and use host LAN IP.
- **Firewall/NAT:** allow inbound port on host firewall/router as appropriate.
- **Reverse proxy/HTTPS:** for LAN/remote access use HTTPS reverse proxy or private network (VPN/Tailscale/SSH tunnel). Verify proxy target `127.0.0.1:8000`, forwarded headers, TLS certs, and health endpoints.
- **HTTPS/session issues behind proxy:** ensure correct scheme/forwarding and access via HTTPS in production contexts.

### Web behavior/auth issues
- **403/CSRF/unlock related failures:** state-changing actions require CSRF (centrally enforced in server middleware); sensitive/admin actions require super-password unlock. Ensure forms/clients include required tokens/proofs and session is unlocked where required. See `docs/web_authz_matrix.md` and `docs/SECURITY.md`.
- **Session/secret issues:** set stable strong `PM_WEB_SECRET_KEY` for shared deployments (changing it invalidates signed sessions/cookies).
- **`--workers` misconfiguration:** must be **`--workers 1`** for SQLite; multiple workers can cause locking/corruption/data issues.
- **Health probe interpretation:** `GET /health` and `/healthz` report liveness/basic health (with readiness summary); `GET /readyz` reports runtime readiness (including data-dir writability). Probe failures/degraded state warrant checking logs, data-dir permissions, and config.

## 5) Data, permissions, and integrity

- **DB locked / write errors:** ensure single writer/process pattern, **`--workers 1`** for web, no concurrent external writers, and storage not read-only.
- **Permission denied on DB/exports/invoices/data dir:** fix ownership/permissions for service user; verify `PM_DATA_DIR` path exists and is writable.
- **Data appears missing after move/update:** check `PM_DATA_DIR` vs default/legacy locations; updates preserve runtime data but running against wrong data dir can look like data loss.
- **Integrity warnings/failures:** run `python -m app_core.doctor --json` and in-app integrity checks. **Take/verify a backup before repair attempts.** Restore from verified backup if corruption is confirmed.

## 6) Updates

- **"Update now" not shown:** open **Settings → Updates & Health → App updates**, click **Check for updates** first — apply appears only when an update is available. Ensure network/GitHub access and a git install.
- **No automatic check when online:** auto-check is not enabled by default; checks are manual (UI/doctor) unless you add in-app periodic/startup checks or schedule external checks.
- **In-app apply blocked (local changes):** `git status` and commit/stash/discard local source changes, then retry.
- **Non-fast-forward/diverged history:** resolve manually (`git fetch`, review divergence, merge/rebase/reset as appropriate for your controlled workflow) then `git pull --ff-only` if applicable.
- **Update applied but behavior unchanged:** restart app/server; verify running commit/version and that correct install/venv is launched.
- **Windows installer/update fails with git reset/checkout/pull errors (e.g. `Reset branch 'main'` followed by failure):** usually caused by a dirty/conflicted checkout or diverged/non-ff state. From the install folder (`%USERPROFILE%\property-manager-tui`): run `git status --porcelain --branch`; commit/stash/discard local changes, then `git fetch --all --tags --prune && git checkout main && git pull --ff-only`; re-run setup (`scripts\setup_windows.ps1 -Action setup`) or the latest bootstrap script. If corrupted, back up runtime data (`PM_DATA_DIR`/DB/exports/invoices), reclone/reinstall, and restore data.
- **Dependency/import errors after update:** re-run setup/dependency install for the environment; compare with `requirements*.txt`/`pyproject.toml` (Windows: `scripts\setup_windows.ps1 -Action setup`; Unix/Termux: `ACTION=setup sh scripts/setup.sh`).
- **Termux web update issues:** if web deps fail to build (e.g. `pydantic-core`/Rust), use TUI-only (`SKIP_WEB=1 MODE=tui`) or update on a fuller host; always run web with `--workers 1`.
- **Rollback needed:** stop app, restore known-good code and matching deps; if schema/data incompatible, restore verified pre-update backup (see `docs/UPDATES.md`, `docs/BACKUP_RESTORE.md`).

## 7) Backups & restore

- **Backup creation fails:** check free disk space, permissions on destination/data dir, and DB accessibility (stop writers if needed for consistent operational practice).
- **Verify fails:** do not restore from that file; inspect storage/media and create a fresh verified backup.
- **Restore issues:** restore must be **manual/offline** (app stopped), files placed in correct `PM_DATA_DIR`, permissions fixed, app restarted, and integrity rechecked. Keep pre-restore safety copies.

## 8) Performance and operational issues (basic)

- **Slow responses on low-resource devices:** prefer TUI; reduce concurrent load; keep web single-worker as required; ensure adequate storage I/O.
- **Disk growth:** monitor DB/exports/invoices/backups; apply retention/archival policies operationally (with verified backups before deletions).
- **Service not auto-starting:** verify service manager config, env vars (`PYTHONPATH`, `PM_WEB_SECRET_KEY`, `PM_DATA_DIR`), working directory, executable path, and logs.

## 9) Escalation checklist (before making risky changes)

- [ ] Capture doctor/health output and relevant logs/errors.
- [ ] Create and verify a fresh backup.
- [ ] Avoid deleting or overwriting DB/data files until root cause is understood.
- [ ] Test fixes in a copy/staging environment when possible.
- [ ] For suspected corruption, security incident, or failed restore: follow `docs/BACKUP_RESTORE.md` and `docs/SECURITY.md` incident guidance.
