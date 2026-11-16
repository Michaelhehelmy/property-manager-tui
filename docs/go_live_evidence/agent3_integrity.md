# Agent 3 Evidence: Data Integrity, Migrations, and Correctness

## Status
- Phase: Started (baseline checks available; staging dataset validation pending)

## Baseline evidence
- Full regression previously recorded: `885 passed, 1 warning, 0 failed` (environment log `/data/data/com.termux/files/usr/tmp/opencode/pm_full_tests_final2.log`).
- FEFO expiry-block fix verified in `src/app_core/services/_monolith.py` (`Insufficient non-expired batch stock available for the requested quantity.` when blocked shortfall).
- Quality/invariant coverage exists in `tests/test_quality_gate_foundations.py`, inventory/recipes/payments/reservations suites (to be re-run on RC).

## Planned tasks
- [ ] Migration/upgrade validation on dataset copies (clean init, prior schema upgrades incl. v8->v9 backfill), idempotency checks
- [ ] Ledger/transaction invariants and reconciliation (payments/refunds/expenses/POS/voids)
- [ ] Inventory/batch/FEFO/expiry correctness (warn/block, multi/specific batch, negative-stock semantics, traceability)
- [ ] Domain flows: reservations/folio lifecycle, POS walk-in/reservation charges, meals/recipes deduction/restore, activities, labels/scan, void/restore, COGS/money/qty/rounding
- [ ] Seed/import/export and backup data fidelity checks
- [ ] Produce reconciliation report (anonymized)

## Evidence/log
- (to be filled with commands, datasets tested, expected vs actual)
