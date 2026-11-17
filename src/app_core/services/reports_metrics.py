"""Canonical reporting/metrics helpers (additive, read-only).

Query/pure helpers for occupancy, ADR, RevPAR, outstanding balances, and
arrivals/in-house/departures based on existing DB structures. These helpers do
not modify existing report behavior or APIs.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .. import db
from ..domain import metrics as dm
from ..domain.dates import DATE_FMT, parse_date

ACTIVE_STAY_STATUSES = ("Reserved", "Checked In", "Checked Out")
IN_HOUSE_STATUSES = ("Reserved", "Checked In")


def _iso_day(value: Any = None) -> str:
    if value is None:
        return datetime.now().strftime(DATE_FMT)
    d = parse_date(value)
    return d.strftime(DATE_FMT) if d else str(value)


def arrivals_in_house_departures(on_date: Any = None) -> dict[str, Any]:
    """Return arrivals, in-house, and departures for a business date.

    Uses existing reservation/check-in/out/status fields and read-only queries.
    """
    day = _iso_day(on_date)
    conn = db.connect()
    arrivals = conn.execute(
        """SELECT id, guest_name, room_id, check_in_date, check_out_date, status
           FROM reservations
           WHERE check_in_date=? AND status IN ('Reserved','Checked In')
           ORDER BY id""",
        (day,),
    ).fetchall()
    departures = conn.execute(
        """SELECT id, guest_name, room_id, check_in_date, check_out_date, status
           FROM reservations
           WHERE check_out_date=? AND status IN ('Reserved','Checked In','Checked Out')
           ORDER BY id""",
        (day,),
    ).fetchall()
    in_house = conn.execute(
        """SELECT id, guest_name, room_id, check_in_date, check_out_date, status
           FROM reservations
           WHERE status='Checked In' AND check_in_date <= ? AND check_out_date > ?
           ORDER BY id""",
        (day, day),
    ).fetchall()
    # normalize sqlite3.Row objects to plain dicts for easy reuse
    def _rows(rows):
        return [dict(r) for r in rows]
    return {
        "date": day,
        "arrivals": _rows(arrivals),
        "departures": _rows(departures),
        "in_house": _rows(in_house),
        "counts": {
            "arrivals": len(arrivals),
            "departures": len(departures),
            "in_house": len(in_house),
        },
    }


def occupancy_adr_revpar(start: Any, end: Any) -> dict[str, Any]:
    """Compute occupancy, ADR, and RevPAR for an inclusive date window.

    Mirrors existing data sources (rooms/reservations/general_ledger) without
    altering existing report functions.
    """
    d1 = parse_date(start)
    d2 = parse_date(end)
    if not d1 or not d2 or d2 < d1:
        return {
            "start": str(start), "end": str(end),
            "occupancy_pct": 0.0, "occupied_room_nights": 0, "available_room_nights": 0,
            "adr": 0.0, "revpar": 0.0, "room_revenue": 0.0,
        }
    start_s = d1.strftime(DATE_FMT)
    end_s = d2.strftime(DATE_FMT)
    days = (d2 - d1).days + 1
    conn = db.connect()
    total_rooms = conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] or 0
    available_room_nights = int(total_rooms) * int(days)

    rows = conn.execute(
        """SELECT check_in_date, check_out_date FROM reservations
           WHERE status IN ('Reserved','Checked In','Checked Out')
             AND NOT (check_out_date <= ? OR check_in_date > ?)""",
        (start_s, end_s),
    ).fetchall()
    occupied_room_nights = 0
    end_excl = d2 + timedelta(days=1)
    for r in rows:
        ci = parse_date(r["check_in_date"])
        co = parse_date(r["check_out_date"])
        if not ci or not co:
            continue
        a = max(ci, d1)
        b = min(co, end_excl)
        if b > a:
            occupied_room_nights += (b - a).days

    rev = conn.execute(
        """SELECT COALESCE(SUM(net + service), 0) FROM general_ledger
           WHERE entry_type IN ('CHARGE','VOID_CHARGE') AND category='Accommodation' AND ref_type='RES'
             AND ref_id IN (
                SELECT id FROM reservations
                WHERE status IN ('Reserved','Checked In','Checked Out')
                  AND NOT (check_out_date <= ? OR check_in_date > ?)
             )""",
        (start_s, end_s),
    ).fetchone()[0] or 0.0

    return {
        "start": start_s,
        "end": end_s,
        "days": days,
        "total_rooms": int(total_rooms),
        "occupied_room_nights": int(occupied_room_nights),
        "available_room_nights": int(available_room_nights),
        "room_revenue": round(float(rev), 2),
        "occupancy_pct": dm.occupancy_rate(occupied_room_nights, available_room_nights),
        "adr": dm.adr(rev, occupied_room_nights),
        "revpar": dm.revpar(rev, available_room_nights),
    }


def reservation_outstanding(reservation_id: int) -> dict[str, Any]:
    """Outstanding balance for one reservation from existing ledger/balance logic.

    Uses services.calculate_guest_balance when available; returns a structured
    result and never raises on calculation errors.
    """
    rid = int(reservation_id)
    out: dict[str, Any] = {"reservation_id": rid, "outstanding": 0.0}
    try:
        from ._monolith import calculate_guest_balance  # local import, additive use
        bal = float(calculate_guest_balance(rid) or 0)
        out["outstanding"] = round(bal if bal > 0 else 0.0, 2)
        out["balance"] = round(bal, 2)
    except Exception as e:
        out["error"] = str(e)
    return out


def outstanding_balances(limit: int | None = None, include_checked_out: bool = True) -> list[dict[str, Any]]:
    """List reservations with positive outstanding balances (read-only).

    Based on existing reservation statuses and balance calculation behavior.
    """
    statuses = ("Reserved", "Checked In", "Checked Out") if include_checked_out else ("Reserved", "Checked In")
    q = "?, " * (len(statuses) - 1) + "?"
    conn = db.connect()
    rows = conn.execute(
        f"""SELECT r.id, r.guest_name, r.room_id, rm.room_number, r.check_in_date, r.check_out_date, r.status
            FROM reservations r
            LEFT JOIN rooms rm ON rm.id = r.room_id
            WHERE r.status IN ({q})
            ORDER BY r.check_out_date DESC, r.id DESC""",
        statuses,
    ).fetchall()
    results = []
    for r in rows:
        item = dict(r)
        ob = reservation_outstanding(r["id"])
        due = float(ob.get("outstanding") or 0)
        if due > 0:
            item["outstanding"] = due
            item["balance"] = ob.get("balance", due)
            results.append(item)
            if limit is not None and len(results) >= max(0, int(limit)):
                break
    return results


def outstanding_summary(limit: int | None = None, include_checked_out: bool = True) -> dict[str, Any]:
    items = outstanding_balances(limit=limit, include_checked_out=include_checked_out)
    total = round(sum(float(x.get("outstanding") or 0) for x in items), 2)
    return {"count": len(items), "total_outstanding": total, "items": items}
