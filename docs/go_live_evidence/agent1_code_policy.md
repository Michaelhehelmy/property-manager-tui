# Agent 1 Evidence: Code, Authz Policy, and Parity Closure

## Status
- Phase: Started (implementation in progress)
- Date: 2026-11-16
- Baseline commit before this work: fe7173c

## Actions completed
1. Baseline audit/fixes (delegated scans + direct fixes)
- Fixed DEFECT-1: `ui/web/routes/reservations.py` now defines module logger (`import logging`, `logger = logging.getLogger(__name__)`) for exception-path logging.
- Fixed DEFECT-2: `ui/web/routes/pos_inventory.py` now defines module logger for exception-path logging.
- Fixed RISK-2 hardening: `ui/web/routes/files.py` now sets RFC 5987-safe `Content-Disposition` (`filename` + `filename*=UTF-8''...`) while keeping `deps.safe_download_path` confinement and `X-Content-Type-Options: nosniff`.
- Verified FEFO expiry-block safety fix present: `src/app_core/services/_monolith.py` raises `Insufficient non-expired batch stock available for the requested quantity.` when `block_sell_expired` prevents shortfall fulfillment.

2. Validation
- `python3 -m py_compile ui/web/routes/reservations.py ui/web/routes/pos_inventory.py ui/web/routes/files.py` -> OK
- `PYTHONPATH=src PM_WEB_SECRET_KEY=test-secret python3 -m pytest tests/web/test_web_routes_core.py tests/web/test_web_reservations.py -q` -> 6 passed, 1 warning
- Prior full regression recorded in environment: `885 passed, 1 warning, 0 failed` (`/data/data/com.termux/files/usr/tmp/opencode/pm_full_tests_final2.log`)

3. Commit
- Committed: "Fix web route logger defects and harden download headers" (see git log).

## RISK-1 (route-level unlock policy) decision record (draft for approval)
- Current implementation (as documented in `docs/web_authz_matrix.md`): CSRF is enforced for all mutating routes; sensitive/destructive/financial/admin actions are unlock-gated with explicit service proofs where required. Some operational front-desk mutations remain CSRF-only (not web-unlock-gated), e.g. reservation create/check-in/standard checkout/payment recording and POS cart/walk-in flows; service-layer rules/transactions still apply.
- Recommendation: ACCEPT AS-IS for controlled single-process/front-desk deployment, with compensating controls (CSRF, per-browser timed unlock for sensitive ops, password/proof for guarded service actions, rate limiting, trusted network/VPN/HTTPS, physical access control, auditability via records/logs). Revisit if deployment model requires stricter role separation.
- Status: PENDING stakeholder/business-owner sign-off. If stricter policy required, implement `require_unlocked_form` + proofs for selected operational mutations in web/TUI and update matrix/docs/tests.

## Remaining Agent 1 tasks
- [ ] Stakeholder sign-off on RISK-1 policy; update `docs/web_authz_matrix.md` and `docs/SECURITY.md` accordingly.
- [ ] Parity sweep for web sub-tabs and TUI core flows (POS/inventory/batches/meals/activities/history, reservations/guests/accounting/reports/settings) including traceability fields in receipts/invoices/history/labels/exports.
- [ ] Assistant/action safety and local-only/grounding review confirmation.
- [ ] Docs sync pass (README, assistant.md, deployment/security/updates/troubleshooting/go-live checklist).
- [ ] Targeted regression tests for any policy/behavior changes; confirm CI green on pushed branch/RC.

## Handoff
- G1 not yet complete (policy sign-off + final doc sync pending).
