"""Reservation lifecycle: create, status, modify, cancel, no-show, delete."""
import pytest
from app_core import services
from app_core.db import connect
from conftest import make_reservation, count_ledger


def test_create_reservation_success(clean_db):
    rid = make_reservation()
    assert rid > 0
    row = connect().execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()
    assert row["status"] == "Reserved"


def test_create_reservation_creates_charge(clean_db):
    rid = make_reservation(rate=100.0)
    charges = connect().execute(
        "SELECT net, service, vat, gross FROM general_ledger WHERE ref_type='RES' AND ref_id=? AND entry_type='CHARGE'",
        (rid,)).fetchall()
    assert len(charges) == 1
    charge = charges[0]
    assert charge["net"] == pytest.approx(200.0)  # 2 nights * 100
    assert charge["service"] > 0
    assert charge["vat"] > 0
    assert charge["gross"] > charge["net"]


def test_create_empty_guest_raises(clean_db):
    with pytest.raises(ValueError):
        services.create_reservation("", "Direct", 1, None, 100, "2026-12-01", "2026-12-03")


def test_create_negative_rate_raises(clean_db):
    with pytest.raises(ValueError):
        services.create_reservation("X", "Direct", 1, None, -100, "2026-12-01", "2026-12-03")


def test_create_checkout_before_checkin_raises(clean_db):
    with pytest.raises(ValueError):
        services.create_reservation("X", "Direct", 1, None, 100, "2026-12-03", "2026-12-01")


def test_create_same_day_checkout_raises(clean_db):
    with pytest.raises(ValueError):
        services.create_reservation("X", "Direct", 1, None, 100, "2026-12-01", "2026-12-01")


def test_create_bad_date_format_raises(clean_db):
    with pytest.raises(ValueError):
        services.create_reservation("X", "Direct", 1, None, 100, "12/01/2026", "2026-12-03")


def test_create_below_min_nights_raises(clean_db):
    services.set_setting("min_nights", "2")
    with pytest.raises(ValueError):
        services.create_reservation("X", "Direct", 1, None, 100, "2026-12-01", "2026-12-02")


def test_create_above_max_nights_raises(clean_db):
    services.set_setting("max_nights", "3")
    with pytest.raises(ValueError):
        services.create_reservation("X", "Direct", 1, None, 100, "2026-12-01", "2026-12-10")


def test_create_invalid_room_raises(clean_db):
    with pytest.raises(Exception):
        # Room 999 doesn't exist -> FK failure
        services.create_reservation("X", "Direct", 999, None, 100, "2026-12-01", "2026-12-03")


def test_room_conflict_raises(clean_db):
    make_reservation(ci="2026-12-01", co="2026-12-05", room_id=1)
    with pytest.raises(ValueError):
        services.create_reservation("Y", "Direct", 1, None, 100, "2026-12-03", "2026-12-07")


def test_overbooking_allowed_when_setting_true(clean_db):
    services.set_setting("allow_overbooking", "true")
    make_reservation(ci="2026-12-01", co="2026-12-05", room_id=1)
    rid = services.create_reservation("Y", "Direct", 1, None, 100, "2026-12-03", "2026-12-07")
    assert rid > 0


def test_create_with_deposit(clean_db):
    rid = make_reservation(deposit=50.0)
    payments = connect().execute(
        "SELECT amount, payment_type FROM payments WHERE reservation_id=?", (rid,)).fetchall()
    assert len(payments) == 1
    assert payments[0]["amount"] == 50.0
    assert payments[0]["payment_type"] == "Deposit"


def test_create_with_negative_deposit_raises(clean_db):
    with pytest.raises(ValueError):
        services.create_reservation("X", "Direct", 1, None, 100, "2026-12-01", "2026-12-03", -10)


def test_status_checkin(clean_db):
    rid = make_reservation()
    services.update_reservation_status(rid, "Checked In")
    row = connect().execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()
    assert row["status"] == "Checked In"


def test_status_checkout_marks_room_dirty(clean_db):
    rid = make_reservation()
    services.update_reservation_status(rid, "Checked In")
    services.update_reservation_status(rid, "Checked Out")
    room = connect().execute("SELECT housekeeping_status FROM rooms WHERE id=1").fetchone()
    assert room["housekeeping_status"] == "Dirty"


def test_cannot_checkin_from_checkout(clean_db):
    rid = make_reservation()
    services.update_reservation_status(rid, "Checked In")
    services.update_reservation_status(rid, "Checked Out")
    with pytest.raises(ValueError):
        services.update_reservation_status(rid, "Checked In")


def test_cannot_checkin_from_cancelled(clean_db):
    rid = make_reservation()
    services.cancel_reservation(rid, "testpass")
    with pytest.raises(ValueError):
        services.update_reservation_status(rid, "Checked In")


def test_cancel_reverses_charges(clean_db):
    rid = make_reservation()
    before = count_ledger(entry_type="CHARGE", ref_type="RES")
    services.cancel_reservation(rid, "testpass")
    after = count_ledger(entry_type="CHARGE", ref_type="RES")
    # Original not deleted, but reversal posted
    assert after == before  # CHARGE count same
    reversals = connect().execute(
        "SELECT COUNT(*) FROM general_ledger WHERE entry_type='VOID_CHARGE' AND ref_id=?",
        (rid,)).fetchone()[0]
    assert reversals == 1


def test_cancel_with_full_refund_policy(clean_db):
    services.set_setting("cancel_policy", "full_refund")
    rid = make_reservation(deposit=100.0)
    services.cancel_reservation(rid, "testpass")
    refunds = connect().execute(
        "SELECT amount FROM payments WHERE reservation_id=? AND payment_type='Refund'",
        (rid,)).fetchall()
    assert len(refunds) == 1
    # Refunds stored as negative amounts (money out)
    assert abs(refunds[0]["amount"]) == 100.0


def test_cancel_with_keep_deposit_policy(clean_db):
    services.set_setting("cancel_policy", "keep_deposit")
    rid = make_reservation(deposit=100.0)
    services.cancel_reservation(rid, "testpass")
    refunds = connect().execute(
        "SELECT amount FROM payments WHERE reservation_id=? AND payment_type='Refund'",
        (rid,)).fetchall()
    # Deposit kept -> no refund
    assert len(refunds) == 0


def test_cancel_no_payments_no_refund(clean_db):
    rid = make_reservation()
    services.cancel_reservation(rid, "testpass")
    refunds = connect().execute(
        "SELECT COUNT(*) FROM payments WHERE reservation_id=? AND payment_type='Refund'",
        (rid,)).fetchone()[0]
    assert refunds == 0


def test_cancel_does_not_require_super_password(clean_db):
    rid = make_reservation()
    services.cancel_reservation(rid, "wrong")
    row = connect().execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()
    assert row["status"] == "Cancelled"


def test_cancel_twice_raises(clean_db):
    rid = make_reservation()
    services.cancel_reservation(rid, "testpass")
    with pytest.raises(ValueError):
        services.cancel_reservation(rid, "testpass")


def test_no_show_reverses_charges(clean_db):
    rid = make_reservation()
    services.mark_no_show(rid, "testpass")
    row = connect().execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()
    assert row["status"] == "No-Show"


def test_no_show_does_not_require_super_password(clean_db):
    rid = make_reservation()
    services.mark_no_show(rid, "wrong")
    row = connect().execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()
    assert row["status"] == "No-Show"


def test_delete_only_reserved(clean_db):
    rid = make_reservation()
    services.update_reservation_status(rid, "Checked In")
    with pytest.raises(ValueError):
        services.delete_reservation(rid, "testpass")


def test_delete_refuses_with_payments(clean_db):
    rid = make_reservation(deposit=100.0)
    with pytest.raises(ValueError):
        services.delete_reservation(rid, "testpass")


def test_delete_reserved_with_no_payments(clean_db):
    rid = make_reservation()
    services.delete_reservation(rid, "testpass")
    row = connect().execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()
    assert row["status"] == "Deleted"


def test_modify_rate(clean_db):
    rid = make_reservation(rate=100.0)
    services.modify_reservation(rid, 150.0, "2026-12-01", "2026-12-03", 1, "testpass")
    row = connect().execute("SELECT agreed_nightly_rate FROM reservations WHERE id=?", (rid,)).fetchone()
    assert row["agreed_nightly_rate"] == 150.0


def test_modify_does_not_require_super_password(clean_db):
    rid = make_reservation()
    services.modify_reservation(rid, 150.0, "2026-12-01", "2026-12-03", 1, "wrong")
    row = connect().execute("SELECT agreed_nightly_rate FROM reservations WHERE id=?", (rid,)).fetchone()
    assert row["agreed_nightly_rate"] == 150.0


def test_modify_only_reserved_or_checkedin(clean_db):
    rid = make_reservation()
    services.cancel_reservation(rid, "testpass")
    with pytest.raises(ValueError):
        services.modify_reservation(rid, 150.0, "2026-12-01", "2026-12-03", 1, "testpass")


def test_modify_occupied_room_raises(clean_db):
    make_reservation(ci="2026-12-05", co="2026-12-10", room_id=2)
    rid = make_reservation(ci="2026-12-01", co="2026-12-03", room_id=1)
    with pytest.raises(ValueError):
        services.modify_reservation(rid, 100.0, "2026-12-05", "2026-12-08", 2, "testpass")

def test_cancel_paid_reservation_requires_password(clean_db):
    rid = make_reservation(deposit=100.0)
    with pytest.raises(PermissionError, match="paid or partially paid"):
        services.cancel_reservation(rid)
    with pytest.raises(PermissionError, match="paid or partially paid"):
        services.cancel_reservation(rid, "wrong")
    services.cancel_reservation(rid, "testpass")
    row = connect().execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()
    assert row["status"] == "Cancelled"


def test_no_show_paid_reservation_requires_password(clean_db):
    rid = make_reservation(ci="2020-01-01", co="2020-01-02", deposit=50.0)
    with pytest.raises(PermissionError, match="paid or partially paid"):
        services.mark_no_show(rid)
    services.mark_no_show(rid, "testpass")
    row = connect().execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()
    assert row["status"] == "No-Show"
