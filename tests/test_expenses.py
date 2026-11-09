"""Expenses: record, void."""
import pytest
import services
from db import connect


def test_record_expense(clean_db):
    services.record_operational_expense(1, "Electric", 500.0, 0.0, "Monthly")
    row = connect().execute("SELECT net_amount FROM operational_expenses").fetchone()
    assert row["net_amount"] == 500.0


def test_record_expense_creates_ledger(clean_db):
    services.record_operational_expense(1, "Electric", 500.0, 0.0, "")
    row = connect().execute(
        "SELECT net, entry_type, category FROM general_ledger WHERE entry_type='EXPENSE'"
    ).fetchone()
    assert row["net"] == 500.0
    assert row["category"] == "Operating"


def test_record_zero_expense_raises(clean_db):
    with pytest.raises(ValueError):
        services.record_operational_expense(1, "X", 0.0)


def test_record_negative_vat_raises(clean_db):
    with pytest.raises(ValueError):
        services.record_operational_expense(1, "X", 100.0, -5.0)


def test_void_expense(clean_db):
    services.record_operational_expense(1, "X", 100.0)
    eid = connect().execute("SELECT id FROM operational_expenses ORDER BY id DESC LIMIT 1").fetchone()["id"]
    services.void_expense(eid, "testpass")
    row = connect().execute("SELECT voided_at FROM operational_expenses WHERE id=?", (eid,)).fetchone()
    assert row["voided_at"] is not None


def test_void_expense_reverses_ledger(clean_db):
    services.record_operational_expense(1, "X", 100.0)
    eid = connect().execute("SELECT id FROM operational_expenses ORDER BY id DESC LIMIT 1").fetchone()["id"]
    services.void_expense(eid, "testpass")
    reversals = connect().execute(
        "SELECT COUNT(*) FROM general_ledger WHERE entry_type='VOID_EXPENSE'").fetchone()[0]
    assert reversals == 1


def test_void_expense_twice_raises(clean_db):
    services.record_operational_expense(1, "X", 100.0)
    eid = connect().execute("SELECT id FROM operational_expenses ORDER BY id DESC LIMIT 1").fetchone()["id"]
    services.void_expense(eid, "testpass")
    with pytest.raises(ValueError):
        services.void_expense(eid, "testpass")


def test_void_expense_requires_password(clean_db):
    services.record_operational_expense(1, "X", 100.0)
    eid = connect().execute("SELECT id FROM operational_expenses ORDER BY id DESC LIMIT 1").fetchone()["id"]
    with pytest.raises(PermissionError):
        services.void_expense(eid, "wrong")


def test_recent_expenses_excludes_voided(clean_db):
    services.record_operational_expense(1, "X", 100.0)
    services.record_operational_expense(1, "Y", 200.0)
    eid = connect().execute("SELECT id FROM operational_expenses WHERE parameter_name='X'").fetchone()["id"]
    services.void_expense(eid, "testpass")
    recent = services.get_recent_expenses()
    assert len(recent) == 1
    assert recent[0]["parameter_name"] == "Y"