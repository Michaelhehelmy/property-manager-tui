# Web Authz/CSRF Matrix (`ui/web`)

Scope: `ui/web/server.py`, `ui/web/deps.py`, `ui/web/routes/*.py`  
Out of scope for this change: `src/app_core/**`, `ui/tui/**`, `tests/**`

## Enforcement model

### CSRF
- **Centralized middleware** (`ui/web/server.py`) enforces CSRF for all state-changing methods (`POST/PUT/PATCH/DELETE`) except `/health` and `/healthz`.
  - `application/json` or requests with `x-csrf-token` header: header token is verified.
  - Form submissions (`application/x-www-form-urlencoded`, `multipart/form-data`, or empty content-type): form field `csrf` is verified.
- Form bodies are cached on `request.state.cached_form` by middleware so route handlers can safely read them again via `deps.get_form(request)` without double-consume issues.
- `deps.require_csrf_form(request)` verifies+returns a form explicitly when needed; `deps.require_unlocked_form(request)` centralizes form access + per-browser unlock for sensitive mutations.
- Tokens are per-browser-session, stored in signed cookie session data, and compared with constant-time comparison.

### Per-browser web unlock (session model)
- Web authorization uses **per-browser signed-session unlock state** (`web_unlock_last` timestamp) with timeout/expiry; it does **not** rely on process-global `services._session` for authorization decisions.
- Timeout is sliding (touched on each request while unlocked) using `services.get_setting("session_timeout_min", "5")`.
- `deps.is_unlocked(request)` / `deps.require_unlock(request)` are the web authz gates; expired sessions are treated as locked.
- `deps.unlock()` verifies the super password, records unlock time in the browser session, and performs best-effort in-process backend session alignment for legacy single-worker internals only (compatibility). `deps.lock()` clears browser unlock state and best-effort locks backend session.

### Service password/proof handling
- Routes that invoke `app_core.services` APIs requiring a super-password proof must pass the **explicit password supplied in the form**; web unlock alone must not bypass service-level password checks.
- `deps.service_password(request, form, allow_session_sentinel=True)` centralizes this: it prefers the provided form password, and only when empty, the browser session is unlocked, and the target service explicitly supports the `"__session__"` sentinel (e.g. inventory restock / batch receive paths that delegate to batch APIs), it passes `"__session__"` with best-effort backend-session sync. For APIs that only accept real passwords, the provided (possibly empty) value is passed through so the service enforces its own requirements.
- `deps.sync_backend_session_if_possible()` is a best-effort compatibility bridge for legacy single-worker internals and is not used for web authorization decisions.

## Mutating route audit (final)

| Route group/module | Mutations | CSRF | Unlock | Sensitivity / service-proof notes |
|---|---|---|---|---|
| `accounting.py` | `POST /accounting/expense`, `/accounting/expense/{eid}/void`, `/accounting/payment`, `/accounting/refund` | Yes | Yes | Financial/admin operations. `void`/`refund` pass explicit form password to service password checks (no weakening). |
| `assistant.py` | `POST /assistant/ask`, `/assistant/feedback`, `/assistant/clear` | Yes | Conditional for `ask`/`feedback` (`assistant_require_unlock` policy or web unlocked); `clear` CSRF-only | Operational assistant remains usable per policy; sensitive assistant access gated by unlock policy. Session-history clear is non-destructive to business data (CSRF-only). |
| `dashboard.py` | none | n/a | n/a | Read-only. |
| `files.py` | none (downloads) | n/a | Yes on `GET /files/{kind}/{name}` | Protected export/invoice downloads; path constrained by `safe_download_path`. |
| `guests.py` | `POST /guests/{gid}/update` | Yes | Yes | Guest/profile modification (sensitive). Passes explicit form password to `services.update_guest`. |
| `pos_inventory.py` | `POST /pos/cart/add`, `/pos/cart/remove`, `/pos/cart/clear`, `/pos/walkin` | Yes | No (CSRF-only, operational front-desk) | Walk-in cart/session and walk-in checkout flows remain usable without unlock; state-changing requests remain CSRF-protected. |
| `pos_inventory.py` | `POST /pos/add`, `/pos/void/{pos_id}`, `/inventory/restock`, `/inventory/add`, `/recipes/set`, `/recipes/clear`, `/meals/save`, `/meals/delete`, `/activities/create`, `/activities/{aid}/toggle`, `/inventory/batch/receive`, `/inventory/label/item/{item_id}`, `/inventory/label/batch/{batch_id}` | Yes | Yes | Posting to reservation, voids, inventory/stock/batch/recipe/meal/activity mutations, labels (admin/financial/inventory/destructive-sensitive). Restock and batch receive use `service_password(..., allow_session_sentinel=True)` because underlying services support `"__session__"`; other service calls continue to require/pass explicit form passwords as applicable. |
| `reservations.py` | `POST /reservations/create`, `/{rid}/checkin`, `/{rid}/checkout`, `/{rid}/pay` | Yes | No (CSRF-only, operational front-desk) | Operational create/check-in/standard checkout/payment recording preserved; CSRF-protected. |
| `reservations.py` | `POST /reservations/{rid}/checkout-override`, `/{rid}/cancel`, `/{rid}/noshow`, `/{rid}/delete`, `/{rid}/modify`, `/{rid}/refund`, `/{rid}/invoice`, `/{rid}/book-activity` | Yes | Yes | Destructive/financial/admin-sensitive actions. Service calls requiring passwords receive explicit form passwords (override/cancel/no-show/delete/modify/refund). Invoice generation and activity booking are unlock-gated. |
| `reports_analytics.py` | `POST /reports/run`, `/reports/analytics-export` | Yes | Yes | Sensitive reporting/data-export operations. |
| `settings_branding.py` | `POST /unlock`, `/unlock/recovery`, `/lock` | Yes | No (auth/session flows) | Session bootstrap/recovery/lock endpoints; CSRF-protected and validated by service/auth logic. |
| `settings_branding.py` | `POST /settings/updates/check` | Yes | No (CSRF-only) | Non-destructive update availability check. |
| `settings_branding.py` | `POST /settings/branding`, `/settings/save`, `/settings/backup`, `/settings/demo-preset`, `/settings/reset`, `/settings/rate-plans/add`, `/settings/rate-plans/{plan_id}/edit`, `/settings/rate-plans/{plan_id}/delete`, `/settings/seed/export`, `/settings/seed/import`, `/settings/rooms/add`, `/settings/rooms/{room_id}/delete`, `/settings/test-label`, `/settings/updates/apply` | Yes | Yes | Admin/destructive/config/data-management actions. Service calls that require super-password proofs receive explicit form passwords (settings save/reset, demo preset, seed import, rate-plan mutations, etc.); unlock gates access but does not replace required service proofs. |

## Notes / limitations
- Cookie session storage is signed client-side state; unlock expiry is enforced by server logic on each request. Compatible with the existing single-worker localhost MVP deployment model.
- Middleware reads/parses form bodies for unsafe form requests to enforce CSRF centrally; handlers reuse the cached form to avoid double-consume issues.
- `GET /inventory/scan` is a non-mutating redirect helper (no CSRF/unlock mutation surface). Service-proof support is only used where `app_core.services` explicitly accepts `"__session__"`; no security-weakening fallback is introduced for password-only service APIs.
