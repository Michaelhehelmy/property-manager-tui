"""Ledger integrity: append-only, ref_type separation, no double-counting."""
import pytest
import services
from db import connect
from conftest import make_reservation, count_ledger, sum_ledger


def test_charge_has_separate_tax_columns(clean_db):
    rid = make_reservation(rate=100.0)
    charge = connect().execute(
        "SELECT net, service, vat, city_tax, gross FROM general_ledger WHERE ref_type='RES' AND entry_type='CHARGE'"
    ).fetchone()
    assert charge["net"] == pytest.approx(200.0)
    assert charge["service"] > 0
    assert charge["vat"] > 0
    assert charge["gross"] == pytest.approx(charge["net"] + charge["service"] + charge["vat"] + charge["city_tax"])


def test_revenue_summary_excludes_vat(clean_db):
    rid = make_reservation(rate=100.0)
    summary = services.get_general_ledger_summary()
    charge = connect().execute(
        "SELECT net, service, vat FROM general_ledger WHERE ref_type='RES' AND entry_type='CHARGE'"
    ).fetchone()
    expected_revenue = charge["net"] + charge["service"]  # VAT excluded
    assert summary["billed_revenue"] == pytest.approx(expected_revenue)


def test_restock_is_asset_not_expense(clean_db):
    """Restock creates ASSET_IN; should not count as P&L expense."""
    services.restock_inventory(1, 10, 3.0, "testpass")
    summary = services.get_general_ledger_summary()
    assert summary["expenses"] == 0.0  # No COGS, no operating expense


def test_sale_creates_cogs(clean_db):
    rid = make_reservation()
    services.add_pos_item(rid, "Water", 5, 20, 1)
    summary = services.get_general_ledger_summary()
    # COGS = 5 * 3.0 = 15
    assert summary["expenses"] >= 15.0


def test_no_cogs_double_count(clean_db):
    """Restock + sale — total cost counted once."""
    services.restock_inventory(1, 100, 3.0, "testpass")  # Purchase 300
    rid = make_reservation()
    services.add_pos_item(rid, "Water", 2, 20, 1)  # COGS 6
    summary = services.get_general_ledger_summary()
    # Only COGS counts as expense, not the restock
    assert summary["expenses"] == pytest.approx(6.0)


def test_reversal_does_not_delete_original(clean_db):
    rid = make_reservation()
    before = connect().execute(
        "SELECT COUNT(*) FROM general_ledger WHERE ref_type='RES' AND entry_type='CHARGE'"
    ).fetchone()[0]
    services.cancel_reservation(rid, "testpass")
    after = connect().execute(
        "SELECT COUNT(*) FROM general_ledger WHERE ref_type='RES' AND entry_type='CHARGE'"
    ).fetchone()[0]
    assert after == before  # Original not deleted


def test_reversal_links_to_original(clean_db):
    rid = make_reservation()
    services.cancel_reservation(rid, "testpass")
    reversal = connect().execute(
        "SELECT reverses_id FROM general_ledger WHERE entry_type='VOID_CHARGE'"
    ).fetchone()
    assert reversal["reverses_id"] is not None


def test_delete_does_not_affect_walkin(clean_db):
    """Walk-in POS #N is a different ref_type from reservation #N."""
    rid = make_reservation()
    services.add_pos_item(None, "Walkin water", 1, 20, None)
    walkin_before = connect().execute(
        "SELECT COUNT(*) FROM general_ledger WHERE ref_type='POS'"
    ).fetchone()[0]
    services.delete_reservation(rid, "testpass")
    walkin_after = connect().execute(
        "SELECT COUNT(*) FROM general_ledger WHERE ref_type='POS'"
    ).fetchone()[0]
    assert walkin_after == walkin_before


def test_payment_has_pay_ref_type(clean_db):
    rid = make_reservation()
    services.record_payment(rid, 100.0, "Cash")
    pay = connect().execute(
        "SELECT ref_type FROM general_ledger WHERE entry_type='PAYMENT'"
    ).fetchone()
    assert pay["ref_type"] == "PAY"


def test_ar_includes_checkedout_unpaid(clean_db):
    """A/R counts unpaid balance from any active status."""
    services.set_setting("checkout_requires_payment", "false")
    rid = make_reservation()
    services.update_reservation_status(rid, "Checked In")
    services.update_reservation_status(rid, "Checked Out")
    summary = services.get_general_ledger_summary()
    assert summary["ar_balance"] > 0


def test_cash_flow_includes_expenses(clean_db):
    services.record_operational_expense(1, "X", 500.0)
    summary = services.get_general_ledger_summary()
    assert summary["cash_out"] >= 500.0


def test_timestamp_is_local(clean_db):
    rid = make_reservation()
    row = connect().execute(
        "SELECT at FROM general_ledger WHERE ref_type='RES' LIMIT 1"
    ).fetchone()
    # Should be recent (within 60 seconds of now)
    from datetime import datetime, timedelta
    at = datetime.strptime(row["at"], "%Y-%m-%d %H:%M:%S")
    assert (datetime.now() - at).total_seconds() < 60