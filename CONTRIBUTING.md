# Contributing to Property Manager (TUI + Web)

Thanks for your interest in contributing, forking, or extending this project.

This repository is a **local-first property management app** with:
- **TUI** (Textual)
- **Web UI** (FastAPI + HTMX/Jinja2)
- **Shared backend** in `src/app_core/` (business logic, DB/services, assistant, updater, doctor)
- **SQLite** datastore (web must run with `--workers 1`)

## Quick links
- **Developer setup & workflows:** [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)
- **Architecture & code map:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- **Deployment/ops:** [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md), [`docs/UPDATES.md`](docs/UPDATES.md), [`docs/SECURITY.md`](docs/SECURITY.md)
- **Web authz/CSRF/unlock matrix (authoritative):** [`docs/web_authz_matrix.md`](docs/web_authz_matrix.md)
- **Native builds (Nuitka/releases):** [`docs/NATIVE_BUILDS.md`](docs/NATIVE_BUILDS.md)
- **Assistant:** [`assistant.md`](assistant.md)

## Ways to contribute
- Bug fixes, reliability/security hardening, tests, docs, UX improvements
- Performance and maintainability refactors (behavior-preserving)
- Platform/install/update improvements (Windows/Unix/Termux/iSH), packaging/release automation
- Assistant/NLU/tooling improvements (grounded, read-only)

## Development principles (project conventions)
- **Shared logic in `app_core`:** keep business rules in `src/app_core/services`/core modules; UIs should orchestrate and render.
- **Preserve runtime data:** never delete or corrupt DB/exports/invoices/`PM_DATA_DIR` in code paths, scripts, or migrations.
- **SQLite safety:** web server always `--workers 1`; avoid patterns that assume concurrent writers.
- **Security:** maintain CSRF protections for state-changing web requests; preserve unlock/super-password gating for sensitive/admin actions per policy (`docs/web_authz_matrix.md`); avoid hardcoded secrets; sanitize/validate inputs and paths; avoid injection/open-redirect issues.
- **Compatibility:** prefer additive DB migrations and backward-compatible service facades/APIs where practical.
- **Code quality:** SRP, clear naming/self-documenting code, guard clauses, avoid dead code/YAGNI, Boy Scout improvements when touching code; apply DRY pragmatically (Rule of Three).
- **Tests:** add/update tests for behavior changes and fixes; keep existing tests passing.

## Getting started (local dev)
```bash
git clone https://github.com/Michaelhehelmy/property-manager-tui.git
cd property-manager-tui
python3 -m venv .venv
. .venv/bin/activate  # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-web.txt -r requirements-dev.txt
# or: pip install -e ".[web,dev]"
```

Run:
```bash
PYTHONPATH=src python3 launcher.py --tui
# Web (always --workers 1)
export PM_WEB_SECRET_KEY="$(openssl rand -hex 32)"  # set appropriately on Windows
PYTHONPATH=src python3 -m uvicorn ui.web.server:app --host 127.0.0.1 --port 8000 --workers 1
```

See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) for full setup, checks, and workflows.

## Validation before submitting
Run the checks relevant to your change (at minimum compile/tests for touched areas):
```bash
PYTHONPATH=src python3 -m compileall -q src ui tests
PYTHONPATH=src python3 -m pytest -q
# lint/dead-code checks (CI-aligned)
bash scripts/lint.sh
# optional coverage
bash scripts/coverage.sh
```
On Windows, run equivalent commands in PowerShell/cmd with the venv activated and `PYTHONPATH=src` set.

## Pull request guidelines
- Keep PRs focused and explain **what** changed and **why**.
- Include reproduction/fix notes for bugs and screenshots/GIFs for UI changes when helpful.
- Update docs when behavior, config, ops, or APIs change (`README.md`, `docs/*`, `assistant.md` as applicable).
- Do not commit runtime/local/generated artifacts: `data/`, `exports/`, `invoices/`, `.coverage*`, `coverage.xml`, `__pycache__/`, `.pytest_cache/`, local backups, `*.bak`, etc.
- Preserve security and data-safety guarantees (CSRF/unlock rules, `--workers 1`, no secrets in code/logs, no destructive migrations without safeguards).

## Reporting issues and security concerns
- Use GitHub Issues for bugs/feature requests with environment, steps to reproduce, expected vs actual behavior, and logs/errors (redact secrets/PII).
- For security-sensitive findings, avoid posting exploit details publicly; report responsibly to maintainers with reproduction details and impact.

## Forking
You are welcome to fork and adapt this project. If you publish a fork:
- Keep attribution/license notices as required.
- Review and set your own secrets/config (`PM_WEB_SECRET_KEY`, `PM_DATA_DIR`, etc.), deployment/TLS, and operational controls.
- Treat update/release/native-build pipelines and landing-page content as fork-specific unless intentionally tracking upstream.
- Re-run tests/checks and validate migrations/data compatibility in your environment.

See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for deeper technical guidance.
