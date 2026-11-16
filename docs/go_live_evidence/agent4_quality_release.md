# Agent 4 Evidence: Test, CI, E2E, Performance, and Release Engineering

## Status
- Phase: Started (baseline local validation; CI/RC/E2E pending)

## Baseline evidence
- Targeted web tests after fixes: `PYTHONPATH=src PM_WEB_SECRET_KEY=test-secret python3 -m pytest tests/web/test_web_routes_core.py tests/web/test_web_reservations.py -q` -> 6 passed, 1 warning.
- Prior full suite: `885 passed, 1 warning, 0 failed` (log `/data/data/com.termux/files/usr/tmp/opencode/pm_full_tests_final2.log`).
- Warning: Starlette/TestClient httpx deprecation warning (non-functional; triage/document).

## Planned tasks
- [ ] Re-run full regression on RC and confirm CI green
- [ ] E2E critical journeys (web + TUI headless where applicable): unlock, reservations lifecycle, POS/batches/FEFO/expiry/labels, payments/refunds/voids, invoices/exports/downloads, settings/seed/backup/update checks
- [ ] Negative/security E2E: unauthorized/locked, CSRF invalid, path traversal, malformed inputs
- [ ] Stress/concurrency smoke under `--workers 1`
- [ ] Performance smoke on target-class hardware
- [ ] Pinned RC/final tag, changelog/release notes, install/update/rollback verification
- [ ] Flaky test triage and quality summary

## Evidence/log
- (to be filled with CI links, command outputs, timings)
