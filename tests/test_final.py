"""Final coverage closure — every reachable error branch."""
import pytest
from app_core import services
from app_core.db import connect
from conftest import make_reservation


# ═══════════════════════════════════════════════════════════════════════════
# PAYMENT ERROR PATHS
# ═══════════════════════════════════════════════════════════════════════════

def test_payment_zero_amount(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.record_payment(rid, 0.0, "Cash")


def test_payment_negative_amount(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.record_payment(rid, -100.0, "Cash")


def test_payment_non_int_reservation(clean_db):
    with pytest.raises(ValueError):
        services.record_payment("not-int", 100.0, "Cash")


def test_payment_nonexistent_reservation(clean_db):
    with pytest.raises(ValueError):
        services.record_payment(9999, 100.0, "Cash")


def test_payment_on_cancelled_reservation(clean_db):
    rid = make_reservation()
    services.cancel_reservation(rid, "testpass")
    with pytest.raises(ValueError, match="Cancelled"):
        services.record_payment(rid, 100.0, "Cash")


def test_payment_on_checkedout_reservation(clean_db):
    rid = make_reservation()
    services.update_reservation_status(rid, "Checked In")
    services.update_reservation_status(rid, "Checked Out")
    with pytest.raises(ValueError):
        services.record_payment(rid, 100.0, "Cash")


def test_payment_on_noshow_reservation(clean_db):
    rid = make_reservation()
    services.mark_no_show(rid, "testpass")
    with pytest.raises(ValueError):
        services.record_payment(rid, 100.0, "Cash")


def test_refund_zero_amount(clean_db):
    rid = make_reservation(deposit=100.0)
    with pytest.raises(ValueError):
        services.record_refund(rid, 0.0, "Cash", "", "testpass")


def test_refund_negative_amount(clean_db):
    rid = make_reservation(deposit=100.0)
    with pytest.raises(ValueError):
        services.record_refund(rid, -50.0, "Cash", "", "testpass")


def test_refund_wrong_password(clean_db):
    rid = make_reservation(deposit=100.0)
    with pytest.raises(PermissionError):
        services.record_refund(rid, 50.0, "Cash", "", "wrong")


def test_refund_more_than_paid(clean_db):
    rid = make_reservation(deposit=100.0)
    with pytest.raises(ValueError, match="exceeds"):
        services.record_refund(rid, 500.0, "Cash", "", "testpass")


# ═══════════════════════════════════════════════════════════════════════════
# POS ERROR PATHS
# ═══════════════════════════════════════════════════════════════════════════

def test_pos_void_already_voided(clean_db):
    rid = make_reservation()
    result = services.add_pos_item(rid, "X", 1, 10, None)
    services.void_pos_item(result["pos_id"], "testpass")
    with pytest.raises(ValueError):
        services.void_pos_item(result["pos_id"], "testpass")


def test_pos_void_nonexistent(clean_db):
    with pytest.raises(ValueError):
        services.void_pos_item(9999, "testpass")


def test_pos_void_wrong_password(clean_db):
    rid = make_reservation()
    result = services.add_pos_item(rid, "X", 1, 10, None)
    with pytest.raises(PermissionError):
        services.void_pos_item(result["pos_id"], "wrong")


def test_pos_zero_qty(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.add_pos_item(rid, "X", 0, 10, None)


def test_pos_negative_qty(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.add_pos_item(rid, "X", -1, 10, None)


def test_pos_negative_price(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.add_pos_item(rid, "X", 1, -10, None)


def test_pos_empty_desc(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.add_pos_item(rid, "", 1, 10, None)


def test_pos_nonexistent_reservation(clean_db):
    with pytest.raises(ValueError):
        services.add_pos_item(9999, "X", 1, 10, None)


def test_pos_nonexistent_inventory(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.add_pos_item(rid, "X", 1, 10, 9999)


def test_pos_inactive_inventory(clean_db):
    from app_core.db import transaction
    with transaction() as c:
        c.execute("INSERT INTO inventory (item_name, category, stock_qty, unit, active) VALUES ('Dead', 'X', 10, 'pcs', 0)")
        iid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.add_pos_item(rid, "X", 1, 10, iid)


def test_pos_insufficient_stock(clean_db):
    services.set_setting("allow_negative_stock", "false")
    rid = make_reservation()
    with pytest.raises(ValueError, match="Insufficient"):
        services.add_pos_item(rid, "Propane", 100, 250, 4)


# ═══════════════════════════════════════════════════════════════════════════
# INVENTORY ERROR PATHS
# ═══════════════════════════════════════════════════════════════════════════

def test_restock_nonexistent_item(clean_db):
    with pytest.raises(ValueError):
        services.restock_inventory(9999, 10, 5.0, "testpass")


def test_restock_zero_qty(clean_db):
    with pytest.raises(ValueError):
        services.restock_inventory(1, 0, 5.0, "testpass")


def test_restock_negative_qty(clean_db):
    with pytest.raises(ValueError):
        services.restock_inventory(1, -5, 5.0, "testpass")


def test_restock_negative_cost(clean_db):
    with pytest.raises(ValueError):
        services.restock_inventory(1, 10, -5.0, "testpass")


def test_restock_non_int_item(clean_db):
    with pytest.raises(ValueError):
        services.restock_inventory("not-int", 10, 5.0, "testpass")


def test_restock_wrong_password(clean_db):
    with pytest.raises(PermissionError):
        services.restock_inventory(1, 10, 5.0, "wrong")


def test_add_inventory_empty_name(clean_db):
    with pytest.raises(ValueError):
        services.add_inventory_item("", "F&B", "pcs", 50, 5.0, 15.0, 10, 100, "testpass")


def test_add_inventory_wrong_password(clean_db):
    with pytest.raises(PermissionError):
        services.add_inventory_item("X", "F&B", "pcs", 50, 5.0, 15.0, 10, 100, "wrong")


def test_update_inventory_empty_name(clean_db):
    with pytest.raises(ValueError):
        services.update_inventory_item(1, "", "F&B", "pcs", 25.0, 5, 200, "testpass")


def test_update_inventory_wrong_password(clean_db):
    with pytest.raises(PermissionError):
        services.update_inventory_item(1, "X", "F&B", "pcs", 25.0, 5, 200, "wrong")


def test_delete_inventory_not_found(clean_db):
    with pytest.raises(ValueError):
        services.delete_inventory_item(9999, "testpass")


def test_delete_inventory_nonzero_stock(clean_db):
    with pytest.raises(ValueError, match="non-zero"):
        services.delete_inventory_item(1, "testpass")


def test_delete_inventory_wrong_password(clean_db):
    from app_core.db import transaction
    with transaction() as c:
        c.execute("INSERT INTO inventory (item_name, category, stock_qty, unit) VALUES ('Empty', 'X', 0, 'pcs')")
        iid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    with pytest.raises(PermissionError):
        services.delete_inventory_item(iid, "wrong")


# ═══════════════════════════════════════════════════════════════════════════
# EXPENSE ERROR PATHS
# ═══════════════════════════════════════════════════════════════════════════

def test_expense_zero_amount(clean_db):
    with pytest.raises(ValueError):
        services.record_operational_expense(1, "X", 0.0)


def test_expense_negative_vat(clean_db):
    with pytest.raises(ValueError):
        services.record_operational_expense(1, "X", 100.0, -5.0)


def test_expense_invalid_category_type(clean_db):
    with pytest.raises(ValueError):
        services.record_operational_expense("not-int", "X", 100.0)


def test_void_expense_not_found(clean_db):
    with pytest.raises(ValueError):
        services.void_expense(9999, "testpass")


def test_void_expense_already_voided(clean_db):
    services.record_operational_expense(1, "X", 100.0)
    eid = connect().execute("SELECT id FROM operational_expenses ORDER BY id DESC LIMIT 1").fetchone()["id"]
    services.void_expense(eid, "testpass")
    with pytest.raises(ValueError):
        services.void_expense(eid, "testpass")


def test_void_expense_wrong_password(clean_db):
    services.record_operational_expense(1, "X", 100.0)
    eid = connect().execute("SELECT id FROM operational_expenses ORDER BY id DESC LIMIT 1").fetchone()["id"]
    with pytest.raises(PermissionError):
        services.void_expense(eid, "wrong")


# ═══════════════════════════════════════════════════════════════════════════
# RESERVATION ERROR PATHS
# ═══════════════════════════════════════════════════════════════════════════

def test_reservation_empty_guest(clean_db):
    with pytest.raises(ValueError):
        services.create_reservation("", "Direct", 1, None, 100, "2026-12-01", "2026-12-03")


def test_reservation_invalid_room(clean_db):
    with pytest.raises(ValueError):
        services.create_reservation("X", "Direct", 0, None, 100, "2026-12-01", "2026-12-03")


def test_reservation_negative_rate(clean_db):
    with pytest.raises(ValueError):
        services.create_reservation("X", "Direct", 1, None, -100, "2026-12-01", "2026-12-03")


def test_reservation_bad_date_format(clean_db):
    with pytest.raises(ValueError):
        services.create_reservation("X", "Direct", 1, None, 100, "12/01/2026", "2026-12-03")


def test_reservation_checkout_before_checkin(clean_db):
    with pytest.raises(ValueError):
        services.create_reservation("X", "Direct", 1, None, 100, "2026-12-03", "2026-12-01")


def test_reservation_same_day(clean_db):
    with pytest.raises(ValueError):
        services.create_reservation("X", "Direct", 1, None, 100, "2026-12-01", "2026-12-01")


def test_reservation_below_min_nights(clean_db):
    services.set_setting("min_nights", "3")
    with pytest.raises(ValueError):
        services.create_reservation("X", "Direct", 1, None, 100, "2026-12-01", "2026-12-02")


def test_reservation_above_max_nights(clean_db):
    services.set_setting("max_nights", "2")
    with pytest.raises(ValueError):
        services.create_reservation("X", "Direct", 1, None, 100, "2026-12-01", "2026-12-10")


def test_reservation_conflict(clean_db):
    make_reservation(ci="2026-12-01", co="2026-12-05", room_id=1)
    with pytest.raises(ValueError, match="already booked"):
        services.create_reservation("X", "Direct", 1, None, 100, "2026-12-03", "2026-12-07")


def test_reservation_negative_deposit(clean_db):
    with pytest.raises(ValueError):
        services.create_reservation("X", "Direct", 1, None, 100, "2026-12-01", "2026-12-03", -10)


def test_status_update_not_found(clean_db):
    with pytest.raises(ValueError):
        services.update_reservation_status(9999, "Checked In")


def test_status_invalid_transition(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError, match="Cannot move"):
        services.update_reservation_status(rid, "Weird")


def test_cancel_not_found(clean_db):
    with pytest.raises(ValueError):
        services.cancel_reservation(9999, "testpass")


def test_cancel_no_password_required(clean_db):
    rid = make_reservation()
    services.cancel_reservation(rid)  # super password not required for reservation ops
    r = services.get_reservation(rid)
    assert r["status"] == "Cancelled"


def test_cancel_already_cancelled(clean_db):
    rid = make_reservation()
    services.cancel_reservation(rid, "testpass")
    with pytest.raises(ValueError):
        services.cancel_reservation(rid, "testpass")


def test_no_show_no_password_required(clean_db):
    rid = make_reservation(ci="2020-01-01", co="2020-01-02")
    services.mark_no_show(rid, "wrong")  # password ignored
    r = services.get_reservation(rid)
    assert r["status"] in ("No-Show", "No Show")


def test_no_show_not_found(clean_db):
    with pytest.raises(ValueError):
        services.mark_no_show(9999, "testpass")


def test_delete_not_found(clean_db):
    with pytest.raises(ValueError):
        services.delete_reservation(9999, "testpass")


def test_delete_no_password_required(clean_db):
    rid = make_reservation()
    services.delete_reservation(rid, "wrong")  # password ignored; Reserved/no payments/POS
    r = services.get_reservation(rid)
    assert r["status"] == "Deleted"


def test_delete_checked_in_reservation(clean_db):
    rid = make_reservation()
    services.update_reservation_status(rid, "Checked In")
    with pytest.raises(ValueError):
        services.delete_reservation(rid, "testpass")


def test_delete_with_payment(clean_db):
    rid = make_reservation(deposit=50.0)
    with pytest.raises(ValueError, match="payments"):
        services.delete_reservation(rid, "testpass")


def test_delete_with_pos_items(clean_db):
    rid = make_reservation()
    services.add_pos_item(rid, "X", 1, 10, None)
    with pytest.raises(ValueError, match="POS"):
        services.delete_reservation(rid, "testpass")


def test_modify_not_found(clean_db):
    with pytest.raises(ValueError):
        services.modify_reservation(9999, 100, "2026-12-01", "2026-12-03", 1, "testpass")


def test_modify_no_password_required(clean_db):
    rid = make_reservation()
    services.modify_reservation(rid, 100, "2026-12-01", "2026-12-03", 1, "wrong")
    r = services.get_reservation(rid)
    assert float(r["agreed_nightly_rate"]) == 100.0


def test_modify_bad_dates(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.modify_reservation(rid, 100, "bad", "2026-12-03", 1, "testpass")


def test_modify_negative_rate(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.modify_reservation(rid, -100, "2026-12-01", "2026-12-03", 1, "testpass")


def test_modify_checked_out_reservation(clean_db):
    rid = make_reservation()
    services.update_reservation_status(rid, "Checked In")
    services.update_reservation_status(rid, "Checked Out")
    with pytest.raises(ValueError):
        services.modify_reservation(rid, 150, "2026-12-01", "2026-12-03", 1, "testpass")


# ═══════════════════════════════════════════════════════════════════════════
# ROOM + RATE PLAN ERROR PATHS
# ═══════════════════════════════════════════════════════════════════════════

def test_room_price_negative(clean_db):
    with pytest.raises(ValueError):
        services.update_room_price(1, -10, "testpass")


def test_room_price_wrong_password(clean_db):
    with pytest.raises(PermissionError):
        services.update_room_price(1, 100, "wrong")


def test_room_type_empty(clean_db):
    with pytest.raises(ValueError):
        services.update_room_type(1, "", "testpass")


def test_room_type_wrong_password(clean_db):
    with pytest.raises(PermissionError):
        services.update_room_type(1, "X", "wrong")


def test_room_housekeeping_invalid_status(clean_db):
    with pytest.raises(ValueError):
        services.update_housekeeping_status(1, "Invalid")


def test_rate_plan_empty_name(clean_db):
    with pytest.raises(ValueError):
        services.add_rate_plan("", 5.0, "testpass")


def test_rate_plan_duplicate(clean_db):
    with pytest.raises(ValueError, match="exists"):
        services.add_rate_plan("Room Only", 0.0, "testpass")


def test_rate_plan_wrong_password(clean_db):
    with pytest.raises(PermissionError):
        services.add_rate_plan("X", 0.0, "wrong")


def test_rate_plan_delete_in_use(clean_db):
    plans = services.get_all_rate_plans()
    pid = plans[0]["id"]
    services.create_reservation("T", "Direct", 1, pid, 100, "2026-12-01", "2026-12-03")
    with pytest.raises(ValueError, match="Cannot delete"):
        services.delete_rate_plan(pid, "testpass")


# ═══════════════════════════════════════════════════════════════════════════
# AUTH ERROR PATHS
# ═══════════════════════════════════════════════════════════════════════════

def test_change_password_wrong_current(clean_db):
    with pytest.raises(PermissionError):
        services.change_password("wrong", "newpass1", "newpass1")


def test_change_password_mismatch(clean_db):
    with pytest.raises(ValueError):
        services.change_password("testpass", "newpass1", "newpass2")


def test_change_password_too_short(clean_db):
    with pytest.raises(ValueError):
        services.change_password("testpass", "abc", "abc")


def test_change_password_same_as_current(clean_db):
    with pytest.raises(ValueError):
        services.change_password("testpass", "testpass", "testpass")


def test_generate_recovery_wrong_password(clean_db):
    with pytest.raises(PermissionError):
        services.generate_recovery_codes("wrong")


def test_verify_recovery_wrong_code(clean_db):
    services.generate_recovery_codes("testpass")
    assert services.verify_recovery_code("WRONGCODE") is False


# ═══════════════════════════════════════════════════════════════════════════
# SETTINGS ERROR PATHS
# ═══════════════════════════════════════════════════════════════════════════

def test_save_settings_wrong_password(clean_db):
    with pytest.raises(PermissionError):
        services.save_settings({"vat_rate": "0.15"}, "wrong")


def test_import_missing_file(clean_db):
    with pytest.raises(ValueError):
        services.import_settings_from_json("/nope.json", "testpass")


def test_import_bad_json(clean_db, tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json")
    with pytest.raises(Exception):
        services.import_settings_from_json(str(p), "testpass")


def test_reset_wrong_password(clean_db):
    with pytest.raises(PermissionError):
        services.reset_to_defaults("wrong")


# ═══════════════════════════════════════════════════════════════════════════
# GUEST ERROR PATHS
# ═══════════════════════════════════════════════════════════════════════════

def test_guest_empty_name(clean_db):
    with pytest.raises(ValueError):
        services.find_or_create_guest("")


def test_guest_update_empty_name(clean_db):
    gid, _, _ = services.find_or_create_guest("X")
    with pytest.raises(ValueError):
        services.update_guest(gid, "", "", "", "", "testpass")


def test_guest_update_wrong_password(clean_db):
    gid, _, _ = services.find_or_create_guest("X")
    with pytest.raises(PermissionError):
        services.update_guest(gid, "Y", "", "", "", "wrong")


def test_merge_wrong_password(clean_db):
    g1, _, _ = services.find_or_create_guest("A")
    g2, _, _ = services.find_or_create_guest("B")
    with pytest.raises(PermissionError):
        services.merge_guests(g1, g2, "wrong")


def test_merge_same_id(clean_db):
    g1, _, _ = services.find_or_create_guest("A")
    with pytest.raises(ValueError):
        services.merge_guests(g1, g1, "testpass")


def test_merge_nonexistent(clean_db):
    g1, _, _ = services.find_or_create_guest("A")
    with pytest.raises(ValueError):
        services.merge_guests(g1, 9999, "testpass")


# ═══════════════════════════════════════════════════════════════════════════
# INVOICE ERROR PATHS
# ═══════════════════════════════════════════════════════════════════════════

def test_invoice_not_found(clean_db):
    with pytest.raises(ValueError):
        services.generate_invoice_text(9999)


# ═══════════════════════════════════════════════════════════════════════════
# BALANCE EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════

def test_balance_nonexistent(clean_db):
    assert services.calculate_guest_balance(9999) == 0.0


def test_breakdown_nonexistent(clean_db):
    assert services.get_balance_breakdown(9999) is None


def test_balance_cancelled(clean_db):
    rid = make_reservation()
    services.cancel_reservation(rid, "testpass")
    assert services.calculate_guest_balance(rid) == 0.0


def test_balance_noshow(clean_db):
    rid = make_reservation()
    services.mark_no_show(rid, "testpass")
    assert services.calculate_guest_balance(rid) == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# ATTRIBUTION
# ═══════════════════════════════════════════════════════════════════════════

def test_attribution_tampered(clean_db):
    from app_core.db import transaction
    with transaction() as c:
        c.execute("UPDATE app_metadata SET value='X' WHERE key='gift_attribution'")
    ok, _ = services.verify_app_attribution()
    assert ok is False


def test_attribution_missing(clean_db):
    from app_core.db import transaction
    with transaction() as c:
        c.execute("DELETE FROM app_metadata WHERE key='gift_attribution'")
    ok, _ = services.verify_app_attribution()
    assert ok is False


# ═══════════════════════════════════════════════════════════════════════════
# SESSION
# ═══════════════════════════════════════════════════════════════════════════

def test_session_unlock_wrong_password(clean_db):
    assert services.unlock_session("wrong") is False


def test_touch_session_locked(clean_db):
    services.lock_session()
    services.touch_session()  # no-op


def test_session_expiry(clean_db):
    from datetime import timedelta
    services.set_setting("session_timeout_minutes", "1")
    services.unlock_session("testpass")
    services._session["last_activity"] -= timedelta(minutes=5)
    assert services.is_session_unlocked() is False


# ═══════════════════════════════════════════════════════════════════════════
# LANGUAGE
# ═══════════════════════════════════════════════════════════════════════════

def test_set_language_invalid(clean_db):
    services.set_language("fr")
    assert services.get_language() == "en"


def test_set_language_arabic(clean_db):
    services.set_language("ar")
    assert services.get_language() == "ar"
    services.set_language("en")


# ═══════════════════════════════════════════════════════════════════════════
# UI HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def test_fmt_positive():
    assert services.fmt(1234.5) == "1,234.50 EGP"


def test_fmt_negative():
    assert services.fmt(-100.0) == "-100.00 EGP"


def test_fmt_usd():
    assert services.fmt(100, "USD") == "100.00 USD"


def test_selected_id_empty_table():
    class T:
        row_count = 0
        cursor_row = 0
        def get_row_at(self, i):
            return ("x",)
    assert services.selected_id(T()) is None


def test_selected_id_valid():
    class T:
        row_count = 2
        cursor_row = 0
        def get_row_at(self, i):
            return ("42", "y")
    assert services.selected_id(T()) == 42


def test_selected_id_non_numeric():
    class T:
        row_count = 1
        cursor_row = 0
        def get_row_at(self, i):
            return ("abc",)
    assert services.selected_id(T()) is None