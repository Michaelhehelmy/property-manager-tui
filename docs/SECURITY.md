# Security Guide (Hardening, Authz, CSRF, Data & Files)

This document provides practical security guidance for operating Property Manager (TUI + Web). It distinguishes **implemented controls** from **operational responsibilities**.

## 1) Security posture summary

- **Local-first app** with shared backend (`src/app_core/`) and SQLite datastore.
- **Web UI** (FastAPI/HTMX) is intended for trusted/local/LAN use or controlled remote access behind HTTPS/private networking.
- **Primary web controls:** CSRF protection on state-changing requests, per-browser unlock/session model for sensitive/admin actions, backend-enforced service proofs where required, protected downloads, and constrained runtime behavior (notably `--workers 1`).

> Treat the web interface as an administrative/operational application. Do not expose it directly to the public internet without TLS, strong secrets, network controls, and monitoring.

## 2) Hardening checklist (go-live)

### Network & transport
- [ ] Bind web to `127.0.0.1` unless remote/LAN access is required.
- [ ] For LAN/remote access, place behind **HTTPS** reverse proxy or private network (**VPN/Tailscale/SSH tunnel**).
- [ ] **Never expose plain HTTP publicly.**
- [ ] Restrict firewall/security groups to required sources and ports only.
- [ ] Use strong TLS configuration and certificate lifecycle management at the proxy.

### Application runtime
- [ ] Run web with **`--workers 1`** (mandatory for SQLite/shared state).
- [ ] Run under a dedicated least-privilege OS user/service account.
- [ ] Set `PYTHONPATH`/working directory correctly and keep process umask/permissions restrictive.
- [ ] Ensure only trusted admins can modify the install directory/source and service definitions.

### Secrets & sessions
- [ ] Set **`PM_WEB_SECRET_KEY`** to a strong random value for any non-local/shared deployment.
- [ ] Store secrets in environment/service manager/secret store (not in committed files, tickets, or shell history where avoidable).
- [ ] Rotate secrets on compromise/suspected exposure and after personnel/device changes as policy requires.
- [ ] Use secure browser contexts (HTTPS) for remote access so cookies/session behavior is protected in transit.

### Data & backups
- [ ] Set/know `PM_DATA_DIR` and protect DB/exports/invoices/backups with strict filesystem permissions.
- [ ] Encrypt disks/backups at rest where required by policy/compliance.
- [ ] Schedule regular backups and verify restores (offline drills). Keep offsite/off-host copies per DR requirements.
- [ ] Limit access to exported files/invoices/labels/backups (may contain sensitive business/personal/financial data).

### Operations
- [ ] Enable monitoring/alerting for availability (`/health`, `/healthz`), errors, disk space, and backup success.
- [ ] Keep OS/runtime dependencies patched; test updates before production rollout.
- [ ] Maintain change control for updates (in-app fast-forward or `git pull --ff-only`), with pre-update backups and restart plan.
- [ ] Document incident response and recovery contacts/procedures.

## 3) Web secrets, cookies, and session behavior

- **`PM_WEB_SECRET_KEY`**: required/critical for non-local/shared deployments (used for signed session/cookie-related security mechanisms as implemented). Use a long random value.
- **Per-browser unlock session:** web authorization uses per-browser signed-session unlock state with timeout/expiry (sliding while unlocked, based on configured session timeout). Expired sessions are treated as locked.
- **Unlock vs service proofs:** web unlock gates sensitive routes, but routes calling `app_core.services` APIs that require explicit super-password/proof must pass required proofs; unlock alone must not bypass service-level checks (see `docs/web_authz_matrix.md`).
- **Operational guidance:** always access over trusted networks/HTTPS; log out/lock when unattended; avoid shared browser profiles on shared devices.

## 4) Authorization model (Web): CSRF & unlock

### 4.1 CSRF (implemented)
- All state-changing methods (**POST/PUT/PATCH/DELETE**) are CSRF-protected (except health endpoints as documented).
- Form submissions include/verify CSRF tokens; JSON/header requests are enforced by middleware/handlers as applicable.
- Mutating HTML forms must include CSRF tokens. Do not bypass CSRF checks.

### 4.2 Super-password unlock (implemented, policy-based)
- **Sensitive/destructive/admin mutations** require an active unlock (super password) in addition to CSRF. Examples include (non-exhaustive, see matrix): financial/accounting operations, inventory/stock/batch/recipe/meal/activity mutations, voids/refunds/cancels/deletes/modifications, exports/backups/seed/demo/settings/rate-plan changes, labels, and protected downloads where gated.
- **Routine operational flows** (e.g. some front-desk create/check-in/checkout/payment and POS cart/walk-in operations) may remain usable without unlock but **still require CSRF**; policy-sensitive overrides and destructive actions remain gated.
- **Assistant:** ask/feedback follow configured unlock/allowlist policy and remain read-only and grounded.

### 4.3 Reference (authoritative for route groups)
- **`docs/web_authz_matrix.md`** documents enforcement model and route-group matrix (CSRF/unlock/conditional/proof notes) for `ui/web`. Consult it before changing routes/templates or when auditing access controls.

## 5) Data handling

- **Data classes (typical):** guest/contact details, reservations/folio balances, payments/refunds, invoices/receipts, inventory/batches/lots/costs/suppliers, expenses, activity bookings, exports/backups, operational logs/artifacts.
- **Minimization & access:** grant access on least-privilege/need-to-know basis; restrict filesystem and UI/admin access.
- **Retention & deletion:** define retention periods for DB, exports, invoices, and backups per business/legal requirements (manual/operational policy). Ensure secure deletion/wiping where required.
- **Integrity:** use in-app/CLI integrity checks (`python -m app_core.doctor`, app health/integrity features) and verify backups before/after restores.
- **Portability/exports:** treat exported files as sensitive; store/transmit securely.

## 6) Printer, label, and file safety

- **Labels/printing:** label generation/printing is an admin-sensitive capability (unlock/CSRF gated as applicable). Validate printer destinations/configuration to avoid leaking sensitive data to unintended/shared printers.
- **File downloads/exports:** protected downloads and exports are access-controlled per web policy; generated artifacts (invoices/receipts/exports/backups) may contain sensitive data—protect storage paths and transmission.
- **Uploads/imports:** seed/config imports and other admin data operations are sensitive (unlock/CSRF/proofs as applicable). Use trusted files only; validate content and keep backups before applying changes.
- **Path safety:** application constrains downloads/paths in implementation; operators must still enforce OS permissions and avoid exposing data directories via web servers or shared mounts unintentionally.

## 7) Backups, restore, and update safety (security-relevant)

- **Backups:** create/verify regularly (`Settings → Backup now` or `scripts/backup_runtime.*` / `python -m app_core.doctor --backup/--verify-backup`). Store securely (encrypted, access-controlled, offsite as needed).
- **Restore:** manual/offline only. Verify backup integrity first, stop services, preserve copies, restore deliberately, restart, and re-verify integrity before returning to service.
- **Updates:** prefer in-app git fast-forward apply for clean checkouts or `git pull --ff-only`; runtime data preserved. Review changelog, back up first, ensure clean working tree, and **restart after apply**. Investigate unexpected local modifications before overwriting.

## 8) Logging, monitoring, and incident readiness (operational)

- Monitor service health (`/health`, `/healthz`), uptime, resource/disk usage, and backup/restore outcomes.
- Protect and review application/proxy/system logs as available; avoid logging secrets.
- Prepare incident steps: isolate host/network if needed, rotate `PM_WEB_SECRET_KEY` and credentials, assess data exposure, restore from verified backups, patch/update, and document timeline/actions.

## 9) Automated vs operational responsibilities

- **Implemented/automated controls:** CSRF enforcement, unlock/session gating, protected route policies per matrix, health probes, doctor/integrity checks, safe backup/verify helpers, git fast-forward update workflow, startup migrations.
- **Operator responsibilities:** TLS/proxy/VPN, firewall/network segmentation, secret management/rotation, OS hardening/patching, access control to hosts/data/printers, backup scheduling/offsite retention/encryption, restore execution, monitoring/alerting/IR, compliance and physical/device security.
