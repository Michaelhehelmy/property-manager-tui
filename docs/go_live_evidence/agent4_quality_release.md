# Agent 4 Evidence: Test, CI, E2E, Performance, and Release Engineering

## Status
- Phase: Started (baseline local validation; CI/RC/E2E pending)

## Baseline evidence
- Targeted web tests after fixes: `PYTHONPATH=src PM_WEB_SECRET_KEY=test-secret python3 -m pytest tests/web/test_web_routes_core.py tests/web/test_web_reservations.py -q` -> 6 passed, 1 warning.
- Full regression (current session, HEAD 0e58f8d, includes logger/header fixes + assistant ui_context signature fix): `844 passed, 1 warning, 0 failed` (`PYTHONPATH=src PM_WEB_SECRET_KEY=test-secret python3 -m pytest tests -q`, ~113s).
- Warning: Starlette/TestClient httpx deprecation warning (non-functional; triage/document).

## CI/release readiness assessment (subagent)
- `.github/workflows/ci.yml` not present (no in-repo CI pipeline). Local hooks only: `scripts/lint.sh` (ruff), `scripts/install_deps_and_test.sh` (pytest), `scripts/coverage.sh` (ad hoc; `pytest-cov` not in dev deps). Release/update is git-based/manual per `docs/UPDATES.md`.
- Gaps vs plan: no automated PR/push gates (lint/tests/coverage/build/release), no dedicated E2E/stress CI stages, no coverage threshold/ratchet/baseline, no automated pinned-release verification or platform matrix for constrained deps (e.g. pydantic-core).
- Existing relevant tests: `tests/web/test_p1_8_smoke_e2e.py`, `tests/web/test_golive_web_integration.py`, `tests/test_golive_invariants_migrations_contracts.py` plus authz/invariant markers and broad unit/integration suites.
- Commands: full `PYTHONPATH=src:. python3 -m pytest -c tests/pytest.ini` (or `scripts/install_deps_and_test.sh`), lint `PYTHONPATH=src:. python3 -m ruff check .`, markers `-m authz` / `-m invariant`, coverage via `pytest-cov` + `scripts/coverage.sh`.

## Planned tasks
- [ ] Add/restore CI workflow with required gates (lint, full tests, web/integration/invariant, coverage threshold/ratchet)
- [ ] Re-run full regression on RC and confirm CI green
- [ ] E2E critical journeys (web + TUI headless where applicable): unlock, reservations lifecycle, POS/batches/FEFO/expiry/labels, payments/refunds/voids, invoices/exports/downloads, settings/seed/backup/update checks
- [ ] Negative/security E2E: unauthorized/locked, CSRF invalid, path traversal, malformed inputs
- [ ] Stress/concurrency smoke under `--workers 1`
- [ ] Performance smoke on target-class hardware
- [ ] Pinned RC/final tag, changelog/release notes, install/update/rollback verification (incl. pinned `GIT_REF` flow)
- [ ] Flaky test triage and quality summary

## Evidence/log
- 2026-11-16: Full suite baseline pass recorded (844 passed, 1 warning, 0 failed). CI workflow creation and E2E/stress/release gates still pending.
