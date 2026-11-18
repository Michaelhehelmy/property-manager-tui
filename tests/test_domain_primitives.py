from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app_core.domain import availability, dates, metrics, money, tax


def test_domain_package_exports():
    # __init__ exports
    assert availability is not None
    assert dates is not None
    assert metrics is not None
    assert money is not None
    assert tax is not None


# ── money ────────────────────────────────────────────────────────────────────

def test_money_to_decimal_and_parse():
    assert money.to_decimal(None) == Decimal("0")
    assert money.to_decimal(None, None) is None
    assert money.to_decimal(Decimal("1.23")) == Decimal("1.23")
    assert money.to_decimal(True) == Decimal("1")
    assert money.to_decimal(5) == Decimal("5")
    assert money.to_decimal(1.5) == Decimal("1.5")
    assert money.to_decimal(" 1,234.50 ") == Decimal("1234.50")
    assert money.to_decimal("EGP 10") == Decimal("10")
    assert money.to_decimal("USD10") == Decimal("10")
    assert money.to_decimal("$10") == Decimal("10")
    assert money.to_decimal("€7") == Decimal("7")
    assert money.to_decimal("£8") == Decimal("8")
    assert money.to_decimal("", Decimal("9")) == Decimal("9")
    assert money.to_decimal("bad", Decimal("2")) == Decimal("2")
    assert money.parse_money("1.234") == Decimal("1.23")
    assert money.parse_money(None, Decimal("3.00")) == Decimal("3.00")


def test_money_ops_and_formatting():
    assert money.quantize_money("1.234") == Decimal("1.23")
    assert money.money_add("1.00", 2, "3.5") == Decimal("6.50")
    assert money.money_sub("10", "3.333") == Decimal("6.67")
    assert money.money_mul("2.5", 3) == Decimal("7.50")
    assert money.money_div("10", 4) == Decimal("2.50")
    assert money.money_div("10", 0, Decimal("1")) == Decimal("1.00")
    assert money.money_div("10", None, Decimal("2")) == Decimal("2.00")
    assert money.percent_of("200", 15) == Decimal("30.00")
    assert money.format_money(12.3) == "12.30"
    assert money.format_money(12.3, "EGP") == "EGP 12.30"
    assert money.format_money(12.345, decimals=3) == "12.345"


def test_money_allocate_proportionally():
    assert money.allocate_proportionally(0, []) == []
    assert money.allocate_proportionally(10, [0, 0]) == [Decimal("0.00"), Decimal("0.00")]
    parts = money.allocate_proportionally("10.00", [1, 1, 1])
    # drift is assigned to the first max-weight index (all equal here), sum must be preserved
    assert parts == [Decimal("3.34"), Decimal("3.33"), Decimal("3.33")]
    assert sum(parts, Decimal("0")) == Decimal("10.00")
    parts2 = money.allocate_proportionally(5, [2, -1, 3])
    assert sum(parts2, Decimal("0")) == Decimal("5.00")
    assert parts2[1] == Decimal("0.00")


# ── dates ────────────────────────────────────────────────────────────────────

def test_dates_parse_and_format():
    assert dates.parse_date(None) is None
    dt = datetime(2026, 11, 18, 12, 30)
    d = date(2026, 11, 18)
    assert dates.parse_date(dt) == d
    assert dates.parse_date(d) == d
    assert dates.parse_date("2026-11-18") == d
    assert dates.parse_date("18/11/2026") == d
    assert dates.parse_date("2026/11/18") == d
    assert dates.parse_date("2026-11-18 10:00:00") == d
    assert dates.parse_date("") is None
    assert dates.parse_date("not-a-date") is None
    assert dates.parse_date("bad-part 10:00:00") is None  # space fallback parse fails -> pass
    assert dates.format_date("2026-11-18") == "2026-11-18"
    assert dates.format_date(None) == ""

    assert dates.parse_datetime(None) is None
    assert dates.parse_datetime(dt) == dt
    assert dates.parse_datetime(d) == datetime(2026, 11, 18, 0, 0)
    assert dates.parse_datetime("2026-11-18 12:30:45") == datetime(2026, 11, 18, 12, 30, 45)
    assert dates.parse_datetime("2026-11-18") == datetime(2026, 11, 18, 0, 0)
    assert dates.parse_datetime("   ") is None
    assert dates.parse_datetime("bad") is None
    assert dates.format_datetime("2026-11-18 12:30:45") == "2026-11-18 12:30:45"
    assert dates.format_datetime(None) == ""


def test_dates_nights_ranges_and_overlap():
    assert dates.nights_between("2026-11-18", "2026-11-20") == 2
    assert dates.nights_between("2026-11-18", "2026-11-18", minimum=1) == 1
    assert dates.nights_between("2026-11-20", "2026-11-18", minimum=0) == 0
    assert dates.nights_between("bad", "x", minimum=2) == 2
    assert dates.stay_nights_exclusive("2026-11-18", "2026-11-20") == 2
    assert dates.stay_nights_exclusive("2026-11-18", "2026-11-18") == 0
    assert dates.stay_nights_exclusive(None, None) == 0
    assert dates.stay_nights_inclusive("2026-11-18", "2026-11-20") == 3
    assert dates.stay_nights_inclusive("2026-11-20", "2026-11-18") == 0

    days = dates.iter_stay_dates("2026-11-18", "2026-11-20")
    assert [d.isoformat() for d in days] == ["2026-11-18", "2026-11-19"]
    assert dates.iter_stay_dates("2026-11-18", "2026-11-20", inclusive_end=True)[-1].isoformat() == "2026-11-20"
    assert dates.iter_stay_dates("2026-11-18", "2026-11-18") == []
    assert dates.iter_stay_dates(None, None) == []

    assert dates.overlaps("2026-11-18", "2026-11-20", "2026-11-19", "2026-11-21") is True
    assert dates.overlaps("2026-11-18", "2026-11-19", "2026-11-19", "2026-11-20") is False
    assert dates.overlaps("2026-11-18", "2026-11-19", "2026-11-19", "2026-11-20", end_exclusive=False) is True
    assert dates.overlaps(None, "2026-11-19", "2026-11-19", "2026-11-20") is False

    assert dates.business_date("2026-11-18 10:00:00") == "2026-11-18"
    assert dates.business_date("2026-11-18 03:00:00", cutoff_time="06:00") == "2026-11-17"
    assert dates.business_date("2026-11-18 07:00:00", cutoff_time="06:00") == "2026-11-18"
    assert dates.business_date("2026-11-18 03:00:00", cutoff_time="bad") == "2026-11-18"
    assert dates.date_range("2026-11-18", "2026-11-20") == ["2026-11-18", "2026-11-19", "2026-11-20"]
    assert dates.date_range("2026-11-18", "2026-11-20", inclusive=False) == ["2026-11-18", "2026-11-19"]
    assert dates.date_range("2026-11-20", "2026-11-18") == []


# ── tax ──────────────────────────────────────────────────────────────────────

def test_tax_helpers():
    assert tax.tax_amount("100", 14) == Decimal("14.00")
    assert tax.gross_from_net("100", 14) == Decimal("114.00")
    assert tax.net_from_gross("114", 14) == Decimal("100.00")
    assert tax.net_from_gross("100", -100) == Decimal("100.00")  # denom 0 guard
    split = tax.split_gross("114", 14)
    assert split["gross"] == Decimal("114.00")
    assert split["net"] == Decimal("100.00")
    assert split["tax"] == Decimal("14.00")

    assert tax.apply_discount("100", discount_percent=10, discount_amount=5) == Decimal("85.00")
    assert tax.apply_discount("10", discount_percent=0, discount_amount=999) == Decimal("0.00")

    line = tax.line_totals(2, "10.00", tax_rate_percent=14, discount_percent=10, discount_amount=1)
    assert line["subtotal"] == Decimal("20.00")
    assert line["net"] == Decimal("17.00")
    assert line["tax"] == Decimal("2.38")
    assert line["gross"] == Decimal("19.38")


# ── availability ─────────────────────────────────────────────────────────────

def test_availability_helpers():
    assert availability.is_window_valid("2026-11-18", "2026-11-20") is True
    assert availability.is_window_valid("2026-11-18", "2026-11-18", min_nights=1) is False
    assert availability.is_window_valid(None, "2026-11-20") is False

    assert availability.windows_overlap("2026-11-18", "2026-11-20", "2026-11-19", "2026-11-21") is True
    assert availability.windows_overlap("2026-11-18", "2026-11-19", "2026-11-19", "2026-11-20") is False
    assert availability.windows_overlap(None, "2026-11-20", "2026-11-19", "2026-11-21") is False

    existing = [
        {"id": 1, "status": "Reserved", "check_in_date": "2026-11-18", "check_out_date": "2026-11-20"},
        {"reservation_id": 2, "status": "Cancelled", "check_in": "2026-11-18", "check_out": "2026-11-19"},
        {"id": 3, "status": "Checked In", "check_in": "2026-11-22", "check_out": "2026-11-23"},
    ]
    assert availability.is_room_available_for_window(existing, "2026-11-20", "2026-11-22") is True
    assert availability.is_room_available_for_window(existing, "2026-11-19", "2026-11-21") is False
    assert availability.is_room_available_for_window(existing, "2026-11-19", "2026-11-21", ignore_reservation_id=1) is True
    assert availability.is_room_available_for_window(None, "2026-11-18", "2026-11-19") is True

    occ = availability.occupied_night_dates("2026-11-18", "2026-11-20")
    assert [d.isoformat() for d in occ] == ["2026-11-18", "2026-11-19"]
    assert availability.occupied_night_dates("2026-11-18", "2026-11-18") == []
    assert availability.occupied_night_dates(None, "2026-11-20") == []


# ── metrics ──────────────────────────────────────────────────────────────────

def test_metrics_formulas():
    assert metrics.occupancy_rate(5, 10) == 50.0
    assert metrics.occupancy_rate(5, 0) == 0.0
    assert metrics.adr(1000, 4) == 250.0
    assert metrics.adr(1000, 0) == 0.0
    assert metrics.revpar(1000, 10) == 100.0
    assert metrics.revpar(1000, 0) == 0.0
    assert metrics.outstanding_amount(150, 100) == 50.0
    assert metrics.outstanding_amount(50, 100) == 0.0
    assert metrics.reservation_nights("2026-11-18", "2026-11-20") == 2


def test_metrics_reservation_counts_and_room_nights():
    reservations = [
        {"status": "Reserved", "check_in_date": "2026-11-18", "check_out_date": "2026-11-20"},
        {"status": "Checked In", "check_in": "2026-11-18", "check_out": "2026-11-21"},
        {"status": "Cancelled", "check_in": "2026-11-18", "check_out": "2026-11-19"},
        {"status": "Checked Out", "check_in": "2026-11-17", "check_out": "2026-11-18"},
        {"status": "Reserved", "check_in": "bad", "check_out": "2026-11-18"},
    ]
    assert metrics.room_nights_sold(reservations) == 2 + 3 + 1
    assert metrics.room_nights_sold(reservations, start="2026-11-19", end="2026-11-20") == 2
    assert metrics.room_nights_sold([], start="2026-11-19", end="2026-11-20") == 0

    assert metrics.count_arrivals(reservations, "2026-11-18") == 2
    assert metrics.count_arrivals(reservations, None) == 0
    # default departure statuses include Reserved/Checked In/Checked Out; last row has bad check-in but valid checkout date
    assert metrics.count_departures(reservations, "2026-11-18") == 2
    assert metrics.count_departures(reservations, "bad") == 0
    assert metrics.count_in_house(reservations, "2026-11-18", statuses={"Checked In"}) == 1
    assert metrics.count_in_house(reservations, "2026-11-17", statuses={"Checked In"}) == 0
    assert metrics.count_in_house(reservations, None) == 0
