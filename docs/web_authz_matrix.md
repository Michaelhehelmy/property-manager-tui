# Web Authz/CSRF Matrix (`ui/web`)

Scope: `ui/web/server.py`, `ui/web/deps.py`, `ui/web/routes/*.py`, `ui/web/templates/**`  
Out of scope for this change: `src/app_core/**`, `ui/tui/**`, `tests/**`

## Enforcement model (final)

### CSRF
- **Centralized middleware** (`ui/web/server.py`) enforces CSRF for all state-changing methods (`POST/PUT/PATCH/DELETE`) except `/health` and `/healthz`.
  - `application/json` or requests with `x-csrf-token` header: header token is verified.
  - Form submissions (`application/x-www-form-urlencoded`, `multipart/form-data`, or empty content-type): form field `csrf` is verified.
- Form bodies are cached on `request.state.cached_form` by middleware so route handlers can safely read them again via `deps.get_form(request)` without double-consume issues.
- `deps.require_csrf_form(request)` verifies+returns a form explicitly when needed; `deps.require_unlocked_form(request)` centralizes form access + per-browser unlock for sensitive mutations.
- **Form/template guarantee:** all mutating HTML forms in `ui/web/templates/**` include a hidden `csrf` token. CSRF is never optional on state-changing requests.

### Per-browser web unlock (session model)
- Web authorization uses **per-browser signed-session unlock state** (`web_unlock_last` timestamp) with timeout/expiry; it does **not** rely on process-global `services._session` for authorization decisions.
- Timeout is sliding (touched on each request while unlocked) using `services.get_setting("session_timeout_min", "5")`.
- `deps.is_unlocked(request)` / `deps.require_unlock(request)` are the web authz gates; expired sessions are treated as locked.
- `deps.unlock()` verifies the super password, records unlock time in the browser session, and performs best-effort in-process backend session alignment for legacy single-worker internals only (compatibility). `deps.lock()` clears browser unlock state and best-effort locks backend session.

### Service password/proof handling (no weakening)
- Routes that invoke `app_core.services` APIs requiring a super-password proof must pass the **explicit password/proof supplied in the form**; web unlock alone must not bypass service-level password checks.
- `deps.service_password(request, form, allow_session_sentinel=True)` centralizes this:
  - Prefers an explicit `form.password` value (never weakened/overridden).
  - Only when the browser is unlocked, no explicit password is provided, **and** the target service explicitly supports the `"__session__"` sentinel, it performs best-effort backend-session sync and passes `"__session__"` for single-worker compatibility.
  - Otherwise returns the provided value (possibly empty) so the service enforces its own requirements.
- **Proof/form guarantees:**
  - Sensitive mutating forms include a `password` (and any required confirmation) input where a service or workflow requires explicit proof (e.g. voids, refunds, expense void, reservation sensitive actions, settings/rate-plan/seed/demo/reset saves, inventory/batch/recipe/meal mutations, guest/profile updates). Unlock gates access, but required service proofs remain enforced by the backend.
  - Operational front-desk mutations (create/check-in/standard checkout/payment, walk-in cart/sale) remain usable without unlock and do not fabricate proofs; they are CSRF-protected.
- `deps.sync_backend_session_if_possible()` is a best-effort compatibility bridge for legacy single-worker internals and is **not** used for web authorization decisions.

## Mutating route audit (final)

| Route group/module | Mutations | CSRF | Unlock | Sensitivity / proof-form / service-proof notes |
|---|---|---|---|---|
| `accounting.py` | `POST /accounting/expense`, `/accounting/expense/{eid}/void`, `/accounting/payment`, `/accounting/refund` | Yes | Yes | Financial/admin operations. Templates include explicit password inputs where service proof is required (`void`, `refund`); routes pass `deps.service_password(request, form)` to service password checks. Expense recording/payment recording are unlock-gated admin actions. |
| `assistant.py` | `POST /assistant/ask`, `/assistant/feedback`, `/assistant/clear` | Yes | Conditional for `ask`/`feedback` (`assistant_require_unlock` policy or web unlocked); `clear` CSRF-only | Operational assistant remains usable per policy; sensitive assistant access gated by unlock policy. Session-history clear is non-destructive to business data (CSRF-only). |
| `dashboard.py` | none | n/a | n/a | Read-only. |
| `files.py` | none (downloads) | n/a | Yes on `GET /files/{kind}/{name}` | Protected export/invoice/receipt downloads; path constrained by `safe_download_path`. |
| `guests.py` | `POST /guests/{gid}/update` | Yes | Yes | Guest/profile modification (sensitive). Form includes explicit super-password field; route passes `deps.service_password(request, form)` to `services.update_guest` (no weakening). |
| `pos_inventory.py` | `POST /pos/cart/add`, `/pos/cart/remove`, `/pos/cart/clear`, `/pos/walkin` | Yes | No (CSRF-only, operational front-desk) | Walk-in cart/session and walk-in checkout remain usable without unlock; state-changing requests remain CSRF-protected. Walk-in sale is batch-aware (cart carries `batch_id`/`lot_code` + trace metadata) and commits atomically via `services.complete_walkin_sale`. |
| `pos_inventory.py` | `POST /pos/add`, `/pos/void/{pos_id}`, `/inventory/restock`, `/inventory/add`, `/recipes/set`, `/recipes/clear`, `/meals/save`, `/meals/delete`, `/activities/create`, `/activities/{aid}/toggle`, `/inventory/batch/receive`, `/inventory/label/item/{item_id}`, `/inventory/label/batch/{batch_id}` | Yes | Yes | Posting to reservation, voids, inventory/stock/batch/recipe/meal/activity mutations, labels (admin/financial/inventory/destructive-sensitive). `restock` and `batch/receive` pass `service_password(..., allow_session_sentinel=True)` for single-worker compatibility **only** where the underlying service supports `"__session__"` and no explicit password is provided; forms still include password fields and explicit proofs are preferred/passed when supplied. `pos/void` and other password-only service calls require/pass explicit form passwords. |
| `reservations.py` | `POST /reservations/create`, `/{rid}/checkin`, `/{rid}/checkout`, `/{rid}/pay` | Yes | No (CSRF-only, operational front-desk) | Operational create/check-in/standard checkout/payment recording preserved; CSRF-protected. Checkout override path remains separately gated. |
| `reservations.py` | `POST /reservations/{rid}/checkout-override`, `/{rid}/cancel`, `/{rid}/noshow`, `/{rid}/delete`, `/{rid}/modify`, `/{rid}/refund`, `/{rid}/invoice`, `/{rid}/book-activity` | Yes | Yes | Destructive/financial/admin-sensitive actions. Forms include explicit password inputs where service/workflow requires proof (override, cancel/no-show when paid/partial as enforced by backend, refund). `modify` and `delete` are unlock-gated admin actions; routes pass explicit form values and backend enforces its own requirements (no web-side weakening). Invoice generation and activity booking are unlock-gated; activity booking is posted as POS to the reservation folio/invoice. |
| `reports_analytics.py` | `POST /reports/run`, `/reports/analytics-export` | Yes | Yes | Sensitive reporting/data-export operations (unlock-gated). |
| `settings_branding.py` | `POST /unlock`, `/unlock/recovery`, `/lock` | Yes | No (auth/session flows) | Session bootstrap/recovery/lock endpoints; CSRF-protected and validated by service/auth logic. |
| `settings_branding.py` | `POST /settings/updates/check` | Yes | No (CSRF-only) | Non-destructive update availability check. |
| `settings_branding.py` | `POST /settings/branding`, `/settings/save`, `/settings/backup`, `/settings/demo-preset`, `/settings/reset`, `/settings/rate-plans/add`, `/settings/rate-plans/{plan_id}/edit`, `/settings/rate-plans/{plan_id}/delete`, `/settings/seed/export`, `/settings/seed/import`, `/settings/rooms/add`, `/settings/rooms/{room_id}/delete`, `/settings/test-label`, `/settings/updates/apply` | Yes | Yes | Admin/destructive/config/data-management actions. Service calls that require super-password proofs receive explicit form passwords/confirmations (settings save/reset, demo preset, seed import, rate-plan mutations, etc.); unlock gates access but does not replace required service proofs. Room add/delete and backup/branding/test-label are unlock-gated admin actions (services may not require separate password args). |

## Batch/lot/expiry/received traceability & receipt parity (web surfaces)

- **Reservation/POS/walk-in flows:** batch-aware selection is supported (specific batch/lot vs auto-FEFO, scan/lot lookup) in POS and reservation POS charge forms. Walk-in cart persists `batch_id`/`lot_code` and, when available, received/expiry trace metadata for UI display; sales pass batch identifiers to services.
- **Trace enrichment in views:** reservation detail, POS (including walk-in recent lines and cart), and guest portfolio routes enrich POS lines with batch `lot_code`, `received_at`, `expiry_date`, and expiry state from `inventory_batches` when available (graceful if missing).
- **Invoice/receipt/printable/export views rendered in web templates:** reservation detail shows per-line batch/lot trace; guest portfolio shows trace on trip POS items and aggregated POS history; reports/downloads list generated invoice/receipt/export artifacts for protected download. Text invoice/receipt/report contents are produced by `app_core.services` (out of scope to modify here) but include trace fields where the generator supports them (e.g. reservation invoice/walk-in receipt text include lot codes); web-rendered views present/download those artifacts and handle missing trace data gracefully. No web change weakens receipt/invoice integrity.
- **Usability/layout:** responsive/no-horizontal-scroll layout maintained via shared CSS (`.table-wrap`, wrapping, responsive grid/nav); templates handle missing/optional trace data gracefully.

## Notes / limitations (final)

- Cookie session storage is signed client-side state; unlock expiry is enforced by server logic on each request. Compatible with the existing single-worker localhost MVP deployment model.
- Middleware reads/parses form bodies for unsafe form requests to enforce CSRF centrally; handlers reuse the cached form to avoid double-consume issues.
- `GET /inventory/scan` is a non-mutating redirect helper (no CSRF/unlock mutation surface).
- **Explicit-password-only service APIs:** service-proof session-sentinel fallback is used only where `app_core.services` explicitly supports `"__session__"` (e.g. inventory restock / batch receive paths as wired). For all other password-gated service APIs, routes require/pass explicit form passwords and cannot be safely "unlocked" without that proof; unlock alone is insufficient by design (no security-weakening fallback introduced).
- Invoice/receipt text generation and some deep portfolio data assembly live in `app_core.services` (out of scope). Web routes/templates enrich and render available traceability and download generated artifacts; full generator-level trace expansion for every historical/export artifact requires service-layer changes outside this scope.
