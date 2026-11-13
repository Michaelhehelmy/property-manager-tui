# Backup, Verify, Restore & Disaster Recovery

This runbook covers **data preservation**, backup creation/verification, and **manual offline restore** for Property Manager.

## 1) Data locations and what to protect

Runtime data is preserved across source/app updates and must be included in backup/DR planning:

- **Database (SQLite):** primary application state.
- **Exports / generated artifacts:** reports/exports as stored by the app.
- **Invoices / receipts / related files:** generated documents/assets as stored by the app.
- **Configured data directory:** `PM_DATA_DIR` if set; otherwise app defaults/legacy locations managed by `app_core.paths` (commonly `data/`, and/or legacy `property_manager.db`, `exports/`, `invoices/` where applicable).

**Operational guidance:** standardize on an explicit `PM_DATA_DIR` for deployments, document its path, and ensure the service user has least-privilege read/write access.

## 2) Backup strategy (recommended)

- **Frequency:** at least daily; increase for high-transaction environments (e.g. periodic during day) based on RPO.
- **Before risky operations:** always take and verify a backup before upgrades/migrations, updates/applies that include migrations risk, bulk imports/seed applies, restores, or maintenance.
- **Retention:** keep multiple restore points (e.g. daily + weekly + monthly) per policy.
- **Offsite/off-host:** copy verified backups to separate storage (different disk/host/location) with encryption and access controls.
- **Integrity:** verify every backup artifact (automated helper available) and periodically perform **restore drills** (manual offline restore in a non-production/staging copy) to validate procedures.

## 3) Create backups (automated/helpers)

### 3.1 In-app (Web/TUI)
- **Settings → Backup now** creates a database backup in the app exports/data area (subject to unlock/CSRF as applicable in Web).
- Suitable for operator-triggered backups; still follow verification and offsite copy practices.

### 3.2 CLI / scripts (safe, non-destructive)

```bash
# Create backup (default location chosen by app/doctor logic)
python3 scripts/backup_runtime.py --backup
PYTHONPATH=src python -m app_core.doctor --backup

# Create backup to explicit path
python3 scripts/backup_runtime.py --backup --to /path/to/backup.db
PYTHONPATH=src python -m app_core.doctor --backup --backup-to /path/to/backup.db

# Verify a backup/candidate DB (read-only)
python3 scripts/backup_runtime.py --verify /path/to/backup.db
PYTHONPATH=src python -m app_core.doctor --verify-backup /path/to/backup.db

# Health/doctor report (optionally JSON / update check)
python3 scripts/backup_runtime.py --doctor --json
PYTHONPATH=src python -m app_core.doctor --json --check-updates
```

Wrappers:
- Unix: `./scripts/backup_runtime.sh [--backup|--verify <path>|--doctor ...]`
- Windows CMD: `scripts\backup_runtime.cmd ...`
- Windows PowerShell: `powershell -ExecutionPolicy Bypass -File scripts\backup_runtime.ps1 --backup`

**Behavior:** helpers create/verify SQLite backups without deleting runtime data. Always verify before relying on a backup for restore.

## 4) Verify backups

- Use `--verify` / `--verify-backup` on the backup file (read-only integrity/schema checks as implemented).
- Treat verification failures as **do not restore**; create a new backup and investigate storage/media/app health.
- For higher assurance, perform periodic test restores in an isolated/staging copy of the environment and run `python -m app_core.doctor --json` plus application smoke tests.

## 5) Restore (manual, offline) — required procedure

There is **no destructive one-click in-app restore**. Restores must be deliberate and offline.

### 5.1 Preconditions
- Identify correct target environment and active data directory (`PM_DATA_DIR` or defaults).
- Select a **verified** backup matching the intended restore point.
- Have operator access to stop/start the service/app and to the filesystem.
- Communicate downtime/impact to users.

### 5.2 Steps
1. **Stop the application/server** (exit TUI; stop uvicorn/service manager). Ensure no writers are active. Web must run with `--workers 1` when started.
2. **Verify the candidate backup**: `scripts/backup_runtime.* --verify <backup.db>` or `python -m app_core.doctor --verify-backup <backup.db>`. Do not proceed if verification fails.
3. **Back up current state** (safety copy): copy the current active DB and, if applicable, relevant `exports/`/`invoices/`/data-dir contents before replacement.
4. **Restore files**: replace the active database file in the correct data directory with the verified backup. Restore related `exports/`/`invoices/`/artifacts if required for consistency with that restore point.
5. **Set permissions/ownership** so the app/service user can read/write restored files and directories.
6. **Start the application/server**. DB migrations run on startup (additive where applicable).
7. **Verify after restore**:
   - `PYTHONPATH=src python -m app_core.doctor --json` (and/or `--strict` as appropriate)
   - in-app integrity/health checks
   - web probes `GET /health`, `GET /healthz` (if web)
   - targeted smoke tests (login/unlock flows, key records, reports, recent transactions)

### 5.3 Roll-forward / partial considerations (operational)
- Restoring an older DB may lose transactions after the backup point (RPO). Plan accordingly.
- If exports/invoices are out of sync with restored DB, reconcile from retained artifacts/backups.
- Document any manual corrections and retain pre-restore safety copies until validation is complete.

## 6) Data preservation across updates (not a substitute for backups)

- **Preserved automatically by update workflows:** DB, exports, invoices, configured data dir (`PM_DATA_DIR`/defaults) are not modified by source updates (in-app git fast-forward or `git pull --ff-only`).
- **Still required:** backups before updates/migrations and independent offsite copies. Updates can succeed while data later becomes corrupted or accidentally deleted by operational actions.

## 7) Disaster recovery (DR) guidance

- **Define objectives:** RPO (acceptable data loss) and RTO (acceptable downtime) per business needs.
- **DR assets:** verified backups + documented restore procedure + known install/bootstrap steps + recorded secrets/config locations (stored securely, separately as appropriate).
- **Recovery environment:** ability to provision a host with Python/runtime (or compatible environment), restore `PM_DATA_DIR` contents, configure `PM_WEB_SECRET_KEY`/TLS/networking, and start with `--workers 1`.
- **Drills:** test full rebuild-and-restore periodically; validate integrity, permissions, and critical workflows.
- **Security:** protect backups in transit/at rest; restrict access; rotate secrets if backup media/hosts are compromised.

## 8) Automated vs operational/manual

- **Automated/helpers:** in-app/CLI backup creation, backup verification, doctor health checks, startup migrations, data preservation during source updates.
- **Operational/manual:** backup scheduling, retention, offsite replication/encryption, access control, restore execution, environment rebuild, DNS/TLS/network cutover, DR drills, and business validation/sign-off.
