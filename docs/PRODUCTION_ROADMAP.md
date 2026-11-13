# Production Roadmap (Phased)

Concise, factual roadmap/checklist for hardening and operating this repository in production-like environments. Based on current implementation and operational audit findings. Items marked **implemented/tooling** exist in-app/CLI; others are **operational/manual**.

## P0 — Go-live safety baseline (required)

- [ ] **Runtime constraints:** run web with `--workers 1` only (SQLite/shared state); use a single writer and writable data directory (`PM_DATA_DIR` or defaults) with correct permissions.
- [ ] **Secrets:** set strong `PM_WEB_SECRET_KEY` for any non-local/shared deployment (required for non-loopback use unless explicit local/dev insecure override).
- [ ] **Transport/network:** bind `127.0.0.1` for local-only use; for LAN/remote access use HTTPS reverse proxy or private network (VPN/Tailscale/SSH tunnel). Do not expose plain HTTP publicly. Restrict firewall exposure.
- [ ] **Health/readiness:** wire and verify `GET /health` and `/healthz` (liveness/basic health with readiness summary) and `GET /readyz` (runtime readiness, including data-dir writability checks).
- [ ] **Backups/restore readiness:** create and verify an initial backup before go-live and before any upgrade/migration/risky operation; document manual offline restore procedure and validate with a restore drill in a non-production copy.
- [ ] **Access controls:** confirm CSRF is enforced centrally in server middleware for state-changing requests and that sensitive/admin mutations remain unlock-gated per `docs/web_authz_matrix.md` and `docs/SECURITY.md`.
- [ ] **Service operation:** run under least-privilege service user with restart policy/autostart configured; document restart-after-update procedure.

## P1 — Operational reliability & maintenance

- [ ] **Update discipline:** use in-app git fast-forward updates or `git pull --ff-only` on clean checkouts; take/verify backup before updates/migrations; refresh dependencies when needed; **restart app/server after apply**.
- [ ] **Rollback/readiness:** record deployed version/tag/commit and keep verified pre-update backups; understand schema/startup compatibility and manual offline restore path if rollback requires data restore.
- [ ] **Backup operations:** schedule regular automated/operator backups, verification, retention, and offsite/off-host encrypted copies per RPO/RTO.
- [ ] **Integrity/doctor checks:** establish routine `python -m app_core.doctor` / in-app integrity checks and post-restore/post-update verification steps.
- [ ] **Environment hardening:** standardize `PM_DATA_DIR`, permissions, log locations, and service supervision (systemd/launchd/NSSM/Task Scheduler/supervisor/Docker with `--workers 1`).

## P2 — Monitoring, security operations, and resilience

- [ ] **Monitoring/alerting:** alert on probe failures/degraded readiness, service downtime, 5xx/error spikes, disk/data-dir issues, certificate expiry, and backup/verify failures.
- [ ] **Security operations:** secret management/rotation, TLS/certificate lifecycle, access control to hosts/data/exports/invoices/backups/printers, log protection/review, and incident response runbooks.
- [ ] **DR maturity:** define RPO/RTO, maintain documented rebuild steps, test full restore drills periodically, and validate critical workflows after recovery.
- [ ] **Change control:** maintenance windows, release/version tracking (including pinned refs/tags where reproducibility is required), and operator training on unlock/CSRF/restore procedures.

## P3 — Continuous improvement (as needed)

- [ ] **Documentation upkeep:** keep README and `docs/*` aligned with implementation (authz/CSRF enforcement, probes, update/restore behavior, platform constraints).
- [ ] **Platform/dependency resilience:** validate constrained-environment fallbacks (e.g. TUI-only when web deps fail), dependency refresh practices, and reproducible installs.
- [ ] **Audit and refinement:** periodically review route/authz matrix, backup/restore drill outcomes, monitoring coverage, and operational pain points; prioritize fixes that reduce data-loss and availability risk.

## References

- `README.md`, `docs/DEPLOYMENT.md`, `docs/SECURITY.md`, `docs/web_authz_matrix.md`
- `docs/BACKUP_RESTORE.md`, `docs/UPDATES.md`, `docs/TROUBLESHOOTING.md`, `docs/GO_LIVE_CHECKLIST.md`
- Tooling/endpoints: in-app **Settings → App updates/Backup now**, `python -m app_core.doctor`, `scripts/backup_runtime.*`, `GET /health`, `/healthz`, `/readyz`
