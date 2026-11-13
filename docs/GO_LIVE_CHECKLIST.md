# Go-Live Checklist (Operational)

Use this checklist to move Property Manager from setup/testing to production-like operation. Items marked **(Automated/tooling available)** have in-app/CLI/script support; others are **operational/manual** and must be owned by operators.

## 1) Scope, ownership, and readiness

- [ ] **Define deployment scope (manual):** site(s), users/roles (operational), devices, TUI vs Web, local/LAN/remote topology.
- [ ] **Assign owners (manual):** system admin, app operator, backup/restore owner, security/contact, escalation path.
- [ ] **Document environment (manual):** host(s), OS, install path, `PM_DATA_DIR`, branch/commit/version, service account, network exposure.
- [ ] **Change/maintenance window plan (manual):** update cadence, restart procedure, rollback contacts.

## 2) Install and configuration validation

- [ ] **Install/verify runtime (automated/manual):** use supported install/setup scripts or manual venv; confirm Python 3.10+ and required deps.
- [ ] **Select mode (manual):** TUI and/or Web per environment capability (TUI preferred on constrained systems like iSH).
- [ ] **Web runtime config (manual):** `--workers 1` enforced, correct `HOST`/`PORT`, `PYTHONPATH`/working directory set.
- [ ] **Set secrets/env (manual, required):** strong `PM_WEB_SECRET_KEY` for any non-local/shared deployment; set `PM_DATA_DIR` if using non-default location.
- [ ] **Service/autostart (manual):** systemd/launchd/NSSM/Task Scheduler/supervisor/Docker configured with restart policy and correct env vars.
- [ ] **Permissions (manual):** service user has least-privilege read/write access to data dir; install/source protected from unauthorized modification.

## 3) Security and access control

- [ ] **Transport security (manual):** local-only on `127.0.0.1` or LAN/remote behind **HTTPS** reverse proxy / VPN / Tailscale. No plain HTTP public exposure.
- [ ] **Firewall/network controls (manual):** restrict inbound access to trusted sources/segments; only required ports exposed.
- [ ] **TLS/domain (manual):** domain/DNS configured (if applicable), valid certificates, renewal/monitoring in place at proxy.
- [ ] **CSRF/unlock model understood (manual):** operators trained on unlock requirements for sensitive/admin actions; review `docs/web_authz_matrix.md` and `docs/SECURITY.md`.
- [ ] **Super-password/credentials handling (manual):** secure storage/process for super password and admin workflows; avoid shared accounts where possible by policy.
- [ ] **Printer/file/data handling (manual):** trusted printer targets, protected export/invoice/backup storage, controlled access to downloads/uploads.

## 4) Data, backups, and disaster recovery

- [ ] **Initial verified backup (automated/tooling):** create and verify backup before go-live (`Settings → Backup now` or `scripts/backup_runtime.py --backup` + `--verify`, or doctor equivalents).
- [ ] **Backup schedule & retention (manual):** define frequency (RPO), retention, and offsite/off-host encrypted copies.
- [ ] **Restore runbook validated (manual + tooling):** perform or tabletop a **manual offline restore** drill in non-production copy; verify with doctor/integrity checks and smoke tests.
- [ ] **Data preservation confirmed (automated behavior):** understand updates preserve DB/exports/invoices/data dir; backups still mandatory.

## 5) Integrity, health, and monitoring

- [ ] **Baseline health/integrity (automated/tooling):** `PYTHONPATH=src python -m app_core.doctor --json` passes (review warnings); in-app integrity checks reviewed.
- [ ] **Web probes configured (automated endpoints, manual wiring):** monitor `GET /health` and/or `GET /healthz` via proxy/service monitor/uptime checks.
- [ ] **Operational monitoring (manual):** alerts for downtime, 5xx/error spikes, disk space, CPU/memory (as relevant), certificate expiry, and backup job success/failure.
- [ ] **Log access/review (manual):** identify app/proxy/system log locations and review process; protect logs and avoid secret leakage.

## 6) Updates and maintenance

- [ ] **Update procedure agreed (manual + tooling):** in-app **Settings → App updates** (git fast-forward) or `git pull --ff-only`; require clean working tree for auto-apply.
- [ ] **Restart procedure documented (manual):** restart required after apply; service restart commands/runbook verified.
- [ ] **Dependency refresh plan (manual/tooling):** re-run setup (`ACTION=setup` / `-Action setup`) when dependencies change.
- [ ] **Rollback plan (manual):** known-good commit/tag identified, verified pre-update backup available, and restore path understood (including schema compatibility considerations).

## 7) Functional pilot / acceptance

- [ ] **Seed/master data readiness (manual + app flows):** rooms/rates/inventory/meals/activities and settings reviewed; sensitive seed/import actions performed with required unlock/proofs and prior backups.
- [ ] **Pilot workflows (manual):** test critical paths in production-like environment:
  - [ ] Reservation create/check-in/checkout/payment/invoice flows
  - [ ] POS/walk-in sales, inventory movements, batch/lot/FEFO/traceability as used
  - [ ] Activities/bookings posting to folio where applicable
  - [ ] Reports/exports/downloads and protected file access
  - [ ] Assistant read-only queries (ops, integrity, updates, traceability) as needed
- [ ] **Receipt/invoice/label outputs (manual):** verify formats, printer destinations (if used), and traceability fields where expected.
- [ ] **User acceptance/sign-off (manual):** operators confirm workflows, data correctness, and support readiness.

## 8) Go-live and post-go-live

- [ ] **Final pre-cutover backup & verify (automated/tooling).**
- [ ] **Go-live communication (manual):** announce start, known limitations, support contacts, and downtime/update windows.
- [ ] **Hypercare period (manual):** heightened monitoring, rapid issue triage, and daily integrity/backup checks initially.
- [ ] **Post-go-live review (manual):** capture issues, tune alerts/backup cadence, document lessons learned, and schedule next update/restore drill.

## 9) Automated vs operational summary

- **Automated/tooling available:** install/setup scripts, in-app/CLI updates (ff-only), health probes, doctor/integrity checks, backup/verify helpers, startup migrations.
- **Operational/manual (must be planned and executed):** topology/TLS/domain/DNS, firewall/VPN, secrets management, service hardening/autostart, monitoring/alerting, backup scheduling/offsite/retention, restore/DR drills, pilot/UAT, training, and governance/sign-off.
