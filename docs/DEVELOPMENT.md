# Development Guide

This guide is for contributors, maintainers, and developers forking/extending the project.

## Prerequisites

- **Python 3.10+** (3.11/3.12 recommended)
- **Git**
- **SQLite** (bundled with Python)
- OS/toolchain packages as needed for your platform (build tools may be required for some deps/wheels)
- For web UI: dependencies in `requirements-web.txt`
- For native builds: see [`docs/NATIVE_BUILDS.md`](NATIVE_BUILDS.md)

## Clone and environment setup

```bash
git clone https://github.com/Michaelhehelmy/property-manager-tui.git
cd property-manager-tui

python3 -m venv .venv
# Linux/macOS/Termux:
. .venv/bin/activate
# Windows PowerShell:
# .\.venv\Scripts\Activate.ps1

pip install -U pip
pip install -r requirements.txt -r requirements-web.txt -r requirements-dev.txt
# alternative:
# pip install -e ".[web,dev]"
```

Notes:
- On constrained environments (e.g. iSH/Termux/some Android ABIs), web deps like `pydantic-core` may require Rust/toolchains or matching wheels. Use TUI-only/dev subset if needed: `pip install -r requirements.txt -r requirements-dev.txt`.
- Keep runtime/test data isolated during dev using `PM_DATA_DIR` when appropriate.

## Running the app (development)

### TUI
```bash
PYTHONPATH=src python3 main.py
# or
PYTHONPATH=src python3 launcher.py --tui
```

### Web (SQLite-safe)
```bash
export PM_WEB_SECRET_KEY="$(openssl rand -hex 32)"   # PowerShell: $env:PM_WEB_SECRET_KEY = '...'
PYTHONPATH=src python3 -m uvicorn ui.web.server:app --host 127.0.0.1 --port 8000 --workers 1 --reload
# or without reload:
# PYTHONPATH=src python3 launcher.py --web --host 127.0.0.1 --port 8000
```

**Important:** always run web with **`--workers 1`**. Set a strong `PM_WEB_SECRET_KEY` for non-local/shared access and use HTTPS/private networking for shared deployments.

### Useful environment variables
- `PM_DATA_DIR` — runtime data directory (DB, exports, invoices)
- `PM_WEB_SECRET_KEY` — required/strongly required for non-local web deployments
- `PM_WEB_COOKIE_SECURE`, `PM_WEB_COOKIE_SAMESITE` — cookie hardening (use `Secure` with HTTPS)
- `GITHUB_TOKEN` / `GH_TOKEN` — optional, for update/release checks to reduce rate limiting
- `PYTHONPATH=src` — commonly required when running modules directly from source

## Tests, lint, and quality checks

```bash
# Compile/syntax sanity
PYTHONPATH=src python3 -m compileall -q src ui tests

# Tests
PYTHONPATH=src python3 -m pytest -q

# Targeted tests
PYTHONPATH=src python3 -m pytest -q tests/web/test_web_deps.py

# Lint/dead-code checks (ruff, CI-aligned baseline)
bash scripts/lint.sh

# Coverage
bash scripts/coverage.sh
```

CI (`.github/workflows/ci.yml`) runs lint, compileall, and tests with coverage across supported Python versions. Keep changes compatible.

## Project conventions

- **Architecture:** shared logic in `src/app_core/`; UIs (`ui/tui`, `ui/web`) call services/helpers and render/orchestrate.
- **DB changes:** prefer additive migrations in `db.py`; preserve compatibility and data safety.
- **Web safety/security:** `--workers 1`, CSRF on state-changing requests, unlock/super-password for sensitive/admin actions, safe redirects/paths, no hardcoded secrets.
- **Assistant:** grounded and read-only; never invent records/actions.
- **Style/quality:** clear naming, guard clauses, SRP, avoid dead code; follow existing patterns; pragmatic DRY/Rule of Three.
- **Artifacts:** do not commit runtime data or generated files (`data/`, exports/invoices outputs as appropriate, coverage artifacts, caches).

## Working with major subsystems

### Services and DB
- Add domain logic in `app_core/services` (respecting existing facade/module structure).
- Keep SQL parameterized; validate and normalize inputs.
- Add/adjust migrations carefully and test upgrade paths on existing DB copies.

### Web routes/templates
- Add routes under `ui/web/routes/` and templates under `ui/web/templates/`.
- Use shared deps/utilities for CSRF, unlock enforcement, form handling, safe redirects, and flash/session behavior.
- Update [`docs/web_authz_matrix.md`](web_authz_matrix.md) when route authz/CSRF/unlock behavior changes.

### TUI
- Tabs live in `ui/tui/tabs/`, modals in `ui/tui/modals/`, app wiring in `ui/tui/app.py`.
- Reuse services and keep UX consistent with existing flows/terminology.

### Assistant
- Extend NLU/tools/formatting in `app_core/assistant/`.
- Keep responses grounded in real data/tools; update intents/examples/evals/seeds as needed.

### Updater and notifications
- `app_core/updater.py` handles git checks/ff-only apply and release/cached update notices.
- Polling-based notifications are surfaced in web/TUI; changes should preserve graceful offline behavior and avoid excessive API calls.

## Install/update scripts and platform support

- Unix: `scripts/install.sh`, `scripts/setup.sh`
- Windows: `scripts/install_windows.ps1/.cmd`, `scripts/setup_windows.ps1/.cmd`
- Web convenience: `scripts/update_and_run_web.sh`
- Backups/ops: `scripts/backup_runtime.*`
- Built-in recovery for dirty/stuck git states: `REPAIR=1` (Unix) or `-Repair` / `$env:REPAIR='1'` (Windows)

When changing scripts, preserve runtime data, keep SQLite web `--workers 1`, and document behavior changes.

## Releases, versioning, and native builds

- App/package version: `pyproject.toml` (`[project].version`) and runtime constants as applicable.
- Tag releases (e.g. `vX.Y.Z`) for reproducibility and native build triggers.
- Native binaries: [`docs/NATIVE_BUILDS.md`](NATIVE_BUILDS.md) and `.github/workflows/release-native.yml` (Nuitka matrix, publishes assets on tag releases). Landing page (`index.html`) auto-detects latest release assets.
- Landing page deployment: static `index.html` + `wrangler.toml` (Cloudflare Worker/Pages).

## Documentation expectations

Update docs with behavior/config/ops changes:
- `README.md`, `CONTRIBUTING.md`
- `docs/ARCHITECTURE.md`, `docs/DEVELOPMENT.md`
- `docs/DEPLOYMENT.md`, `docs/UPDATES.md`, `docs/SECURITY.md`, `docs/TROUBLESHOOTING.md`, `docs/BACKUP_RESTORE.md`
- `docs/web_authz_matrix.md`, `assistant.md`, `docs/NATIVE_BUILDS.md` as relevant

## Debugging tips

- **Health/readiness:** `GET /health`, `/healthz`, `/readyz` (web)
- **Doctor:** `PYTHONPATH=src python -m app_core.doctor --json [--check-updates] [--strict]`
- **Backups/verify:** `python scripts/backup_runtime.py --backup`, `--verify <path>`
- **Update issues:** check git state (`status`, branch/ref, ff-only feasibility), use `REPAIR=1` recovery when appropriate, ensure deps refreshed and app restarted.
- **Web issues:** confirm `--workers 1`, `PM_WEB_SECRET_KEY`, CSRF token/unlock state, cookie/HTTPS settings, and data-dir permissions.

## Forking notes

- Rename/rebrand as desired (display/branding, package metadata, repo URLs, landing page/release targets).
- Configure your own secrets, hosting/TLS, CI/release credentials, and GitHub repo references for updater/release checks and workflows.
- Re-validate security, migrations, backups/restore, and platform-specific scripts in your target environments.
