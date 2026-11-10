"""Manager assistant: grounded read-only core + tiered NLU (rules/sklearn/setfit, optional)
+ feedback/correction learning + optional talking/full LLM.

- Facts come ONLY from real backend tools/services (read-only).
- Default is light/fast (no ML deps). ML routers are optional and lazy-loaded.
- Learns over time: user ratings/corrections stored locally; instant exemplar memory;
  periodic retraining via scripts/train_assistant_intent.py.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import services

DATE_FMT = getattr(services, "DATE_FMT", "%Y-%m-%d")
BASE_DIR = Path(getattr(services, "BASE_DIR", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(getattr(services, "DATA_DIR", BASE_DIR / "data"))
INTENT_DATA_DIR = DATA_DIR / "assistant_nlu"
SEED_INTENTS_PATH = INTENT_DATA_DIR / "intents_seed.jsonl"
FEEDBACK_JSONL = INTENT_DATA_DIR / "feedback.jsonl"  # mirror/export; DB is source of truth
DEFAULT_SETFIT_MODEL_DIR = BASE_DIR / "models" / "assistant_intent_setfit"
DEFAULT_SKLEARN_MODEL_PATH = BASE_DIR / "models" / "assistant_intent_sklearn.joblib"

INTENT_LABELS = [
    "capabilities_help", "report_pl", "report_cash_flow", "report_vat", "report_payment_methods",
    "report_occupancy", "report_daily_ops", "decision_support", "guest_search", "guest_profile",
    "guest_last_visit", "guest_top_items_last_visit", "reservation_summary", "reservation_balance",
    "rate_plans", "occupancy_summary", "ledger_integrity", "property_stats", "ambiguous_needs_clarification",
    "unsupported_or_unsafe", "compound_multi_intent",
]

# ── Utilities ────────────────────────────────────────────────────────────────

def _today():
    return datetime.now().date()

def _iso(d):
    return d.strftime(DATE_FMT)

def _safe_int(v):
    try:
        if v is None:
            return None
        s = str(v).strip()
        if s == "":
            return None
        return int(s)
    except Exception:
        return None

def _norm_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[\u2018\u2019\u201c\u201d`]", "'", s)
    s = re.sub(r"\s+", " ", s)
    return s

def _token_set(s: str):
    return set(re.findall(r"[a-z0-9#@.+-]+", _norm_text(s)))


# ── Date/range parsing (T0 improved) ─────────────────────────────────────────

def _parse_iso(s):
    try:
        return datetime.strptime(s, DATE_FMT).date()
    except Exception:
        return None

def _parse_date_range(text: str):
    t = (text or "")
    tl = t.lower()
    today = _today()
    # explicit range: between/from X to/and Y
    m = re.search(r"\b(?:between|from)\s+(20\d{2}-\d{2}-\d{2})\s+(?:to|and|until|-+)\s+(20\d{2}-\d{2}-\d{2})\b", tl)
    if m:
        d1, d2 = _parse_iso(m.group(1)), _parse_iso(m.group(2))
        if d1 and d2:
            if d2 < d1:
                d1, d2 = d2, d1
            return _iso(d1), _iso(d2)
    dates = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", t)
    if len(dates) >= 2:
        d1, d2 = _parse_iso(dates[0]), _parse_iso(dates[-1])
        a, b = dates[0], dates[-1]
        if d1 and d2 and d2 < d1:
            a, b = b, a
        return a, b
    # "since <date>"
    m = re.search(r"\bsince\s+(20\d{2}-\d{2}-\d{2})\b", tl)
    if m:
        d = _parse_iso(m.group(1))
        if d:
            return _iso(d), _iso(today)
    if len(dates) == 1 and any(k in tl for k in ["on ", "for ", "dated "]):
        return dates[0], dates[0]
    if "today" in tl:
        return _iso(today), _iso(today)
    if "yesterday" in tl:
        y = today - timedelta(days=1)
        return _iso(y), _iso(y)
    if "tomorrow" in tl:
        d = today + timedelta(days=1)
        return _iso(d), _iso(d)
    if "this week" in tl or "current week" in tl:
        start = today - timedelta(days=today.weekday())
        return _iso(start), _iso(today)
    if "last week" in tl or "previous week" in tl:
        start_this = today - timedelta(days=today.weekday())
        start_prev = start_this - timedelta(days=7)
        end_prev = start_this - timedelta(days=1)
        return _iso(start_prev), _iso(end_prev)
    if "last 7 days" in tl or "past 7 days" in tl or "previous 7 days" in tl:
        return _iso(today - timedelta(days=6)), _iso(today)
    if "last 14 days" in tl or "past 14 days" in tl or "fortnight" in tl:
        return _iso(today - timedelta(days=13)), _iso(today)
    if "last 30 days" in tl or "past 30 days" in tl or "previous 30 days" in tl:
        return _iso(today - timedelta(days=29)), _iso(today)
    if "last 90 days" in tl or "past 90 days" in tl or "quarter" in tl:
        return _iso(today - timedelta(days=89)), _iso(today)
    if "this month" in tl or "current month" in tl or "month to date" in tl or "mtd" in tl:
        start = today.replace(day=1)
        return _iso(start), _iso(today)
    if "last month" in tl or "previous month" in tl:
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        start_prev = last_prev.replace(day=1)
        return _iso(start_prev), _iso(last_prev)
    if len(dates) == 1:
        return dates[0], dates[0]
    # default: month-to-date
    start = today.replace(day=1)
    return _iso(start), _iso(today)


# ── Slot/entity extraction (T0, deterministic, high precision) ────────────────

def extract_slots(text: str) -> dict:
    t = text or ""
    tl = t.lower()
    slots: dict[str, Any] = {}
    # reservation id
    m = re.search(r"\b(?:reservation|res|booking)\s*#?\s*(\d+)\b", tl)
    if m:
        slots["reservation_id"] = int(m.group(1))
    # guest id
    m = re.search(r"\bguest\s*#?\s*(\d+)\b", tl)
    if m:
        slots["guest_id"] = int(m.group(1))
    # date range
    s, e = _parse_date_range(t)
    slots["date_start"], slots["date_end"] = s, e
    # report kind hints
    if any(k in tl for k in ["p&l", "pnl", "profit and loss", "profit-wise", "bottom line", "net profit", "income versus outgoings", "earnings and costs", "revenue beat expenses", "financial performance", "sales in, costs out"]):
        slots["report_kind"] = "pl"
    elif any(k in tl for k in ["cash flow", "cashflow", "cash in", "cash out", "money movement", "cash-positive", "liquidity", "ins and outs of cash"]):
        slots["report_kind"] = "cash_flow"
    elif any(k in tl for k in ["vat", "value-added tax", "tax report", "output", "input tax"]):
        slots["report_kind"] = "vat"
    elif any(k in tl for k in ["payment method", "payments by method", "paid us", "by cash, card", "split by cash", "payment channels", "settlement methods", "how did guests pay", "how were they paid", "collections by method", "mix of settlement", "paid by cash", "bank transfers or cash"]):
        slots["report_kind"] = "payment_methods"
    elif any(k in tl for k in ["occupancy", "adr", "revpar", "room-nights", "fill rate", "how full", "how busy", "utilized", "how occupied"]):
        slots["report_kind"] = "occupancy"
    elif any(k in tl for k in ["daily ops", "arrivals", "departures", "in-house", "in house", "check-ins", "checkouts", "check outs", "handover", "front-desk style ops", "stayovers"]):
        slots["report_kind"] = "daily_ops"
    # guest name/query hint
    mq = re.search(r"[\"""'`]([^\"""'`]{2,})[\"""'`]", t)
    if mq:
        slots["guest_query"] = mq.group(1).strip()
    if "guest_query" not in slots:
        mf = re.search(r"\b(?:find|search|look up|locate|guest|customer|named?|called|for|about)\s+([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s'.-]{1,50}?)(?:\s+(?:last|most|ordered|visit|stayed|reservation|balance|profile|history|check|who)\b|[?.!,]|$)", t, re.I)
        if mf:
            cand = mf.group(1).strip(" ?.,!")
            if cand.lower() not in {"id","number","#","the","a","someone","anyone","he","she","they","him","her","them","last","visit","most","ordered"} and not re.fullmatch(r"\d+", cand):
                slots["guest_query"] = cand
    return slots


def resolve_guest(text: str, slots: dict | None = None):
    slots = slots or extract_slots(text)
    gid = slots.get("guest_id")
    if gid:
        g = services.get_guest_by_id(gid)
        return ([g] if g else []), gid, None
    q = slots.get("guest_query")
    if q:
        # clean common noise
        q2 = re.sub(r"(?i)\b(find|search|look up|locate|guest|customer|named?|called|for|about|profile|someone|anyone|matching|matches|resembles?)\b", " ", q)
        q2 = re.sub(r"\s+", " ", q2).strip(" ?.,!")
        if len(q2) < 2:
            q2 = q
        return services.assistant_find_guests(q2, limit=8), None, q2
    # fallback: old heuristic
    candidates, explicit = _resolve_guest_from_text(text)
    return candidates, explicit, None


# Keep legacy helper for compatibility
def _resolve_guest_from_text(text: str):
    m = re.search(r"\bguest\s*#?\s*(\d+)\b", text, re.I)
    if m:
        gid = int(m.group(1))
        g = services.get_guest_by_id(gid)
        return ([g] if g else []), gid
    name = None
    mq = re.search(r"[\"""'`]([^\"""'`]{2,})[\"""'`]", text)
    if mq:
        name = mq.group(1).strip()
    if not name:
        mf = re.search(r"\b(?:for|of|guest|customer)\s+([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s'.-]{1,60}?)(?:\s+(?:last|most|ordered|visit|stayed|reservation|balance)\b|[?.!,]|$)", text, re.I)
        if mf:
            name = mf.group(1).strip(" ?.,!")
    if not name:
        return [], None
    if name.lower() in {"last visit", "most", "ordered", "the guest", "a guest", "guest", "he", "she", "they", "him", "her", "them"}:
        return [], None
    return services.assistant_find_guests(name, limit=8), None


# ── Read-only tools (same as before, real backend) ───────────────────────────

def tool_list_capabilities(args=None):
    return {
        "capabilities": [
            "Reports: P&L, cash flow, VAT, payment methods, occupancy, daily ops (date/range)",
            "Decision support: KPIs and rule-based signals for a date range",
            "Guests: search, profile, last visit, most-ordered items on last visit",
            "Reservations: summary/details and guest balance by reservation ID",
            "Reference: rate plans, occupancy summary",
            "Health: read-only ledger/data integrity check",
        ],
        "notes": [
            "All answers are grounded in real backend data/tools (read-only).",
            "‘Last visit’ = most recent Checked Out reservation; otherwise most recent non-deleted reservation.",
            "For guest questions, provide a name/phone fragment or guest/reservation ID if ambiguous.",
        ],
    }


def tool_search_guests(args):
    q = (args or {}).get("query") or (args or {}).get("q") or ""
    limit = (args or {}).get("limit") or 10
    return {"query": q, "guests": services.assistant_find_guests(q, limit=limit)}


def tool_get_guest_profile(args):
    gid = _safe_int((args or {}).get("guest_id") or (args or {}).get("id"))
    if not gid:
        return {"error": "guest_id is required"}
    guest = services.get_guest_by_id(gid)
    if not guest:
        return {"error": f"Guest #{gid} not found"}
    history = services.get_guest_history_by_id(gid)
    hist = [{k: r[k] for k in r.keys()} for r in history[:10]]
    return {"guest": guest, "recent_reservations": hist, "reservation_count_shown": len(hist)}


def tool_get_guest_last_visit(args):
    gid = _safe_int((args or {}).get("guest_id") or (args or {}).get("id"))
    if not gid:
        return {"error": "guest_id is required"}
    return services.assistant_get_guest_last_visit(gid)


def tool_get_guest_top_items_last_visit(args):
    gid = _safe_int((args or {}).get("guest_id") or (args or {}).get("id"))
    limit = (args or {}).get("limit") or 5
    if not gid:
        return {"error": "guest_id is required"}
    return services.assistant_get_top_items_last_visit(gid, limit=limit)


def tool_get_reservation_summary(args):
    rid = _safe_int((args or {}).get("reservation_id") or (args or {}).get("id"))
    if not rid:
        return {"error": "reservation_id is required"}
    res = services.assistant_reservation_summary(rid)
    return res or {"error": f"Reservation #{rid} not found"}


def tool_get_guest_balance(args):
    rid = _safe_int((args or {}).get("reservation_id") or (args or {}).get("id"))
    if not rid:
        return {"error": "reservation_id is required"}
    try:
        return {"reservation_id": rid, "balance": services.calculate_guest_balance(rid)}
    except Exception as e:
        return {"reservation_id": rid, "error": str(e)}


def tool_get_report(args):
    kind = ((args or {}).get("kind") or "pl").lower().strip()
    start, end = (args or {}).get("start"), (args or {}).get("end")
    if not start or not end:
        start, end = _parse_date_range((args or {}).get("range") or "")
    try:
        if kind in ("pl", "pnl", "profit", "profit_and_loss"):
            return {"kind": "pl", "start": start, "end": end, "report": services.report_pl_statement(start, end)}
        if kind in ("cash", "cash_flow", "cashflow"):
            return {"kind": "cash_flow", "start": start, "end": end, "report": services.report_cash_flow(start, end)}
        if kind in ("vat", "tax"):
            return {"kind": "vat", "start": start, "end": end, "report": services.report_vat(start, end)}
        if kind in ("payment_methods", "payments"):
            return {"kind": "payment_methods", "start": start, "end": end, "report": services.report_payment_methods(start, end)}
        if kind in ("occupancy", "occ"):
            return {"kind": "occupancy", "start": start, "end": end, "report": services.report_occupancy(start, end)}
        if kind in ("daily_ops", "daily", "ops"):
            day = end or start
            return {"kind": "daily_ops", "date": day, "report": services.report_daily_ops(day)}
        return {"error": f"Unknown report kind '{kind}'", "supported": ["pl", "cash_flow", "vat", "payment_methods", "occupancy", "daily_ops"]}
    except Exception as e:
        return {"kind": kind, "start": start, "end": end, "error": str(e)}


def tool_get_decision_support(args):
    start, end = (args or {}).get("start"), (args or {}).get("end")
    if not start or not end:
        start, end = _parse_date_range((args or {}).get("range") or "")
    try:
        return {"start": start, "end": end, "decision_support": services.dataset_decision_support_v1(start, end), "financial_summary": services.dataset_financial_summary(start, end)}
    except Exception as e:
        return {"start": start, "end": end, "error": str(e)}


def tool_list_rate_plans(args=None):
    try:
        plans = []
        for p in services.get_all_rate_plans():
            d = {k: p[k] for k in p.keys()}
            try:
                d["reservations_using_plan"] = services.count_reservations_using_plan(p["id"])
            except Exception:
                d["reservations_using_plan"] = None
            plans.append(d)
        return {"rate_plans": plans}
    except Exception as e:
        return {"error": str(e)}


def tool_get_occupancy_summary(args):
    start, end = (args or {}).get("start"), (args or {}).get("end")
    if not start or not end:
        start, end = _parse_date_range((args or {}).get("range") or "")
    try:
        return {"start": start, "end": end, "occupancy": services.get_occupancy_for_window(start, end)}
    except Exception as e:
        return {"start": start, "end": end, "error": str(e)}


def tool_verify_ledger_integrity(args=None):
    try:
        return services.verify_ledger_integrity()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_get_property_stats(args=None):
    """Read-only quick counts/stats: guests, reservations by status, rooms, in-house etc."""
    try:
        from .db import connect
        c = connect()
        stats = {}
        stats["guests_total"] = c.execute("SELECT COUNT(*) AS n FROM guests").fetchone()["n"]
        stats["rooms_total"] = c.execute("SELECT COUNT(*) AS n FROM rooms").fetchone()["n"]
        rows = c.execute("SELECT status, COUNT(*) AS n FROM reservations GROUP BY status").fetchall()
        stats["reservations_by_status"] = {r["status"]: r["n"] for r in rows}
        stats["reservations_total"] = sum(stats["reservations_by_status"].values())
        stats["in_house"] = stats["reservations_by_status"].get("Checked In", 0)
        today = datetime.now().strftime(DATE_FMT)
        stats["arrivals_today"] = c.execute("SELECT COUNT(*) AS n FROM reservations WHERE check_in_date=? AND status IN ('Reserved','Checked In')", (today,)).fetchone()["n"]
        stats["departures_today"] = c.execute("SELECT COUNT(*) AS n FROM reservations WHERE check_out_date=? AND status IN ('Reserved','Checked In')", (today,)).fetchone()["n"]
        return {"stats": stats, "as_of": today}
    except Exception as e:
        return {"error": str(e)}


TOOLS = {
    "list_capabilities": {"fn": tool_list_capabilities, "description": "List what the assistant can answer using real backend data.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    "search_guests": {"fn": tool_search_guests, "description": "Search guests by name/phone/email fragment.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"], "additionalProperties": False}},
    "get_guest_profile": {"fn": tool_get_guest_profile, "description": "Get guest profile and recent reservations.", "parameters": {"type": "object", "properties": {"guest_id": {"type": "integer"}}, "required": ["guest_id"], "additionalProperties": False}},
    "get_guest_last_visit": {"fn": tool_get_guest_last_visit, "description": "Get guest's last visit (most recent Checked Out, else most recent non-deleted).", "parameters": {"type": "object", "properties": {"guest_id": {"type": "integer"}}, "required": ["guest_id"], "additionalProperties": False}},
    "get_guest_top_items_last_visit": {"fn": tool_get_guest_top_items_last_visit, "description": "What a guest ordered most on their last visit (non-voided POS items, ranked by quantity).", "parameters": {"type": "object", "properties": {"guest_id": {"type": "integer"}, "limit": {"type": "integer"}}, "required": ["guest_id"], "additionalProperties": False}},
    "get_reservation_summary": {"fn": tool_get_reservation_summary, "description": "Reservation details, balance, and active POS line count.", "parameters": {"type": "object", "properties": {"reservation_id": {"type": "integer"}}, "required": ["reservation_id"], "additionalProperties": False}},
    "get_guest_balance": {"fn": tool_get_guest_balance, "description": "Guest/reservation balance by reservation_id.", "parameters": {"type": "object", "properties": {"reservation_id": {"type": "integer"}}, "required": ["reservation_id"], "additionalProperties": False}},
    "get_report": {"fn": tool_get_report, "description": "Run report: pl, cash_flow, vat, payment_methods, occupancy, daily_ops.", "parameters": {"type": "object", "properties": {"kind": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}, "range": {"type": "string"}}, "required": ["kind"], "additionalProperties": False}},
    "get_decision_support": {"fn": tool_get_decision_support, "description": "Decision-support KPIs/signals + financial summary for a date range.", "parameters": {"type": "object", "properties": {"start": {"type": "string"}, "end": {"type": "string"}, "range": {"type": "string"}}, "additionalProperties": False}},
    "list_rate_plans": {"fn": tool_list_rate_plans, "description": "List rate plans and reservation usage counts.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    "get_occupancy_summary": {"fn": tool_get_occupancy_summary, "description": "Occupancy summary for a date range.", "parameters": {"type": "object", "properties": {"start": {"type": "string"}, "end": {"type": "string"}, "range": {"type": "string"}}, "additionalProperties": False}},
    "verify_ledger_integrity": {"fn": tool_verify_ledger_integrity, "description": "Read-only ledger/data integrity health check (orphans, basic sanity).", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    "get_property_stats": {"fn": tool_get_property_stats, "description": "Read-only property stats/counts (guests total, rooms, reservations by status, in-house, arrivals/departures today).", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
}


def execute_tool(name: str, args: dict | None = None) -> dict:
    spec = TOOLS.get(name)
    if not spec:
        return {"error": f"Unknown tool '{name}'", "available_tools": sorted(TOOLS.keys())}
    try:
        return spec["fn"](args or {})
    except Exception as e:
        return {"error": f"Tool '{name}' failed: {e}"}


def _format_data(title: str, data: Any) -> str:
    try:
        body = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    except Exception:
        body = str(data)
    if len(body) > 6000:
        body = body[:6000] + "\n... (truncated)"
    return f"{title}\n{body}"


# ── Feedback & exemplar memory (learn over time) ──────────────────────────────

def _ensure_nlu_dirs():
    try:
        INTENT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

def record_feedback(message: str, predicted_intent: str = "", corrected_intent: str = "",
                    rating: int = 0, confidence: float | None = None, slots: dict | None = None,
                    corrected_slots: dict | None = None, tools_used: list | None = None,
                    answer: str = "", user_note: str = "", source: str = "", status: str = "new") -> int | None:
    _ensure_nlu_dirs()
    payload = {
        "created_at": datetime.now().strftime(getattr(services, "DATETIME_FMT", "%Y-%m-%d %H:%M:%S")),
        "message": message or "",
        "predicted_intent": predicted_intent or "",
        "corrected_intent": corrected_intent or "",
        "confidence": confidence,
        "slots": slots or {},
        "corrected_slots": corrected_slots or {},
        "tools_used": tools_used or [],
        "answer_preview": (answer or "")[:500],
        "rating": int(rating or 0),
        "user_note": user_note or "",
        "status": status or "new",
        "source": source or "",
    }
    fid = None
    try:
        from .db import connect
        cur = connect().execute(
            """INSERT INTO assistant_feedback
               (message, predicted_intent, corrected_intent, confidence, slots_json, corrected_slots_json,
                tools_used_json, answer_preview, rating, user_note, status, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (payload["message"], payload["predicted_intent"], payload["corrected_intent"], payload["confidence"],
             json.dumps(payload["slots"], ensure_ascii=False, default=str),
             json.dumps(payload["corrected_slots"], ensure_ascii=False, default=str),
             json.dumps(payload["tools_used"], ensure_ascii=False, default=str),
             payload["answer_preview"], payload["rating"], payload["user_note"], payload["status"], payload["source"]),
        )
        connect().commit()
        fid = cur.lastrowid
    except Exception:
        fid = None
    try:
        with FEEDBACK_JSONL.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"id": fid, **payload}, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass
    # invalidate exemplar cache
    global _EXEMPLAR_CACHE
    _EXEMPLAR_CACHE = None
    return fid


def list_feedback(limit: int = 100, status: str | None = None):
    try:
        from .db import connect
        q = "SELECT * FROM assistant_feedback"
        args = []
        if status:
            q += " WHERE status=?"; args.append(status)
        q += " ORDER BY id DESC LIMIT ?"; args.append(int(limit or 100))
        rows = connect().execute(q, args).fetchall()
        out = []
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            for j in ("slots_json", "corrected_slots_json", "tools_used_json"):
                try:
                    d[j.replace("_json", "")] = json.loads(d.get(j) or "{}" if "slots" in j else "[]")
                except Exception:
                    d[j.replace("_json", "")] = {}
            out.append(d)
        return out
    except Exception:
        return []


def get_correction_exemplars(limit: int = 500):
    """Verified/corrected examples for instant learning: (text, intent, slots)."""
    out = []
    try:
        from .db import connect
        rows = connect().execute(
            """SELECT message, corrected_intent, corrected_slots_json, predicted_intent, slots_json
               FROM assistant_feedback
               WHERE (corrected_intent != '' OR rating = 1) AND message != ''
               ORDER BY id DESC LIMIT ?""", (int(limit or 500),)
        ).fetchall()
        for r in rows:
            intent = r["corrected_intent"] or r["predicted_intent"] or ""
            if not intent:
                continue
            try:
                slots = json.loads(r["corrected_slots_json"] or r["slots_json"] or "{}")
            except Exception:
                slots = {}
            out.append({"text": r["message"], "label": intent, "slots": slots})
    except Exception:
        pass
    # also include seed/gold if present? seed is for training; corrections are higher trust
    return out


_EXEMPLAR_CACHE = None

def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b); union = len(a | b)
    return inter / union if union else 0.0

def exemplar_match(text: str, threshold: float = 0.72):
    """Instant learning: find best matching corrected/verified example (text similarity fallback)."""
    global _EXEMPLAR_CACHE
    if _EXEMPLAR_CACHE is None:
        _EXEMPLAR_CACHE = get_correction_exemplars(limit=1000)
    if not _EXEMPLAR_CACHE:
        return None
    qn = _norm_text(text)
    qset = _token_set(qn)
    best = None
    best_score = 0.0
    for ex in _EXEMPLAR_CACHE:
        tn = _norm_text(ex.get("text") or "")
        if not tn:
            continue
        if tn == qn:
            return {"intent": ex["label"], "confidence": 0.99, "slots": ex.get("slots") or {}, "source": "exemplar_exact", "matched_text": ex.get("text")}
        score = _jaccard(qset, _token_set(tn))
        # boost substring containment
        if len(qn) >= 8 and (qn in tn or tn in qn):
            score = max(score, 0.9)
        if score > best_score:
            best_score = score
            best = ex
    if best and best_score >= threshold:
        return {"intent": best["label"], "confidence": min(0.95, 0.6 + best_score * 0.4), "slots": best.get("slots") or {}, "source": "exemplar_similar", "score": best_score, "matched_text": best.get("text")}
    return None


# ── NLU routers (tiered, optional) ───────────────────────────────────────────

_SKLEARN_CACHE = None
_SETFIT_CACHE = None

def _load_sklearn_router():
    global _SKLEARN_CACHE
    if _SKLEARN_CACHE is not None:
        return _SKLEARN_CACHE
    try:
        import joblib  # type: ignore
        path = Path(os.environ.get("ASSISTANT_SKLEARN_MODEL", str(DEFAULT_SKLEARN_MODEL_PATH)))
        if not path.exists():
            _SKLEARN_CACHE = False
            return None
        obj = joblib.load(path)
        _SKLEARN_CACHE = obj
        return obj
    except Exception:
        _SKLEARN_CACHE = False
        return None

def _load_setfit_router():
    global _SETFIT_CACHE
    if _SETFIT_CACHE is not None:
        return _SETFIT_CACHE
    try:
        from setfit import SetFitModel  # type: ignore
        path = Path(os.environ.get("ASSISTANT_SETFIT_MODEL_DIR", str(DEFAULT_SETFIT_MODEL_DIR)))
        if not path.exists():
            _SETFIT_CACHE = False
            return None
        model = SetFitModel.from_pretrained(str(path))
        _SETFIT_CACHE = model
        return model
    except Exception:
        _SETFIT_CACHE = False
        return None

def _predict_intent_ml(text: str):
    mode = (os.environ.get("ASSISTANT_NLU") or "auto").strip().lower()
    # Try SetFit first if auto/requested
    if mode in ("auto", "setfit"):
        model = _load_setfit_router()
        if model is not None:
            try:
                # SetFitModel can predict and sometimes predict_proba depending; handle gracefully
                labels = model.predict([text])
                label = labels[0] if isinstance(labels, (list, tuple)) else labels
                conf = 0.7
                try:
                    probs = model.predict_proba([text])
                    import numpy as np  # type: ignore
                    p = probs[0] if hasattr(probs, "__len__") else probs
                    if hasattr(p, "tolist"):
                        arr = list(p.tolist() if hasattr(p, "tolist") else p)
                        conf = float(max(arr)) if arr else conf
                except Exception:
                    pass
                return {"intent": str(label), "confidence": float(conf), "router": "setfit"}
            except Exception:
                pass
        if mode == "setfit":
            return None
    # sklearn
    if mode in ("auto", "sklearn"):
        obj = _load_sklearn_router()
        if obj is not None:
            try:
                vec = obj.get("vectorizer") or obj.get("vec")
                clf = obj.get("classifier") or obj.get("clf") or obj.get("model")
                if vec is not None and clf is not None:
                    X = vec.transform([text])
                    pred = clf.predict(X)[0]
                    conf = 0.65
                    try:
                        if hasattr(clf, "predict_proba"):
                            proba = clf.predict_proba(X)[0]
                            conf = float(max(proba))
                        elif hasattr(clf, "decision_function"):
                            import math
                            df = clf.decision_function(X)
                            vals = df[0] if getattr(df, "ndim", 1) > 1 else df
                            # softmax-ish
                            exps = [math.exp(float(v)) for v in (vals.tolist() if hasattr(vals, "tolist") else list(vals))]
                            s = sum(exps) or 1.0
                            conf = float(max(exps) / s)
                    except Exception:
                        pass
                    return {"intent": str(pred), "confidence": float(conf), "router": "sklearn"}
            except Exception:
                pass
    return None


def _has_any(tl: str, keys):
    return any(k in tl for k in keys)

def rule_classify(text: str, slots: dict | None = None) -> dict:
    """T0 deterministic intent classification (robust cues). Returns intent+confidence+slots."""
    slots = slots or extract_slots(text)
    tl = _norm_text(text)
    conf = 0.9
    rid = slots.get("reservation_id")
    gid = slots.get("guest_id")
    gq = slots.get("guest_query")

    # unsafe/write/out-of-scope (read-only assistant)
    if _has_any(tl, ["go ahead and delete", "delete reservation", "delete room", "and refund", "refund the guest", "check the guest in", "check in the guest", "change room", "change price", "reset the super password", "reset password for me", "email the guest", "send invoice", "sync bookings", "add a new room", "import 500", "override checkout", "hack into", "ignore the rules", "hidden admin"]):
        return {"intent": "unsupported_or_unsafe", "confidence": 0.96, "slots": slots, "router": "rules"}

    # capabilities
    if _has_any(tl, ["what can you do", "how can you help", "capabilities", "your abilities", "what are you wired into", "menu of queries", "what tools", "how do i phrase", "boundaries start and end", "assist day to day"]):
        return {"intent": "capabilities_help", "confidence": 0.98, "slots": slots, "router": "rules"}

    # property stats / counts
    if _has_any(tl, ["how many guests", "number of guests", "total guests", "guest count", "guests do i have", "guests do we have", "how many customers", "how many reservations", "number of reservations", "total reservations", "reservation count", "how many rooms", "number of rooms", "total rooms", "how many bookings", "arrivals today", "departures today", "in house", "in-house count", "how many checked in", "property stats", "overall stats", "quick stats"]):
        return {"intent": "property_stats", "confidence": 0.92, "slots": slots, "router": "rules"}

    # compound cues
    multi = sum(1 for m in ["p&l", "pnl", "profit and loss", "cash flow", "cashflow", "vat", "occupancy", "daily ops", "decision support", "rate plan", "health check", "integrity", "ledger"] if m in tl)
    guest_chain = (gid or gq or "guest #" in tl or "guest " in tl) and sum(1 for m in ["profile", "last visit", "last stay", "most recent", "ordered most", "top-ordered", "most ordered"] if m in tl) >= 2
    res_chain = rid and sum(1 for m in ["balance", "summary", "details", "folio"] if m in tl) >= 2
    if multi >= 2 or guest_chain or res_chain or _has_any(tl, ["p&l and cash flow", "and also tell me occupancy", "summary and also", "list rate plans and also"]):
        return {"intent": "compound_multi_intent", "confidence": 0.82, "slots": slots, "router": "rules"}

    # reservation-specific
    if rid and _has_any(tl, ["balance", "owe", "outstanding", "amount due", "settled", "paid versus due", "left to collect", "folio balance"]):
        if _has_any(tl, ["summary", "details", "overview", "folio snapshot", "rundown", "everything important", "who's it for", "dates, rate, plan"]):
            return {"intent": "compound_multi_intent", "confidence": 0.8, "slots": slots, "router": "rules"}
        return {"intent": "reservation_balance", "confidence": 0.95, "slots": slots, "router": "rules"}
    if rid and _has_any(tl, ["summary", "details", "overview", "folio", "status", "what's going on", "rundown", "look up booking", "reservation"]):
        return {"intent": "reservation_summary", "confidence": 0.93, "slots": slots, "router": "rules"}

    # guest intents (priority: top items > profile details > last visit)
    if gid or gq or _has_any(tl, ["guest", "customer", "visitor", "contact"]):
        if _has_any(tl, ["most ordered", "ordered most", "ordered the most", "ordering the most", "buy the most", "bought the most", "top-ordered", "biggest pos orders", "topped their list", "most frequently", "most of during that visit", "consume/purchase most", "what did they order", "what did he order", "what did she order", "what did customer", "what did guest", "items did they buy", "which items did", "what they ordered most", "order the most"]):
            return {"intent": "guest_top_items_last_visit", "confidence": 0.94, "slots": slots, "router": "rules"}
        if _has_any(tl, ["profile", "everything we know", "contact info", "past reservations", "recent stay history", "background for guest", "account/profile view", "history with us", "details and latest bookings", "full profile", "who are they", "what's on file", "what’s on file", "open the profile", "their details", "show guest"]):
            # but if explicitly asking when last stayed as primary, prefer last visit
            if _has_any(tl, ["when did", "last time", "most recent visit", "most recent stay", "latest visit", "latest stay", "last stay with us", "previous visit here", "when was their previous visit", "last came", "last checked out"]):
                return {"intent": "guest_last_visit", "confidence": 0.91, "slots": slots, "router": "rules"}
            if not _has_any(tl, ["ordered most", "most ordered", "buy the most"]):
                return {"intent": "guest_profile", "confidence": 0.9, "slots": slots, "router": "rules"}
        if _has_any(tl, ["last visit", "last stay", "previous visit", "previous trip", "last time", "most recent stay", "most recent visit", "latest stay", "latest visit", "last came", "most recent checked-out", "previous stay", "latest booking was that", "when was the last time"]):
            return {"intent": "guest_last_visit", "confidence": 0.92, "slots": slots, "router": "rules"}
    if _has_any(tl, ["find guest", "search guest", "look up a guest", "locate a guest", "find a guest", "trying to find a guest", "anyone in the system", "possible matches", "who matches", "who do we have on file", "show candidates", "resembling", "look up a customer", "locate him", "locate her", "search guests for", "locate a contact", "pull up any matching guest", "can you find the guest"]):
        return {"intent": "guest_search", "confidence": 0.88, "slots": slots, "router": "rules"}

    # reports / analytics
    if _has_any(tl, ["decision support", "kpi", "how are we doing", "manager's overview", "manager’s overview", "executive snapshot", "signals", "warning signs", "strengths and risks", "actionable insights", "briefing me"]):
        return {"intent": "decision_support", "confidence": 0.9, "slots": slots, "router": "rules"}
    if _has_any(tl, ["daily ops", "arrivals", "departures", "in-house", "in house", "check-ins and check-outs", "handover", "front-desk style ops", "stayovers", "operational view", "ops picture", "ops summary"]):
        return {"intent": "report_daily_ops", "confidence": 0.9, "slots": slots, "router": "rules"}
    if _has_any(tl, ["health check", "integrity", "ledger integrity", "data integrity", "reconcile ledger", "orphaned", "mismatched records", "sanity check", "audit the database", "consistency", "books/data consistency"]):
        return {"intent": "ledger_integrity", "confidence": 0.93, "slots": slots, "router": "rules"}
    if _has_any(tl, ["rate plan", "pricing plan", "plans/packages", "catalog of rate plans"]):
        return {"intent": "rate_plans", "confidence": 0.92, "slots": slots, "router": "rules"}
    # report kind by slot
    rk = slots.get("report_kind")
    if rk == "pl":
        return {"intent": "report_pl", "confidence": 0.9, "slots": slots, "router": "rules"}
    if rk == "cash_flow":
        return {"intent": "report_cash_flow", "confidence": 0.9, "slots": slots, "router": "rules"}
    if rk == "vat":
        return {"intent": "report_vat", "confidence": 0.9, "slots": slots, "router": "rules"}
    if rk == "payment_methods":
        return {"intent": "report_payment_methods", "confidence": 0.9, "slots": slots, "router": "rules"}
    if rk == "daily_ops":
        return {"intent": "report_daily_ops", "confidence": 0.88, "slots": slots, "router": "rules"}
    if rk == "occupancy":
        if _has_any(tl, ["report", "adr", "revpar", "performance", "room-nights", "metrics", "how full were we", "fill rate", "sold vs available", "revenue-per-room"]):
            return {"intent": "report_occupancy", "confidence": 0.88, "slots": slots, "router": "rules"}
        return {"intent": "occupancy_summary", "confidence": 0.84, "slots": slots, "router": "rules"}

    # ambiguous pronouns/missing entities
    if _has_any(tl, ["he ordered", "she ordered", "they ordered", "his last visit", "her last visit", "their last visit", "that guest", "they owe", "do they owe"]):
        return {"intent": "ambiguous_needs_clarification", "confidence": 0.85, "slots": slots, "router": "rules"}
    if tl in {"balance", "show me the balance", "what's the balance", "what is the balance"} or (_has_any(tl, ["balance", "owe"]) and not rid and not gid):
        if _has_any(tl, ["balance", "owe", "outstanding"]):
            return {"intent": "ambiguous_needs_clarification", "confidence": 0.8, "slots": slots, "router": "rules"}
    if (("report for" in tl or "give me the report" in tl) and not rk and not _has_any(tl, ["daily ops","arrivals","departures"])) or (tl.strip() in {"show me the report for last month", "give me the report for last month."}):
        return {"intent": "ambiguous_needs_clarification", "confidence": 0.82, "slots": slots, "router": "rules"}
    if re.search(r"\breport\b", tl) and not rk and _has_any(tl, ["last month", "this month", "today", "yesterday", "last week"]) and not _has_any(tl, ["daily ops","arrivals","departures","check-ins","checkouts"]):
        # generic "report" without kind
        if not _has_any(tl, ["p&l","pnl","profit","cash flow","cashflow","vat","tax","payment method","occupancy","adr","revpar"]):
            return {"intent": "ambiguous_needs_clarification", "confidence": 0.78, "slots": slots, "router": "rules"}
    if _has_any(tl, ["last time", "last visit", "when did they"]) and not gid and not gq:
        return {"intent": "ambiguous_needs_clarification", "confidence": 0.75, "slots": slots, "router": "rules"}

    # weak fallbacks
    if _has_any(tl, ["report", "revenue", "expenses", "profit"]):
        return {"intent": "report_pl", "confidence": 0.55, "slots": slots, "router": "rules"}
    if "guest" in tl and (gq or _has_any(tl, ["find", "search", "look up"])):
        return {"intent": "guest_search", "confidence": 0.55, "slots": slots, "router": "rules"}

    return {"intent": "capabilities_help", "confidence": 0.25, "slots": slots, "router": "rules", "low_confidence": True}


def classify_intent(text: str) -> dict:
    slots = extract_slots(text)
    # instant exemplar learning
    ex = exemplar_match(text)
    if ex:
        # merge slots: explicit extraction wins
        ex_slots = dict(ex.get("slots") or {})
        ex_slots.update({k: v for k, v in slots.items() if v not in (None, "", [])})
        return {"intent": ex.get("intent"), "confidence": ex.get("confidence", 0.9), "slots": ex_slots, "router": ex.get("source", "exemplar")}
    ml = _predict_intent_ml(text)
    if ml and ml.get("intent"):
        intent = ml["intent"]
        conf = float(ml.get("confidence", 0.6))
        # guard: if ML predicts something odd, fall back
        if intent not in INTENT_LABELS:
            intent = rule_classify(text, slots)["intent"]
        # boost/override with strong deterministic signals
        rl = rule_classify(text, slots)
        if rl.get("intent") == "unsupported_or_unsafe":
            return rl
        if slots.get("reservation_id") and rl.get("intent") in ("reservation_balance", "reservation_summary", "compound_multi_intent"):
            rl["router"] = rl.get("router", "rules") + "+ml"
            return rl
        if conf < 0.55 and rl.get("confidence", 0) >= conf:
            return rl
        slots2 = dict(slots)
        return {"intent": intent, "confidence": conf, "slots": slots2, "router": ml.get("router", "ml")}
    return rule_classify(text, slots)


# ── Intent execution / policy ─────────────────────────────────────────────────

def _guest_disambiguation(candidates, q=None):
    data = {"candidates": candidates, "query": q}
    lines = ["I found multiple matching guests (real backend search). Please specify the guest ID:"]
    for g in candidates[:8]:
        try:
            lines.append(f"- #{g['id']}: {g.get('name','')} (phone: {g.get('phone','') or '-'}, reservations: {g.get('reservation_count', 0)})")
        except Exception:
            lines.append(f"- {g}")
    return {"ok": True, "intent": "ambiguous_needs_clarification", "needs_clarification": True,
            "tools_used": ["search_guests"] if q else [], "data": data, "answer": "\n".join(lines)}


def _execute_single(intent: str, text: str, slots: dict):
    tools_used = []
    start, end = slots.get("date_start"), slots.get("date_end")
    if not start or not end:
        start, end = _parse_date_range(text)
    rid = slots.get("reservation_id")

    if intent == "capabilities_help":
        data = execute_tool("list_capabilities", {}); tools_used.append("list_capabilities")
        caps = data.get("capabilities", [])
        ans = "I can answer using real backend data (read-only):\n- " + "\n- ".join(caps) + \
              "\n\nExamples: 'P&L for last month', 'VAT 2026-01-01 to 2026-01-31', 'find guest Ahmed', 'what did guest #3 order most on their last visit?', 'reservation #12 balance'."
        return {"ok": True, "intent": intent, "tools_used": tools_used, "data": data, "answer": ans}

    if intent == "unsupported_or_unsafe":
        return {"ok": False, "intent": intent, "tools_used": [], "data": {"reason": "unsafe_or_out_of_scope"},
                "answer": "I can only help with read-only questions using real backend data (reports, guests, reservations, balances, rate plans, occupancy, integrity). I can’t perform actions or changes (create/delete/refund/check-in/override/reset) or guess unavailable data. If you tell me what you want to look up, I can fetch it."}

    if intent == "ambiguous_needs_clarification":
        # try to be specific
        if _has_any(_norm_text(text), ["balance", "owe", "outstanding"]) and not rid:
            return {"ok": True, "intent": intent, "needs_clarification": True, "tools_used": [], "data": {"missing": "reservation_id"},
                    "answer": "Which reservation should I check the balance for? Please provide the reservation ID (e.g. 'reservation #12 balance')."}
        if _has_any(_norm_text(text), ["ordered", "last visit", "last stay", "profile", "when did they", "he", "she", "they"]) and not (slots.get("guest_id") or slots.get("guest_query")):
            return {"ok": True, "intent": intent, "needs_clarification": True, "tools_used": [], "data": {"missing": "guest"},
                    "answer": "Which guest do you mean? Please provide a guest ID (e.g. guest #3) or a name/phone fragment so I can look them up."}
        if "report" in _norm_text(text) and not slots.get("report_kind"):
            return {"ok": True, "intent": intent, "needs_clarification": True, "tools_used": [], "data": {"missing": "report_kind"},
                    "answer": "Which report do you want for that period (P&L, cash flow, VAT, payment methods, occupancy, or daily ops)? Please specify the report type and date range."}
        return {"ok": True, "intent": intent, "needs_clarification": True, "tools_used": [], "data": {"missing": "context"},
                "answer": "I need a bit more detail to answer from real data (e.g. guest ID/name, reservation ID, which report, or the date range). What exactly should I look up?"}

    if intent in ("reservation_balance", "reservation_summary"):
        if not rid:
            return _execute_single("ambiguous_needs_clarification", text, slots)
        if intent == "reservation_balance":
            data = execute_tool("get_guest_balance", {"reservation_id": rid}); tools_used.append("get_guest_balance")
        else:
            data = execute_tool("get_reservation_summary", {"reservation_id": rid}); tools_used.append("get_reservation_summary")
        return {"ok": True, "intent": intent, "tools_used": tools_used, "data": data, "answer": _format_data(f"Reservation #{rid}", data)}

    if intent in ("guest_search", "guest_profile", "guest_last_visit", "guest_top_items_last_visit"):
        guests, explicit_id, q = resolve_guest(text, slots)
        if intent == "guest_search":
            query = q or slots.get("guest_query") or ""
            if not query and guests:
                data = {"query": query, "guests": guests}
            else:
                if not query:
                    # try to extract from text
                    query = re.sub(r"(?i)\b(find|search|look up|locate|guest|guests|customer|for|named?|called|who do we have on file that resembles|resembling|matches|matching|anyone in the system whose)\b", " ", text)
                    query = re.sub(r"\s+", " ", query).strip(" ?.,")
                data = execute_tool("search_guests", {"query": query, "limit": 10}); tools_used.append("search_guests")
            return {"ok": True, "intent": intent, "tools_used": tools_used, "data": data, "answer": _format_data(f"Guest search: {data.get('query','')}", data)}
        # intents needing resolved guest
        if explicit_id and not guests:
            return {"ok": False, "intent": intent, "tools_used": [], "data": {"error": f"Guest #{explicit_id} not found"}, "answer": f"I couldn't find guest #{explicit_id} (real backend lookup)."}
        if len(guests) == 1 or (explicit_id and len(guests) == 1):
            g = guests[0]
            gid = g["id"] if isinstance(g, dict) else explicit_id
            if intent == "guest_top_items_last_visit":
                data = execute_tool("get_guest_top_items_last_visit", {"guest_id": gid, "limit": 5}); tools_used.append("get_guest_top_items_last_visit")
                title = f"Most ordered items — last visit (guest #{gid} {g.get('name','') if isinstance(g, dict) else ''})"
            elif intent == "guest_last_visit":
                data = execute_tool("get_guest_last_visit", {"guest_id": gid}); tools_used.append("get_guest_last_visit")
                title = f"Last visit (guest #{gid})"
            else:
                data = execute_tool("get_guest_profile", {"guest_id": gid}); tools_used.append("get_guest_profile")
                title = f"Guest #{gid} profile"
            return {"ok": True, "intent": intent, "tools_used": tools_used, "data": data, "answer": _format_data(title, data)}
        if len(guests) > 1:
            return _guest_disambiguation(guests, q or slots.get("guest_query"))
        # none found
        if slots.get("guest_id") or slots.get("guest_query"):
            return {"ok": False, "intent": intent, "tools_used": [], "data": {"query": slots.get("guest_query"), "guest_id": slots.get("guest_id")},
                    "answer": "I couldn't find a matching guest in the real backend data. Please try a different name/phone fragment or a specific guest ID."}
        return _execute_single("ambiguous_needs_clarification", text, slots)

    if intent in ("report_pl", "report_cash_flow", "report_vat", "report_payment_methods", "report_occupancy", "report_daily_ops"):
        kind = intent.replace("report_", "")
        args = {"kind": kind, "start": start, "end": end}
        data = execute_tool("get_report", args); tools_used.append("get_report")
        label = end if kind == "daily_ops" else f"{start} to {end}"
        return {"ok": True, "intent": intent, "tools_used": tools_used, "data": data, "answer": _format_data(f"Report {kind} ({label})", data)}

    if intent == "decision_support":
        data = execute_tool("get_decision_support", {"start": start, "end": end}); tools_used.append("get_decision_support")
        return {"ok": True, "intent": intent, "tools_used": tools_used, "data": data, "answer": _format_data(f"Decision support ({start} to {end})", data)}
    if intent == "rate_plans":
        data = execute_tool("list_rate_plans", {}); tools_used.append("list_rate_plans")
        return {"ok": True, "intent": intent, "tools_used": tools_used, "data": data, "answer": _format_data("Rate plans", data)}
    if intent == "occupancy_summary":
        data = execute_tool("get_occupancy_summary", {"start": start, "end": end}); tools_used.append("get_occupancy_summary")
        return {"ok": True, "intent": intent, "tools_used": tools_used, "data": data, "answer": _format_data(f"Occupancy summary ({start} to {end})", data)}
    if intent == "ledger_integrity":
        data = execute_tool("verify_ledger_integrity", {}); tools_used.append("verify_ledger_integrity")
        status = "OK" if (data or {}).get("ok") else "ISSUES FOUND"
        return {"ok": True, "intent": intent, "tools_used": tools_used, "data": data, "answer": _format_data(f"Ledger/data integrity health check: {status}", data)}

    if intent == "property_stats":
        data = execute_tool("get_property_stats", {}); tools_used.append("get_property_stats")
        stats = (data or {}).get("stats") or {}
        lines = ["Property stats (real backend data):"]
        if stats:
            if "guests_total" in stats: lines.append(f"- Guests (total): {stats['guests_total']}")
            if "rooms_total" in stats: lines.append(f"- Rooms (total): {stats['rooms_total']}")
            if "reservations_total" in stats: lines.append(f"- Reservations (total): {stats['reservations_total']}")
            if "reservations_by_status" in stats: lines.append(f"- Reservations by status: {json.dumps(stats['reservations_by_status'], ensure_ascii=False)}")
            if "in_house" in stats: lines.append(f"- In-house (Checked In): {stats['in_house']}")
            if "arrivals_today" in stats: lines.append(f"- Arrivals today: {stats['arrivals_today']}")
            if "departures_today" in stats: lines.append(f"- Departures today: {stats['departures_today']}")
        else:
            lines.append(_format_data("Stats", data))
        return {"ok": True, "intent": intent, "tools_used": tools_used, "data": data, "answer": "\n".join(lines)}

    # fallback
    data = execute_tool("list_capabilities", {}); tools_used.append("list_capabilities")
    return {"ok": True, "intent": "capabilities_help", "tools_used": tools_used, "data": data,
            "answer": "I couldn't confidently map that request. " + _format_data("Capabilities", data)}


def _execute_compound(text: str, slots: dict):
    tl = _norm_text(text)
    subs = []
    # detect sub-intents
    if slots.get("reservation_id") and _has_any(tl, ["balance", "owe", "outstanding"]):
        subs.append(("reservation_balance", slots))
    if slots.get("reservation_id") and _has_any(tl, ["summary", "details", "folio", "overview"]):
        subs.append(("reservation_summary", slots))
    if _has_any(tl, ["decision support", "kpi", "how are we doing", "signals"]):
        subs.append(("decision_support", slots))
    for intent, rk in [("report_pl","pl"),("report_cash_flow","cash_flow"),("report_vat","vat"),("report_payment_methods","payment_methods"),("report_occupancy","occupancy"),("report_daily_ops","daily_ops")]:
        cues = {
            "pl": ["p&l","pnl","profit and loss","profit","net profit"],
            "cash_flow": ["cash flow","cashflow"],
            "vat": ["vat","value-added tax","tax report"],
            "payment_methods": ["payment method","payments by method","payment channels"],
            "occupancy": ["occupancy","adr","revpar","how full","how busy"],
            "daily_ops": ["daily ops","arrivals","departures","in-house","in house"],
        }[rk]
        if _has_any(tl, cues):
            subs.append((intent, slots))
    if _has_any(tl, ["rate plan", "pricing plan"]):
        subs.append(("rate_plans", slots))
    if _has_any(tl, ["health check", "integrity", "ledger integrity"]):
        subs.append(("ledger_integrity", slots))
    # guest chain
    guests, explicit_id, q = resolve_guest(text, slots)
    if slots.get("guest_id") or q or _has_any(tl, ["find guest", "search guest"]):
        if _has_any(tl, ["find guest", "search guest", "search for"]) and not explicit_id:
            subs.append(("guest_search", slots))
        if explicit_id or (len(guests) == 1):
            g_slots = dict(slots)
            if len(guests) == 1 and not explicit_id:
                g_slots["guest_id"] = guests[0]["id"]
            if _has_any(tl, ["profile", "everything we know", "history"]):
                subs.append(("guest_profile", g_slots))
            if _has_any(tl, ["last visit", "last stay", "most recent", "when did", "last time"]):
                subs.append(("guest_last_visit", g_slots))
            if _has_any(tl, ["ordered most", "most ordered", "top-ordered", "buy the most", "topped their list"]):
                subs.append(("guest_top_items_last_visit", g_slots))
        elif len(guests) > 1 and not explicit_id:
            return _guest_disambiguation(guests, q)

    # dedupe preserve order
    seen = set(); uniq = []
    for it, sl in subs:
        key = (it, sl.get("guest_id"), sl.get("reservation_id"), sl.get("report_kind"))
        if key in seen: continue
        seen.add(key); uniq.append((it, sl))
    if not uniq:
        return _execute_single("ambiguous_needs_clarification", text, slots)
    if len(uniq) == 1:
        return _execute_single(uniq[0][0], text, uniq[0][1])

    tools_used = []; parts = []; data_all = {}
    ok = True
    for it, sl in uniq[:5]:
        res = _execute_single(it, text, sl)
        tools_used.extend(res.get("tools_used") or [])
        parts.append(f"## {it}\n{res.get('answer','')}")
        data_all[it] = res.get("data")
        ok = ok and bool(res.get("ok", True))
    return {"ok": ok, "intent": "compound_multi_intent", "tools_used": list(dict.fromkeys(tools_used)),
            "data": data_all, "answer": "\n\n".join(parts)}


# ── Public routing / chat (compat + enhanced) ─────────────────────────────────

def deterministic_route(message: str) -> dict | None:
    """Compatibility wrapper: deterministic/rule-first route returning grounded result or None-like fallback handled by chat."""
    text = (message or "").strip()
    if not text:
        return None
    slots = extract_slots(text)
    cls = rule_classify(text, slots)
    intent = cls.get("intent", "capabilities_help")
    conf = cls.get("confidence", 0.0)
    # if very low confidence and no strong slot, let chat try ML/LLM fallback paths by returning a weak fallback marker
    if cls.get("low_confidence") and conf < 0.4 and not (slots.get("reservation_id") or slots.get("guest_id")):
        return None
    if intent == "compound_multi_intent":
        res = _execute_compound(text, slots)
    else:
        res = _execute_single(intent, text, slots)
    res.setdefault("source", "core_deterministic")
    res.setdefault("ok", True)
    res.setdefault("tools_used", [])
    res["intent"] = res.get("intent") or intent
    res["confidence"] = conf
    res["slots"] = slots
    res["router"] = cls.get("router", "rules")
    return res


# ── LLM config / transport (optional, compatible) ────────────────────────────

def _llm_config():
    try:
        provider = (services.get_setting("assistant_provider", "auto") or "auto").strip()
        response_mode = (services.get_setting("assistant_response_mode", "talking") or "talking").strip()
        base = (services.get_setting("assistant_llm_base_url", "") or "").strip().rstrip("/")
        model = (services.get_setting("assistant_llm_model", "") or "").strip()
        key = (services.get_setting("assistant_llm_api_key", "") or "").strip()
    except Exception:
        provider, response_mode, base, model, key = "auto", "talking", "", "", ""
    enabled = provider != "deterministic_only" and bool(base and model)
    return {"provider": provider, "response_mode": response_mode, "base_url": base, "model": model, "api_key": key, "enabled": enabled}


def _openai_chat(messages, config, tools_schema=None, temperature=0.1, timeout=60):
    try:
        import requests
    except Exception as e:
        return None, f"requests not available: {e}"
    url = (config.get("base_url") or "") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if config.get("api_key"):
        headers["Authorization"] = f"Bearer {config['api_key']}"
    payload = {"model": config.get("model"), "messages": messages, "temperature": temperature}
    if tools_schema:
        payload["tools"] = tools_schema
        payload["tool_choice"] = "auto"
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if r.status_code >= 400:
            return None, f"LLM HTTP {r.status_code}: {r.text[:300]}"
        return r.json(), None
    except Exception as e:
        return None, str(e)


def talking_rephrase(message: str, core_result: dict, config: dict) -> str | None:
    if not config or not config.get("enabled"):
        return None
    core_answer = (core_result or {}).get("answer") or ""
    data = (core_result or {}).get("data")
    try:
        data_str = json.dumps(data, ensure_ascii=False, default=str)[:5000]
    except Exception:
        data_str = str(data)[:5000]
    system = (
        "You are a friendly property-manager assistant. You MUST answer in clear English. "
        "You are given GROUNDED BACKEND DATA and a core answer. Rephrase/summarize it nicely for the manager. "
        "Do NOT invent facts, numbers, names, dates, IDs, or items. If the data is unclear or has an error, say so plainly. "
        "Keep IDs/dates/amounts accurate when present."
    )
    user = f"Manager question: {message}\n\nCore grounded answer:\n{core_answer}\n\nGrounded data (JSON):\n{data_str}"
    resp, err = _openai_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        config, tools_schema=None, temperature=0.2, timeout=45,
    )
    if err or not resp:
        return None
    msg = ((resp.get("choices") or [{}])[0].get("message") or {})
    text = (msg.get("content") or "").strip()
    return text or None


def llm_route(message: str, history: list[dict] | None = None, config: dict | None = None) -> dict | None:
    config = config or _llm_config()
    if not config.get("enabled"):
        return None
    tools_schema = []
    for name, spec in TOOLS.items():
        tools_schema.append({"type": "function", "function": {"name": name, "description": spec["description"], "parameters": spec["parameters"]}})
    system = (
        "You are a property-manager assistant. Use ONLY the provided tools for facts/numbers/guests/reports. "
        "Never invent data. If a tool result is ambiguous (e.g. multiple guests), ask the user to clarify. "
        "Cite key IDs/dates from tool results. Answer in clear English. "
        "For 'last visit': most recent Checked Out reservation, else most recent non-deleted reservation. "
        "For 'most ordered last visit': use get_guest_top_items_last_visit (non-voided POS items ranked by quantity)."
    )
    msgs = [{"role": "system", "content": system}]
    for h in (history or [])[-8:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            msgs.append({"role": h["role"], "content": str(h["content"])[:4000]})
    msgs.append({"role": "user", "content": message})
    tools_used = []
    last_data = None
    for _ in range(5):
        resp, err = _openai_chat(msgs, config, tools_schema=tools_schema, temperature=0.1, timeout=60)
        if err:
            det = deterministic_route(message)
            return {"ok": bool(det), "source": "full_llm_error_fallback_core" if det else "full_llm_error",
                    "tools_used": (det or {}).get("tools_used") or tools_used, "data": (det or {}).get("data") or {"error": err},
                    "answer": ((det or {}).get("answer") or "") or f"Talking/LLM unavailable ({err}). Try a core query like reports, guest search, or reservation details.",
                    "llm_error": err}
        choice = (resp.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        if tool_calls:
            msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})
            for tc in tool_calls:
                fn = (tc.get("function") or {})
                name = fn.get("name")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                result = execute_tool(name, args)
                tools_used.append(name)
                last_data = result
                msgs.append({"role": "tool", "tool_call_id": tc.get("id"), "name": name, "content": json.dumps(result, ensure_ascii=False, default=str)[:8000]})
            continue
        answer = (msg.get("content") or "").strip() or _format_data("Result", last_data)
        return {"ok": True, "source": "full_llm_tools", "tools_used": tools_used, "data": last_data, "answer": answer}
    return {"ok": True, "source": "full_llm_tools", "tools_used": tools_used, "data": last_data, "answer": _format_data("Result (max tool rounds reached)", last_data)}


def chat(message: str, history: list[dict] | None = None) -> dict:
    message = (message or "").strip()
    if not message:
        return {"ok": False, "source": "none", "tools_used": [], "data": None, "answer": "Please enter a question."}
    try:
        services.touch_session()
    except Exception:
        pass
    config = _llm_config()
    mode = config.get("response_mode") or "talking"
    provider = config.get("provider") or "auto"

    if mode == "full_llm" and config.get("enabled"):
        res = llm_route(message, history=history, config=config)
        if res:
            res["response_mode"] = mode
            return res

    # enhanced route: classify (rules/exemplar/ml) + execute
    slots = extract_slots(message)
    cls = classify_intent(message)
    intent = cls.get("intent") or "capabilities_help"
    confidence = float(cls.get("confidence") or 0.0)
    slots = {**slots, **(cls.get("slots") or {})}
    # policy: low confidence -> clarify/fallback unless strong deterministic signal
    strong = bool(slots.get("reservation_id") or slots.get("guest_id") or cls.get("router") == "rules" and confidence >= 0.85)
    if intent == "compound_multi_intent":
        core = _execute_compound(message, slots)
    elif confidence < 0.45 and not strong and intent not in ("capabilities_help", "unsupported_or_unsafe"):
        if config.get("enabled") and provider in ("auto", "openai_compatible") and mode != "core":
            res = llm_route(message, history=history, config=config)
            if res:
                res["response_mode"] = mode
                res["intent"] = res.get("intent") or intent
                res["confidence"] = confidence
                return res
        core = _execute_single("ambiguous_needs_clarification", message, slots)
        core["intent"] = intent
    else:
        # if rules fell back to capabilities with very low confidence, don't spam capabilities as if matched
        if intent == "capabilities_help" and confidence < 0.5 and cls.get("low_confidence"):
            core = {"ok": True, "intent": "ambiguous_needs_clarification", "needs_clarification": True, "tools_used": [], "data": {"router_confidence": confidence},
                    "answer": "I’m not confident I understood that request from the available data/tools. "
                              "I can help with: reports (P&L, cash flow, VAT, payment methods, occupancy, daily ops), decision support, property stats/counts, guest search/profile/last visit/most-ordered last visit, reservation summary/balance, rate plans, occupancy summary, and ledger integrity. "
                              "Please rephrase with specifics (guest/reservation ID, report type, or date range)."}
        else:
            core = _execute_single(intent, message, slots)

    core.setdefault("source", "core_deterministic")
    core["intent"] = core.get("intent") or intent
    core["confidence"] = confidence
    core["slots"] = slots
    core["router"] = cls.get("router", "rules")

    # assisted retrieval fallback if core is weak capabilities fallback and LLM available
    if not core.get("ok") or (core.get("intent") == "capabilities_help" and confidence < 0.4 and cls.get("router") == "rules"):
        if config.get("enabled") and provider in ("auto", "openai_compatible") and mode != "core":
            res = llm_route(message, history=history, config=config)
            if res:
                res["response_mode"] = mode
                res["intent"] = res.get("intent") or intent
                res["confidence"] = confidence
                return res

    core["response_mode"] = mode
    core["core_answer"] = core.get("answer")
    core["core_source"] = core.get("source")

    # talking layer
    if mode == "talking" and core.get("ok") and config.get("enabled") and not core.get("needs_clarification"):
        polished = talking_rephrase(message, core, config)
        if polished:
            core["answer"] = polished
            core["source"] = f"talking+{core.get('core_source')}"
            core["talking_model"] = config.get("model")
        else:
            core["source"] = f"talking_unavailable+{core.get('core_source')}"
    elif mode == "core":
        core["source"] = core.get("core_source") or core.get("source")

    return core

