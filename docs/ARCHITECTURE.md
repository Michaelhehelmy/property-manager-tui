# Architecture Guide

This document describes the high-level architecture, module boundaries, data flow, and extension points for developers and contributors.

## Overview

Property Manager is a **local-first** application with:
- **Shared core/backend:** `src/app_core/` (DB, services/business logic, paths/config, assistant, updater, doctor, barcodes/integrity helpers)
- **Two frontends:**
  - **TUI:** Textual app in `ui/tui/` (entry: `main.py`, launcher: `launcher.py --tui`)
  - **Web UI:** FastAPI + HTMX/Jinja2 in `ui/web/` (entry: `ui.web.server:app`, launcher: `launcher.py --web`)
- **Datastore:** SQLite (local file under `PM_DATA_DIR`/defaults). **Web must run with `--workers 1`.**
- **Landing page:** static `index.html` (+ `wrangler.toml`) for download/install guidance and release-asset auto-detection (deployable via Cloudflare Worker/Pages).

Design intent: keep business rules and data access centralized in `app_core` so both UIs behave consistently.

## Repository layout (developer map)

```text
.
├─ main.py                     # TUI entry point
├─ launcher.py                 # Unified launcher/menu (TUI/Web)
├─ index.html                  # Landing/download page (static)
├─ wrangler.toml               # Cloudflare Worker static-assets config for landing page
├─ pyproject.toml              # Packaging, deps/extras, scripts, ruff config
├─ requirements*.txt           # Runtime/web/dev dependency sets
├─ scripts/                    # Install/setup/update/backup/build/lint/coverage helpers
├─ src/app_core/               # Shared backend (authoritative business/data layer)
│  ├─ db.py                    # SQLite connection/schema/migrations/helpers
│  ├─ paths.py                 # Data dirs/paths, runtime permissions/readiness helpers
│  ├─ services/                # Business logic/services facade + modules (and monolith internals)
│  ├─ assistant/               # Grounded read-only assistant (NLU/tools/engine/runtime/formatting)
│  ├─ updater.py               # Git/release update checks, cached notices, ff-only apply
│  ├─ doctor.py                # Health/integrity/backup-verify/update-status checks
│  └─ barcodes*.py             # Barcode/batch/label related logic (as implemented)
├─ ui/
│  ├─ tui/                     # Textual UI (app, tabs, modals, helpers)
│  └─ web/                     # FastAPI routes/deps/rendering/templates/static
├─ docs/                       # Architecture, dev/ops/security/deployment/runbooks
├─ tests/                      # Pytest suite (core, tui, web, scripts, etc.)
└─ .github/workflows/          # CI and release-native build workflow
```

> Some areas are modularized with compatibility facades (e.g. `app_core.services`, `app_core.assistant`, TUI tabs/modals packages). Prefer stable/public-facing module APIs when extending.

## Core backend (`src/app_core/`)

### Database and migrations (`db.py`)
- Owns SQLite connection management, schema creation, and migrations.
- Migrations run on startup/init paths and should be **additive/backward-compatible** where possible.
- Public helpers (e.g. initialization/readiness checks) are used by web readiness probes and doctor flows.
- **Rule:** avoid destructive schema/data changes without explicit safeguards and backup/restore guidance.

### Paths and runtime data (`paths.py`)
- Resolves data locations (`PM_DATA_DIR` overrides/defaults), DB/export/invoice paths, and ensures directories exist.
- Provides runtime permission/readiness checks used by `/healthz`/`/readyz` and `doctor`.
- Runtime data (DB, exports, invoices, data dir) must be preserved across updates, installs, and most scripts.

### Services/business logic (`services/`)
- Central place for domain operations: reservations/guests/folio/payments, POS/inventory/batches/recipes/meals/activities, accounting/expenses/ledger, reports/analytics, settings/branding, seed/demo/backup helpers, integrity checks, etc.
- Exposed via service modules/facade for UI consumption; some internals remain in monolith implementations with compatibility wrappers.
- **Rule:** validate inputs, enforce business invariants, and keep UIs thin. Parameterize SQL and avoid injection.

### Assistant (`assistant/`)
- **Grounded, read-only** assistant over real app data/tools/services.
- Components include NLU/routing, tool execution, formatting, feedback/corrections, and runtime/engine orchestration.
- Supports response modes (e.g. deterministic core, talking rephrase via compatible endpoint, tool-calling mode) as configured; must not invent data or perform unsafe mutations.

### Updater (`updater.py`)
- **Git installs:** checks commits/changelog and applies **fast-forward-only** updates; preserves runtime data; restart required after apply.
- **Release awareness:** can fetch latest GitHub release metadata for notifications/native-update awareness.
- **Cached update notices:** polling-based checks (configurable, rate-limited/cached) used by Web/TUI notifications.
- Network access to GitHub required for remote checks; failures should degrade gracefully.

### Doctor and integrity (`doctor.py`)
- Operational diagnostics: environment/config/data-dir permissions, DB/schema/integrity checks, backup/verify helpers, update status checks (`--check-updates`), JSON/strict modes.
- Used by CLI and referenced by ops workflows.

## Web UI (`ui/web/`)

- **`server.py`:** FastAPI app factory, middleware (sessions, CSRF enforcement for state-changing methods, request logging/error handling), static/templates mounting, router registration, health/readiness endpoints.
- **`deps.py`:** session/cookie handling, CSRF token/verification, unlock state, auth/rate-limit helpers, flash/session utilities, secure cookie/secret configuration (`PM_WEB_SECRET_KEY`, cookie flags).
- **`routes/`:** endpoint groups (dashboard, reservations, guests, POS/inventory, accounting, reports/analytics, assistant, settings/branding, files, etc.).
- **`route_utils.py` / helpers:** shared route utilities (e.g. safe local redirects/paths, flash redirects).
- **`rendering.py`:** template context assembly (brand, flashes, CSRF, unlock state, update notice banner, etc.).
- **`templates/` & `static/`:** Jinja2/HTMX UI, shared partials/components, CSS/JS assets.

### Web security model (high level)
- **CSRF:** enforced for state-changing requests (forms/tokens and header/middleware paths as applicable).
- **Unlock/super-password:** required for sensitive/destructive/admin mutations (and some protected downloads/exports/actions) per policy.
- **Sessions/secrets:** strong `PM_WEB_SECRET_KEY` required for non-local/shared deployments; cookie security flags configurable for HTTPS deployments.
- **Path/redirect safety:** local-path sanitization and controlled file handling for exports/downloads.
- **Authoritative matrix:** see [`docs/web_authz_matrix.md`](web_authz_matrix.md).

## TUI (`ui/tui/`)

- **`app.py`:** Textual application, startup flow (branding/setup wizard, loading, password setup as needed), update notification check, global behavior.
- **`tabs/`:** feature tabs (front desk/reservations, POS/inventory, accounting, settings, assistant, etc.) with shared/common helpers.
- **`modals/`:** modal screens for CRUD/actions/wizards (branding wizard, auth/unlock-related flows, domain modals).
- **`helpers.py`:** shared UI/util helpers.

TUI calls the same `app_core` services as the web UI and should preserve business/security invariants.

## Key runtime flows

### Startup
1. Resolve paths/data dirs and initialize DB/migrations.
2. Load settings/branding/language as needed.
3. Start TUI flow (wizards/loading) or Web server (`create_app` -> routes/middleware).
4. Optional/best-effort update notification checks (polling/cached).

### Update flow (git install)
1. Check updates/changelog (`updater.check_for_updates`, git remote compare).
2. Apply via fast-forward only when clean (`updater.apply_update`).
3. Preserve runtime data; restart process to load new code; refresh deps if required.

### Update notifications (polling)
- `updater.get_cached_update_notice()` caches results for a configurable interval.
- Web injects notice into render context/banner; TUI shows startup notification.
- Not push-based: clients notify when running and online.

## Extension points (common tasks)

- **New domain capability:** add/extend service logic in `app_core/services` (+ DB/schema changes/migrations in `db.py` if needed) → expose in web route/template and/or TUI tab/modal → add tests and docs.
- **New web endpoint/page:** route in `ui/web/routes/*`, template/partial in `ui/web/templates/*`, enforce CSRF/unlock/rate-limit/path safety as appropriate, update `docs/web_authz_matrix.md` if authz posture changes.
- **New assistant capability:** add grounded read-only tool/NLU mapping in `app_core/assistant/*`, keep responses factual and policy-compliant, add seeds/tests/eval updates.
- **Install/update/ops:** scripts in `scripts/`, docs in `docs/`, CI in `.github/workflows/`.
- **Native releases:** `docs/NATIVE_BUILDS.md`, `.github/workflows/release-native.yml`, landing page asset detection in `index.html`.

## Invariants to preserve
- Shared backend source of truth in `app_core`
- SQLite web runtime: `--workers 1`
- Runtime data preserved across updates/builds/scripts (use `PM_DATA_DIR` appropriately)
- CSRF + unlock/sensitive-action protections and safe file/redirect handling in web
- Grounded, read-only assistant behavior (no invented data, no unsafe mutations)
