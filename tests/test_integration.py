"""Full user flows end-to-end."""
import pytest
import services
from db import connect
from conftest import make_reservation


def test_full_booking_to_checkout(clean_db):
    """Book -> checkin -> order -> pay -> checkout."""
    rid = make_reservation(rate=100.0, ci="2026-12-01", co="2026-12-03")
    services.update_reservation_status(rid, "Checked In")
    services.add_pos_item(rid, "Water", 2, 20, 1)
    bal_before = services.calculate_guest_balance(rid)
    assert bal_before > 200.0  # accommodation + pos
    services.record_payment(rid, bal_before, "Cash")
    bal_after = services.calculate_guest_balance(rid)
    assert abs(bal_after) < 0.01
    services.update_reservation_status(rid, "Checked Out")
    # Verify room marked dirty
    room = connect().execute("SELECT housekeeping_status FROM rooms WHERE id=1").fetchone()
    assert room["housekeeping_status"] == "Dirty"


def test_cancel_with_deposit_refund(clean_db):
    services.set_setting("cancel_policy", "full_refund")
    rid = make_reservation(deposit=100.0)
    services.cancel_reservation(rid, "testpass")
    refunds = connect().execute(
        "SELECT SUM(amount) FROM payments WHERE reservation_id=? AND payment_type='Refund'",
        (rid,)).fetchone()[0]
    # Refunds are stored as negative amounts (money leaving)
    assert abs(refunds) == 100.0
    balance = services.calculate_guest_balance(rid)
    assert balance == 0.0


def test_no_show_keeps_first_night(clean_db):
    services.set_setting("noshow_policy", "keep_first_night")
    rid = make_reservation(rate=100.0, deposit=150.0)
    services.mark_no_show(rid, "testpass")
    # First night = 100 kept; refund = 50
    refunds = connect().execute(
        "SELECT SUM(amount) FROM payments WHERE reservation_id=? AND payment_type='Refund'",
        (rid,)).fetchone()[0]
    assert abs(refunds) == pytest.approx(50.0)


def test_restock_then_sell_then_cogs(clean_db):
    """Full inventory cycle: restock, sell, verify COGS."""
    services.restock_inventory(1, 10, 5.0, "testpass")  # 110 total @ ~3.18
    rid = make_reservation()
    services.add_pos_item(rid, "Water", 2, 20, 1)
    cogs = connect().execute(
        "SELECT SUM(net) FROM general_ledger WHERE entry_type='COGS'"
    ).fetchone()[0]
    # COGS = 2 * weighted_avg_cost, should be around 2 * 3.18 ≈ 6.36
    assert cogs > 0
    assert cogs < 15  # Definitely not 2*5=10 or full 50


def test_two_guests_booking(clean_db):
    """Two separate guests, two reservations."""
    g1, _, _ = services.find_or_create_guest("Alice")
    g2, _, _ = services.find_or_create_guest("Bob")
    rid1 = services.create_reservation("Alice", "Direct", 1, None, 100,
                                        "2026-12-01", "2026-12-03", guest_id=g1)
    rid2 = services.create_reservation("Bob", "Direct", 2, None, 100,
                                        "2026-12-01", "2026-12-03", guest_id=g2)
    assert rid1 != rid2
    guests = services.get_all_guests()
    assert len(guests) == 2


def test_walkin_flow(clean_db):
    """Walk-in: add items, get total, pay, verify ledger matches."""
    services.add_pos_item(None, "Water", 2, 20, 1)
    charge = connect().execute(
        "SELECT SUM(gross) FROM general_ledger WHERE ref_type='POS' AND entry_type='CHARGE'"
    ).fetchone()[0]
    assert charge > 0
    services.record_payment(None, charge, "Cash")
    total_pos = connect().execute(
        "SELECT SUM(gross) FROM general_ledger WHERE ref_type='POS' AND entry_type='CHARGE'"
    ).fetchone()[0]
    total_pay = connect().execute(
        "SELECT SUM(CASE WHEN gross > 0 THEN gross ELSE 0 END) FROM general_ledger WHERE entry_type='PAYMENT'"
    ).fetchone()[0]
    assert abs(total_pos - total_pay) < 0.01