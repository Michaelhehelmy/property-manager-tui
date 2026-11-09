"""Payments: record, refund, balance."""
import pytest
import services
from db import connect
from conftest import make_reservation


def test_record_payment(clean_db):
    rid = make_reservation()
    services.record_payment(rid, 100.0, "Cash")
    p = connect().execute(
        "SELECT amount FROM payments WHERE reservation_id=?", (rid,)).fetchall()
    assert len(p) == 1
    assert p[0]["amount"] == 100.0


def test_record_payment_creates_ledger(clean_db):
    rid = make_reservation()
    services.record_payment(rid, 100.0, "Cash")
    pay = connect().execute(
        "SELECT gross, method FROM general_ledger WHERE entry_type='PAYMENT'").fetchone()
    assert pay["gross"] == 100.0
    assert pay["method"] == "Cash"


def test_record_negative_payment_raises(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.record_payment(rid, -50.0, "Cash")


def test_record_zero_payment_raises(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.record_payment(rid, 0.0, "Cash")


def test_record_payment_on_cancelled_raises(clean_db):
    rid = make_reservation()
    services.cancel_reservation(rid, "testpass")
    with pytest.raises(ValueError):
        services.record_payment(rid, 100.0, "Cash")


def test_record_payment_on_checked_out_raises(clean_db):
    rid = make_reservation()
    services.update_reservation_status(rid, "Checked In")
    services.update_reservation_status(rid, "Checked Out")
    with pytest.raises(ValueError):
        services.record_payment(rid, 100.0, "Cash")


def test_walkin_payment(clean_db):
    services.record_payment(None, 50.0, "Cash")
    p = connect().execute("SELECT amount FROM payments WHERE reservation_id IS NULL").fetchone()
    assert p["amount"] == 50.0


def test_refund_requires_password(clean_db):
    rid = make_reservation(deposit=100.0)
    with pytest.raises(PermissionError):
        services.record_refund(rid, 50.0, "Cash", "", "wrong")


def test_refund_success(clean_db):
    rid = make_reservation(deposit=100.0)
    services.record_refund(rid, 50.0, "Cash", "partial", "testpass")
    p = connect().execute(
        "SELECT amount FROM payments WHERE reservation_id=? AND payment_type='Refund'",
        (rid,)).fetchone()
    assert p["amount"] == -50.0


def test_refund_exceeds_paid_raises(clean_db):
    rid = make_reservation(deposit=100.0)
    with pytest.raises(ValueError):
        services.record_refund(rid, 150.0, "Cash", "", "testpass")


def test_refund_caps_to_paid(clean_db):
    rid = make_reservation(deposit=100.0)
    services.record_refund(rid, 100.0, "Cash", "", "testpass")
    # Balance should now be full charge again
    balance = services.calculate_guest_balance(rid)
    assert balance > 0


def test_balance_breakdown_totals(clean_db):
    rid = make_reservation(rate=100.0)
    services.record_payment(rid, 50.0, "Cash")
    bd = services.get_balance_breakdown(rid)
    assert bd["total_paid"] == 50.0
    assert bd["total_charges"] > 0
    assert bd["balance_due"] == pytest.approx(bd["total_charges"] - 50.0)


def test_balance_zero_for_cancelled(clean_db):
    rid = make_reservation()
    services.cancel_reservation(rid, "testpass")
    balance = services.calculate_guest_balance(rid)
    assert balance == 0.0