"""Pure availability/date-window helpers (Phase 0 placeholder primitives).

These helpers are intentionally side-effect-free and DB-agnostic so future
availability logic can build on them without changing current behavior.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from .dates import parse_date, stay_nights_exclusive


def is_window_valid(check_in: Any, check_out: Any, min_nights: int = 1) -> bool:
    ci = parse_date(check_in)
    co = parse_date(check_out)
    if not ci or not co:
        return False
    return stay_nights_exclusive(ci, co) >= max(0, int(min_nights))


def windows_overlap(check_in_a: Any, check_out_a: Any, check_in_b: Any, check_out_b: Any) -> bool:
    """Hotel-style overlap: [check_in, check_out) intersects [check_in, check_out)."""
    a1, a2 = parse_date(check_in_a), parse_date(check_out_a)
    b1, b2 = parse_date(check_in_b), parse_date(check_out_b)
    if not all([a1, a2, b1, b2]):
        return False
    return a1 < b2 and b1 < a2


def is_room_available_for_window(existing_reservations: Iterable[dict[str, Any]],
                                 check_in: Any, check_out: Any,
                                 ignore_reservation_id: Any = None,
                                 active_statuses: set[str] | None = None) -> bool:
    """Pure check against in-memory reservation windows."""
    active = active_statuses or {"Reserved", "Checked In"}
    ignore = None if ignore_reservation_id is None else str(ignore_reservation_id)
    for r in existing_reservations or []:
        rid = r.get("reservation_id", r.get("id"))
        if ignore is not None and str(rid) == ignore:
            continue
        status = (r.get("status") or "").strip()
        if status and status not in active:
            continue
        ci = r.get("check_in", r.get("check_in_date"))
        co = r.get("check_out", r.get("check_out_date"))
        if windows_overlap(check_in, check_out, ci, co):
            return False
    return True


def occupied_night_dates(check_in: Any, check_out: Any):
    """Return occupied night dates for a stay window [check_in, check_out)."""
    ci = parse_date(check_in)
    co = parse_date(check_out)
    if not ci or not co or co <= ci:
        return []
    out = []
    cur = ci
    while cur < co:
        out.append(cur)
        cur += timedelta(days=1)
    return out
