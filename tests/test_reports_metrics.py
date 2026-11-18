from __future__ import annotations

from datetime import date, timedelta

import pytest

from app_core import db, services
from app_core.services import reports_metrics as rm
from conftest import make_reservation


def _d(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).strftime("%Y-%m-%d")


def test_arrivals_in_house_departures(clean_db):
    rid1 = make_reservation(ci=_d(10), co=_d(12), room_id=1, guest="Arriving")  # Reserved, arrives day 10
    rid2 = make_reservation(ci=_d(9), co=_d(11), room_id=2, guest="Departing")
    services.update_reservation_status(rid2, "Checked In")
    services.update_reservation_status(rid2, "Checked Out")  # departs day 11
    rid3 = make_reservation(ci=_d(8), co=_d(12), room_id=3, guest="InHouse")
    services.update_reservation_status(rid3, "Checked In")  # in house on day 10

    out = rm.arrivals_in_house_departures(_d(10))
    assert out["date"] == _d(10)
    assert any(r["id"] == rid1 for r in out["arrivals"])
    assert any(r["id"] == rid3 for r in out["in_house"])
    assert out["counts"]["arrivals"] >= 1
    assert out["counts"]["in_house"] >= 1

    dep = rm.arrivals_in_house_departures(_d(11))
    assert any(r["id"] == rid2 for r in dep["departures"])
    assert dep["counts"]["departures"] >= 1

    # default date path
    today_out = rm.arrivals_in_house_departures()
    assert "counts" in today_out and "date" in today_out


def test_occupancy_adr_revpar_and_invalid_range(clean_db):
    make_reservation(ci=_d(20), co=_d(22), rate=100.0, room_id=1, guest="Occ1")
    # reservation charge posted on create should contribute room revenue/occupied nights in window
    out = rm.occupancy_adr_revpar(_d(20), _d(21))
    assert out["start"] == _d(20)
    assert out["days"] == 2
    assert out["available_room_nights"] >= 0
    assert out["occupied_room_nights"] >= 1
    assert out["occupancy_pct"] >= 0.0
    assert "adr" in out and "revpar" in out and "room_revenue" in out

    bad = rm.occupancy_adr_revpar(_d(22), _d(20))
    assert bad["occupied_room_nights"] == 0
    assert bad["available_room_nights"] == 0


def test_outstanding_balances_and_summary(clean_db, monkeypatch):
    # fully paid / zero balance should not appear
    rid_paid = make_reservation(ci=_d(30), co=_d(31), rate=50.0, room_id=1, guest="Paid", deposit=9999.0)
    # unpaid reservation should appear
    rid_due = make_reservation(ci=_d(32), co=_d(34), rate=120.0, room_id=2, guest="Due", deposit=0.0)

    one = rm.reservation_outstanding(rid_due)
    assert one["reservation_id"] == rid_due
    assert one.get("outstanding", 0) >= 0

    items = rm.outstanding_balances()
    ids = [i["id"] for i in items]
    assert rid_due in ids
    assert rid_paid not in ids

    limited = rm.outstanding_balances(limit=1)
    assert len(limited) <= 1

    no_co = rm.outstanding_balances(include_checked_out=False)
    assert isinstance(no_co, list)

    summary = rm.outstanding_summary()
    assert summary["count"] == len(items)
    assert summary["total_outstanding"] >= 0
    assert "items" in summary

    # error path: nonexistent reservation should not raise
    missing = rm.reservation_outstanding(999999)
    assert missing["reservation_id"] == 999999
    assert "outstanding" in missing

    # exercise exception handling branch
    import app_core.services._monolith as monolith
    monkeypatch.setattr(monolith, "calculate_guest_balance", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    err = rm.reservation_outstanding(rid_due)
    assert err["reservation_id"] == rid_due and "error" in err


def test_occupancy_adr_revpar_skips_bad_dates(clean_db):
    # insert a reservation row with invalid dates to hit continue branch
    make_reservation(ci=_d(40), co=_d(41), rate=50.0, room_id=1, guest="Good")
    conn = db.connect()
    conn.execute(
        "INSERT INTO reservations (guest_name, source, room_id, agreed_nightly_rate, check_in_date, check_out_date, status) VALUES (?,?,?,?,?,?,?)",
        ("BadDates", "Direct", 2, 10.0, "bad", "worse", "Reserved"),
    )
    conn.commit()
    out = rm.occupancy_adr_revpar(_d(40), _d(41))
    assert out["days"] == 2
    assert out["occupied_room_nights"] >= 1
