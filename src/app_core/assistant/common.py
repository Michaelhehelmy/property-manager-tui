"""Common constants and deterministic helpers for assistant modules."""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from .. import services

DATE_FMT = getattr(services, "DATE_FMT", "%Y-%m-%d")
BASE_DIR = Path(getattr(services, "BASE_DIR", Path(__file__).resolve().parents[3]))
DATA_DIR = Path(getattr(services, "DATA_DIR", BASE_DIR / "data"))
INTENT_DATA_DIR = DATA_DIR / "assistant_nlu"
SEED_INTENTS_PATH = INTENT_DATA_DIR / "intents_seed.jsonl"
FEEDBACK_JSONL = INTENT_DATA_DIR / "feedback.jsonl"
DEFAULT_SETFIT_MODEL_DIR = BASE_DIR / "models" / "assistant_intent_setfit"
DEFAULT_SKLEARN_MODEL_PATH = BASE_DIR / "models" / "assistant_intent_sklearn.joblib"

INTENT_LABELS = [
    "capabilities_help", "greeting_smalltalk", "property_stats", "today_ops", "business_partner_briefing", "decision_support",
    "report_pl", "report_cash_flow", "report_vat", "report_payment_methods", "report_occupancy", "report_daily_ops",
    "occupancy_summary", "compare_periods", "outstanding_balances", "inventory_alerts", "top_selling_items",
    "expenses_summary", "sales_breakdown", "reservation_pipeline", "housekeeping_summary", "room_availability",
    "menu_info", "rate_plans", "ledger_integrity", "guest_search", "guest_profile", "guest_portfolio", "guest_last_visit",
    "guest_top_items_last_visit", "guest_last_visit_spend", "guest_activity_bookings",
    "list_activities", "activity_stats",
    "reservation_summary", "reservation_balance",
    "recent_activity", "expiring_batches", "inventory_batches", "trace_barcode", "app_update_status", "health_diagnostics",
    "ambiguous_needs_clarification", "unsupported_or_unsafe", "compound_multi_intent",
]

# Utilities
def _today():
    return datetime.now().date()

def _iso(d):
    return d.strftime(DATE_FMT)

def _safe_int(v):
    try:
        if v is None: return None
        s = str(v).strip()
        if s == "": return None
        return int(re.sub(r"[^\d-]", "", s)) if isinstance(s, str) else int(s)
    except (TypeError, ValueError):
        return None

def _norm_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[\u2018\u2019\u201c\u201d`]", "'", s)
    s = re.sub(r"\s+", " ", s)
    return s

def _token_set(s: str):
    return set(re.findall(r"[a-z0-9#@.+%-]+", _norm_text(s)))

def _sim(a: str, b: str) -> float:
    a, b = _norm_text(a), _norm_text(b)
    if not a or not b: return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()

def _fmt_money(x):
    try:
        return f"{float(x):,.2f}"
    except (TypeError, ValueError):
        return str(x)

def _parse_iso(s):
    try:
        return datetime.strptime(s, DATE_FMT).date()
    except (TypeError, ValueError, AttributeError):
        return None

def _yn(v):
    return "yes" if v else "no"

def _short(s, n=120):
    s = (str(s) if s is not None else "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"

def _val(d, *keys, default=None):
    cur = d
    for k in keys:
        if isinstance(cur, dict): cur = cur.get(k)
        else: return default
    return default if cur is None else cur

def _prev_period(start, end):
    try:
        d1 = datetime.strptime(start, DATE_FMT).date()
        d2 = datetime.strptime(end, DATE_FMT).date()
        days = (d2 - d1).days + 1
        p2 = d1 - timedelta(days=1)
        p1 = p2 - timedelta(days=days - 1)
        return _iso(p1), _iso(p2)
    except (TypeError, ValueError, AttributeError):
        logger.debug("assistant._prev_period: failed to compute previous period start=%r end=%r", start, end, exc_info=True)
        return start, end

# ── Date/range parsing ───────────────────────────────────────────────────────

def _parse_date_range(text: str):
    t = (text or "")
    tl = t.lower()
    today = _today()
    m = re.search(r"\b(?:between|from)\s+(20\d{2}-\d{2}-\d{2})\s+(?:to|and|until|-+)\s+(20\d{2}-\d{2}-\d{2})\b", tl)
    if m:
        d1, d2 = _parse_iso(m.group(1)), _parse_iso(m.group(2))
        if d1 and d2:
            if d2 < d1: d1, d2 = d2, d1
            return _iso(d1), _iso(d2)
    dates = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", t)
    if len(dates) >= 2:
        d1, d2 = _parse_iso(dates[0]), _parse_iso(dates[-1])
        a, b = dates[0], dates[-1]
        if d1 and d2 and d2 < d1: a, b = b, a
        return a, b
    m = re.search(r"\bsince\s+(20\d{2}-\d{2}-\d{2})\b", tl)
    if m:
        d = _parse_iso(m.group(1))
        if d: return _iso(d), _iso(today)
    if len(dates) == 1 and any(k in tl for k in ["on ", "for ", "dated "]):
        return dates[0], dates[0]
    if "today" in tl:
        return _iso(today), _iso(today)
    if "yesterday" in tl:
        y = today - timedelta(days=1); return _iso(y), _iso(y)
    if "tomorrow" in tl:
        d = today + timedelta(days=1); return _iso(d), _iso(d)
    if "this week" in tl or "current week" in tl:
        start = today - timedelta(days=today.weekday()); return _iso(start), _iso(today)
    if "last week" in tl or "previous week" in tl:
        start_this = today - timedelta(days=today.weekday())
        return _iso(start_this - timedelta(days=7)), _iso(start_this - timedelta(days=1))
    if re.search(r"\b(last|past|previous)\s+(7|seven)\s+days\b", tl):
        return _iso(today - timedelta(days=6)), _iso(today)
    if re.search(r"\b(last|past|previous)\s+(14|fourteen)\s+days\b", tl) or "fortnight" in tl:
        return _iso(today - timedelta(days=13)), _iso(today)
    if re.search(r"\b(last|past|previous)\s+(30|thirty)\s+days\b", tl):
        return _iso(today - timedelta(days=29)), _iso(today)
    if re.search(r"\b(last|past|previous)\s+(90|ninety)\s+days\b", tl) or "quarter" in tl:
        return _iso(today - timedelta(days=89)), _iso(today)
    if "this month" in tl or "current month" in tl or "month to date" in tl or "mtd" in tl:
        start = today.replace(day=1); return _iso(start), _iso(today)
    if "last month" in tl or "previous month" in tl:
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return _iso(last_prev.replace(day=1)), _iso(last_prev)
    if len(dates) == 1:
        return dates[0], dates[0]
    start = today.replace(day=1)
    return _iso(start), _iso(today)

def _prev_period(start, end):
    try:
        d1 = datetime.strptime(start, DATE_FMT).date()
        d2 = datetime.strptime(end, DATE_FMT).date()
        days = (d2 - d1).days + 1
        p2 = d1 - timedelta(days=1)
        p1 = p2 - timedelta(days=days - 1)
        return _iso(p1), _iso(p2)
    except (TypeError, ValueError, AttributeError):
        logger.debug("assistant._prev_period: failed to compute previous period start=%r end=%r", start, end, exc_info=True)
        return start, end

# ── Slot extraction & context carryover ──────────────────────────────────────

def _context_from_history(history):
    ctx = {}
    if not isinstance(history, list): return ctx
    for h in reversed(history):
        if h.get("role") == "assistant":
            slots = h.get("slots") or {}
            for k in ("guest_id", "reservation_id", "date_start", "date_end"):
                if slots.get(k) and k not in ctx: ctx[k] = slots.get(k)
            if h.get("intent") and "last_intent" not in ctx: ctx["last_intent"] = h.get("intent")
            if h.get("resolved_guest_id") and "guest_id" not in ctx: ctx["guest_id"] = h.get("resolved_guest_id")
        elif h.get("role") == "user":
            # light carryover not needed
            pass
        if len(ctx) >= 6: break
    return ctx

def extract_slots(text: str, history=None) -> dict:
    t = text or ""
    tl = _norm_text(t)
    slots: dict[str, Any] = {}
    m = re.search(r"\b(?:reservation|res|booking|folio)\s*#?\s*(\d+)\b", tl)
    if m: slots["reservation_id"] = int(m.group(1))
    m = re.search(r"\bguest\s*#?\s*(\d+)\b", tl)
    if m: slots["guest_id"] = int(m.group(1))
    m = re.search(r"\broom\s*#?\s*([a-z0-9-]+)\b", tl)
    if m: slots["room_query"] = m.group(1)
    s, e = _parse_date_range(t); slots["date_start"], slots["date_end"] = s, e
    rk = None
    if any(k in tl for k in ["p&l", "pnl", "profit and loss", "profit-wise", "bottom line", "net profit", "income versus outgoings", "earnings and costs", "revenue beat expenses", "financial performance", "sales in, costs out"]): rk = "pl"
    elif any(k in tl for k in ["cash flow", "cashflow", "cash in", "cash out", "money movement", "cash-positive", "liquidity", "ins and outs of cash"]): rk = "cash_flow"
    elif any(k in tl for k in ["value-added tax", "tax report", "output", "input tax"]) or re.search(r"(?<![a-z0-9])vat(?![a-z0-9])", tl): rk = "vat"
    elif any(k in tl for k in ["payment method", "payments by method", "paid us", "by cash, card", "split by cash", "payment channels", "settlement methods", "how did guests pay", "collections by method", "mix of settlement", "bank transfers or cash"]): rk = "payment_methods"
    elif any(k in tl for k in ["daily ops", "arrivals", "departures", "in-house", "in house", "check-ins", "checkouts", "check outs", "handover", "front-desk style ops", "stayovers", "ops picture", "ops summary", "operational view"]): rk = "daily_ops"
    elif any(k in tl for k in ["occupancy", "adr", "revpar", "room-nights", "fill rate", "how full", "how busy", "utilized", "how occupied"]): rk = "occupancy"
    if rk: slots["report_kind"] = rk
    mq = re.search(r"[\"""'`]([^\"""'`]{2,})[\"""'`]", t)
    if mq:
        slots["guest_query"] = mq.group(1).strip()
    if "guest_query" not in slots:
        mf = re.search(r"\b(?:find|search|look up|locate|named?|called|for|about)\s+(?:guest\s+|customer\s+)?([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s'.-]{1,50}?)(?:\s+(?:last|most|ordered|visit|stayed|reservation|balance|profile|history|check|who|and|portfolio|invoices|activities|trips)\b|[?.!,]|$)", t, re.I)
        if mf:
            cand = mf.group(1).strip(" ?.,!")
            bad = {"id","number","#","the","a","someone","anyone","he","she","they","him","her","them","last","visit","most","ordered","me","us","today","yesterday","month","week"}
            if cand.lower() not in bad and not re.fullmatch(r"\d+", cand):
                slots["guest_query"] = cand
    ctx = _context_from_history(history)
    followup = any(k in tl for k in ["same guest", "that guest", "his ", "her ", "their ", "same reservation", "that reservation", "for him", "for her", "for them", "and for", "what about", "same period", "same range", "break that down", "show me more", "their portfolio", "his portfolio", "her portfolio"])
    if followup or (len(t.split()) <= 6 and not slots.get("guest_id") and not slots.get("reservation_id")):
        for k in ("guest_id", "reservation_id", "date_start", "date_end"):
            if not slots.get(k) and ctx.get(k): slots[k] = ctx[k]
    slots["context"] = ctx
    return slots

def resolve_guest(text: str, slots: dict | None = None):
    slots = slots or extract_slots(text)
    gid = slots.get("guest_id")
    if gid:
        g = services.get_guest_by_id(gid)
        return ([g] if g else []), gid, None
    q = slots.get("guest_query")
    if q:
        q2 = re.sub(r"(?i)\b(find|search|look up|locate|guest|customer|named?|called|for|about|profile|someone|anyone|matching|matches|resembles?|show|possible|candidates|portfolio)\b", " ", q)
        q2 = re.sub(r"\s+", " ", q2).strip(" ?.,!")
        if len(q2) < 2: q2 = q
        return services.assistant_find_guests(q2, limit=8), None, q2
    return [], None, None

def _resolve_guest_from_text(text: str):  # compat
    guests, explicit, q = resolve_guest(text)
    return guests, explicit




def _format_data(title, data):
    """Fallback: compact structured view (kept for traceability)."""
    try:
        body = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    except Exception:
        body = str(data)
    if len(body) > 6000:
        body = body[:6000] + "\n... (truncated)"
    return f"{title}\n{body}"
