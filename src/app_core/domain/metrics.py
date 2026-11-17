"""Pure reporting/metric formulas (canonical, additive).

These functions compute metrics from provided values/records without DB access,
so they can be reused by services, reports, and future business logic.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from .dates import parse_date, stay_nights_exclusive
from .money import ZERO, money_div, to_decimal


def occupancy_rate(occupied_room_nights: Any, available_room_nights: Any) -> float:
    occ = float(to_decimal(occupied_room_nights, ZERO) or ZERO)
    avail = float(to_decimal(available_room_nights, ZERO) or ZERO)
    if avail <= 0:
        return 0.0
    return round((occ / avail) * 100.0, 2)


def adr(room_revenue: Any, rooms_sold: Any) -> float:
    rev = to_decimal(room_revenue, ZERO) or ZERO
    sold = to_decimal(rooms_sold, ZERO) or ZERO
    if sold <= 0:
        return 0.0
    return float(money_div(rev, sold))


def revpar(room_revenue: Any, available_room_nights: Any) -> float:
    rev = to_decimal(room_revenue, ZERO) or ZERO
    avail = to_decimal(available_room_nights, ZERO) or ZERO
    if avail <= 0:
        return 0.0
    return float(money_div(rev, avail))


def outstanding_amount(total_charges: Any, total_paid: Any) -> float:
    charges = to_decimal(total_charges, ZERO) or ZERO
    paid = to_decimal(total_paid, ZERO) or ZERO
    due = charges - paid
    return float(due if due > 0 else ZERO)


def reservation_nights(check_in: Any, check_out: Any) -> int:
    return stay_nights_exclusive(check_in, check_out)


def room_nights_sold(reservations: Iterable[dict[str, Any]], start: Any = None, end: Any = None,
                     active_statuses: set[str] | None = None) -> int:
    """Sum occupied room-nights for reservations, optionally clipped to [start,end).

    `start`/`end` use hotel-style exclusive end semantics when provided.
    """
    active = active_statuses or {"Reserved", "Checked In", "Checked Out"}
    win_start = parse_date(start) if start else None
    win_end = parse_date(end) if end else None
    total = 0
    for r in reservations or []:
        status = (r.get("status") or "").strip()
        if status and status not in active:
            continue
        ci = parse_date(r.get("check_in", r.get("check_in_date")))
        co = parse_date(r.get("check_out", r.get("check_out_date")))
        if not ci or not co or co <= ci:
            continue
        a = max(ci, win_start) if win_start else ci
        b = min(co, win_end) if win_end else co
        if b > a:
            total += (b - a).days
    return int(total)


def count_arrivals(reservations: Iterable[dict[str, Any]], on_date: Any,
                   statuses: set[str] | None = None) -> int:
    day = parse_date(on_date)
    if not day:
        return 0
    allowed = statuses or {"Reserved", "Checked In"}
    n = 0
    for r in reservations or []:
        if (r.get("status") or "") not in allowed:
            continue
        if parse_date(r.get("check_in", r.get("check_in_date"))) == day:
            n += 1
    return n


def count_departures(reservations: Iterable[dict[str, Any]], on_date: Any,
                     statuses: set[str] | None = None) -> int:
    day = parse_date(on_date)
    if not day:
        return 0
    allowed = statuses or {"Reserved", "Checked In", "Checked Out"}
    n = 0
    for r in reservations or []:
        if (r.get("status") or "") not in allowed:
            continue
        if parse_date(r.get("check_out", r.get("check_out_date"))) == day:
            n += 1
    return n


def count_in_house(reservations: Iterable[dict[str, Any]], on_date: Any,
                   statuses: set[str] | None = None) -> int:
    day = parse_date(on_date)
    if not day:
        return 0
    allowed = statuses or {"Checked In"}
    n = 0
    for r in reservations or []:
        if (r.get("status") or "") not in allowed:
            continue
        ci = parse_date(r.get("check_in", r.get("check_in_date")))
        co = parse_date(r.get("check_out", r.get("check_out_date")))
        if ci and co and ci <= day < co:
            n += 1
    return n
