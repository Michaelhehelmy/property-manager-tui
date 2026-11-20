"""Date and stay/business-date helpers.

Pure, additive helpers aligned with the app's common ISO date format (%Y-%m-%d).
No existing behavior is modified.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

DATE_FMT = "%Y-%m-%d"
DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


def parse_date(value: Any, fmt: str = DATE_FMT) -> date | None:
    """Parse a date/datetime/string to a date. Returns None if unparseable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    for f in (fmt, DATE_FMT, "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, f).date()
        except Exception:
            continue
    if " " in s:
        try:
            return datetime.strptime(s.split(" ", 1)[0], DATE_FMT).date()
        except Exception:
            pass
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None


def format_date(value: Any, fmt: str = DATE_FMT) -> str:
    d = parse_date(value)
    return d.strftime(fmt) if d else ""


def parse_datetime(value: Any, fmt: str = DATETIME_FMT) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    s = str(value).strip()
    if not s:
        return None
    for f in (fmt, DATETIME_FMT, DATE_FMT):
        try:
            return datetime.strptime(s, f)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def format_datetime(value: Any, fmt: str = DATETIME_FMT) -> str:
    dt = parse_datetime(value)
    return dt.strftime(fmt) if dt else ""


def nights_between(check_in: Any, check_out: Any, minimum: int = 1) -> int:
    """Exclusive check-out night count (checkout day not charged as a night)."""
    ci = parse_date(check_in)
    co = parse_date(check_out)
    if not ci or not co:
        return max(0, int(minimum))
    delta = (co - ci).days
    if delta < 0:
        delta = 0
    if minimum is not None and delta < minimum:
        delta = max(delta, int(minimum)) if (co > ci or minimum == 0) else int(minimum)
    return int(delta)


def stay_nights_exclusive(check_in: Any, check_out: Any) -> int:
    """Exclusive night count with floor 0 (no forced minimum)."""
    ci = parse_date(check_in)
    co = parse_date(check_out)
    if not ci or not co or co <= ci:
        return 0
    return (co - ci).days


def stay_nights_inclusive(check_in: Any, check_out: Any) -> int:
    """Inclusive stay span in nights/days count (both endpoints counted)."""
    ci = parse_date(check_in)
    co = parse_date(check_out)
    if not ci or not co or co < ci:
        return 0
    return (co - ci).days + 1


def iter_stay_dates(check_in: Any, check_out: Any, inclusive_end: bool = False):
    """Return each date in a stay window.

    By default uses exclusive checkout (night dates / occupied nights).
    """
    ci = parse_date(check_in)
    co = parse_date(check_out)
    if not ci or not co:
        return []
    end = co if inclusive_end else co - timedelta(days=1)
    if end < ci:
        return []
    cur = ci
    out = []
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def overlaps(start_a: Any, end_a: Any, start_b: Any, end_b: Any, end_exclusive: bool = True) -> bool:
    """Return True if two date windows overlap."""
    a1, a2 = parse_date(start_a), parse_date(end_a)
    b1, b2 = parse_date(start_b), parse_date(end_b)
    if not all([a1, a2, b1, b2]):
        return False
    if end_exclusive:
        return a1 < b2 and b1 < a2
    return a1 <= b2 and b1 <= a2


def business_date(on_dt: Any = None, cutoff_time: str | None = None, fmt: str = DATE_FMT) -> str:
    """Return business date string (optional cutoff rolls back before cutoff)."""
    dt = parse_datetime(on_dt) or datetime.now()
    if cutoff_time:
        try:
            hh, mm = [int(x) for x in str(cutoff_time).split(":", 1)]
            cutoff = dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if dt < cutoff:
                dt = dt - timedelta(days=1)
        except Exception:
            pass
    return dt.strftime(fmt)


def date_range(start: Any, end: Any, inclusive: bool = True) -> list[str]:
    """List date strings from start..end."""
    d1 = parse_date(start)
    d2 = parse_date(end)
    if not d1 or not d2 or d2 < d1:
        return []
    out = []
    cur = d1
    last = d2 if inclusive else (d2 - timedelta(days=1))
    while cur <= last:
        out.append(cur.strftime(DATE_FMT))
        cur += timedelta(days=1)
    return out
