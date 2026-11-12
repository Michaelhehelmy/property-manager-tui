# Web Authz/CSRF Matrix (ui/web)

Scope: `ui/web/routes/*.py`, `ui/web/deps.py`, `ui/web/server.py`

## Conventions
- **CSRF**: All state-changing requests (POST/PUT/PATCH/DELETE) must be verified. Form posts use `deps.verify_csrf(request, form.get("csrf"))` (or centralized form helpers). JSON/header requests are enforced by middleware (`ui/web/server.py`) and/or `deps.csrf_middleware`.
- **Unlock**: `deps.require_unlock(request)` gates sensitive/destructive/admin mutations (financial/accounting, inventory/stock/batch/recipe/meal/activity, voids/refunds/cancels/deletes/modifications, exports/backups/seed/demo/settings changes, labels, etc.). Redirects to `/unlock` if not unlocked.
- **Operational front-desk** flows are kept usable where appropriate (e.g. create reservation, check-in/checkout/pay, POS cart operations, walk-in sale) while still requiring CSRF.

## Route Groups

| Group | Mutations | CSRF | Unlock | Notes |
|---|---|---|---|---|
| `accounting.py` | expense, expense void, payment, refund | Yes (form) | Yes | Financial/accounting operations. |
| `assistant.py` | ask, feedback, clear | Yes (form) | Conditional (ask/feedback follow assistant unlock policy) | `assistant_ask` enforces per-setting unlock/allowlist; operational usage preserved. |
| `dashboard.py` | — (GET only) | n/a | n/a | Read-only. |
| `files.py` | — (GET only) | n/a | Yes (download) | Protected downloads require unlock. |
| `guests.py` | guest update | Yes (form) | Yes | Guest data modifications protected. |
| `pos_inventory.py` | pos/add, cart add/remove/clear, walkin, pos void, restock, inv add, recipes set/clear, meals save/delete, activities create/toggle, batch receive, labels (item/batch) | Yes (form) | Mixed | Cart ops/walk-in kept usable (CSRF only). Charges/voids/stock/batch/recipe/meal/activity/labels require unlock. |
| `reservations.py` | create, checkin, checkout, checkout-override, cancel, noshow, delete, modify, pay, refund, invoice, book-activity | Yes (form; `_act` helper used) | Mixed | Create/checkin/checkout/pay kept usable (CSRF). Overrides/cancel/noshow/delete/modify/refund/invoice/book-activity require unlock. |
| `reports_analytics.py` | run report, analytics export | Yes (form) | Yes | Reports/exports gated. |
| `settings_branding.py` | unlock/lock/recovery, branding/save, backup, demo-preset, reset, rate-plans add/edit/delete, seed export/import, rooms add/delete, test-label, updates check/apply | Yes (form) | Mixed | Auth actions have CSRF only; admin/config/backup/seed/demo/labels/updates-apply/rooms require unlock. |

## Implementation Notes
- Centralized CSRF: middleware in `server.py` enforces for JSON/header; routes verify form tokens. Added `deps.require_csrf_form()` helper for centralized form verification.
- Session/CSRF tokens managed via `deps` (HttpOnly, SameSite cookies).
