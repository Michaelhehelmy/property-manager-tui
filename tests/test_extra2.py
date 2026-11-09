"""Additional tests to close remaining coverage branches."""
import pytest
import services
from db import connect
from conftest import make_reservation


# ═══════════════════════════════════════════════════════════════════════════
# _validate_setting — every branch
# ═══════════════════════════════════════════════════════════════════════════

def test_validate_pct_zero(clean_db):
    norm, err = services._validate_setting("vat_rate", "0.0")
    assert norm == "0.0" and err is None


def test_validate_pct_one(clean_db):
    norm, err = services._validate_setting("vat_rate", "1.0")
    assert norm == "1.0" and err is None


def test_validate_int_zero(clean_db):
    norm, err = services._validate_setting("min_nights", "0")
    assert norm == "0" and err is None


def test_validate_text_empty(clean_db):
    norm, err = services._validate_setting("property_address", "")
    assert norm == "" and err is None


def test_validate_text_multiline(clean_db):
    norm, err = services._validate_setting("property_address", "Line1\nLine2")
    assert "Line1" in norm


def test_validate_bool_garbage(clean_db):
    norm, err = services._validate_setting("checkout_requires_payment", "xyz")
    assert norm == "false"


def test_validate_bool_yes(clean_db):
    norm, err = services._validate_setting("checkout_requires_payment", "yes")
    assert norm == "true"


def test_validate_bool_on(clean_db):
    norm, err = services._validate_setting("checkout_requires_payment", "on")
    assert norm == "true"


def test_validate_bool_one(clean_db):
    norm, err = services._validate_setting("checkout_requires_payment", "1")
    assert norm == "true"


# ═══════════════════════════════════════════════════════════════════════════
# save_settings — atomic error path
# ═══════════════════════════════════════════════════════════════════════════

def test_save_settings_partial_invalid_nothing_saved(clean_db):
    count, errs = services.save_settings(
        {"vat_rate": "0.15", "min_nights": "-1"}, "testpass")
    assert count == 0
    assert len(errs) == 1
    assert services.get_setting("vat_rate") == "0.14"


def test_save_settings_all_invalid(clean_db):
    count, errs = services.save_settings(
        {"vat_rate": "2", "min_nights": "-1"}, "testpass")
    assert count == 0
    assert len(errs) == 2


# ═══════════════════════════════════════════════════════════════════════════
# Guest matching — every branch
# ═══════════════════════════════════════════════════════════════════════════

def test_find_matches_empty_name(clean_db):
    assert services.find_guest_matches("") == []


def test_find_matches_by_phone(clean_db):
    services.find_or_create_guest("Alice", phone="+201001234567")
    matches = services.find_guest_matches("Alice", phone="01001234567")
    assert len(matches) == 1
    assert matches[0]["score"] == 1.0


def test_find_matches_by_email(clean_db):
    services.find_or_create_guest("Alice", email="alice@x.com")
    matches = services.find_guest_matches("Alice", email="ALICE@x.com")
    assert len(matches) == 1
    assert matches[0]["score"] >= 0.98


def test_find_matches_no_phone_entered(clean_db):
    services.find_or_create_guest("Alice", phone="+201001234567")
    matches = services.find_guest_matches("Alice")
    assert len(matches) == 1


def test_find_matches_no_phone_stored(clean_db):
    services.find_or_create_guest("Alice")
    matches = services.find_guest_matches("Alice", phone="+201001234567")
    assert len(matches) == 1


def test_find_matches_threshold_zero(clean_db):
    services.find_or_create_guest("Alice")
    matches = services.find_guest_matches("Bob", threshold=0.0)
    assert len(matches) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# Reservations — all transitions
# ═══════════════════════════════════════════════════════════════════════════

def test_transition_reserved_to_cancelled(clean_db):
    rid = make_reservation()
    services.cancel_reservation(rid, "testpass")
    assert services.get_reservation(rid)["status"] == "Cancelled"


def test_transition_reserved_to_noshow(clean_db):
    rid = make_reservation()
    services.mark_no_show(rid, "testpass")
    assert services.get_reservation(rid)["status"] == "No-Show"


def test_transition_checkedin_to_checkedout(clean_db):
    rid = make_reservation()
    services.update_reservation_status(rid, "Checked In")
    services.update_reservation_status(rid, "Checked Out")
    assert services.get_reservation(rid)["status"] == "Checked Out"


def test_transition_deleted_to_anything_fails(clean_db):
    rid = make_reservation()
    services.delete_reservation(rid, "testpass")
    with pytest.raises(ValueError):
        services.update_reservation_status(rid, "Checked In")


# ═══════════════════════════════════════════════════════════════════════════
# Room pricing edges
# ═══════════════════════════════════════════════════════════════════════════

def test_update_room_price_zero(clean_db):
    services.update_room_price(1, 0.0, "testpass")
    assert services.get_room(1)["base_price"] == 0.0


def test_update_room_price_huge(clean_db):
    services.update_room_price(1, 99999.99, "testpass")
    assert services.get_room(1)["base_price"] == 99999.99


# ═══════════════════════════════════════════════════════════════════════════
# Audit trail coverage
# ═══════════════════════════════════════════════════════════════════════════

def test_audit_covers_multiple_actions(clean_db):
    make_reservation()
    services.update_room_price(1, 60.0, "testpass")
    services.add_rate_plan("Test Plan", 5.0, "testpass")
    log = services.get_audit_log(100)
    actions = {r["action"] for r in log}
    assert "reservation_create" in actions
    assert "room_price_change" in actions
    assert "rate_plan_add" in actions


def test_audit_log_actor(clean_db):
    make_reservation()
    log = services.get_audit_log(10)
    assert all(r["actor"] == "manager" for r in log)


# ═══════════════════════════════════════════════════════════════════════════
# Balance edge cases
# ═══════════════════════════════════════════════════════════════════════════

def test_balance_overpayment(clean_db):
    rid = make_reservation(rate=100.0, ci="2026-12-01", co="2026-12-02")
    services.record_payment(rid, 500.0, "Cash")
    assert services.calculate_guest_balance(rid) < 0


def test_balance_after_partial_refund(clean_db):
    rid = make_reservation(rate=100.0, ci="2026-12-01", co="2026-12-02")
    services.record_payment(rid, 200.0, "Cash")
    services.record_refund(rid, 50.0, "Cash", "test", "testpass")
    assert services.calculate_guest_balance(rid) < 0


# ═══════════════════════════════════════════════════════════════════════════
# Cash flow
# ═══════════════════════════════════════════════════════════════════════════

def test_cash_flow_multiple_payments(clean_db):
    rid = make_reservation()
    services.record_payment(rid, 100.0, "Cash")
    services.record_payment(rid, 50.0, "Card")
    s = services.get_general_ledger_summary()
    assert s["cash_in"] >= 150.0


def test_cash_out_includes_expenses(clean_db):
    services.record_operational_expense(1, "Test", 100.0)
    s = services.get_general_ledger_summary()
    assert s["cash_out"] >= 100.0


# ═══════════════════════════════════════════════════════════════════════════
# Reports — empty data
# ═══════════════════════════════════════════════════════════════════════════

def test_pl_no_data(clean_db):
    text = services.report_pl_statement("2099-01-01", "2099-01-02")
    assert "0.00" in text


def test_vat_no_data(clean_db):
    text = services.report_vat("2099-01-01", "2099-01-02")
    assert "0.00" in text


def test_occupancy_no_data(clean_db):
    text = services.report_occupancy("2099-01-01", "2099-01-02")
    assert "OCCUPANCY" in text


# ═══════════════════════════════════════════════════════════════════════════
# Heatmap coverage
# ═══════════════════════════════════════════════════════════════════════════

def test_heatmap_full_range(clean_db):
    make_reservation(ci="2027-06-01", co="2027-06-05", room_id=1)
    data = services.get_occupancy_heatmap_data("2027-06-01", "2027-06-05")
    # Inclusive range: 01, 02, 03, 04, 05 = 5 days
    assert len(data["dates"]) == 5


# ═══════════════════════════════════════════════════════════════════════════
# Language functions
# ═══════════════════════════════════════════════════════════════════════════

def test_get_language_returns_current(clean_db):
    services.set_language("ar")
    assert services.get_language() == "ar"
    services.set_language("en")
    assert services.get_language() == "en"


# ═══════════════════════════════════════════════════════════════════════════
# Count rate plan usage
# ═══════════════════════════════════════════════════════════════════════════

def test_count_zero(clean_db):
    pid = services.get_all_rate_plans()[0]["id"]
    assert services.count_reservations_using_plan(pid) == 0


def test_count_two(clean_db):
    pid = services.get_all_rate_plans()[0]["id"]
    services.create_reservation("A", "Direct", 1, pid, 100, "2026-12-01", "2026-12-03")
    services.create_reservation("B", "Direct", 2, pid, 100, "2026-12-01", "2026-12-03")
    assert services.count_reservations_using_plan(pid) == 2


# ═══════════════════════════════════════════════════════════════════════════
# UI helpers
# ═══════════════════════════════════════════════════════════════════════════

def test_fmt_negative():
    assert services.fmt(-100.0) == "-100.00 EGP"


def test_selected_id_no_cursor():
    class MockTable:
        row_count = 1
        cursor_row = None
        def get_row_at(self, i):
            raise IndexError("no cursor")
    assert services.selected_id(MockTable()) is None


# ═══════════════════════════════════════════════════════════════════════════
# Attribution
# ═══════════════════════════════════════════════════════════════════════════

def test_attribution_intact(clean_db):
    ok, msg = services.verify_app_attribution()
    assert ok is True


# ═══════════════════════════════════════════════════════════════════════════
# Backup
# ═══════════════════════════════════════════════════════════════════════════

def test_backup_contains_data(clean_db):
    make_reservation(guest="BackupTest")
    path = services.backup_now()
    import sqlite3
    conn = sqlite3.connect(path)
    rows = conn.execute("SELECT guest_name FROM reservations").fetchall()
    conn.close()
    assert any("BackupTest" in r[0] for r in rows)


# ═══════════════════════════════════════════════════════════════════════════
# Session
# ═══════════════════════════════════════════════════════════════════════════

def test_touch_session_refreshes(clean_db):
    services.unlock_session("testpass")
    before = services._session["last_activity"]
    import time
    time.sleep(0.01)
    services.touch_session()
    assert services._session["last_activity"] > before
    services.lock_session()


# ═══════════════════════════════════════════════════════════════════════════
# Update inventory requires password
# ═══════════════════════════════════════════════════════════════════════════

def test_update_inventory_wrong_password(clean_db):
    with pytest.raises(PermissionError):
        services.update_inventory_item(1, "X", "F&B", "pcs", 25.0, 5, 200, "wrong")


# ═══════════════════════════════════════════════════════════════════════════
# Voids on custom items
# ═══════════════════════════════════════════════════════════════════════════

def test_void_custom_item(clean_db):
    rid = make_reservation()
    result = services.add_pos_item(rid, "Custom", 1, 50, None)
    services.void_pos_item(result["pos_id"], "testpass")
    row = connect().execute(
        "SELECT voided_at FROM pos_tabs WHERE id=?", (result["pos_id"],)).fetchone()
    assert row["voided_at"] is not None