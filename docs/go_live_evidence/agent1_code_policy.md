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

## RISK-1 (route-level unlock policy) decision record (APPROVED)
- **Decision:** ACCEPT AS-IS for trusted/local/LAN/VPN or HTTPS-reverse-proxy single-process front-desk deployments.
- **Policy:** CSRF enforced for all mutating routes. Sensitive/admin/financial/destructive actions remain unlock-gated with service proofs/checks where required. Selected operational front-desk mutations may remain CSRF-only (e.g. reservation create/check-in/standard checkout/payment recording, POS cart/walk-in, non-destructive update check) relying on service-layer validation/transactions and compensating controls.
- **Compensating controls:** strong persistent `PM_WEB_SECRET_KEY`, secure cookies over HTTPS, `--workers 1`, least-privilege permissions, no public plain-HTTP exposure, firewall/VPN/reverse-proxy restrictions, session timeout/rate limiting/physical controls, and regular verified backups/restore drills. Reassess/tighten for shared or less-trusted environments.
- **Docs updated:** `docs/web_authz_matrix.md` (added accepted policy, omitted routes `/assistant/action/confirm`, `/assistant/action/cancel`, `/setup`, and proof/enforcement clarifications), `docs/SECURITY.md`, `docs/GO_LIVE_CHECKLIST.md`.

## RISK-1/authz scan findings (subagent, actionable)
- Mutating routes without `require_unlocked_form`/`require_unlock` (CSRF still middleware-enforced): reservations `POST /reservations/create|/{rid}/checkin|/{rid}/checkout|/{rid}/pay` (operational; services mostly no password except checkout override path), POS `POST /pos/cart/add|remove|clear|/pos/walkin` (session/financial+inventory mutations without unlock/proof), assistant `POST /assistant/ask|feedback|clear` (policy/conditional gates; clear CSRF-only) and `POST /assistant/action/confirm|cancel` (conditional policy gate, proof passthrough), settings `POST /settings/updates/check` (CSRF-only, session/network side effects), and `POST /setup` (first-run config mutation).
- Matrix discrepancies to fix: `POST /assistant/action/confirm`, `POST /assistant/action/cancel`, and `POST /setup` omitted from `docs/web_authz_matrix.md`; some handlers use `require_unlock+get_form` vs `require_unlocked_form` (document or normalize); proof/enforcement mismatches noted (e.g. delete/modify collect/pass password but service verification may be policy-only; several unlock-gated routes call services with no password params) - align UI/docs/service enforcement to avoid false assurance.
- Agent 1 next code/doc actions: update matrix for completeness/accuracy, decide tighten-vs-accept for operational CSRF-only mutations, and resolve proof/enforcement mismatches (service-side enforcement or UI/doc correction) with tests.

## Proof/enforcement alignment note (2026-11-17)
- Reviewed `services.modify_reservation` and `services.delete_reservation` (`src/app_core/services/_monolith.py`): signatures accept `password=None` but current implementation enforces policy/state (status/payments/POS/availability) without password verification. Per approved easiest path, **kept behavior unchanged** and documented in `docs/web_authz_matrix.md` that routes may pass proof values while UI/docs must not imply service-password enforcement where service API does not require it. Tightening to require verified super-password is a policy change requiring service+UI+test updates.

## Remaining Agent 1 tasks
- [x] Stakeholder sign-off on RISK-1 policy; update `docs/web_authz_matrix.md` and `docs/SECURITY.md` accordingly.
- [x] Resolve matrix discrepancies for omitted routes (`/assistant/action/confirm`, `/assistant/action/cancel`, `/setup`) and document proof/enforcement nuances.
- [ ] Optional (policy decision): enforce password verification in `modify_reservation`/`delete_reservation` and align any other proof-mismatch paths (service+UI+tests), or confirm doc/UI-alignment-only approach.
- [ ] Parity/traceability sweep for web flows (POS/inventory/batches/meals/activities/history, reservations/guests/accounting/reports/settings) including fields in receipts/invoices/history/labels/exports (legacy TUI removed).
- [ ] Assistant/action safety and local-only/grounding review confirmation.
- [ ] Docs sync pass (README, assistant.md, deployment/security/updates/troubleshooting/go-live checklist as needed).
- [ ] Confirm CI green on pushed branch/RC.

## Handoff
- G1 near-complete for approved policy/docs; pending optional proof-tightening decision, parity sweep, and pushed CI confirmation. Current HEAD: 56c05ef.
