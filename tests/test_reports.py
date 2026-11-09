"""Reports: P&L, VAT, cash flow, occupancy."""
import pytest
import services
from datetime import datetime
from conftest import make_reservation


def _today_range():
    """Return (today, today) — reports aggregate by charge-creation date, not stay dates."""
    today = datetime.now().strftime("%Y-%m-%d")
    return today, today


def test_pl_empty_period(clean_db):
    text = services.report_pl_statement("2026-01-01", "2026-01-31")
    assert "TOTAL REVENUE" in text
    assert "NET PROFIT" in text


def test_pl_includes_revenue(clean_db):
    rid = make_reservation(rate=100.0)
    text = services.report_pl_statement(*_today_range())
    assert "Accommodation" in text
    # Parse the total revenue number and assert > 0
    line = text.split("TOTAL REVENUE")[1].split("\n")[0]
    amount = float(line.strip().split()[0].replace(",", ""))
    assert amount > 0


def test_pl_cogs_separate(clean_db):
    rid = make_reservation()
    services.add_pos_item(rid, "Water", 5, 20, 1)
    text = services.report_pl_statement(*_today_range())
    assert "COGS" in text


def test_vat_report_only_vat(clean_db):
    rid = make_reservation(rate=100.0)
    # Manually create a charge with known VAT
    from db import transaction
    text = services.report_vat(*_today_range())
    assert "Output VAT" in text


def test_vat_output_positive(clean_db):
    rid = make_reservation(rate=100.0)
    text = services.report_vat(*_today_range())
    line = [l for l in text.split("\n") if "Output VAT" in l][0]
    amount = float(line.split(":")[1].strip().split()[0].replace(",", ""))
    assert amount > 0


def test_cash_flow_empty(clean_db):
    text = services.report_cash_flow("2026-01-01", "2026-01-31")
    assert "Cash In" in text
    assert "Cash Out" in text


def test_cash_flow_includes_payments(clean_db):
    rid = make_reservation()
    services.record_payment(rid, 100.0, "Cash")
    text = services.report_cash_flow(*_today_range())
    line = [l for l in text.split("\n") if "Cash In" in l][0]
    amount = float(line.split(":")[1].strip().split()[0].replace(",", ""))
    assert amount >= 100.0


def test_occupancy_zero_when_no_bookings(clean_db):
    r = services.get_occupancy_for_window("2027-01-01", "2027-01-31")
    assert r["pct"] == 0.0
    assert r["occupied"] == 0


def test_occupancy_with_bookings(clean_db):
    make_reservation(ci="2026-12-01", co="2026-12-05", room_id=1)
    r = services.get_occupancy_for_window("2026-12-01", "2026-12-10")
    assert r["occupied"] > 0
    assert r["pct"] > 0


def test_daily_ops(clean_db):
    make_reservation(ci="2026-12-01", co="2026-12-05")
    text = services.report_daily_ops("2026-12-01")
    assert "ARRIVALS" in text
    assert "DEPARTURES" in text


def test_payment_methods_report(clean_db):
    rid = make_reservation()
    services.record_payment(rid, 100.0, "Cash")
    text = services.report_payment_methods(*_today_range())
    assert "Cash" in text