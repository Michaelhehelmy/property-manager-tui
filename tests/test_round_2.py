"""Round 2 — hit remaining reachable lines."""
import pytest
from unittest.mock import patch, MagicMock
import services
import db
from db import connect, transaction
from conftest import make_reservation


# ═══════════════════════════════════════════════════════════════════════════
# SETTINGS — schema_version skip + write path
# ═══════════════════════════════════════════════════════════════════════════

def test_save_settings_schema_version_skipped(clean_db):
    """save_settings should ignore schema_version key."""
    count, errs = services.save_settings(
        {"schema_version": "999", "vat_rate": "0.15"}, "testpass")
    assert errs == []
    # vat_rate saved, schema_version ignored
    assert services.get_setting("vat_rate") == "0.15"
    assert services.get_setting("schema_version") == "4"


def test_save_settings_only_schema_version(clean_db):
    """save_settings with only schema_version = 0 changes."""
    count, errs = services.save_settings({"schema_version": "999"}, "testpass")
    assert count == 0
    assert errs == []


def test_validate_str_and_text_identical(clean_db):
    """str and text types behave the same."""
    norm1, _ = services._validate_setting("property_name", "  Test  ")
    norm2, _ = services._validate_setting("property_address", "  Test  ")
    assert norm1 == norm2 == "Test"


# ═══════════════════════════════════════════════════════════════════════════
# GUEST MATCHING — phone/email branches
# ═══════════════════════════════════════════════════════════════════════════

def test_guest_match_by_phone_exact(clean_db):
    services.find_or_create_guest("Alice", phone="+201001234567")
    matches = services.find_guest_matches("Different Name", phone="01001234567")
    assert len(matches) == 1
    assert matches[0]["score"] == 1.0


def test_guest_match_by_email_exact(clean_db):
    services.find_or_create_guest("Alice", email="alice@example.com")
    matches = services.find_guest_matches("Bob", email="ALICE@EXAMPLE.COM")
    assert len(matches) == 1
    assert matches[0]["score"] >= 0.98


def test_guest_match_no_phone_provided(clean_db):
    """Guest has phone but caller didn't provide one → name only."""
    services.find_or_create_guest("Alice", phone="+201001234567")
    matches = services.find_guest_matches("Alice")
    assert len(matches) == 1


def test_guest_match_caller_phone_but_guest_no_phone(clean_db):
    """Caller phone but guest has none → name score only."""
    services.find_or_create_guest("Alice")
    matches = services.find_guest_matches("Alice", phone="+201001234567")
    assert len(matches) == 1


def test_guest_match_empty_email(clean_db):
    services.find_or_create_guest("Alice", email="")
    matches = services.find_guest_matches("Alice", email="")
    assert len(matches) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# RESERVATION — rate_plan type, overbooking, modify branches
# ═══════════════════════════════════════════════════════════════════════════

def test_create_reservation_plan_string_coerced(clean_db):
    """Non-int rate_plan_id is coerced to None (audit P1-2)."""
    rid = services.create_reservation("X", "Direct", 1, "not-a-plan", 100,
                                       "2026-12-01", "2026-12-03")
    r = services.get_reservation(rid)
    assert r["rate_plan_id"] is None


def test_create_reservation_plan_zero_fails_fk(clean_db):
    """rate_plan_id=0 must fail FK — this is the audit P1-2 bug at the service layer.
    UI's job is to send None for blank, not 0."""
    with pytest.raises(Exception):  # sqlite3.IntegrityError
        services.create_reservation("X", "Direct", 1, 0, 100,
                                     "2026-12-01", "2026-12-03")


def test_overbooking_allowed_setting_true(clean_db):
    services.set_setting("allow_overbooking", "true")
    make_reservation(ci="2026-12-01", co="2026-12-05", room_id=1)
    rid = services.create_reservation("Y", "Direct", 1, None, 100,
                                       "2026-12-03", "2026-12-07")
    assert rid > 0


def test_modify_same_room(clean_db):
    rid = make_reservation(ci="2026-12-01", co="2026-12-03", room_id=1)
    services.modify_reservation(rid, 150, "2026-12-01", "2026-12-03", 1, "testpass")
    r = services.get_reservation(rid)
    assert r["agreed_nightly_rate"] == 150


def test_modify_different_room(clean_db):
    rid = make_reservation(ci="2026-12-01", co="2026-12-03", room_id=1)
    services.modify_reservation(rid, 150, "2026-12-01", "2026-12-03", 2, "testpass")
    r = services.get_reservation(rid)
    assert r["room_id"] == 2
    # Old room should be dirty
    room1 = services.get_room(1)
    assert room1["housekeeping_status"] == "Dirty"


def test_modify_checkedin_reservation(clean_db):
    """Audit: modify was only allowed for Reserved; now Checked In also allowed."""
    rid = make_reservation(ci="2026-12-01", co="2026-12-03", room_id=1)
    services.update_reservation_status(rid, "Checked In")
    services.modify_reservation(rid, 200, "2026-12-01", "2026-12-04", 1, "testpass")
    r = services.get_reservation(rid)
    assert r["agreed_nightly_rate"] == 200


# ═══════════════════════════════════════════════════════════════════════════
# CANCEL — no POS items, has POS items, refund=0
# ═══════════════════════════════════════════════════════════════════════════

def test_cancel_no_refund_when_no_payments(clean_db):
    rid = make_reservation()
    services.cancel_reservation(rid, "testpass")
    refunds = connect().execute(
        "SELECT COUNT(*) FROM payments WHERE reservation_id=? AND payment_type='Refund'",
        (rid,)).fetchone()[0]
    assert refunds == 0


def test_cancel_voids_pos_items(clean_db):
    rid = make_reservation()
    services.add_pos_item(rid, "Water", 2, 20, 1)
    services.cancel_reservation(rid, "testpass")
    rows = connect().execute(
        "SELECT voided_at FROM pos_tabs WHERE reservation_id=?", (rid,)).fetchall()
    assert all(r["voided_at"] is not None for r in rows)


def test_cancel_restores_stock(clean_db):
    rid = make_reservation()
    before = connect().execute("SELECT stock_qty FROM inventory WHERE id=1").fetchone()["stock_qty"]
    services.add_pos_item(rid, "Water", 3, 20, 1)
    after_add = connect().execute("SELECT stock_qty FROM inventory WHERE id=1").fetchone()["stock_qty"]
    assert after_add == before - 3
    services.cancel_reservation(rid, "testpass")
    after_cancel = connect().execute("SELECT stock_qty FROM inventory WHERE id=1").fetchone()["stock_qty"]
    assert after_cancel == before


def test_cancel_with_refund_creates_ledger_reversal(clean_db):
    rid = make_reservation(deposit=100.0)
    services.cancel_reservation(rid, "testpass")
    reversals = connect().execute(
        "SELECT COUNT(*) FROM general_ledger WHERE entry_type='VOID_CHARGE' AND ref_id=?",
        (rid,)).fetchone()[0]
    assert reversals >= 1


# ═══════════════════════════════════════════════════════════════════════════
# DELETE — payments/POS present
# ═══════════════════════════════════════════════════════════════════════════

def test_delete_with_payments(clean_db):
    rid = make_reservation(deposit=50.0)
    with pytest.raises(ValueError, match="payments"):
        services.delete_reservation(rid, "testpass")


def test_delete_with_pos(clean_db):
    rid = make_reservation()
    services.add_pos_item(rid, "X", 1, 10, None)
    with pytest.raises(ValueError, match="POS"):
        services.delete_reservation(rid, "testpass")


def test_delete_reserved_clean(clean_db):
    rid = make_reservation()
    services.delete_reservation(rid, "testpass")
    assert services.get_reservation(rid)["status"] == "Deleted"


# ═══════════════════════════════════════════════════════════════════════════
# POS — inventory edge cases
# ═══════════════════════════════════════════════════════════════════════════

def test_pos_inventory_not_found(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError, match="not found"):
        services.add_pos_item(rid, "X", 1, 10, 9999)


def test_pos_inventory_inactive(clean_db):
    with transaction() as c:
        c.execute("INSERT INTO inventory (item_name, category, stock_qty, unit, active) VALUES ('Dead', 'X', 10, 'pcs', 0)")
        iid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    rid = make_reservation()
    with pytest.raises(ValueError, match="inactive"):
        services.add_pos_item(rid, "Dead", 1, 10, iid)


def test_pos_negative_stock_blocked(clean_db):
    services.set_setting("allow_negative_stock", "false")
    rid = make_reservation()
    with pytest.raises(ValueError, match="Insufficient"):
        services.add_pos_item(rid, "Propane", 100, 250, 4)


def test_pos_negative_stock_allowed(clean_db):
    """Default setting allows negative stock."""
    rid = make_reservation()
    services.add_pos_item(rid, "Propane", 100, 250, 4)
    stock = connect().execute("SELECT stock_qty FROM inventory WHERE id=4").fetchone()["stock_qty"]
    assert stock < 0


def test_pos_with_custom_no_inventory(clean_db):
    """Custom items skip inventory lookup entirely."""
    rid = make_reservation()
    result = services.add_pos_item(rid, "Laundry", 1, 50, None)
    assert result["pos_id"] > 0


# ═══════════════════════════════════════════════════════════════════════════
# INVENTORY
# ═══════════════════════════════════════════════════════════════════════════

def test_restock_zero_stock_item(clean_db):
    """Restock an item that currently has zero stock."""
    with transaction() as c:
        c.execute("UPDATE inventory SET stock_qty=0 WHERE id=1")
    services.restock_inventory(1, 10, 3.0, "testpass")
    stock = connect().execute("SELECT stock_qty FROM inventory WHERE id=1").fetchone()["stock_qty"]
    assert stock == 10


def test_restock_first_time_cost(clean_db):
    """Old cost = 0, new cost applies."""
    with transaction() as c:
        c.execute("UPDATE inventory SET stock_qty=0, unit_cost=0 WHERE id=1")
    services.restock_inventory(1, 10, 5.0, "testpass")
    cost = connect().execute("SELECT unit_cost FROM inventory WHERE id=1").fetchone()["unit_cost"]
    assert cost == 5.0


# ═══════════════════════════════════════════════════════════════════════════
# ATTESTATION / ATTRIBUTION
# ═══════════════════════════════════════════════════════════════════════════

def test_attribution_value_tampered(clean_db):
    with transaction() as c:
        c.execute("UPDATE app_metadata SET value='Changed' WHERE key='gift_attribution'")
    ok, msg = services.verify_app_attribution()
    assert ok is False


def test_attribution_hash_tampered(clean_db):
    with transaction() as c:
        c.execute("UPDATE app_metadata SET hash_signature='bad' WHERE key='gift_attribution'")
    ok, _ = services.verify_app_attribution()
    assert ok is False


# ═══════════════════════════════════════════════════════════════════════════
# BALANCE / REPORT empty branches
# ═══════════════════════════════════════════════════════════════════════════

def test_balance_overpaid(clean_db):
    """Guest paid more than charges."""
    rid = make_reservation(rate=100.0, ci="2026-12-01", co="2026-12-02")
    services.record_payment(rid, 5000.0, "Cash")
    assert services.calculate_guest_balance(rid) < 0


def test_summary_with_no_active_reservations(clean_db):
    s = services.get_general_ledger_summary()
    assert s["billed_revenue"] == 0
    assert s["ar_balance"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# UI HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def test_selected_id_when_cursor_negative():
    class T:
        row_count = 2
        cursor_row = -1
        def get_row_at(self, i):
            raise IndexError()
    assert services.selected_id(T()) is None


def test_selected_id_when_value_type_error():
    class T:
        row_count = 1
        cursor_row = 0
        def get_row_at(self, i):
            return (None,)  # int(None) raises TypeError
    assert services.selected_id(T()) is None


# ═══════════════════════════════════════════════════════════════════════════
# DB — line 229
# ═══════════════════════════════════════════════════════════════════════════

def test_db_transaction_rolls_back(clean_db):
    """Force a rollback via exception inside transaction."""
    with pytest.raises(ValueError):
        with db.transaction() as c:
            c.execute("INSERT INTO guests (canonical_name) VALUES ('rollback')")
            raise ValueError("boom")
    row = connect().execute(
        "SELECT 1 FROM guests WHERE canonical_name='rollback'").fetchone()
    assert row is None