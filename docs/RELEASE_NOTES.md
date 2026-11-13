# Release Notes (commit-readiness wrap-up)

> Unreleased working-tree snapshot (no commit created). Summarizes the current diff scope for release/commit readiness review.

## Notable fixes and hardening

- **Web runtime hardening and observability:** centralized CSRF enforcement in server middleware for unsafe methods (with cached form handling and explicit rejection logging), request logging with duration/unlock context (excluding health probes), and readiness/liveness endpoints refined (`/health`, `/healthz`, `/readyz`) with runtime checks including DB/data/export/invoice directory writability and DB initialization state.
- **Auth/session/proof handling:** per-browser signed-session unlock behavior and CSRF/session hardening retained; web-layer password-proof UX clarified so sensitive/admin actions that require backend service proofs continue to pass explicit proofs, while unlock-gated actions whose backend APIs do not take a service password no longer imply a misleading extra proof prompt in the UI. See `docs/web_authz_matrix.md`.
- **P0/core correctness fixes:** batch sale allocation now avoids satisfying shortfall via aggregate/negative overflow when expiry-blocked (safety policy preserved), dashboard day snapshot/service helpers and DB readiness helper (`is_initialized`) added for clearer operational state, and assistant runtime config/status surfaced via public helper.
- **UI and operator workflow improvements:** web templates/CSS/routes adjusted for safer rendering, clearer settings/accounting/POS/reservations/reports/assistant flows, backup/update messaging, and traceability/batch/operator context where applicable.
- **Docs and test coverage:** operational/deployment/security/updates/backup/troubleshooting docs and authz matrix refreshed; `pytest.ini` added and automated tests expanded/updated across assistant, dashboards, DB integrity/idempotency, recipes, TUI modals, and web rendering/reservation/routes coverage.

## Operational notes

- **Web runtime:** always run with `--workers 1` (SQLite/shared state). Set a strong `PM_WEB_SECRET_KEY` for any non-local/shared deployment; bind `127.0.0.1` for local-only use or run behind HTTPS reverse proxy/private networking (VPN/Tailscale/SSH tunnel) for LAN/remote access. Do not expose plain HTTP publicly.
- **Health/readiness probes:** monitor `GET /health` and/or `GET /healthz` for liveness/basic health and `GET /readyz` for runtime readiness (including data-dir/DB/export/invoice writability checks). Configure service/proxy monitors accordingly.
- **Backups, restore, and updates:** take and verify backups before go-live and before upgrades/migrations/risky operations (`Settings → Backup now`, `scripts/backup_runtime.py --backup/--verify`, or doctor flows). Runtime data (`PM_DATA_DIR`, DB, exports, invoices) is preserved by source updates, but restores remain **manual/offline** and must be validated by restore drills. For git installs use in-app fast-forward apply or `git pull --ff-only` on a clean tree, refresh deps when needed, and **restart after apply**.
- **Access controls:** CSRF is enforced centrally for state-changing requests; sensitive/admin mutations remain unlock-gated per policy and `docs/web_authz_matrix.md`, with backend service-level proof requirements still enforced where applicable.

## Known remaining risks and follow-ups

- **Single-writer SQLite constraints remain:** web must stay on `--workers 1`; in-process/session/rate-limit assumptions are not distributed and require disciplined deployment, permissions, and monitoring.
- **Operational/manual controls still required:** TLS/proxy/cert lifecycle, firewall/VPN, secret rotation/management, backup scheduling/offsite retention/encryption, restore/DR drills, monitoring/alerting, service supervision, and incident/runbook ownership are not automated.
- **Rollback/schema compatibility:** startup migrations are additive where applicable, but code rollback after migrations may require restoring a verified pre-update backup to align schema/data with older code; validate in a non-production copy.
- **Environment/platform variability:** constrained environments may need TUI-only fallback or dependency/toolchain workarounds (e.g. web wheel/build issues); operators should pin/test releases in their target environment and keep docs/runbooks current.
- **Validation context:** automated tests and docs were updated in this working tree, but no committed CI/test-gate artifacts or coverage report outputs are included here. Run the full recommended validation commands below before commit/release.

## References

- `docs/PRODUCTION_ROADMAP.md`, `docs/GO_LIVE_CHECKLIST.md`, `docs/DEPLOYMENT.md`, `docs/SECURITY.md`, `docs/web_authz_matrix.md`
- `docs/BACKUP_RESTORE.md`, `docs/UPDATES.md`, `docs/TROUBLESHOOTING.md`, `README.md`
- Tooling/endpoints: `python -m app_core.doctor`, `scripts/backup_runtime.py`, in-app **Settings → App updates/Backup now**, `GET /health`, `/healthz`, `/readyz`
