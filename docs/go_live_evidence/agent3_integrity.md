# Agent 3 Evidence: Data Integrity, Migrations, and Correctness

## Status
- Phase: Started (baseline checks available; staging dataset validation pending)

## Baseline evidence
- Full regression (current session, HEAD 0e58f8d): `844 passed, 1 warning, 0 failed` (`cd spms && PYTHONPATH=src PM_WEB_SECRET_KEY=test-secret python3 -m pytest tests -q`, ~113s).
- FEFO expiry-block fix verified in `src/app_core/services/_monolith.py` (`Insufficient non-expired batch stock available for the requested quantity.` when blocked shortfall).
- Quality/invariant coverage exists in `tests/test_quality_gate_foundations.py`, inventory/recipes/payments/reservations suites (to be re-run on RC and on staging dataset copies).

## Planned tasks
- [ ] Migration/upgrade validation on dataset copies (clean init, prior schema upgrades incl. v8->v9 backfill), idempotency checks
- [ ] Ledger/transaction invariants and reconciliation (payments/refunds/expenses/POS/voids)
- [ ] Inventory/batch/FEFO/expiry correctness (warn/block, multi/specific batch, negative-stock semantics, traceability)
- [ ] Domain flows: reservations/folio lifecycle, POS walk-in/reservation charges, meals/recipes deduction/restore, activities, labels/scan, void/restore, COGS/money/qty/rounding
- [ ] Seed/import/export and backup data fidelity checks
- [ ] Produce reconciliation report (anonymized)

## Evidence/log
- 2026-11-17: `PYTHONPATH=src python3 -m app_core.doctor --json` -> ok=true, schema user_version=9 matches expected, sqlite integrity ok, foreign_key_check ok, batch_stock mismatch_count=0, data dirs writable.
- 2026-11-17: `PYTHONPATH=src python3 -m app_core.doctor --backup --backup-to /data/data/com.termux/files/usr/tmp/opencode/pm_go_live_test_backup.db --json` -> backup created and verified ok (schema/integrity/consistency checks passed). Off-host retention and full restore drill on clean environment still required (Agent 5).
- 2026-11-17: `PYTHONPATH=src python3 -m pytest tests/test_quality_gate_foundations.py -q` -> 9 passed.
- 2026-11-17: `PYTHONPATH=src PM_WEB_SECRET_KEY=test-secret python3 -m pytest tests -q -k "inventory or batch or fefo or expiry or ledger or payment or reservation or recipe or cogs or seed or import or export or migration"` -> 250 passed, 594 deselected, 1 warning. Migration/reconciliation validation on sanitized real dataset copies and full restore drill still required.
