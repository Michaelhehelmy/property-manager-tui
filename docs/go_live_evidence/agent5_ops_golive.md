# Agent 5 Evidence: Deployment, Backup/DR, Observability, and Pilot/Go-Live

## Status
- Phase: Not started (requires deployment profile, security config, and RC tag)

## Prereqs to collect
- Target environment/profile and access (host, OS, network/TLS/proxy/VPN, service user)
- Strong `PM_WEB_SECRET_KEY`, `PM_DATA_DIR`, cookie/TLS settings (Agent 2)
- Pinned RC tag and rollback target (Agent 4)

## Planned tasks
- [ ] Staging/production-like deployment (`--workers 1`, reverse proxy/TLS or VPN, firewall, permissions)
- [ ] Validate install/update/launcher scripts and target OS matrix; printer/label/export devices as in scope
- [ ] Health/readiness and doctor checks (`/health`, `/healthz`, `/readyz`, `python3 -m app_core.doctor --json`)
- [ ] Supervision/restart, log rotation, time sync, disk monitoring
- [ ] Automated backups + off-host storage; `--backup --verify-backup`; full restore drill (RPO/RTO measured)
- [ ] Monitoring/alerts and incident/runbook setup
- [ ] Execute `docs/GO_LIVE_CHECKLIST.md`, pilot/UAT, smoke/reconciliation, hypercare, sign-off

## Evidence/log
- (to be filled with configs sanitized, drill timings/results, checklist outcomes)
