# Web Authz/CSRF Matrix (`ui/web`)

Scope: `ui/web/server.py`, `ui/web/deps.py`, `ui/web/routes/*.py`, `ui/web/templates/**`  
Out of scope (not modified): `src/app_core/**`, `ui/tui/**`, `tests/**`, `scripts/**`, `README.md`, `assistant.md`, other docs.

## Enforcement model

### CSRF
- **Active enforcement is centralized in server middleware** (`ui/web/server.py`) for all state-changing methods (`POST/PUT/PATCH/DELETE`) except `/health` and `/healthz`.
  - `application/json` or requests with `x-csrf-token` header: header token is verified.
  - Form submissions (`application/x-www-form-urlencoded`, `multipart/form-data`, or empty content-type): form field `csrf` is verified.
- Form bodies are cached on `request.state.cached_form` by middleware so route handlers can safely read them again via `deps.get_form(request)` without double-consume issues.
- `deps.require_csrf_form(request)` and `deps.require_unlocked_form(request)` are helper utilities for cached-form access/explicit verification and unlock gating in handlers where needed; they are not separate active CSRF enforcement paths and do not replace centralized middleware enforcement.
- **Template guarantee:** all mutating HTML forms in `ui/web/templates/**` include a hidden `csrf` token. CSRF is never optional on state-changing requests.

### Session/cookie hardening
- Signed cookie session (`pm_session`, `itsdangerous.URLSafeSerializer`) with `HttpOnly=True`, `SameSite=Lax`, `path=/`, and `max_age` aligned to configured session timeout window (+grace). Server-side unlock expiry remains authoritative on every request.
- **`PM_WEB_COOKIE_SECURE`**: set to `1`/`true`/`yes`/`on` to enable the `Secure` cookie flag for HTTPS deployments (`deps.cookie_secure_enabled()`).
- **Secret requirements/warnings**:
  - `PM_WEB_SECRET_KEY` is required for non-local deployment (non-loopback host hint) unless `PM_WEB_ALLOW_INSECURE_DEV=1` is explicitly set (local/dev override only).
  - If unset in local/dev fallback, a loud warning is emitted and an insecure development secret is used.
  - If set but short (<32 chars), a warning is emitted recommending a strong random production secret.

### Per-browser web unlock and expiry
- Web authorization uses **per-browser signed-session unlock state** (`web_unlock_last` timestamp) with timeout/expiry; it does **not** rely on process-global `services._session` for authorization decisions.
- Timeout is sliding (touched on middleware for unlocked requests) using `services.get_setting("session_timeout_min", "5")` (minimum 1 minute). Expired sessions are treated as locked.
- `deps.is_unlocked(request)` / `deps.require_unlock(request)` gate protected/sensitive actions (redirect to `/unlock?next=...` when locked).
- `deps.unlock()` verifies the super password, records unlock time, clears unlock auth-failure counters, and performs best-effort in-process backend session alignment for legacy single-worker internals only (compatibility). `deps.lock()` clears browser unlock state and best-effort locks backend session.

### Brute-force / rate-limit safeguards (lightweight, in-process)
- Best-effort in-process attempt limiting for auth-sensitive endpoints (appropriate for `--workers 1` single-worker MVP; not distributed/persistent):
  - `POST /unlock`: limited (per client IP) with 429-style user feedback via flash+redirect.
  - `POST /unlock/recovery`: stricter limit/window; failed recovery attempts recorded; counter cleared on successful recovery.
- Successful unlock clears unlock failure counters. Sensitive admin actions remain CSRF-protected and unlock-gated; service-level password checks still apply where required.

### Service password/proof handling (no weakening)
- Routes that invoke `app_core.services` APIs requiring super-password proof must pass explicit password/proof supplied in the form where applicable; web unlock alone must not bypass service-level password checks.
- `deps.service_password(request, form, allow_session_sentinel=True)` centralizes resolution:
  - Prefers an explicit `form.password` value (never weakened/overridden).
  - Only when the browser is unlocked, no explicit password is provided, **and** the target service explicitly supports the `"__session__"` sentinel, it performs best-effort backend-session sync and passes `"__session__"` for single-worker compatibility.
  - Otherwise returns the provided value (possibly empty) so the service enforces its own requirements.
- Sensitive/admin mutating forms were hardened to include explicit `password` (and any required confirmation) inputs where services/workflows require or may require proof (e.g. reservation modify/delete/cancel/no-show/override/refund/invoice/activity-book/POS-charge context, POS void/inventory/recipes/meals/activities/labels, guest/profile updates, accounting void/refund and unlock-gated admin ops, settings save/reset/demo/seed/rate-plans/branding/backup/rooms/test-label/update-apply). Unlock gates access, but required service proofs remain enforced by the backend.
- `deps.sync_backend_session_if_possible()` is a best-effort compatibility bridge for legacy single-worker internals and is **not** used for web authorization decisions.

### File download/upload/response handling
- `GET /files/{kind}/{name}`: requires unlock; confined access via `deps.safe_download_path` (`kind` allowlist `export|invoice`, rejects traversal/separators/NUL, resolves within approved base dir, requires existing regular file). Responses include `X-Content-Type-Options: nosniff` and attachment disposition.
- Seed config import (`POST /settings/seed/import`, unlock-gated): web-layer validation for uploaded filename extension (`.json`), allowed content-types, max size (5 MB), temp-file handling, and explicit confirmation+password passed to service. Service-layer first-time/operational-data restrictions remain authoritative.

## Mutating route audit

| Route group/module | Mutations | CSRF | Unlock | Sensitivity / proof-form / service-proof notes |
|---|---|---|---|---|
| `accounting.py` | `POST /accounting/expense`, `/accounting/expense/{eid}/void`, `/accounting/payment`, `/accounting/refund` | Yes | Yes | Financial/admin operations. Explicit password inputs provided for proof-requiring actions (void/refund); routes pass `deps.service_password(request, form)`. Expense/payment recording are unlock-gated admin actions (CSRF-protected; backend enforces any requirements). |
| `assistant.py` | `POST /assistant/ask`, `/assistant/feedback`, `/assistant/clear` | Yes | Conditional for `ask`/`feedback` (`assistant_require_unlock` policy or web unlocked); `clear` CSRF-only | Assistant data access gated by policy/unlock; history clear is non-destructive to business data (CSRF-only). |
| `dashboard.py` | none | n/a | n/a | Read-only. |
| `files.py` | none (downloads) | n/a | Yes (`GET /files/{kind}/{name}`) | Protected downloads confined to export/invoice dirs with traversal/symlink-escape prevention and nosniff headers. |
| `guests.py` | `POST /guests/{gid}/update` | Yes | Yes | Sensitive profile modification. Explicit super-password field; route passes `deps.service_password(request, form)` to `services.update_guest`. |
| `pos_inventory.py` | `POST /pos/cart/add`, `/pos/cart/remove`, `/pos/cart/clear`, `/pos/walkin` | Yes | No (CSRF-only, operational front-desk) | Operational cart/walk-in checkout remain usable without unlock; CSRF-protected. Batch-aware cart/atomic checkout preserved. |
| `pos_inventory.py` | `POST /pos/add`, `/pos/void/{pos_id}`, `/inventory/restock`, `/inventory/add`, `/recipes/set`, `/recipes/clear`, `/meals/save`, `/meals/delete`, `/activities/create`, `/activities/{aid}/toggle`, `/inventory/batch/receive`, `/inventory/label/item/{item_id}`, `/inventory/label/batch/{batch_id}` | Yes | Yes | Admin/financial/inventory-sensitive mutations. Routes pass explicit proofs to password-gated service calls where required (`void_pos_item`, `add_inventory_item`, recipes/meals, etc.); `restock` and `batch/receive` allow `"__session__"` sentinel only when unlocked+supported by service and no explicit password provided (single-worker compatibility). `add_pos_item`, `create_activity`/`set_activity_active`, and label-print actions take no service password parameter; web UI no longer prompts for unused proofs on these unlock-gated routes (CSRF/unlock enforced; backend/service rules still apply). |
| `reservations.py` | `POST /reservations/create`, `/{rid}/checkin`, `/{rid}/checkout`, `/{rid}/pay` | Yes | No (CSRF-only, operational front-desk) | Operational front-desk flows preserved and CSRF-protected. Standard checkout separately blocks override cases per backend rules. |
| `reservations.py` | `POST /reservations/{rid}/checkout-override`, `/{rid}/cancel`, `/{rid}/noshow`, `/{rid}/delete`, `/{rid}/modify`, `/{rid}/refund`, `/{rid}/invoice`, `/{rid}/book-activity` | Yes | Yes | Destructive/financial/admin-sensitive actions. Routes pass explicit proofs to password-gated service calls (`checkout_reservation` override, `cancel_reservation`, `mark_no_show`, `delete_reservation`, `modify_reservation`, `record_refund`) as required by backend policy/signature. `generate_invoice_text`/`save_invoice_file`, `add_pos_item`, and `book_activity` take no service password parameter; web UI avoids misleading password prompts for these unlock-gated routes (CSRF/unlock enforced). |
| `reports_analytics.py` | `POST /reports/run`, `/reports/analytics-export` | Yes | Yes | Sensitive reporting/data-export operations (unlock-gated). |
| `settings_branding.py` | `POST /unlock`, `/unlock/recovery`, `/lock` | Yes | No (auth/session flows) | Session bootstrap/recovery/lock; CSRF-protected, rate-limited (in-process) for unlock/recovery, validated by auth/service logic. |
| `settings_branding.py` | `POST /settings/updates/check` | Yes | No (CSRF-only) | Non-destructive update availability check. |
| `settings_branding.py` | `POST /settings/branding`, `/settings/save`, `/settings/backup`, `/settings/demo-preset`, `/settings/reset`, `/settings/rate-plans/add`, `/settings/rate-plans/{plan_id}/edit`, `/settings/rate-plans/{plan_id}/delete`, `/settings/seed/export`, `/settings/seed/import`, `/settings/rooms/add`, `/settings/rooms/{room_id}/delete`, `/settings/test-label`, `/settings/updates/apply` | Yes | Yes | Admin/destructive/config/data-management actions. Routes pass `deps.service_password(...)` and enforce confirmations for password-gated service calls (`save_settings`, demo preset/reset, rate plan CRUD, seed import). `apply_branding_settings`, `backup_now`, `add_room`/`delete_room`, `test_label_print`, seed export, and updater apply take no service password parameter; web UI avoids misleading password prompts on those unlock-gated routes (CSRF/unlock still enforced). |

## Residual limitations
- Signed cookie sessions store state client-side; server enforces unlock expiry per request. Model remains aligned with existing single-worker localhost MVP (`--workers 1`). In-process rate limiting is per-process and not shared across workers/instances.
- Middleware parses/caches unsafe form bodies for centralized CSRF enforcement; handlers must reuse cached forms to avoid double-consume.
- Service-password sentinel fallback (`"__session__"`) is used only for explicitly supported service paths (e.g. inventory restock/batch receive as wired) and only when unlocked with no explicit password provided. Other password-gated service APIs require explicit proofs; where backend APIs accept no password parameter, unlock+CSRF gating is the web-layer control and explicit proof fields may be accepted but not consumed.
- Not all legacy minor admin forms were exhaustively redesigned for UX, but hardening focused on CSRF/unlock/proof routing and adding explicit proof inputs for sensitive flows. Any additional service-specific proof requirements outside modified scope require `app_core` changes.
- Invoice/receipt/report text generation and deep portfolio assembly live in `app_core.services` (out of scope). Web routes/templates render/download generated artifacts and present available traceability with safe handling of missing data.
