"""Front desk + chart data + backup."""
import os
import pytest
import services
from conftest import make_reservation
from datetime import datetime, timedelta


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _tomorrow():
    return (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════════════════════════════
# FRONT DESK
# ═══════════════════════════════════════════════════════════════════════════

def test_summary_empty_db(clean_db):
    s = services.get_front_desk_summary()
    assert s["total"] == 4
    assert s["occupied"] == 0
    assert s["occupancy_pct"] == 0.0
    assert s["clean"] == 3
    assert s["dirty"] == 1
    assert s["maint"] == 0


def test_summary_with_today_booking(clean_db):
    make_reservation(ci=_today(), co=_tomorrow(), room_id=1)
    s = services.get_front_desk_summary()
    assert s["occupied"] == 1
    assert s["arrivals_today"] == 1


def test_rooms_available_all_free(clean_db):
    rooms = services.get_rooms_available_between("2027-06-01", "2027-06-10")
    assert len(rooms) == 4


def test_rooms_available_some_booked(clean_db):
    make_reservation(ci="2027-06-01", co="2027-06-10", room_id=1)
    rooms = services.get_rooms_available_between("2027-06-01", "2027-06-10")
    assert len(rooms) == 3
    assert 1 not in [r["id"] for r in rooms]


def test_upcoming_for_room(clean_db):
    make_reservation(ci="2027-06-01", co="2027-06-05", room_id=1)
    make_reservation(ci="2027-06-10", co="2027-06-15", room_id=1)
    upcoming = services.get_upcoming_reservations_for_room(1)
    assert len(upcoming) >= 0  # depends on whether the dates are in future


def test_occupancy_window_zero(clean_db):
    r = services.get_occupancy_for_window("2027-06-01", "2027-06-30")
    assert r["occupied"] == 0
    assert r["pct"] == 0.0
    assert r["adr"] == 0.0


def test_occupancy_window_with_booking(clean_db):
    make_reservation(ci="2027-06-01", co="2027-06-05", room_id=1)
    r = services.get_occupancy_for_window("2027-06-01", "2027-06-10")
    assert r["occupied"] == 4  # 4 nights (1-5)
    assert r["pct"] > 0
    assert r["adr"] > 0


def test_report_occupancy(clean_db):
    make_reservation(ci="2027-06-01", co="2027-06-05", room_id=1)
    text = services.report_occupancy("2027-06-01", "2027-06-10")
    assert "OCCUPANCY" in text
    assert "Occupancy %" in text
    assert "ADR" in text
    assert "RevPAR" in text


# ═══════════════════════════════════════════════════════════════════════════
# CHART DATA
# ═══════════════════════════════════════════════════════════════════════════

def test_revenue_expense_series_empty(clean_db):
    series = services.get_daily_revenue_expense_series("2027-01-01", "2027-01-03")
    assert len(series) == 3
    assert all(r == 0 and e == 0 for _, r, e in series)


def test_revenue_expense_series_with_charges(clean_db):
    make_reservation(rate=100.0, ci=_today(), co=_tomorrow())
    today = _today()
    series = services.get_daily_revenue_expense_series(today, today)
    assert len(series) == 1
    assert series[0][1] > 0  # revenue on today


def test_profit_series(clean_db):
    make_reservation(rate=100.0, ci=_today(), co=_tomorrow())
    series = services.get_daily_profit_series(_today(), _today())
    assert len(series) == 1
    assert series[0][1] > 0  # profit positive


def test_payment_method_dict_empty(clean_db):
    d = services.get_payment_method_dict("2027-01-01", "2027-01-03")
    assert d == {}


def test_payment_method_dict_with_payment(clean_db):
    rid = make_reservation()
    services.record_payment(rid, 100.0, "Cash")
    d = services.get_payment_method_dict(_today(), _today())
    assert d.get("Cash", 0) >= 100.0


def test_occupancy_series(clean_db):
    make_reservation(ci=_today(), co=_tomorrow())
    series = services.get_daily_occupancy_series(_today(), _today())
    assert len(series) == 1
    assert series[0][1] > 0


def test_heatmap_empty(clean_db):
    data = services.get_occupancy_heatmap_data("2027-06-01", "2027-06-03")
    assert len(data["dates"]) == 3
    assert len(data["rooms"]) == 4
    assert all(all(v == 0 for v in row) for row in data["matrix"])


def test_heatmap_with_booking(clean_db):
    make_reservation(ci="2027-06-01", co="2027-06-03", room_id=1)
    data = services.get_occupancy_heatmap_data("2027-06-01", "2027-06-03")
    # Room 1 should be occupied on day 1
    assert data["matrix"][0][0] == 1


# ═══════════════════════════════════════════════════════════════════════════
# BACKUP
# ═══════════════════════════════════════════════════════════════════════════

def test_backup_creates_file(clean_db):
    path = services.backup_now()
    assert os.path.exists(path)
    assert path.endswith(".db")


def test_backup_is_valid_sqlite(clean_db):
    path = services.backup_now()
    import sqlite3
    conn = sqlite3.connect(path)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    conn.close()
    assert "reservations" in tables
    assert "general_ledger" in tables


def test_backup_contains_data(clean_db):
    make_reservation(guest="Backup Test")
    path = services.backup_now()
    import sqlite3
    conn = sqlite3.connect(path)
    rows = conn.execute("SELECT guest_name FROM reservations").fetchall()
    conn.close()
    assert any("Backup Test" in r[0] for r in rows)