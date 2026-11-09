"""Round 3 — final reachable branches."""
import pytest
from unittest.mock import patch, MagicMock
from app_core import services
from app_core import db
from app_core.db import connect, transaction
from conftest import make_reservation


# ═══════════════════════════════════════════════════════════════════════════
# INIT / METADATA — defensive seed lines
# ═══════════════════════════════════════════════════════════════════════════

def test_init_db_idempotent(clean_db):
    """Calling init_db twice is a no-op (already initialized)."""
    services.init_db()
    services.init_db()
    # Rooms still 4
    assert len(services.get_all_rooms()) == 4


def test_ensure_metadata_recreates(clean_db):
    """If attribution row was deleted, calling init_db repairs it."""
    with transaction() as c:
        c.execute("DELETE FROM app_metadata WHERE key='gift_attribution'")
    services.init_db()
    ok, _ = services.verify_app_attribution()
    assert ok is True


# ═══════════════════════════════════════════════════════════════════════════
# _validate_setting — every type branch
# ═══════════════════════════════════════════════════════════════════════════

def test_validate_unknown_returns_error(clean_db):
    _, err = services._validate_setting("nonexistent_key_xyz", "x")
    assert err is not None
    assert "Unknown" in err


def test_validate_enum_without_options(clean_db):
    """Enum with no options defined still returns value unchanged."""
    # schema_version has no options
    norm, err = services._validate_setting("schema_version", "4")
    assert norm == "4"
    assert err is None


# ═══════════════════════════════════════════════════════════════════════════
# save_settings — write path
# ═══════════════════════════════════════════════════════════════════════════

def test_save_settings_bulk_write(clean_db):
    """Save multiple settings, all should persist."""
    count, errs = services.save_settings(
        {"vat_rate": "0.15", "service_charge_rate": "0.12", "city_tax_per_night": "5"},
        "testpass")
    assert count == 3
    assert errs == []
    assert services.get_setting("vat_rate") == "0.15"
    assert services.get_setting("service_charge_rate") == "0.12"
    assert float(services.get_setting("city_tax_per_night")) == 5.0


def test_save_settings_partial_error_rolls_back(clean_db):
    """If any invalid, none saved."""
    count, errs = services.save_settings(
        {"vat_rate": "0.15", "min_nights": "-1"}, "testpass")
    assert count == 0
    assert len(errs) == 1
    assert services.get_setting("vat_rate") == "0.14"  # unchanged


# ═══════════════════════════════════════════════════════════════════════════
# Guest matching — phone/email
# ═══════════════════════════════════════════════════════════════════════════

def test_guest_match_phone_exact_score(clean_db):
    services.find_or_create_guest("Alice", phone="+201001234567")
    matches = services.find_guest_matches("Different Name", phone="01001234567")
    assert len(matches) == 1
    assert matches[0]["score"] == 1.0


def test_guest_match_email_exact_score(clean_db):
    services.find_or_create_guest("Alice", email="alice@x.com")
    matches = services.find_guest_matches("Bob", email="ALICE@x.com")
    assert len(matches) == 1
    assert matches[0]["score"] >= 0.98


def test_guest_match_caller_phone_no_guest_phone(clean_db):
    services.find_or_create_guest("Alice")  # no phone
    matches = services.find_guest_matches("Alice", phone="+201001234567")
    assert len(matches) == 1


def test_guest_match_caller_email_no_guest_email(clean_db):
    services.find_or_create_guest("Alice")  # no email
    matches = services.find_guest_matches("Alice", email="x@y.com")
    assert len(matches) == 1


# ═══════════════════════════════════════════════════════════════════════════
# create_reservation — coercion + overbooking
# ═══════════════════════════════════════════════════════════════════════════

def test_create_plan_string_coerced_none(clean_db):
    """String rate_plan_id → None (audit P1-2)."""
    rid = services.create_reservation("X", "Direct", 1, "not-int", 100,
                                       "2026-12-01", "2026-12-03")
    r = services.get_reservation(rid)
    assert r["rate_plan_id"] is None


def test_create_overbooking_allowed(clean_db):
    services.set_setting("allow_overbooking", "true")
    make_reservation(ci="2026-12-01", co="2026-12-05", room_id=1)
    rid = services.create_reservation("Y", "Direct", 1, None, 100,
                                       "2026-12-03", "2026-12-07")
    assert rid > 0


# ═══════════════════════════════════════════════════════════════════════════
# Cancel / no-show — POS voiding path
# ═══════════════════════════════════════════════════════════════════════════

def test_cancel_with_pos_voids_and_restocks(clean_db):
    rid = make_reservation()
    before = connect().execute("SELECT stock_qty FROM inventory WHERE id=1").fetchone()["stock_qty"]
    services.add_pos_item(rid, "Water", 3, 20, 1)
    services.cancel_reservation(rid, "testpass")
    after = connect().execute("SELECT stock_qty FROM inventory WHERE id=1").fetchone()["stock_qty"]
    assert after == before


def test_cancel_with_no_refund_needed(clean_db):
    """No payments → no refund row created."""
    rid = make_reservation()
    services.cancel_reservation(rid, "testpass")
    refunds = connect().execute(
        "SELECT COUNT(*) FROM payments WHERE reservation_id=? AND payment_type='Refund'",
        (rid,)).fetchone()[0]
    assert refunds == 0


def test_no_show_with_deposit_and_keep_policy(clean_db):
    services.set_setting("noshow_policy", "keep_deposit")
    rid = make_reservation(deposit=100.0)
    services.mark_no_show(rid, "testpass")
    # Deposit kept → no refund
    refunds = connect().execute(
        "SELECT COUNT(*) FROM payments WHERE reservation_id=? AND payment_type='Refund'",
        (rid,)).fetchone()[0]
    assert refunds == 0


# ═══════════════════════════════════════════════════════════════════════════
# Delete — refusals
# ═══════════════════════════════════════════════════════════════════════════

def test_delete_with_payments_refused(clean_db):
    rid = make_reservation(deposit=50.0)
    with pytest.raises(ValueError, match="payments"):
        services.delete_reservation(rid, "testpass")


def test_delete_with_pos_refused(clean_db):
    rid = make_reservation()
    services.add_pos_item(rid, "X", 1, 10, None)
    with pytest.raises(ValueError, match="POS"):
        services.delete_reservation(rid, "testpass")


# ═══════════════════════════════════════════════════════════════════════════
# POS — inventory branches
# ═══════════════════════════════════════════════════════════════════════════

def test_pos_inventory_missing(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError, match="not found"):
        services.add_pos_item(rid, "Ghost", 1, 10, 9999)


def test_pos_inventory_inactive(clean_db):
    with transaction() as c:
        c.execute("INSERT INTO inventory (item_name, category, stock_qty, unit, active) "
                  "VALUES ('Dead', 'X', 10, 'pcs', 0)")
        iid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    rid = make_reservation()
    with pytest.raises(ValueError, match="inactive"):
        services.add_pos_item(rid, "Dead", 1, 10, iid)


def test_pos_negative_stock_blocked(clean_db):
    services.set_setting("allow_negative_stock", "false")
    rid = make_reservation()
    with pytest.raises(ValueError, match="Insufficient"):
        services.add_pos_item(rid, "Propane", 100, 250, 4)


def test_pos_cogs_written_when_unit_cost_positive(clean_db):
    rid = make_reservation()
    services.add_pos_item(rid, "Water", 2, 20, 1)  # unit_cost=3
    cogs = connect().execute(
        "SELECT COUNT(*) FROM general_ledger WHERE entry_type='COGS'").fetchone()[0]
    assert cogs == 1


def test_pos_no_cogs_when_unit_cost_zero(clean_db):
    with transaction() as c:
        c.execute("UPDATE inventory SET unit_cost=0 WHERE id=1")
    rid = make_reservation()
    services.add_pos_item(rid, "Water", 1, 20, 1)
    cogs = connect().execute(
        "SELECT COUNT(*) FROM general_ledger WHERE entry_type='COGS'").fetchone()[0]
    assert cogs == 0


def test_pos_walkin_reservation_none(clean_db):
    """None is walk-in — no reservation linked."""
    result = services.add_pos_item(None, "Water", 1, 20, None)
    row = connect().execute(
        "SELECT reservation_id FROM pos_tabs WHERE id=?", (result["pos_id"],)).fetchone()
    assert row["reservation_id"] is None


def test_pos_void_custom_item(clean_db):
    rid = make_reservation()
    result = services.add_pos_item(rid, "Service", 1, 50, None)  # no inv
    services.void_pos_item(result["pos_id"], "testpass")
    row = connect().execute(
        "SELECT voided_at FROM pos_tabs WHERE id=?", (result["pos_id"],)).fetchone()
    assert row["voided_at"] is not None


# ═══════════════════════════════════════════════════════════════════════════
# Inventory — cost edges
# ═══════════════════════════════════════════════════════════════════════════

def test_restock_zero_stock_uses_new_cost(clean_db):
    with transaction() as c:
        c.execute("UPDATE inventory SET stock_qty=0, unit_cost=0 WHERE id=1")
    services.restock_inventory(1, 10, 5.0, "testpass")
    cost = connect().execute("SELECT unit_cost FROM inventory WHERE id=1").fetchone()["unit_cost"]
    assert cost == 5.0


def test_restock_zero_unit_cost_preserves_old(clean_db):
    """Restock with unit_cost=0 keeps existing cost (no weighted avg update)."""
    before = connect().execute("SELECT unit_cost FROM inventory WHERE id=1").fetchone()["unit_cost"]
    services.restock_inventory(1, 10, 0.0, "testpass")
    after = connect().execute("SELECT unit_cost FROM inventory WHERE id=1").fetchone()["unit_cost"]
    assert after == before


# ═══════════════════════════════════════════════════════════════════════════
# Balance — overpay edge
# ═══════════════════════════════════════════════════════════════════════════

def test_balance_overpaid_negative(clean_db):
    rid = make_reservation(rate=100.0, ci="2026-12-01", co="2026-12-02")
    services.record_payment(rid, 5000.0, "Cash")
    assert services.calculate_guest_balance(rid) < 0


# ═══════════════════════════════════════════════════════════════════════════
# Attribution — tamper
# ═══════════════════════════════════════════════════════════════════════════

def test_attribution_value_tampered(clean_db):
    with transaction() as c:
        c.execute("UPDATE app_metadata SET value='Hacked' WHERE key='gift_attribution'")
    ok, msg = services.verify_app_attribution()
    assert ok is False
    assert "tampered" in msg.lower()


def test_attribution_hash_tampered(clean_db):
    with transaction() as c:
        c.execute("UPDATE app_metadata SET hash_signature='bad' WHERE key='gift_attribution'")
    ok, _ = services.verify_app_attribution()
    assert ok is False


# ═══════════════════════════════════════════════════════════════════════════
# selected_id — TypeError fix (verify)
# ═══════════════════════════════════════════════════════════════════════════

def test_selected_id_none_returns_none():
    class T:
        row_count = 1
        cursor_row = 0
        def get_row_at(self, i):
            return (None,)
    assert services.selected_id(T()) is None


def test_selected_id_valid_int():
    class T:
        row_count = 1
        cursor_row = 0
        def get_row_at(self, i):
            return ("42",)
    assert services.selected_id(T()) == 42