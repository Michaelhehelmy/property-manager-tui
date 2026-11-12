# Web Authz/CSRF Matrix (`ui/web`)

Scope: `ui/web/server.py`, `ui/web/deps.py`, `ui/web/routes/*.py`  
Out of scope for this change: `src/app_core/**`, `ui/tui/**`, `tests/**`

## Enforcement model

### CSRF
- **Centralized middleware** (`ui/web/server.py`) enforces CSRF for all state-changing methods (`POST/PUT/PATCH/DELETE`) except health probes.
  - `application/json` or requests with `x-csrf-token` header: header token is verified.
  - Form submissions (`application/x-www-form-urlencoded`, `multipart/form-data`, or empty content-type): form field `csrf` is verified.
- Form bodies are cached on `request.state.cached_form` by middleware so route handlers can safely read them again via `deps.get_form(request)`.
- `deps.require_csrf_form(request)` / `deps.get_form(request)` provide centralized handler-side form access/verification helpers.
- Tokens are per-browser-session, stored in signed cookie session data, and compared with constant-time comparison.

### Per-browser web unlock (session model)
- Web authorization uses **per-browser signed-session unlock state** (`web_unlock_last` timestamp) with timeout/expiry; it does **not** rely on process-global `services._session` for authorization decisions.
- Timeout is sliding (touched on each request while unlocked) using `services.get_setting("session_timeout_min", "5")`.
- `deps.is_unlocked(request)` / `deps.require_unlock(request)` are the web authz gates; expired sessions are treated as locked.
- `deps.unlock()` verifies the super password, records unlock time in the browser session, and performs best-effort in-process backend session alignment for legacy single-worker internals only (compatibility). `deps.lock()` clears browser unlock state and best-effort locks backend session.
- Compatibility: routes that call service APIs requiring password proofs continue to pass explicit passwords from forms where required; authorization remains browser-session-scoped.

## Mutating route audit

| Route module | Mutations | CSRF | Unlock | Sensitivity / notes |
|---|---|---|---|---|
| `accounting.py` | `POST /accounting/expense`, `/accounting/expense/{eid}/void`, `/accounting/payment`, `/accounting/refund` | Yes (middleware + centralized form access) | Yes | Financial/accounting operations (admin/financial). |
| `assistant.py` | `POST /assistant/ask`, `/assistant/feedback`, `/assistant/clear` | Yes | Conditional for `ask`/`feedback` (assistant unlock policy: `assistant_require_unlock` or web unlocked). `clear` is CSRF-only (session history clear). | Operational assistant use preserved per policy; sensitive data access gated by policy. |
| `dashboard.py` | none | n/a | n/a | Read-only. |
| `files.py` | none | n/a | Yes on `GET /files/{kind}/{name}` | Protected downloads (exports/invoices) require unlock. |
| `guests.py` | `POST /guests/{gid}/update` | Yes | Yes | Guest/profile data modification (sensitive). |
| `pos_inventory.py` | `POST /pos/add`, `/pos/cart/add`, `/pos/cart/remove`, `/pos/cart/clear`, `/pos/walkin`, `/pos/void/{pos_id}`, `/inventory/restock`, `/inventory/add`, `/recipes/set`, `/recipes/clear`, `/meals/save`, `/meals/delete`, `/activities/create`, `/activities/{aid}/toggle`, `/inventory/batch/receive`, `/inventory/label/item/{item_id}`, `/inventory/label/batch/{batch_id}` | Yes | Mixed | **Operational (CSRF only):** cart add/remove/clear, walk-in checkout. **Unlock required:** posting/charging to reservation (`/pos/add`), voids, inventory/stock/batch/recipes/meals/activities mutations, labels (admin/financial/inventory/destructive-sensitive). |
| `reservations.py` | `POST /reservations/create`, `/{rid}/checkin`, `/{rid}/checkout`, `/{rid}/checkout-override`, `/{rid}/cancel`, `/{rid}/noshow`, `/{rid}/delete`, `/{rid}/modify`, `/{rid}/pay`, `/{rid}/refund`, `/{rid}/invoice`, `/{rid}/book-activity` | Yes | Mixed | **Operational (CSRF only):** create, check-in, standard checkout, payment recording. **Unlock required:** checkout override, cancel, no-show, delete, modify, refund, invoice generation, booking/charging activity (destructive/financial/admin-sensitive). |
| `reports_analytics.py` | `POST /reports/run`, `/reports/analytics-export` | Yes | Yes | Reporting/exports gated (sensitive/admin data export). |
| `settings_branding.py` | `POST /unlock`, `/unlock/recovery`, `/lock`, `/settings/branding`, `/settings/save`, `/settings/backup`, `/settings/demo-preset`, `/settings/reset`, `/settings/rate-plans/add`, `/settings/rate-plans/{plan_id}/edit`, `/settings/rate-plans/{plan_id}/delete`, `/settings/seed/export`, `/settings/seed/import`, `/settings/rooms/add`, `/settings/rooms/{room_id}/delete`, `/settings/test-label`, `/settings/updates/check`, `/settings/updates/apply` | Yes | Mixed | **Auth/session flows (CSRF only):** unlock, recovery, lock. **Unlock required:** branding/config save/reset, backup, demo/seed import-export, rooms/rate-plan admin mutations, labels, updates apply (admin/destructive/data-management). **CSRF only:** updates check (non-destructive availability check). |

## Notes / limitations
- Cookie session storage is signed client-side state; unlock expiry is enforced by server logic on each request. This is compatible with the existing single-worker localhost MVP deployment model.
- `sync_backend_session_if_possible()` is a best-effort compatibility bridge for legacy service internals that may inspect process-global session state; web route authorization does not depend on it.
- Middleware reads/parses form bodies for unsafe form requests to enforce CSRF centrally; handlers reuse the cached form to avoid double-consume issues.
