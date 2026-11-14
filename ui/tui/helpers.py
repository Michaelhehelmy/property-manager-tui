"""Shared/pure UI helpers for TUI refactors (formatting, parsing, decisions)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable


def safe_int(value: Any) -> int | None:
    try:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip():
            return int(value.strip())
    except Exception:
        return None
    return None


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def parse_ymd(value: str) -> datetime | None:
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d")
    except Exception:
        return None


def compute_window_range(mode_index: int | None, now: datetime, week_start_day: str, from_value: str, to_value: str) -> tuple[str, str]:
    """Pure helper for front-desk window range selection."""
    idx = 2 if mode_index is None else mode_index
    if idx == 0:
        d = now.strftime("%Y-%m-%d")
        return d, d
    if idx == 1:
        start_idx = {"monday": 0, "sunday": 6, "saturday": 5}.get((week_start_day or "monday").lower(), 0)
        start = now - timedelta(days=(now.weekday() - start_idx) % 7)
        return start.strftime("%Y-%m-%d"), (start + timedelta(days=6)).strftime("%Y-%m-%d")
    if idx == 2:
        start = now.replace(day=1)
        nm = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start.strftime("%Y-%m-%d"), (nm - timedelta(days=1)).strftime("%Y-%m-%d")
    return (from_value or "").strip(), (to_value or "").strip()


def format_assistant_prefix(intent: Any, confidence: Any, source: Any, tools: Iterable[str] | None) -> str:
    tools_s = ",".join([t for t in (tools or []) if t]) or "-"
    conf_s = f"{confidence:.2f}" if isinstance(confidence, (int, float)) else "?"
    return f"Assistant [intent={intent or '?'} conf={conf_s} source={source or '?'}; tools={tools_s}]:\n"


def typing_step_for_length(length: int) -> tuple[int, float]:
    if length > 500:
        return 4, 0.01
    if length > 180:
        return 2, 0.02
    return 1, 0.02
