"""Final coverage closure — every reachable branch."""
import pytest
import services
import db
from db import connect, transaction
from conftest import make_reservation
from datetime import datetime, timedelta
from unittest.mock import patch


# ═══════════════════════════════════════════════════════════════════════════
# DB LAYER
# ═══════════════════════════════════════════════════════════════════════════

def test_db_close_reopens(clean_db):
    """Cover db.close() — closes then reopens on next connect()."""
    assert db._conn is not None
    db.close()
    assert db._conn is None
    # Reopen
    c = db.connect()
    assert c is not None


def test_db_transaction_commit_success(clean_db):
    with db.transaction() as c:
        c.execute("INSERT INTO guests (canonical_name) VALUES ('commit_ok')")
    assert connect().execute(
        "SELECT 1 FROM guests WHERE canonical_name='commit_ok'").fetchone()


# ═══════════════════════════════════════════════════════════════════════════
# SETTINGS — text type, save error path
# ═══════════════════════════════════════════════════════════════════════════

def test_validate_text_type(clean_db):
    norm, err = services._validate_setting("property_address", "Line1\nLine2")
    assert err is None
    assert "Line1" in norm


def test_save_settings_partial_error_rolls_back(clean_db):
    """If any value is invalid, nothing is saved."""
    count, errs = services.save_settings(
        {"vat_rate": "0.15", "min_nights": "-1"}, "testpass")
    assert count == 0
    assert len(errs) == 1
    # vat_rate should NOT have been saved
    assert services.get_setting("vat_rate") == "0.14"


def test_audit_failure_swallowed(clean_db):
    """_audit failures must not propagate (defensive)."""
    with patch("db.transaction", side_effect=Exception("db down")):
        # Should not raise
        services._audit("test_action")


# ═══════════════════════════════════════════════════════════════════════════
# AUTH — lockout edge cases
# ═══════════════════════════════════════════════════════════════════════════

def test_lockout_expires(clean_db):
    """After lockout window passes, correct password works again."""
    services.set_setting("lockout_attempts", "2")
    services.set_setting("lockout_minutes", "5")
    services.verify_password("wrong")
    services.verify_password("wrong")
    assert services.verify_password("testpass") is False
    # Simulate lockout expiry
    with transaction() as c:
        past = (datetime.now() - timedelta(minutes=10)).isoformat()
        c.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('lockout_until', ?)", (past,))
    assert services.verify_password("testpass") is True


def test_lockout_invalid_timestamp(clean_db):
    """Corrupt lockout_until is ignored gracefully."""
    with transaction() as c:
        c.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('lockout_until', 'garbage')")
    # Should not crash — treats as expired
    assert services.verify_password("testpass") is True


def test_verify_no_salt_stored(clean_db):
    """If password hash exists but salt missing, verify returns False."""
    with transaction() as c:
        c.execute("DELETE FROM app_metadata WHERE key='super_password_salt'")
    assert services.verify_password("testpass") is False


# ═══════════════════════════════════════════════════════════════════════════
# GUESTS — edge cases
# ═══════════════════════════════════════════════════════════════════════════

def test_find_guest_matches_empty_name(clean_db):
    assert services.find_guest_matches("") == []
    assert services.find_guest_matches("   ") == []


def test_find_or_create_empty_raises(clean_db):
    with pytest.raises(ValueError, match="required"):
        services.find_or_create_guest("")


# ═══════════════════════════════════════════════════════════════════════════
# RESERVATIONS — status edge cases
# ═══════════════════════════════════════════════════════════════════════════

def test_update_status_not_found(clean_db):
    with pytest.raises(ValueError, match="not found"):
        services.update_reservation_status(9999, "Checked In")


def test_update_status_invalid_transition(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError, match="Cannot move"):
        services.update_reservation_status(rid, "Weird")


def test_cancel_not_found(clean_db):
    with pytest.raises(ValueError, match="not found"):
        services.cancel_reservation(9999, "testpass")


def test_no_show_not_found(clean_db):
    with pytest.raises(ValueError, match="not found"):
        services.mark_no_show(9999, "testpass")


def test_delete_not_found(clean_db):
    with pytest.raises(ValueError, match="not found"):
        services.delete_reservation(9999, "testpass")


def test_delete_refuses_pos_items(clean_db):
    rid = make_reservation()
    services.add_pos_item(rid, "Water", 1, 20, None)
    with pytest.raises(ValueError, match="POS items"):
        services.delete_reservation(rid, "testpass")


# ═══════════════════════════════════════════════════════════════════════════
# POS — negative stock branch
# ═══════════════════════════════════════════════════════════════════════════

def test_add_pos_negative_stock_blocked(clean_db):
    services.set_setting("allow_negative_stock", "false")
    rid = make_reservation()
    with pytest.raises(ValueError, match="Insufficient"):
        services.add_pos_item(rid, "Propane", 100, 250, 4)  # stock 3


def test_add_pos_walkin_with_reservation_id_none(clean_db):
    """None is walk-in, equivalent to 0."""
    result = services.add_pos_item(None, "Water", 1, 20, None)
    assert result["pos_id"] > 0
    # No reservation_id set
    row = connect().execute("SELECT reservation_id FROM pos_tabs WHERE id=?",
                            (result["pos_id"],)).fetchone()
    assert row["reservation_id"] is None


def test_void_walkin_pos(clean_db):
    result = services.add_pos_item(None, "Water", 1, 20, 1)
    services.void_pos_item(result["pos_id"], "testpass")
    row = connect().execute("SELECT voided_at FROM pos_tabs WHERE id=?",
                            (result["pos_id"],)).fetchone()
    assert row["voided_at"] is not None


# ═══════════════════════════════════════════════════════════════════════════
# PAYMENTS — nonexistent, zero-amount paths
# ═══════════════════════════════════════════════════════════════════════════

def test_record_payment_zero_amount(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.record_payment(rid, 0.0, "Cash")


def test_record_payment_negative_amount(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.record_payment(rid, -50.0, "Cash")


def test_record_payment_none_reservation_walkin(clean_db):
    services.record_payment(None, 100.0, "Cash")
    row = connect().execute("SELECT amount FROM payments WHERE reservation_id IS NULL").fetchone()
    assert row["amount"] == 100.0


def test_record_refund_zero_amount(clean_db):
    rid = make_reservation(deposit=100.0)
    with pytest.raises(ValueError):
        services.record_refund(rid, 0.0, "Cash", "", "testpass")


def test_record_refund_negative_amount(clean_db):
    rid = make_reservation(deposit=100.0)
    with pytest.raises(ValueError):
        services.record_refund(rid, -50.0, "Cash", "", "testpass")


# ═══════════════════════════════════════════════════════════════════════════
# EXPENSES — invalid category FK
# ═══════════════════════════════════════════════════════════════════════════

def test_expense_invalid_category_raises(clean_db):
    with pytest.raises(Exception):  # FK violation
        services.record_operational_expense(9999, "X", 100.0)


def test_expense_zero_amount_raises(clean_db):
    with pytest.raises(ValueError, match="positive"):
        services.record_operational_expense(1, "X", 0.0)


def test_void_expense_not_found(clean_db):
    with pytest.raises(ValueError, match="not found"):
        services.void_expense(9999, "testpass")


# ═══════════════════════════════════════════════════════════════════════════
# BALANCE — cancelled/no-show/checked-out branches
# ═══════════════════════════════════════════════════════════════════════════

def test_balance_cancelled_returns_zero(clean_db):
    rid = make_reservation()
    services.cancel_reservation(rid, "testpass")
    assert services.calculate_guest_balance(rid) == 0.0


def test_balance_noshow_returns_zero(clean_db):
    rid = make_reservation()
    services.mark_no_show(rid, "testpass")
    assert services.calculate_guest_balance(rid) == 0.0


def test_balance_nonexistent_returns_zero(clean_db):
    assert services.calculate_guest_balance(9999) == 0.0


def test_breakdown_nonexistent_returns_none(clean_db):
    assert services.get_balance_breakdown(9999) is None


def test_breakdown_cancelled(clean_db):
    rid = make_reservation()
    services.cancel_reservation(rid, "testpass")
    bd = services.get_balance_breakdown(rid)
    assert bd["balance_due"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# LEDGER / A/R
# ═══════════════════════════════════════════════════════════════════════════

def test_summary_empty(clean_db):
    s = services.get_general_ledger_summary()
    assert s["billed_revenue"] == 0
    assert s["ar_balance"] == 0


def test_summary_ignores_negative_balances(clean_db):
    """Overpaid reservation doesn't count in A/R."""
    rid = make_reservation(rate=100.0, ci="2026-12-01", co="2026-12-02")
    services.record_payment(rid, 5000.0, "Cash")  # huge overpay
    s = services.get_general_ledger_summary()
    # Balance is negative (guest owes us nothing), not in A/R
    assert s["ar_balance"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# REPORTS — empty data branches
# ═══════════════════════════════════════════════════════════════════════════

def test_pl_no_expense_category(clean_db):
    text = services.report_pl_statement("2026-01-01", "2026-01-31")
    assert "COGS" in text


def test_daily_ops_empty(clean_db):
    text = services.report_daily_ops("2026-01-01")
    assert "ARRIVALS (0)" in text


def test_payment_methods_empty(clean_db):
    text = services.report_payment_methods("2026-01-01", "2026-01-31")
    assert "TOTAL" in text


# ═══════════════════════════════════════════════════════════════════════════
# INVENTORY — remaining branches
# ═══════════════════════════════════════════════════════════════════════════

def test_restock_nonexistent_item(clean_db):
    with pytest.raises(ValueError, match="not found"):
        services.restock_inventory(9999, 10, 5.0, "testpass")


def test_restock_negative_cost(clean_db):
    with pytest.raises(ValueError, match="negative"):
        services.restock_inventory(1, 10, -5.0, "testpass")


def test_restock_zero_qty(clean_db):
    with pytest.raises(ValueError, match="positive"):
        services.restock_inventory(1, 0, 5.0, "testpass")


def test_update_inventory_empty_name(clean_db):
    with pytest.raises(ValueError, match="required"):
        services.update_inventory_item(1, "", "F&B", "pcs", 25.0, 5, 200, "testpass")


def test_add_inventory_empty_name(clean_db):
    with pytest.raises(ValueError, match="required"):
        services.add_inventory_item("", "F&B", "pcs", 50, 5.0, 15.0, 10, 100, "testpass")


def test_delete_inventory_not_found(clean_db):
    with pytest.raises(ValueError, match="not found"):
        services.delete_inventory_item(9999, "testpass")


# ═══════════════════════════════════════════════════════════════════════════
# ATTRIBUTION
# ═══════════════════════════════════════════════════════════════════════════

def test_attribution_valid(clean_db):
    ok, msg = services.verify_app_attribution()
    assert ok is True


def test_attribution_missing(clean_db):
    with transaction() as c:
        c.execute("DELETE FROM app_metadata WHERE key='gift_attribution'")
    ok, msg = services.verify_app_attribution()
    assert ok is False
    assert "missing" in msg.lower()