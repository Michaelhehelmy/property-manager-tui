"""Manager assistant: grounded read-only core, local-only (no external/OpenAI LLM).

- Facts come ONLY from real backend tools/services/DB (read-only). No hallucinations.
- No external LLM calls. "Talking" polish is done locally:
    1) optional local GGUF model via llama-cpp-python (if installed + model path configured)
    2) deterministic friendly formatter/rephraser (always available, no deps)
- Tiered NLU (no required deps): instant exemplar corrections > optional sklearn/setfit > weighted rules/slots.
- Learns over time: ratings/corrections stored locally (DB+JSONL) and applied instantly via exemplar memory.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import services

DATE_FMT = getattr(services, "DATE_FMT", "%Y-%m-%d")
BASE_DIR = Path(getattr(services, "BASE_DIR", Path(__file__).resolve().parents[2]))
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

# ── Utilities ────────────────────────────────────────────────────────────────

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
    except Exception:
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
    try: return f"{float(x):,.2f}"
    except Exception: return str(x)

def _parse_iso(s):
    try: return datetime.strptime(s, DATE_FMT).date()
    except Exception: return None

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
    except Exception:
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

# ── Read-only tools (grounded) ───────────────────────────────────────────────

def tool_list_capabilities(args=None):
    return {"capabilities": [
        "Business partner briefing & decision support (KPIs, risks, opportunities, suggested actions)",
        "Property/ops stats and today snapshot (guests, rooms, reservations, arrivals/departures, housekeeping)",
        "Reports: P&L, cash flow, VAT, payment methods, occupancy, daily ops; compare periods",
        "Guests: search, profile, full 360° portfolio (all trips/stays, POS activity, payments, invoices, totals/stats), last visit, most-ordered items and spend on last visit, booked activities/trips",
        "Activities/Trips (excursions/experiences): registered catalog, booking/order stats, and guest/reservation bookings (booked like POS to folio/invoice)",
        "Reservations: summary/details, balance, outstanding balances/AR, pipeline",
        "Inventory: alerts (low/over/negative), top-selling items, expiring/expired batches and batch/lot lists, barcode/SKU/lot traceability (resolved item or lot with received/expiry/remaining and recent movements); menu/meals",
        "Integrity, doctor & readiness: read-only ledger/data integrity checks and doctor/health diagnostics (schema, SQLite/FK integrity, batch-stock consistency, printer/update readiness, backup/restore readiness guidance)",
        "App version & updates: local app version/commit state and safe update guidance (in-app git-based fast-forward check/apply with changelog/details; runtime data DB/exports/invoices preserved, restart after apply)",
        "Expenses and sales breakdown; rate plans; room availability",
    ], "notes": [
        "All answers grounded in real backend data (read-only).",
        "Doctor/health diagnostics are read-only and never delete or modify runtime data.",
        "Last visit = most recent Checked Out, else most recent non-deleted.",
        "Booked activities/trips are posted as POS charges to the reservation folio/invoice.",
        "Inventory traceability is batch/lot-aware when enabled: you can review expiring batches, list lots, and trace barcodes/SKUs/lot codes to see received, expiry, remaining quantity, and movements.",
    ]}

def tool_search_guests(args):
    q = (args or {}).get("query") or (args or {}).get("q") or ""
    return {"query": q, "guests": services.assistant_find_guests(q, limit=(args or {}).get("limit") or 10)}

def tool_get_guest_profile(args):
    gid = _safe_int((args or {}).get("guest_id") or (args or {}).get("id"))
    if not gid: return {"error": "guest_id is required"}
    guest = services.get_guest_by_id(gid)
    if not guest: return {"error": f"Guest #{gid} not found"}
    history = services.get_guest_history_by_id(gid)
    hist = [{k: r[k] for k in r.keys()} for r in history[:10]]
    return {"guest": guest, "recent_reservations": hist, "reservation_count_shown": len(hist)}

def tool_get_guest_last_visit(args):
    gid = _safe_int((args or {}).get("guest_id") or (args or {}).get("id"))
    if not gid: return {"error": "guest_id is required"}
    return services.assistant_get_guest_last_visit(gid)

def tool_get_guest_top_items_last_visit(args):
    gid = _safe_int((args or {}).get("guest_id") or (args or {}).get("id"))
    if not gid: return {"error": "guest_id is required"}
    return services.assistant_get_top_items_last_visit(gid, limit=(args or {}).get("limit") or 5)

def tool_get_guest_last_visit_spend(args):
    gid = _safe_int((args or {}).get("guest_id") or (args or {}).get("id"))
    if not gid: return {"error": "guest_id is required"}
    visit = services.assistant_get_guest_last_visit(gid)
    lv = visit.get("last_visit") or {}
    rid = lv.get("reservation_id") or lv.get("id")
    if not rid: return {**visit, "spend": None}
    try:
        bd = services.get_balance_breakdown(rid)
        items = services.assistant_get_top_items_for_reservation(rid, limit=10)
        return {**visit, "balance_breakdown": bd, "top_items": items}
    except Exception as e:
        return {**visit, "spend_error": str(e)}

def tool_get_guest_portfolio(args):
    gid = _safe_int((args or {}).get("guest_id") or (args or {}).get("id"))
    if not gid: return {"error": "guest_id is required"}
    try:
        p = services.get_guest_portfolio(gid)
        if not p: return {"error": f"Guest #{gid} not found"}
        return p
    except Exception as e:
        return {"error": str(e)}

def tool_list_activities(args=None):
    try:
        active_only = bool((args or {}).get("active_only", False))
        items = services.list_activities(active_only=active_only)
        return {"activities": items, "count": len(items)}
    except Exception as e:
        return {"error": str(e)}

def tool_get_activity_stats(args=None):
    try:
        aid = _safe_int((args or {}).get("activity_id"))
        stats = services.get_activity_stats(activity_id=aid)
        return {"activity_stats": stats}
    except Exception as e:
        return {"error": str(e)}

def tool_list_activity_bookings(args=None):
    try:
        aid = _safe_int((args or {}).get("activity_id"))
        rid = _safe_int((args or {}).get("reservation_id"))
        gid = _safe_int((args or {}).get("guest_id"))
        limit = int((args or {}).get("limit") or 100)
        bookings = services.list_activity_bookings(activity_id=aid, reservation_id=rid, guest_id=gid, limit=limit)
        return {"bookings": bookings, "count": len(bookings)}
    except Exception as e:
        return {"error": str(e)}

def tool_get_reservation_summary(args):
    rid = _safe_int((args or {}).get("reservation_id") or (args or {}).get("id"))
    if not rid: return {"error": "reservation_id is required"}
    return services.assistant_reservation_summary(rid) or {"error": f"Reservation #{rid} not found"}

def tool_get_guest_balance(args):
    rid = _safe_int((args or {}).get("reservation_id") or (args or {}).get("id"))
    if not rid: return {"error": "reservation_id is required"}
    try:
        return {"reservation_id": rid, "balance": services.calculate_guest_balance(rid), "breakdown": services.get_balance_breakdown(rid)}
    except Exception as e:
        return {"reservation_id": rid, "error": str(e)}

def tool_get_report(args):
    kind = ((args or {}).get("kind") or "pl").lower().strip()
    start, end = (args or {}).get("start"), (args or {}).get("end")
    if not start or not end: start, end = _parse_date_range((args or {}).get("range") or "")
    try:
        if kind in ("pl","pnl","profit","profit_and_loss"): return {"kind":"pl","start":start,"end":end,"report": services.report_pl_statement(start,end)}
        if kind in ("cash","cash_flow","cashflow"): return {"kind":"cash_flow","start":start,"end":end,"report": services.report_cash_flow(start,end)}
        if kind in ("vat","tax"): return {"kind":"vat","start":start,"end":end,"report": services.report_vat(start,end)}
        if kind in ("payment_methods","payments"): return {"kind":"payment_methods","start":start,"end":end,"report": services.report_payment_methods(start,end)}
        if kind in ("occupancy","occ"): return {"kind":"occupancy","start":start,"end":end,"report": services.report_occupancy(start,end)}
        if kind in ("daily_ops","daily","ops"):
            day = end or start; return {"kind":"daily_ops","date":day,"report": services.report_daily_ops(day)}
        return {"error": f"Unknown report kind '{kind}'"}
    except Exception as e:
        return {"kind": kind, "start": start, "end": end, "error": str(e)}

def tool_get_decision_support(args):
    start, end = (args or {}).get("start"), (args or {}).get("end")
    if not start or not end: start, end = _parse_date_range((args or {}).get("range") or "")
    try:
        return {"start": start, "end": end, "decision_support": services.dataset_decision_support_v1(start,end), "financial_summary": services.dataset_financial_summary(start,end)}
    except Exception as e:
        return {"start": start, "end": end, "error": str(e)}

def tool_list_rate_plans(args=None):
    try:
        plans = []
        for p in services.get_all_rate_plans():
            d = {k: p[k] for k in p.keys()}
            try: d["reservations_using_plan"] = services.count_reservations_using_plan(p["id"])
            except Exception: d["reservations_using_plan"] = None
            plans.append(d)
        return {"rate_plans": plans}
    except Exception as e:
        return {"error": str(e)}

def tool_get_occupancy_summary(args):
    start, end = (args or {}).get("start"), (args or {}).get("end")
    if not start or not end: start, end = _parse_date_range((args or {}).get("range") or "")
    try:
        return {"start": start, "end": end, "occupancy": services.get_occupancy_for_window(start, end)}
    except Exception as e:
        return {"start": start, "end": end, "error": str(e)}

def tool_verify_ledger_integrity(args=None):
    try: return services.verify_ledger_integrity()
    except Exception as e: return {"ok": False, "error": str(e)}

def tool_get_property_stats(args=None):
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
        today = _iso(_today())
        stats["arrivals_today"] = c.execute("SELECT COUNT(*) AS n FROM reservations WHERE check_in_date=? AND status IN ('Reserved','Checked In')", (today,)).fetchone()["n"]
        stats["departures_today"] = c.execute("SELECT COUNT(*) AS n FROM reservations WHERE check_out_date=? AND status IN ('Reserved','Checked In')", (today,)).fetchone()["n"]
        return {"stats": stats, "as_of": today}
    except Exception as e:
        return {"error": str(e)}

def tool_get_today_ops(args=None):
    try:
        today = _iso(_today())
        ops = services.report_daily_ops(today)
        fd = None
        try: fd = services.get_front_desk_summary()
        except Exception: fd = None
        hk = tool_get_housekeeping_summary({}).get("summary")
        return {"date": today, "daily_ops": ops, "front_desk": fd, "housekeeping": hk}
    except Exception as e:
        return {"error": str(e)}

def tool_get_housekeeping_summary(args=None):
    try:
        from .db import connect
        rows = connect().execute("SELECT housekeeping_status, COUNT(*) AS n FROM rooms GROUP BY housekeeping_status").fetchall()
        by = {r["housekeeping_status"]: r["n"] for r in rows}
        total = sum(by.values())
        return {"summary": {"by_status": by, "total_rooms": total}}
    except Exception as e:
        return {"error": str(e)}

def tool_get_inventory_alerts(args=None):
    try:
        w = services.get_inventory_warnings()
        def pack(lst, keys):
            out = []
            for t in lst:
                out.append({k: t[i] for i, k in enumerate(keys)})
            return out
        return {"alerts": {
            "negative": pack(w.get("negative", []), ["id","item_name","stock_qty"]),
            "low": pack(w.get("low", []), ["id","item_name","stock_qty","threshold"]),
            "overstock": pack(w.get("over", []), ["id","item_name","stock_qty","threshold"]),
        }}
    except Exception as e:
        return {"error": str(e)}

def tool_get_outstanding_balances(args=None):
    try:
        from .db import connect
        c = connect()
        rows = c.execute("""SELECT r.id, r.guest_name, r.status, r.check_in_date, r.check_out_date, r.agreed_nightly_rate, rm.room_number
                            FROM reservations r LEFT JOIN rooms rm ON r.room_id=rm.id
                            WHERE r.status IN ('Reserved','Checked In','Checked Out') ORDER BY r.check_out_date DESC""").fetchall()
        outstanding = []
        total_due = 0.0
        for r in rows:
            try: bal = services.calculate_guest_balance(r["id"])
            except Exception: bal = 0.0
            if bal and bal > 0.009:
                outstanding.append({"reservation_id": r["id"], "guest_name": r["guest_name"], "room": r["room_number"],
                                    "status": r["status"], "check_in": r["check_in_date"], "check_out": r["check_out_date"],
                                    "balance_due": round(bal, 2)})
                total_due += bal
        outstanding.sort(key=lambda x: x["balance_due"], reverse=True)
        return {"outstanding": outstanding[:50], "count": len(outstanding), "total_due": round(total_due, 2)}
    except Exception as e:
        return {"error": str(e)}

def tool_get_top_selling_items(args=None):
    start, end = (args or {}).get("start"), (args or {}).get("end")
    if not start or not end: start, end = _parse_date_range((args or {}).get("range") or "")
    limit = int((args or {}).get("limit") or 10)
    try:
        from .db import connect
        rows = connect().execute("""
            SELECT item_description, SUM(quantity) AS qty_sold,
                   SUM((unit_price * quantity) + COALESCE(tax_amount,0)) AS gross, COUNT(*) AS lines
            FROM pos_tabs
            WHERE voided_at IS NULL AND date(created_at) >= ? AND date(created_at) <= ?
            GROUP BY item_description ORDER BY qty_sold DESC, gross DESC LIMIT ?""", (start, end, limit)).fetchall()
        items = [{"item": r["item_description"], "qty_sold": r["qty_sold"], "gross": round(r["gross"] or 0, 2), "lines": r["lines"]} for r in rows]
        return {"start": start, "end": end, "top_items": items}
    except Exception as e:
        return {"start": start, "end": end, "error": str(e)}

def tool_get_expenses_summary(args=None):
    start, end = (args or {}).get("start"), (args or {}).get("end")
    if not start or not end: start, end = _parse_date_range((args or {}).get("range") or "")
    try:
        from .db import connect
        rows = connect().execute("""
            SELECT c.category_name, COUNT(*) AS n, SUM(e.net_amount) AS net, SUM(e.vat_amount) AS vat,
                   SUM(e.net_amount + e.vat_amount) AS total
            FROM operational_expenses e JOIN expense_categories c ON e.category_id=c.id
            WHERE e.voided_at IS NULL AND date(e.at) >= ? AND date(e.at) <= ?
            GROUP BY c.category_name ORDER BY total DESC""", (start, end)).fetchall()
        cats = [{"category": r["category_name"], "count": r["n"], "net": round(r["net"] or 0,2), "vat": round(r["vat"] or 0,2), "total": round(r["total"] or 0,2)} for r in rows]
        total = round(sum(x["total"] for x in cats), 2)
        return {"start": start, "end": end, "by_category": cats, "total": total}
    except Exception as e:
        return {"start": start, "end": end, "error": str(e)}

def tool_get_sales_breakdown(args=None):
    start, end = (args or {}).get("start"), (args or {}).get("end")
    if not start or not end: start, end = _parse_date_range((args or {}).get("range") or "")
    try:
        from .db import connect
        c = connect()
        by_item = c.execute("""
            SELECT item_description, SUM(quantity) AS qty, SUM((unit_price*quantity)+COALESCE(tax_amount,0)) AS gross
            FROM pos_tabs WHERE voided_at IS NULL AND date(created_at)>=? AND date(created_at)<=?
            GROUP BY item_description ORDER BY gross DESC LIMIT 20""", (start,end)).fetchall()
        pay = c.execute("""
            SELECT method, COUNT(*) AS n, SUM(amount) AS total FROM payments
            WHERE voided_at IS NULL AND date(at)>=? AND date(at)<=?
            GROUP BY method ORDER BY total DESC""", (start,end)).fetchall()
        src = c.execute("""
            SELECT source, COUNT(*) AS n FROM reservations
            WHERE status NOT IN ('Deleted') AND date(created_at)>=? AND date(created_at)<=?
            GROUP BY source ORDER BY n DESC""", (start,end)).fetchall()
        return {"start": start, "end": end,
                "pos_by_item": [{"item":r["item_description"],"qty":r["qty"],"gross":round(r["gross"] or 0,2)} for r in by_item],
                "payments_by_method": [{"method":r["method"],"count":r["n"],"total":round(r["total"] or 0,2)} for r in pay],
                "reservations_by_source": [{"source":r["source"],"count":r["n"]} for r in src]}
    except Exception as e:
        return {"start": start, "end": end, "error": str(e)}

def tool_get_reservation_pipeline(args=None):
    try:
        from .db import connect
        c = connect()
        rows = c.execute("SELECT status, COUNT(*) AS n FROM reservations GROUP BY status").fetchall()
        by_status = {r["status"]: r["n"] for r in rows}
        today = _iso(_today()); soon = _iso(_today()+timedelta(days=7))
        upcoming = c.execute("""SELECT r.id, r.guest_name, rm.room_number, r.check_in_date, r.check_out_date, r.status
                                FROM reservations r LEFT JOIN rooms rm ON r.room_id=rm.id
                                WHERE r.status='Reserved' AND r.check_in_date>=? AND r.check_in_date<=?
                                ORDER BY r.check_in_date LIMIT 20""", (today, soon)).fetchall()
        return {"by_status": by_status, "upcoming_arrivals_7d": [dict(r) for r in upcoming]}
    except Exception as e:
        return {"error": str(e)}

def tool_get_room_availability(args=None):
    start = (args or {}).get("start") or (args or {}).get("check_in") or _iso(_today())
    end = (args or {}).get("end") or (args or {}).get("check_out") or _iso(_today()+timedelta(days=1))
    try:
        rows = services.get_rooms_available_between(start, end)
        return {"start": start, "end": end, "available_count": len(rows), "rooms": [dict(r) for r in rows[:50]]}
    except Exception as e:
        return {"start": start, "end": end, "error": str(e)}

def tool_get_menu_info(args=None):
    try:
        meals = services.list_meals(active_only=False) if hasattr(services, 'list_meals') else []
        out = []
        for m in (meals or [])[:80]:
            try: d = dict(m) if not isinstance(m, dict) else m
            except Exception: d = {"id": getattr(m, 'id', None), "name": m.get("name") if hasattr(m,'get') else str(m)}
            out.append(d)
        return {"meals": out, "count": len(out)}
    except Exception as e:
        return {"error": str(e)}

def tool_get_recent_activity(args=None):
    try:
        limit = int((args or {}).get("limit") or 20)
        audit = services.get_audit_log(limit)
        rows = [dict(r) for r in audit] if audit else []
        return {"recent_audit": rows}
    except Exception as e:
        return {"error": str(e)}

def tool_compare_periods(args=None):
    start, end = (args or {}).get("start"), (args or {}).get("end")
    if not start or not end: start, end = _parse_date_range((args or {}).get("range") or "this month")
    p1s, p1e = _prev_period(start, end)
    def snap(s,e):
        out = {"start": s, "end": e}
        try:
            pl = services.report_pl_statement(s,e)
            out["pl"] = pl if isinstance(pl, dict) else {"text": str(pl)[:1000]}
        except Exception as ex: out["pl_error"] = str(ex)
        try:
            occ = services.get_occupancy_for_window(s,e); out["occupancy"] = occ
        except Exception as ex: out["occ_error"] = str(ex)
        try:
            cf = services.report_cash_flow(s,e); out["cash_flow"] = cf if isinstance(cf, dict) else {"text": str(cf)[:800]}
        except Exception as ex: out["cash_error"] = str(ex)
        return out
    return {"current": snap(start,end), "previous": snap(p1s,p1e)}

def tool_business_partner_briefing(args=None):
    start, end = (args or {}).get("start"), (args or {}).get("end")
    if not start or not end: start, end = _parse_date_range((args or {}).get("range") or "this month")
    data = {"start": start, "end": end, "sections": {}}
    insights = {"snapshot": [], "good": [], "watchouts": [], "actions": []}
    try:
        ps = tool_get_property_stats({}).get("stats") or {}
        data["sections"]["property_stats"] = ps
        insights["snapshot"].append(f"Guests {ps.get('guests_total',0)}, rooms {ps.get('rooms_total',0)}, reservations {ps.get('reservations_total',0)}, in-house {ps.get('in_house',0)}.")
    except Exception: pass
    try:
        fd = services.get_front_desk_summary(); data["sections"]["front_desk"] = fd
        insights["snapshot"].append(f"Today: arrivals {fd.get('arrivals_today',0)}, departures {fd.get('departures_today',0)}, occupancy {fd.get('occupancy_pct',0)}% (clean {fd.get('clean',0)}, dirty {fd.get('dirty',0)}, maint {fd.get('maint',0)}).")
        if fd.get('dirty',0) > 0: insights["watchouts"].append(f"{fd['dirty']} room(s) marked Dirty — prioritize housekeeping for readiness.")
        if fd.get('maint',0) > 0: insights["watchouts"].append(f"{fd['maint']} room(s) in Maintenance — impacts sellable inventory.")
    except Exception: pass
    try:
        pl = services.report_pl_statement(start,end); data["sections"]["pl"] = pl
        if isinstance(pl, dict):
            rev = float(pl.get("revenue", pl.get("total_revenue", pl.get("net_revenue", 0))) or 0)
            exp = float(pl.get("expenses", pl.get("total_expenses", 0)) or 0)
            net = float(pl.get("net_profit", pl.get("net_income", rev-exp)) or (rev-exp))
            insights["snapshot"].append(f"Period {start}→{end}: revenue {_fmt_money(rev)}, expenses {_fmt_money(exp)}, net {_fmt_money(net)}.")
            if net < 0: insights["watchouts"].append("Net result is negative for the period — review costs and pricing/mix.")
            elif rev > 0 and net/rev >= 0.2: insights["good"].append("Healthy net margin in the period.")
            if exp > rev and rev > 0: insights["watchouts"].append("Expenses exceed revenue in the period.")
        elif isinstance(pl, str):
            insights["snapshot"].append(f"P&L ({start}→{end}) available (text report).")
    except Exception: pass
    try:
        occ = services.get_occupancy_for_window(start,end); data["sections"]["occupancy"] = occ
        pct = float(occ.get("pct", occ.get("occupancy_pct", 0)) or 0)
        insights["snapshot"].append(f"Occupancy {pct}% (range), ADR {_fmt_money(occ.get('adr',0))}, RevPAR {_fmt_money(occ.get('revpar',0))}.")
        if pct >= 80: insights["good"].append("Strong occupancy — consider upsell, protect rate, and optimize channel mix.")
        elif pct <= 35: insights["watchouts"].append("Low occupancy — consider promotions, packages, or distribution/channel actions.")
    except Exception: pass
    try:
        ar = tool_get_outstanding_balances({}); data["sections"]["outstanding"] = ar
        if ar.get("count",0) > 0:
            insights["watchouts"].append(f"Outstanding balances: {ar['count']} reservation(s), total due {_fmt_money(ar.get('total_due',0))} — prioritize collections/follow-up.")
        else:
            insights["good"].append("No outstanding guest balances detected on active/checked-out reservations.")
    except Exception: pass
    try:
        inv = tool_get_inventory_alerts({})["alerts"]; data["sections"]["inventory_alerts"] = inv
        lowc = len(inv.get("low",[]))+len(inv.get("negative",[])); overc = len(inv.get("overstock",[]))
        if lowc: insights["watchouts"].append(f"Inventory: {lowc} low/negative item(s) — restock to avoid stockouts/service issues.")
        if overc: insights["watchouts"].append(f"Inventory: {overc} overstock item(s) — consider slowing purchases/promoting usage.")
        if not lowc and not overc: insights["good"].append("Inventory levels within thresholds (no low/negative/overstock alerts).")
    except Exception: pass
    try:
        top = tool_get_top_selling_items({"start":start,"end":end,"limit":5}); data["sections"]["top_selling_items"] = top
        if top.get("top_items"):
            names = ", ".join([f"{i['item']} (x{i['qty_sold']})" for i in top["top_items"][:3]])
            insights["good"].append(f"Top sellers ({start}→{end}): {names} — keep stocked/feature.")
    except Exception: pass
    try:
        integ = services.verify_ledger_integrity(); data["sections"]["integrity"] = integ
        if integ.get("ok"): insights["good"].append("Ledger/data integrity check: OK.")
        else: insights["watchouts"].append("Ledger/data integrity reported issues — review health check before relying on reports.")
    except Exception: pass
    for w in insights["watchouts"][:5]:
        if "Outstanding balances" in w: insights["actions"].append("Follow up on unpaid balances (review reservation balances and collect).")
        elif "low/negative" in w: insights["actions"].append("Restock low/negative inventory items and verify recipes/ingredient usage.")
        elif "Dirty" in w or "Maintenance" in w: insights["actions"].append("Coordinate housekeeping/maintenance to maximize ready rooms.")
        elif "negative for the period" in w or "Expenses exceed revenue" in w: insights["actions"].append("Review expense drivers and revenue/pricing mix for the period.")
        elif "Low occupancy" in w: insights["actions"].append("Consider targeted offers or channel adjustments to lift occupancy.")
        elif "integrity reported issues" in w: insights["actions"].append("Run/open Health Check and resolve flagged inconsistencies.")
    seen=set(); insights["actions"]=[a for a in insights["actions"] if not (a in seen or seen.add(a))]
    if not insights["actions"]: insights["actions"].append("Maintain current performance; monitor occupancy, collections, and stock daily.")
    data["insights"] = insights
    return data

def tool_get_app_update_status(args=None):
    try:
        from . import updater
        local = updater.get_local_state()
        notes = [
            "Use Settings → App updates → Check for updates (web/TUI) to see available updates, changelog, and apply safely while preserving data (DB/exports/invoices).",
            "For git-based installs, apply uses fast-forward pull only; restart the app after a successful update.",
            "Create/verify a backup before applying updates in production-like environments; runtime data is preserved.",
        ]
        return {"app_version": local.get("app_version"), "local": local, "how_to_check": notes[0], "notes": notes}
    except Exception as e:
        return {"error": str(e)}


def tool_get_health_diagnostics(args=None):
    """Read-only doctor/health diagnostics (paths, schema, integrity, batch-stock, printer, backup/restore, update)."""
    try:
        from . import doctor
        report = doctor.collect_health(check_updates=False)
        return {
            "ok": report.get("ok"),
            "checked_at": report.get("checked_at"),
            "paths": report.get("paths"),
            "schema": report.get("schema"),
            "db": report.get("db"),
            "integrity": report.get("integrity"),
            "consistency": report.get("consistency"),
            "readiness": report.get("readiness"),
            "backup_restore": report.get("backup_restore"),
            "update": (report.get("update") or {}).get("local") or report.get("update"),
            "config_issues": report.get("config_issues"),
            "notes": report.get("notes"),
        }
    except Exception as e:
        return {"error": str(e)}


def tool_get_expiring_batches(args=None):
    try:
        args = args or {}
        days = args.get("within_days") or args.get("days")
        try:
            days = int(days) if days not in (None, "") else None
        except Exception:
            days = None
        include_expired = bool(args.get("include_expired", True))
        rows = services.get_expiring_batches(days, include_expired=include_expired)
        out = [dict(r) for r in (rows or [])]
        return {"batches": out, "count": len(out), "within_days": days, "include_expired": include_expired}
    except Exception as e:
        return {"error": str(e)}

def tool_list_inventory_batches(args=None):
    try:
        args = args or {}
        item_id = args.get("item_id")
        try:
            item_id = int(item_id) if item_id not in (None, "") else None
        except Exception:
            item_id = None
        status = args.get("status") or None
        expiring_within_days = args.get("expiring_within_days") or args.get("within_days")
        try:
            expiring_within_days = int(expiring_within_days) if expiring_within_days not in (None, "") else None
        except Exception:
            expiring_within_days = None
        include_depleted = bool(args.get("include_depleted", False))
        rows = services.list_inventory_batches(item_id=item_id, status=status, expiring_within_days=expiring_within_days, include_depleted=include_depleted)
        out = [dict(r) for r in (rows or [])]
        return {"batches": out, "count": len(out), "item_id": item_id}
    except Exception as e:
        return {"error": str(e)}

def tool_trace_barcode(args=None):
    try:
        code = ((args or {}).get("code") or (args or {}).get("barcode") or (args or {}).get("sku") or "").strip()
        if not code:
            return {"error": "Provide a barcode/SKU/lot code to trace."}
        res = services.resolve_barcode(code)
        # enrich batch with recent movements if batch found
        if res.get("type") == "batch" and res.get("batch"):
            try:
                bid = (res["batch"] or {}).get("id")
                if bid:
                    from .db import connect
                    logs = connect().execute(
                        "SELECT at, change_type, quantity, reference, unit_cost, balance_after FROM inventory_logs WHERE batch_id=? ORDER BY id DESC LIMIT 20",
                        (bid,)).fetchall()
                    res["batch_movements"] = [dict(r) for r in logs]
            except Exception:
                pass
        return res
    except Exception as e:
        return {"error": str(e)}


TOOLS = {
    "list_capabilities": {"fn": tool_list_capabilities, "description": "List assistant capabilities.", "parameters": {"type":"object","properties":{},"additionalProperties":False}},
    "get_property_stats": {"fn": tool_get_property_stats, "description": "Property counts/stats (guests, rooms, reservations by status, in-house, arrivals/departures today).", "parameters": {"type":"object","properties":{},"additionalProperties":False}},
    "get_today_ops": {"fn": tool_get_today_ops, "description": "Today's operations snapshot (arrivals/departures/in-house, front desk, housekeeping).", "parameters": {"type":"object","properties":{},"additionalProperties":False}},
    "business_partner_briefing": {"fn": tool_business_partner_briefing, "description": "Business-partner briefing: KPIs, risks, opportunities, suggested actions for a period.", "parameters": {"type":"object","properties":{"start":{"type":"string"},"end":{"type":"string"},"range":{"type":"string"}},"additionalProperties":False}},
    "get_decision_support": {"fn": tool_get_decision_support, "description": "Decision-support KPIs/signals + financial summary.", "parameters": {"type":"object","properties":{"start":{"type":"string"},"end":{"type":"string"},"range":{"type":"string"}},"additionalProperties":False}},
    "get_report": {"fn": tool_get_report, "description": "Run report: pl, cash_flow, vat, payment_methods, occupancy, daily_ops.", "parameters": {"type":"object","properties":{"kind":{"type":"string"},"start":{"type":"string"},"end":{"type":"string"},"range":{"type":"string"}},"required":["kind"],"additionalProperties":False}},
    "get_occupancy_summary": {"fn": tool_get_occupancy_summary, "description": "Occupancy summary for a date range.", "parameters": {"type":"object","properties":{"start":{"type":"string"},"end":{"type":"string"},"range":{"type":"string"}},"additionalProperties":False}},
    "compare_periods": {"fn": tool_compare_periods, "description": "Compare current period vs previous period (P&L, occupancy, cash flow).", "parameters": {"type":"object","properties":{"start":{"type":"string"},"end":{"type":"string"},"range":{"type":"string"}},"additionalProperties":False}},
    "get_outstanding_balances": {"fn": tool_get_outstanding_balances, "description": "Reservations with unpaid/outstanding balances (who owes money).", "parameters": {"type":"object","properties":{},"additionalProperties":False}},
    "get_inventory_alerts": {"fn": tool_get_inventory_alerts, "description": "Inventory alerts: low/negative/overstock items.", "parameters": {"type":"object","properties":{},"additionalProperties":False}},
    "get_top_selling_items": {"fn": tool_get_top_selling_items, "description": "Top selling POS items by quantity/revenue in a period.", "parameters": {"type":"object","properties":{"start":{"type":"string"},"end":{"type":"string"},"range":{"type":"string"},"limit":{"type":"integer"}},"additionalProperties":False}},
    "get_expenses_summary": {"fn": tool_get_expenses_summary, "description": "Operational expenses summary by category for a period.", "parameters": {"type":"object","properties":{"start":{"type":"string"},"end":{"type":"string"},"range":{"type":"string"}},"additionalProperties":False}},
    "get_sales_breakdown": {"fn": tool_get_sales_breakdown, "description": "Sales/POS and payment/reservation-source breakdown for a period.", "parameters": {"type":"object","properties":{"start":{"type":"string"},"end":{"type":"string"},"range":{"type":"string"}},"additionalProperties":False}},
    "get_reservation_pipeline": {"fn": tool_get_reservation_pipeline, "description": "Reservation counts by status and upcoming arrivals (7 days).", "parameters": {"type":"object","properties":{},"additionalProperties":False}},
    "get_housekeeping_summary": {"fn": tool_get_housekeeping_summary, "description": "Room housekeeping status summary.", "parameters": {"type":"object","properties":{},"additionalProperties":False}},
    "get_room_availability": {"fn": tool_get_room_availability, "description": "Available rooms for a date range.", "parameters": {"type":"object","properties":{"start":{"type":"string"},"end":{"type":"string"}},"additionalProperties":False}},
    "get_menu_info": {"fn": tool_get_menu_info, "description": "Meals/menu information.", "parameters": {"type":"object","properties":{},"additionalProperties":False}},
    "list_rate_plans": {"fn": tool_list_rate_plans, "description": "List rate plans and usage.", "parameters": {"type":"object","properties":{},"additionalProperties":False}},
    "verify_ledger_integrity": {"fn": tool_verify_ledger_integrity, "description": "Read-only ledger/data integrity health check.", "parameters": {"type":"object","properties":{},"additionalProperties":False}},
    "search_guests": {"fn": tool_search_guests, "description": "Search guests by name/phone/email fragment.", "parameters": {"type":"object","properties":{"query":{"type":"string"},"limit":{"type":"integer"}},"required":["query"],"additionalProperties":False}},
    "get_guest_profile": {"fn": tool_get_guest_profile, "description": "Guest profile and recent reservations.", "parameters": {"type":"object","properties":{"guest_id":{"type":"integer"}},"required":["guest_id"],"additionalProperties":False}},
    "get_guest_last_visit": {"fn": tool_get_guest_last_visit, "description": "Guest's last visit (most recent Checked Out, else most recent non-deleted).", "parameters": {"type":"object","properties":{"guest_id":{"type":"integer"}},"required":["guest_id"],"additionalProperties":False}},
    "get_guest_top_items_last_visit": {"fn": tool_get_guest_top_items_last_visit, "description": "Most ordered items on guest's last visit (non-voided POS).", "parameters": {"type":"object","properties":{"guest_id":{"type":"integer"},"limit":{"type":"integer"}},"required":["guest_id"],"additionalProperties":False}},
    "get_guest_last_visit_spend": {"fn": tool_get_guest_last_visit_spend, "description": "Guest's last visit with spend/balance and top items.", "parameters": {"type":"object","properties":{"guest_id":{"type":"integer"}},"required":["guest_id"],"additionalProperties":False}},
    "get_guest_portfolio": {"fn": tool_get_guest_portfolio, "description": "Full guest 360° portfolio: profile, lifetime stats, all trips/stays with folio/charges/paid/due, all POS/activity orders per trip and overall, all payments, invoices, top items.", "parameters": {"type":"object","properties":{"guest_id":{"type":"integer"}},"required":["guest_id"],"additionalProperties":False}},
    "list_activities": {"fn": tool_list_activities, "description": "List registered guest activities/trips/excursions catalog.", "parameters": {"type":"object","properties":{"active_only":{"type":"boolean"}},"additionalProperties":False}},
    "get_activity_stats": {"fn": tool_get_activity_stats, "description": "Per-activity/trip booking statistics (bookings, qty, revenue) grounded in real bookings.", "parameters": {"type":"object","properties":{"activity_id":{"type":"integer"}},"additionalProperties":False}},
    "list_activity_bookings": {"fn": tool_list_activity_bookings, "description": "List booked/ordered guest activities-trips (POS-linked bookings) with reservation/guest context; filter by guest_id, reservation_id, or activity_id.", "parameters": {"type":"object","properties":{"guest_id":{"type":"integer"},"reservation_id":{"type":"integer"},"activity_id":{"type":"integer"},"limit":{"type":"integer"}},"additionalProperties":False}},
    "get_reservation_summary": {"fn": tool_get_reservation_summary, "description": "Reservation details, balance, POS count.", "parameters": {"type":"object","properties":{"reservation_id":{"type":"integer"}},"required":["reservation_id"],"additionalProperties":False}},
    "get_guest_balance": {"fn": tool_get_guest_balance, "description": "Reservation/guest balance and breakdown.", "parameters": {"type":"object","properties":{"reservation_id":{"type":"integer"}},"required":["reservation_id"],"additionalProperties":False}},
    "get_recent_activity": {"fn": tool_get_recent_activity, "description": "Recent audit/activity log.", "parameters": {"type":"object","properties":{"limit":{"type":"integer"}},"additionalProperties":False}},
    "get_expiring_batches": {"fn": tool_get_expiring_batches, "description": "List expiring/expired inventory batches (traceable lots) within days.", "parameters": {"type":"object","properties":{"within_days":{"type":"integer"},"include_expired":{"type":"boolean"}},"additionalProperties":False}},
    "list_inventory_batches": {"fn": tool_list_inventory_batches, "description": "List inventory batches/lots with received/expiry/remaining/cost for an item or all.", "parameters": {"type":"object","properties":{"item_id":{"type":"integer"},"status":{"type":"string"},"expiring_within_days":{"type":"integer"},"include_depleted":{"type":"boolean"}},"additionalProperties":False}},
    "trace_barcode": {"fn": tool_trace_barcode, "description": "Trace a barcode/SKU/lot code to the inventory item or batch (lot, received, expiry, remaining, movements).", "parameters": {"type":"object","properties":{"code":{"type":"string"},"barcode":{"type":"string"},"sku":{"type":"string"}},"additionalProperties":False}},
    "get_app_update_status": {"fn": tool_get_app_update_status, "description": "Get local app version/commit/update state and how to check/apply updates (data preserved).", "parameters": {"type":"object","properties":{},"additionalProperties":False}},
    "get_health_diagnostics": {"fn": tool_get_health_diagnostics, "description": "Read-only doctor/health diagnostics: schema/integrity, batch-stock consistency, printer/update readiness, and backup/restore guidance.", "parameters": {"type":"object","properties":{},"additionalProperties":False}},
}

def execute_tool(name: str, args: dict | None = None) -> dict:
    spec = TOOLS.get(name)
    if not spec:
        return {"error": f"Unknown tool '{name}'", "available_tools": sorted(TOOLS.keys())}
    try:
        return spec["fn"](args or {})
    except Exception as e:
        return {"error": f"Tool '{name}' failed: {e}"}

# ── Friendly formatting & deterministic synthesis (local, no deps) ───────────

def _format_data(title: str, data: Any) -> str:
    """Fallback: compact structured view (kept for traceability)."""
    try:
        body = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    except Exception:
        body = str(data)
    if len(body) > 6000:
        body = body[:6000] + "\n... (truncated)"
    return f"{title}\n{body}"

def _friendly_open(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "Sure — here's what I found based on real app data."
    return text

def _fmt_capabilities(data: dict) -> str:
    caps = (data or {}).get("capabilities") or []
    notes = (data or {}).get("notes") or []
    lines = ["Sure — I’m your grounded manager assistant and business partner (read-only, based on real app data). I can look things up, summarize performance, and guide you with examples."]
    lines.append("")
    lines.append("**What I can help with**")
    for c in caps:
        lines.append(f"- {c}")
    if notes:
        lines.append("")
        for n in notes: lines.append(f"**Note:** {n}")
    lines.append("")
    lines.append("**How to use (examples you can ask)**")
    lines.append("- **Business & ops:** `business partner briefing for this month`, `how are we doing?`, `today's ops`, `property stats`, `housekeeping status`, `reservation pipeline`, `available rooms tomorrow`")
    lines.append("- **Money & reports:** `who owes money?`, `P&L last month`, `cash flow this month`, `VAT 2026-11-01 to 2026-11-30`, `payment methods this month`, `occupancy last month`, `compare this month vs last month`, `expenses breakdown last month`, `top selling items this month`, `sales breakdown this week`")
    lines.append("- **Inventory & catalog:** `any low stock items?`, `top selling items`, `what expires soon?`, `expiring batches in 7 days`, `list inventory batches`, `trace barcode LOT-...` / `trace SKU ...` (lot received/expiry/movements), `menu`, `rate plans`, `list registered activities`, `activity booking statistics`")
    lines.append("- **Guests (360°):** `find guest Ahmed`, `guest #3 profile`, `show guest #3 full portfolio` (all stays, POS/orders, payments, invoices, totals), `when was guest #3 last visit?`, `what did guest #3 order most on last visit?`, `guest #3 last visit spend`, `what activities did guest #3 book?`")
    lines.append("- **Reservations:** `reservation #12 summary`, `reservation #12 balance`")
    lines.append("- **Health, doctor & updates:** `ledger integrity check`, `doctor report` / `health check` / `backup restore readiness`, `batch stock mismatch`, `recent activity`, `check for updates` / `what app version`")
    lines.append("")
    lines.append("**Tips**")
    lines.append("- Be specific when you can: use IDs (`guest #3`, `reservation #12`), names (`find guest Sara`), report names, or date ranges (`2026-10-01 to 2026-10-31`, `last month`, `this week`).")
    lines.append("- If I’m unsure or find multiple matches, I’ll ask you to clarify — reply with the ID I suggest.")
    lines.append("- I only read real app data (no changes, no guesses). If something is missing for the period, I’ll tell you.")
    lines.append("- You can rate answers and correct my intent in the app so I learn your phrasing over time.")
    return "\n".join(lines)

def _fmt_stats(data: dict) -> str:
    stats = (data or {}).get("stats") or {}
    as_of = (data or {}).get("as_of") or _iso(_today())
    if not stats:
        return "I couldn’t retrieve property stats right now. Want me to try again or check today’s ops?"
    lines = [f"Sure — here’s your property snapshot (as of {as_of}, from real records):"]
    if "guests_total" in stats: lines.append(f"- Guests: **{stats['guests_total']}** total")
    if "rooms_total" in stats: lines.append(f"- Rooms: **{stats['rooms_total']}** total")
    if "reservations_total" in stats: lines.append(f"- Reservations: **{stats['reservations_total']}** total")
    by = stats.get("reservations_by_status") or {}
    if by:
        parts = [f"{k}: {v}" for k,v in by.items()]
        lines.append(f"- By status: {', '.join(parts)}")
    if "in_house" in stats: lines.append(f"- In-house now (Checked In): **{stats['in_house']}**")
    if "arrivals_today" in stats: lines.append(f"- Arrivals today: **{stats['arrivals_today']}**")
    if "departures_today" in stats: lines.append(f"- Departures today: **{stats['departures_today']}**")
    return "\n".join(lines)

def _fmt_today_ops(data: dict) -> str:
    if data.get("error"):
        return f"I couldn’t load today’s ops right now ({data.get('error')}). Want to try property stats instead?"
    date = data.get("date") or _iso(_today())
    fd = data.get("front_desk") or {}
    hk = data.get("housekeeping") or {}
    ops = data.get("daily_ops")
    lines = [f"Here’s today’s operations snapshot for **{date}** (grounded in real data):"]
    if fd:
        lines.append(f"- Front desk: arrivals **{fd.get('arrivals_today',0)}**, departures **{fd.get('departures_today',0)}**, in-house **{fd.get('in_house', fd.get('inhouse', 0))}**, occupancy **{fd.get('occupancy_pct',0)}%**")
        lines.append(f"- Rooms readiness: clean **{fd.get('clean',0)}**, dirty **{fd.get('dirty',0)}**, maintenance **{fd.get('maint',0)}**")
    if hk and hk.get("by_status"):
        parts = [f"{k}: {v}" for k,v in (hk.get("by_status") or {}).items()]
        lines.append(f"- Housekeeping: {', '.join(parts)} (total {hk.get('total_rooms',0)})")
    if isinstance(ops, dict):
        # summarize common keys
        for k in ("arrivals","departures","in_house","stayovers"):
            v = ops.get(k)
            if v is not None and k not in ("",):
                if isinstance(v, list): lines.append(f"- {k.replace('_',' ').title()}: **{len(v)}**")
                elif isinstance(v, (int,float,str)): lines.append(f"- {k.replace('_',' ').title()}: **{v}**")
    elif isinstance(ops, str) and ops.strip():
        # include concise excerpt
        excerpt = "\n".join([ln for ln in ops.strip().splitlines() if ln.strip()][:12])
        lines.append("")
        lines.append("Ops details:")
        lines.append(excerpt)
    if not fd and not hk and not ops:
        lines.append("- No ops details were returned for today.")
    return "\n".join(lines)

def _fmt_partner_briefing(data: dict) -> str:
    ins = (data or {}).get("insights") or {}
    start, end = data.get("start"), data.get("end")
    out = [f"Here’s your business partner briefing for **{start} → {end}** (grounded in real data):"]
    if ins.get("snapshot"):
        out.append("\n**Snapshot**"); out += [f"- {x}" for x in ins["snapshot"]]
    if ins.get("good"):
        out.append("\n**What’s going well**"); out += [f"- {x}" for x in ins["good"]]
    if ins.get("watchouts"):
        out.append("\n**Watch-outs**"); out += [f"- {x}" for x in ins["watchouts"]]
    else:
        out.append("\n**Watch-outs**"); out.append("- None flagged by the current checks — keep monitoring collections, stock, and readiness.")
    if ins.get("actions"):
        out.append("\n**Suggested actions (data-informed)**"); out += [f"- {x}" for x in ins["actions"]]
    return "\n".join(out)

def _fmt_report(data: dict) -> str:
    if data.get("error"):
        return f"I couldn’t generate that report ({data.get('error')}). Want to try a different report or date range?"
    kind = data.get("kind") or "report"
    report = data.get("report")
    label = data.get("date") or f"{data.get('start','')} → {data.get('end','')}"
    title = kind.replace("_"," ").upper()
    lines = [f"Here’s the **{title}** report for **{label}** (grounded in real records):"]
    if isinstance(report, dict):
        # show key metrics first
        keys_order = ["revenue","total_revenue","net_revenue","expenses","total_expenses","net_profit","net_income","net","cash_in","cash_out","net_cash","vat_collected","vat_paid","net_vat","occupancy_pct","adr","revpar","room_nights","rooms_sold","rooms_available","count","total"]
        shown = set()
        metrics = []
        for k in keys_order:
            if k in report and isinstance(report[k], (int,float,str)) and not isinstance(report[k], bool):
                metrics.append((k, report[k])); shown.add(k)
        if metrics:
            for k,v in metrics[:12]:
                name = k.replace('_',' ').title()
                if isinstance(v,(int,float)) and any(x in k for x in ["revenue","expense","profit","income","net","cash","vat","adr","revpar","total"]):
                    lines.append(f"- {name}: **{_fmt_money(v)}**")
                else:
                    lines.append(f"- {name}: **{v}**")
        # include other scalar/string fields
        for k,v in report.items():
            if k in shown: continue
            if isinstance(v, (int,float,str)) and not isinstance(v,bool) and len(lines) < 22:
                lines.append(f"- {k.replace('_',' ').title()}: **{_fmt_money(v) if isinstance(v,(int,float)) and 'pct' not in k and 'count' not in k else v}**")
            elif isinstance(v, list) and v and len(lines) < 22:
                lines.append(f"- {k.replace('_',' ').title()}: {len(v)} item(s)")
        if "text" in report and isinstance(report["text"], str):
            lines.append("\nDetails:\n" + "\n".join(report["text"].strip().splitlines()[:20]))
    elif isinstance(report, str):
        excerpt = "\n".join(report.strip().splitlines()[:25])
        lines.append(excerpt if excerpt else "- (empty report)")
    else:
        lines.append(_format_data("Report data", report))
    return "\n".join(lines)

def _fmt_occupancy(data: dict) -> str:
    if data.get("error"):
        return f"I couldn’t load occupancy ({data.get('error')})."
    occ = data.get("occupancy") or {}
    start, end = data.get("start"), data.get("end")
    if not occ:
        return f"No occupancy data found for **{start} → {end}**."
    lines = [f"Occupancy summary for **{start} → {end}** (real data):"]
    for k,label in [("pct","Occupancy"),("occupancy_pct","Occupancy"),("adr","ADR"),("revpar","RevPAR"),("room_nights","Room nights"),("rooms_sold","Rooms sold"),("rooms_available","Rooms available"),("revenue","Revenue"),("total_revenue","Revenue")]:
        if k in occ and occ[k] is not None and label+"**" not in "\n".join(lines):
            v = occ[k]
            if isinstance(v,(int,float)) and k not in ("room_nights","rooms_sold","rooms_available") and "pct" not in k:
                lines.append(f"- {label}: **{_fmt_money(v)}**" if "pct" not in k else f"- {label}: **{v}%**")
            else:
                lines.append(f"- {label}: **{v}{'%' if 'pct' in k else ''}**")
    # include any other scalars
    for k,v in occ.items():
        if isinstance(v,(int,float,str)) and k not in ("pct","occupancy_pct","adr","revpar","room_nights","rooms_sold","rooms_available","revenue","total_revenue") and len(lines)<18:
            nm=k.replace('_',' ').title()
            lines.append(f"- {nm}: **{v}**")
    return "\n".join(lines)

def _fmt_compare(data: dict) -> str:
    cur = data.get("current") or {}; prev = data.get("previous") or {}
    lines = [f"Here’s a grounded comparison: **{cur.get('start','?')} → {cur.get('end','?')}** vs **{prev.get('start','?')} → {prev.get('end','?')}**."]
    def pl_net(x):
        pl = x.get("pl") or {}
        if isinstance(pl, dict):
            rev = float(pl.get("revenue", pl.get("total_revenue", pl.get("net_revenue", 0))) or 0)
            exp = float(pl.get("expenses", pl.get("total_expenses", 0)) or 0)
            net = float(pl.get("net_profit", pl.get("net_income", rev-exp)) or (rev-exp))
            return rev, exp, net
        return None
    cpl, ppl = pl_net(cur), pl_net(prev)
    if cpl and ppl:
        lines.append(f"- **P&L:** net {_fmt_money(cpl[2])} (rev {_fmt_money(cpl[0])}, exp {_fmt_money(cpl[1])}) vs {_fmt_money(ppl[2])} previously (Δ {_fmt_money(cpl[2]-ppl[2])}).")
    cocc = (cur.get("occupancy") or {}); pocc = (prev.get("occupancy") or {})
    if cocc or pocc:
        cp = float(cocc.get("pct", cocc.get("occupancy_pct", 0)) or 0); pp = float(pocc.get("pct", pocc.get("occupancy_pct", 0)) or 0)
        lines.append(f"- **Occupancy:** {cp}% vs {pp}% (Δ {round(cp-pp,2)} pp), ADR {_fmt_money(cocc.get('adr',0))} vs {_fmt_money(pocc.get('adr',0))}, RevPAR {_fmt_money(cocc.get('revpar',0))} vs {_fmt_money(pocc.get('revpar',0))}.")
    ccf = cur.get("cash_flow"); pcf = prev.get("cash_flow")
    if ccf or pcf:
        def nc(cf):
            if isinstance(cf, dict):
                for k in ("net_cash","net","cash_net"):
                    if k in cf: return float(cf.get(k) or 0)
            return None
        a,b = nc(ccf), nc(pcf)
        if a is not None or b is not None:
            lines.append(f"- **Cash flow (net):** {_fmt_money(a or 0)} vs {_fmt_money(b or 0)}.")
    if len(lines) == 1:
        lines.append("- Detailed period data was retrieved; ask for a specific report (P&L, occupancy, or cash flow) if you want the full breakdown.")
    return "\n".join(lines)

def _fmt_outstanding(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load outstanding balances ({data.get('error')})."
    count = int(data.get("count") or 0); total = float(data.get("total_due") or 0)
    items = data.get("outstanding") or []
    if count == 0:
        return "Good news — there are **no outstanding balances** on active/checked-out reservations right now (based on real records)."
    lines = [f"Here’s who currently owes money (real data): **{count}** reservation(s), **{_fmt_money(total)}** total due."]
    for r in items[:15]:
        lines.append(f"- Res #{r.get('reservation_id')} — {r.get('guest_name','Guest')} (room {r.get('room') or '-'}, {r.get('status','')}, {r.get('check_in','')}→{r.get('check_out','')}): **{_fmt_money(r.get('balance_due',0))}** due")
    if count > 15:
        lines.append(f"- …and {count-15} more. Ask if you want the full list or the largest balances.")
    return "\n".join(lines)

def _fmt_inventory_alerts(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load inventory alerts ({data.get('error')})."
    a = (data or {}).get("alerts") or {}
    neg = a.get("negative") or []; low = a.get("low") or []; over = a.get("overstock") or []
    if not neg and not low and not over:
        return "Inventory looks healthy — **no low, negative, or overstock alerts** right now (real data)."
    lines = ["Here are the current inventory alerts (real data):"]
    if neg:
        lines.append(f"- **Negative stock ({len(neg)}):** " + ", ".join([f"{i.get('item_name')} (#{i.get('id')}, qty {i.get('stock_qty')})" for i in neg[:8]]))
    if low:
        lines.append(f"- **Low stock ({len(low)}):** " + ", ".join([f"{i.get('item_name')} (#{i.get('id')}, qty {i.get('stock_qty')}, thr {i.get('threshold')})" for i in low[:8]]))
    if over:
        lines.append(f"- **Overstock ({len(over)}):** " + ", ".join([f"{i.get('item_name')} (#{i.get('id')}, qty {i.get('stock_qty')}, thr {i.get('threshold')})" for i in over[:8]]))
    return "\n".join(lines)

def _fmt_top_sellers(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load top sellers ({data.get('error')})."
    items = data.get("top_items") or []
    start, end = data.get("start"), data.get("end")
    if not items:
        return f"No non-voided POS sales found for **{start} → {end}**. Want to try a different period?"
    lines = [f"Top selling items for **{start} → {end}** (grounded in POS data):"]
    for i,it in enumerate(items[:10],1):
        lines.append(f"{i}. **{it.get('item')}** — {it.get('qty_sold')} sold, {_fmt_money(it.get('gross',0))} gross ({it.get('lines',0)} lines)")
    return "\n".join(lines)

def _fmt_expenses(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load expenses ({data.get('error')})."
    cats = data.get("by_category") or []; total = float(data.get("total") or 0)
    start, end = data.get("start"), data.get("end")
    if not cats:
        return f"No expenses recorded for **{start} → {end}**."
    lines = [f"Expenses summary for **{start} → {end}** (real data): **{_fmt_money(total)}** total."]
    for c in cats[:12]:
        lines.append(f"- {c.get('category')}: **{_fmt_money(c.get('total',0))}** (net {_fmt_money(c.get('net',0))}, VAT {_fmt_money(c.get('vat',0))}, {c.get('count',0)} entries)")
    if len(cats) > 12: lines.append(f"- …and {len(cats)-12} more categories.")
    return "\n".join(lines)

def _fmt_sales_breakdown(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load sales breakdown ({data.get('error')})."
    start,end = data.get("start"), data.get("end")
    lines = [f"Sales & collections breakdown for **{start} → {end}** (real data):"]
    pos = data.get("pos_by_item") or []
    if pos:
        lines.append("**POS by item (top):**")
        for i in pos[:10]: lines.append(f"- {i.get('item')}: {i.get('qty')} sold, {_fmt_money(i.get('gross',0))} gross")
    pays = data.get("payments_by_method") or []
    if pays:
        lines.append("**Payments by method:**")
        for p in pays: lines.append(f"- {p.get('method')}: {_fmt_money(p.get('total',0))} ({p.get('count',0)} txns)")
    src = data.get("reservations_by_source") or []
    if src:
        lines.append("**Reservations by source:** " + ", ".join([f"{s.get('source')}: {s.get('count')}" for s in src[:8]]))
    if len(lines) == 1: lines.append("- No sales/payment/source data found for this period.")
    return "\n".join(lines)

def _fmt_pipeline(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load the reservation pipeline ({data.get('error')})."
    by = data.get("by_status") or {}
    upcoming = data.get("upcoming_arrivals_7d") or []
    lines = ["Reservation pipeline (real data):"]
    if by:
        lines.append("- **By status:** " + ", ".join([f"{k}: {v}" for k,v in by.items()]))
    if upcoming:
        lines.append(f"- **Upcoming arrivals (next 7 days): {len(upcoming)}**")
        for r in upcoming[:10]: lines.append(f"  - Res #{r.get('id')} {r.get('guest_name','')} — room {r.get('room_number') or '-'}, {r.get('check_in_date')}→{r.get('check_out_date')} ({r.get('status')})")
        if len(upcoming)>10: lines.append(f"  - …and {len(upcoming)-10} more.")
    else:
        lines.append("- No upcoming arrivals in the next 7 days.")
    return "\n".join(lines)

def _fmt_housekeeping(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load housekeeping summary ({data.get('error')})."
    s = data.get("summary") or {}
    by = s.get("by_status") or {}
    total = s.get("total_rooms", sum(by.values()) if by else 0)
    if not by:
        return "No housekeeping data available right now."
    parts = [f"{k}: **{v}**" for k,v in by.items()]
    return f"Housekeeping status (real data, {total} rooms): " + ", ".join(parts) + "."

def _fmt_availability(data: dict) -> str:
    if data.get("error"): return f"I couldn’t check availability ({data.get('error')})."
    start,end = data.get("start"), data.get("end")
    rooms = data.get("rooms") or []; count = data.get("available_count", len(rooms))
    if count == 0:
        return f"No rooms are available for **{start} → {end}** (based on real availability). Want to try different dates?"
    lines = [f"Room availability for **{start} → {end}**: **{count}** room(s) available."]
    for r in rooms[:15]:
        lines.append(f"- {r.get('room_number')} ({r.get('room_type','')}) — base {_fmt_money(r.get('base_price',0))}, housekeeping: {r.get('housekeeping_status','')}")
    if count > 15: lines.append(f"- …and {count-15} more.")
    return "\n".join(lines)

def _fmt_menu(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load the menu ({data.get('error')})."
    meals = data.get("meals") or []
    if not meals: return "No meals/menu items are registered yet."
    lines = [f"Menu / meals (real data, {data.get('count', len(meals))} items):"]
    for m in meals[:20]:
        name = m.get("name") or m.get("item_name") or f"Meal #{m.get('id')}"
        price = m.get("selling_price", m.get("price"))
        active = m.get("active")
        extra = f" — {_fmt_money(price)}" if price is not None else ""
        if active is not None: extra += f" ({'active' if active else 'inactive'})"
        desc = _short(m.get("description") or "", 80)
        lines.append(f"- **{name}**{extra}" + (f": {desc}" if desc else ""))
    if len(meals)>20: lines.append(f"- …and {len(meals)-20} more.")
    return "\n".join(lines)

def _fmt_rate_plans(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load rate plans ({data.get('error')})."
    plans = data.get("rate_plans") or []
    if not plans: return "No rate plans found."
    lines = [f"Rate plans (real data, {len(plans)}):"]
    for p in plans[:30]:
        name = p.get("name") or f"Plan #{p.get('id')}"
        mod = p.get("price_modifier", p.get("modifier", 0))
        used = p.get("reservations_using_plan")
        used_s = f", used by {used} reservation(s)" if used is not None else ""
        lines.append(f"- **{name}** (#{p.get('id')}): modifier {mod}{used_s}")
    return "\n".join(lines)

def _fmt_health_diagnostics(data: dict) -> str:
    if data.get("error"): return f"I couldn’t collect health/doctor diagnostics ({data.get('error')})."
    lines = [f"Doctor/health diagnostics ({'OK' if data.get('ok') else 'issues detected'}, real local data):"]
    sch = data.get("schema") or {}
    if sch:
        lines.append(f"- Schema: version {sch.get('user_version')} (expected {sch.get('expected_version')}), matches={sch.get('matches_expected')}")
        t = sch.get("tables") or {}
        if t: lines.append(f"  - Tables: ok={t.get('ok')} present={t.get('present_count')} missing={len(t.get('missing_tables') or [])}")
    integ = (data.get("integrity") or {}).get("sqlite") or {}
    if integ:
        lines.append(f"- SQLite integrity: {integ.get('integrity_check')} | FK violations: {len(integ.get('foreign_key_violations') or [])}")
    ledger = (data.get("integrity") or {}).get("ledger")
    if ledger is not None:
        lines.append(f"- Ledger integrity: {ledger}")
    cons = (data.get("consistency") or {}).get("batch_stock") or {}
    if cons:
        lines.append(f"- Batch/stock consistency: ok={cons.get('ok')} checked={cons.get('checked')} mismatches={cons.get('mismatch_count', 0)}")
        for m in (cons.get("mismatches") or [])[:3]:
            lines.append(f"  - item #{m.get('item_id')} {m.get('item_name')}: stock {m.get('stock_qty')} vs active batch remaining {m.get('batch_remaining_active')}")
    pr = (data.get("readiness") or {}).get("printer") or {}
    if pr:
        lines.append(f"- Printer readiness: configured={pr.get('configured')} mode={pr.get('mode','')} target_set={pr.get('target_set', False)}")
    br = data.get("backup_restore") or {}
    if br:
        rd = br.get("readiness") or {}
        lines.append(f"- Backup/restore readiness: db_exists={rd.get('db_exists')} data_dir_writable={rd.get('data_dir_writable')}")
        b = br.get("backup") or {}
        if b:
            lines.append(f"  - Backup helper: {b.get('cli_helper')} (stop app recommended={b.get('stop_app_first')})")
        r = br.get("restore") or {}
        if r:
            lines.append(f"  - Restore verify helper: {r.get('verify_helper')}")
    upd = data.get("update") or {}
    if isinstance(upd, dict) and (upd.get("app_version") or upd.get("commit_short") or upd.get("branch")):
        lines.append(f"- App/update (local): version={upd.get('app_version') or '-'} commit={upd.get('commit_short') or '-'} branch={upd.get('branch') or '-'} dirty={upd.get('dirty')}")
    issues = data.get("config_issues") or []
    if issues:
        lines.append(f"- Config issues: {len(issues)}")
        for iss in issues[:5]:
            lines.append(f"  - [{iss.get('severity','info')}] {iss.get('code')}: {iss.get('message')}")
    lines.append("Diagnostics are read-only and grounded in local data; runtime data (DB/exports/invoices) is preserved by doctor/update/backup guidance.")
    return "\n".join(lines)


def _fmt_integrity(data: dict) -> str:
    if data.get("error"): return f"I couldn’t run the integrity/health check ({data.get('error')})."
    if "backup_restore" in data or "consistency" in data or "readiness" in data:
        return _fmt_health_diagnostics(data)
    ok = data.get("ok")
    if ok:
        return "Ledger/data integrity check: **OK** — no issues reported by the current read-only checks (real data)."
    issues = data.get("issues") or data.get("errors") or data.get("problems")
    lines = ["Ledger/data integrity check: **issues found** (real data)."]
    if isinstance(issues, list) and issues:
        for it in issues[:12]: lines.append(f"- {it}")
    elif issues:
        lines.append(f"- {issues}")
    else:
        for k,v in data.items():
            if k=="ok": continue
            if v and isinstance(v,(list,dict,str)) and len(lines)<12:
                lines.append(f"- {k}: {_short(json.dumps(v, ensure_ascii=False, default=str) if not isinstance(v,str) else v, 140)}")
    lines.append("Ask for a `doctor report` for full schema/batch-stock/printer/backup-readiness diagnostics (read-only).")
    return "\n".join(lines)

def _fmt_recent_activity(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load recent activity ({data.get('error')})."
    rows = data.get("recent_audit") or []
    if not rows: return "No recent audit/activity entries found."
    lines = [f"Recent activity (latest {len(rows)} audit entries, real data):"]
    for r in rows[:20]:
        when = r.get("at") or r.get("created_at") or ""
        action = r.get("action") or r.get("event") or r.get("type") or ""
        detail = r.get("detail") or r.get("description") or r.get("entity") or ""
        who = r.get("user") or r.get("actor") or ""
        parts = [p for p in [str(when).strip(), str(action).strip(), str(detail).strip(), str(who).strip()] if p]
        lines.append("- " + " | ".join(parts) if parts else f"- { _short(json.dumps(r, ensure_ascii=False, default=str), 160)}")
    if len(rows)>20: lines.append(f"- …and {len(rows)-20} more.")
    return "\n".join(lines)

def _fmt_activities_catalog(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load activities ({data.get('error')})."
    items = data.get("activities") or []
    if not items: return "No activities/trips are registered yet. You can register them in **POS / Inventory → Activities / Trips**."
    lines = [f"Registered activities/trips catalog (real data, {data.get('count', len(items))}):"]
    for a in items[:25]:
        status = "active" if a.get("active") else "inactive"
        lines.append(f"- **{a.get('name')}** (#{a.get('id')}, {a.get('category','Trip')}, {_fmt_money(a.get('unit_price',0))}, {status})" + (f": {_short(a.get('description',''),80)}" if a.get('description') else ""))
    if len(items)>25: lines.append(f"- …and {len(items)-25} more.")
    lines.append("\nBookings for these activities are posted to the reservation folio and invoice (like POS).")
    return "\n".join(lines)

def _fmt_activity_stats(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load activity stats ({data.get('error')})."
    stats = data.get("activity_stats") or []
    if not stats: return "No activity/trip statistics available yet."
    lines = ["Activity/trip booking statistics (grounded in real bookings):"]
    for s in stats[:20]:
        lines.append(f"- **{s.get('activity_name')}** (#{s.get('activity_id')}, {s.get('category','')}): {s.get('bookings',0)} booking(s), qty {s.get('total_qty',0)}, revenue {_fmt_money(s.get('gross_revenue',0))}" + (f" — last booked {s.get('last_booked')}" if s.get('last_booked') else ""))
    if len(stats)>20: lines.append(f"- …and {len(stats)-20} more.")
    return "\n".join(lines)

def _fmt_activity_bookings(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load activity bookings ({data.get('error')})."
    bookings = data.get("bookings") or []
    if not bookings: return "No booked activities/trips found for the selected filter (real data)."
    lines = [f"Booked activities/trips ({data.get('count', len(bookings))} found, grounded in real bookings):"]
    for b in bookings[:20]:
        who = b.get("guest_name") or (f"guest #{b.get('guest_id')}" if b.get("guest_id") else "guest")
        lines.append(f"- **{b.get('activity_name') or b.get('item_description')}** x{b.get('quantity',1)} ({_fmt_money(b.get('unit_price',0))} each) — Res #{b.get('reservation_id')} {who}, room {b.get('room_number') or '-'}, {b.get('check_in_date','')}→{b.get('check_out_date','')} (booked {b.get('created_at','')})")
    if len(bookings)>20: lines.append(f"- …and {len(bookings)-20} more.")
    return "\n".join(lines)

def _fmt_reservation(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load the reservation ({data.get('error')})."
    rid = data.get("reservation_id") or data.get("id")
    # get_guest_balance shape
    if "breakdown" in data or ("balance" in data and "reservation_id" in data):
        bd = data.get("breakdown") or {}
        bal = data.get("balance", bd.get("balance_due", 0))
        lines = [f"Reservation #{rid} — balance (real data):"]
        lines.append(f"- **Balance due:** {_fmt_money(bal)}")
        if bd:
            for k,label in [("total_charges","Total charges"),("accommodation_gross","Accommodation"),("pos_gross","POS/activities"),("total_paid","Total paid")]:
                if bd.get(k) is not None: lines.append(f"- {label}: {_fmt_money(bd.get(k))}")
        return "\n".join(lines)
    # summary shape
    lines = [f"Reservation #{rid} (real data):"]
    for k,label in [("guest_name","Guest"),("guest_id","Guest ID"),("status","Status"),("room_number","Room"),("room_type","Room type"),("check_in_date","Check-in"),("check_out_date","Check-out"),("nights","Nights"),("source","Source"),("rate_plan","Rate plan")]:
        if data.get(k) is not None: lines.append(f"- {label}: {data.get(k)}")
    for k,label in [("agreed_nightly_rate","Nightly rate"),("tax_amount","Tax")]:
        if data.get(k) is not None: lines.append(f"- {label}: {_fmt_money(data.get(k))}")
    bal = data.get("balance") or data.get("balance_due")
    if bal is not None: lines.append(f"- **Balance due:** {_fmt_money(bal)}")
    if data.get("pos_count") is not None: lines.append(f"- POS/activity items: {data.get('pos_count')}")
    if data.get("notes"): lines.append(f"- Notes: {_short(data.get('notes'), 140)}")
    return "\n".join(lines)

def _fmt_guest_search(data: dict) -> str:
    if data.get("error"): return f"I couldn’t search guests ({data.get('error')})."
    q = data.get("query") or ""; guests = data.get("guests") or []
    if not guests:
        return f"I couldn’t find any guests matching “{q}” in the real records. Try a different name/phone/email fragment or a guest ID."
    lines = [f"I found **{len(guests)}** guest match(es) for “{q}” (real data):"]
    for g in guests[:10]:
        name = g.get("name") or g.get("canonical_name") or "Guest"
        lines.append(f"- #{g.get('id')} **{name}** — phone: {g.get('phone') or '-'}, email: {g.get('email') or '-'}, stays: {g.get('stays', g.get('reservation_count', 0))}, last stay: {g.get('last_stay') or '-'}")
    if len(guests)>10: lines.append(f"- …and {len(guests)-10} more.")
    lines.append("Tell me the guest ID (e.g. `guest #12`) if you want their full portfolio, last visit, or bookings.")
    return "\n".join(lines)

def _fmt_guest_profile(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load that guest profile ({data.get('error')})."
    g = data.get("guest") or {}
    if not g and data.get("id"): g = data
    gid = g.get("id") or g.get("guest_id")
    name = g.get("name") or g.get("canonical_name") or f"Guest #{gid}"
    lines = [f"Here’s the profile for **{name}** (#{gid}, real data):"]
    lines.append(f"- Phone: {g.get('phone') or '-'} | Email: {g.get('email') or '-'}")
    if g.get("notes"): lines.append(f"- Notes: {_short(g.get('notes'), 160)}")
    recents = data.get("recent_reservations") or data.get("reservations") or []
    if recents:
        lines.append(f"- Recent reservations ({min(len(recents),5)} shown):")
        for r in recents[:5]:
            lines.append(f"  - Res #{r.get('id')} room {r.get('room_number') or r.get('room_id') or '-'} {r.get('check_in_date','')}→{r.get('check_out_date','')} ({r.get('status','')})")
    else:
        lines.append("- No reservations on file.")
    return "\n".join(lines)

def _fmt_guest_last_visit(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load the last visit ({data.get('error')})."
    if not data.get("found") and not data.get("last_visit"):
        gid = data.get("guest_id") or "this guest"
        return f"I couldn’t find a last visit for guest #{gid} in the real records (no eligible reservations)."
    lv = data.get("last_visit") or data
    gid = data.get("guest_id") or lv.get("guest_id")
    basis = data.get("basis") or ("most recent Checked Out stay" if lv.get("status") == "Checked Out" else "most recent non-deleted reservation")
    lines = [f"Last visit for guest #{gid} (based on {basis}, real data):"]
    rid = lv.get("reservation_id") or lv.get("id")
    if rid: lines.append(f"- Reservation: #{rid} ({lv.get('status','')})")
    if lv.get("room_number") or lv.get("room_id"): lines.append(f"- Room: {lv.get('room_number') or lv.get('room_id')} {('('+lv.get('room_type')+')') if lv.get('room_type') else ''}".strip())
    if lv.get("check_in_date") or lv.get("check_out_date"): lines.append(f"- Dates: {lv.get('check_in_date','')} → {lv.get('check_out_date','')}" + (f" ({lv.get('nights')} nights)" if lv.get('nights') else ""))
    if lv.get("agreed_nightly_rate") is not None: lines.append(f"- Nightly rate: {_fmt_money(lv.get('agreed_nightly_rate'))}")
    if lv.get("source"): lines.append(f"- Source: {lv.get('source')}")
    return "\n".join(lines)

def _fmt_guest_top_items(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load top ordered items ({data.get('error')})."
    gid = data.get("guest_id")
    if not data.get("found") and not data.get("items"):
        rid = (data.get("last_visit") or {}).get("reservation_id") or (data.get("last_visit") or {}).get("id")
        if rid: return f"Guest #{gid}’s last visit (Res #{rid}) has no non-voided POS/activity orders recorded."
        return f"I couldn’t determine last-visit orders for guest #{gid} (no eligible last visit found)."
    lv = data.get("last_visit") or {}
    items = data.get("items") or []
    rid = lv.get("reservation_id") or lv.get("id")
    lines = [f"Most ordered items on guest #{gid}’s last visit (Res #{rid}, real POS data):"]
    if not items:
        lines.append("- No non-voided items/orders recorded for that stay.")
    else:
        for i,it in enumerate(items[:10],1):
            lines.append(f"{i}. **{it.get('item_description') or it.get('item')}** — qty {it.get('total_qty', it.get('qty', it.get('quantity',0)))}" + (f", spend {_fmt_money(it.get('gross_spend', it.get('gross',0)))}" if it.get('gross_spend') is not None or it.get('gross') is not None else ""))
    return "\n".join(lines)

def _fmt_guest_last_visit_spend(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load last-visit spend ({data.get('error')})."
    gid = data.get("guest_id")
    lv = data.get("last_visit") or {}
    rid = lv.get("reservation_id") or lv.get("id")
    lines = [f"Last visit spend for guest #{gid} (real data):"]
    if rid: lines.append(f"- Stay: Res #{rid} {lv.get('check_in_date','')}→{lv.get('check_out_date','')} ({lv.get('status','')}) room {lv.get('room_number') or '-'}")
    bd = data.get("balance_breakdown") or {}
    if bd:
        for k,label in [("total_charges","Total charges"),("accommodation_gross","Accommodation"),("pos_gross","POS/activities"),("total_paid","Total paid"),("balance_due","Balance due")]:
            if bd.get(k) is not None:
                prefix = "**" if k=="balance_due" else ""
                lines.append(f"- {label}: {prefix}{_fmt_money(bd.get(k))}{prefix}")
    elif data.get("spend") is not None:
        lines.append(f"- Spend/balance: {_fmt_money(data.get('spend'))}")
    items = data.get("top_items") or []
    if items:
        lines.append("- Top items on that stay: " + ", ".join([f"{it.get('item_description') or it.get('item')} (x{it.get('total_qty', it.get('qty',0))})" for it in items[:5]]))
    return "\n".join(lines)

def _fmt_guest_portfolio(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load the guest portfolio ({data.get('error')})."
    g = data.get("guest") or {}; s = data.get("stats") or {}
    gid = g.get("id") or g.get("guest_id"); name = g.get("name") or g.get("canonical_name") or f"Guest #{gid}"
    lines = [f"Guest portfolio (360°) for **{name}** (#{gid}) — grounded in real data:"]
    lines.append(f"- Contact: phone {g.get('phone') or '-'}, email {g.get('email') or '-'}")
    lines.append(f"- Stays: **{s.get('stays_total',0)}** total ({s.get('completed_stays',0)} completed, {s.get('total_nights',0)} nights) | first: {s.get('first_visit') or '-'} | last: {s.get('last_visit') or '-'} | favorite room: {s.get('favorite_room') or '-'}")
    lines.append(f"- Financials: charged **{_fmt_money(s.get('total_charges',0))}**, paid **{_fmt_money(s.get('total_paid',0))}**, outstanding **{_fmt_money(s.get('total_outstanding',0))}**, POS/activities **{_fmt_money(s.get('pos_gross_total',0))}**")
    trips = data.get("trips") or []
    if trips:
        lines.append(f"\n**Trips / reservations ({len(trips)}):**")
        for t in trips[:8]:
            bd = t.get("balance") or {}
            invc = t.get("invoice_files") or t.get("invoices") or []
            invn = len(invc) if isinstance(invc, list) else 0
            lines.append(f"- Res #{t.get('id')} room {t.get('room_number') or '-'} {t.get('check_in_date','')}→{t.get('check_out_date','')} ({t.get('status','')}): charges {_fmt_money(bd.get('total_charges',0))}, paid {_fmt_money(bd.get('total_paid',0))}, due {_fmt_money(bd.get('balance_due',0))}, POS items {t.get('pos_active_count', t.get('pos_count',0))}, invoices {invn}")
        if len(trips)>8: lines.append(f"- …and {len(trips)-8} more stays (open the guest portfolio in the app for full per-stay detail).")
    else:
        lines.append("- No trips/reservations on file.")
    top = data.get("top_items_overall") or []
    if top:
        lines.append("**Top items overall:** " + ", ".join([f"{i.get('item')} ({_fmt_money(i.get('gross',0))})" for i in top[:6]]))
    posn = data.get("all_pos_count", 0); payn = data.get("all_payments_count", 0)
    lines.append(f"\nTotals on record: {posn} POS/activity line(s), {payn} payment(s). All figures above are from real backend data.")
    return "\n".join(lines)

def _fmt_decision_support(data: dict) -> str:
    if data.get("error"): return f"I couldn’t load decision support ({data.get('error')})."
    start,end = data.get("start"), data.get("end")
    lines = [f"Decision support for **{start} → {end}** (grounded in real data):"]
    ds = data.get("decision_support")
    fs = data.get("financial_summary")
    if isinstance(ds, dict):
        # highlights
        for k in ("kpis","signals","strengths","risks","recommendations","summary"):
            v = ds.get(k)
            if v is None: continue
            if isinstance(v, list) and v:
                lines.append(f"**{k.replace('_',' ').title()}:**")
                for it in v[:8]: lines.append(f"- {it if isinstance(it,str) else _short(json.dumps(it,ensure_ascii=False,default=str),140)}")
            elif isinstance(v, dict) and v:
                lines.append(f"**{k.replace('_',' ').title()}:**")
                for kk,vv in list(v.items())[:8]: lines.append(f"- {kk}: {vv}")
            elif isinstance(v, str) and v.strip():
                lines.append(f"- {k.replace('_',' ').title()}: {v.strip()}")
        # scalar kpis
        for k,v in ds.items():
            if k in ("kpis","signals","strengths","risks","recommendations","summary"): continue
            if isinstance(v,(int,float,str)) and len(lines)<22:
                lines.append(f"- {k.replace('_',' ').title()}: **{_fmt_money(v) if isinstance(v,(int,float)) and 'pct' not in k else v}**")
    elif isinstance(ds, str) and ds.strip():
        lines.append("\n".join(ds.strip().splitlines()[:20]))
    if isinstance(fs, dict) and fs:
        lines.append("**Financial summary:**")
        for k,v in list(fs.items())[:10]:
            if isinstance(v,(int,float,str)):
                lines.append(f"- {k.replace('_',' ').title()}: **{_fmt_money(v) if isinstance(v,(int,float)) and 'pct' not in k else v}**")
    if len(lines)==1: lines.append(_format_data("Decision support data", data))
    return "\n".join(lines)

def _proactive_suggestions(intent: str, data: dict, answer_text: str = "") -> list[str]:
    sug = []
    try:
        if intent == "outstanding_balances":
            if (data or {}).get("count",0) > 0: sug.append("Want the full list sorted by largest balance, or a specific reservation’s folio?")
            sug.append("I can also run a business partner briefing for this month.")
        elif intent == "inventory_alerts":
            a = (data or {}).get("alerts") or {}
            if (a.get("low") or a.get("negative")): sug.append("Want to see top-selling items to prioritize what to restock?")
        elif intent == "property_stats":
            sug.append("Ask for “today’s ops” or “business partner briefing for this month” for more detail.")
        elif intent == "today_ops":
            sug.append("Want housekeeping details, today’s arrivals/departures list, or who owes money?")
        elif intent in ("report_pl","report_cash_flow","report_vat","report_payment_methods","report_occupancy","occupancy_summary"):
            sug.append("I can compare this period to the previous one if you’d like.")
        elif intent == "compare_periods":
            sug.append("Want a business partner briefing or a deeper P&L/cash-flow breakdown for the current period?")
        elif intent in ("guest_profile","guest_last_visit","guest_top_items_last_visit","guest_last_visit_spend"):
            gid = (data or {}).get("guest_id") or (((data or {}).get("guest") or {}).get("id"))
            if gid: sug.append(f"Want the full 360° portfolio for guest #{gid} (all stays, POS, payments, invoices)?")
            sug.append("I can also show their booked activities/trips.")
        elif intent == "guest_portfolio":
            gid = ((data or {}).get("guest") or {}).get("id") or (data or {}).get("guest_id")
            outst = (((data or {}).get("stats") or {}).get("total_outstanding") or 0)
            if outst and outst > 0: sug.append("This guest has an outstanding balance — want details on which reservation is due?")
            if gid: sug.append(f"Want to see booked activities/trips for guest #{gid}?")
        elif intent == "guest_search":
            if (data or {}).get("guests"): sug.append("Tell me the guest ID to open their full portfolio or last visit.")
        elif intent == "reservation_summary":
            rid = (data or {}).get("reservation_id") or (data or {}).get("id")
            bal = (data or {}).get("balance_due") or (data or {}).get("balance")
            if bal and float(bal) > 0: sug.append(f"Reservation #{rid} has a balance due — want the folio breakdown?")
        elif intent == "reservation_balance":
            sug.append("Want the full reservation summary as well?")
        elif intent == "top_selling_items":
            sug.append("Want to check inventory alerts to make sure top sellers are stocked?")
        elif intent == "list_activities":
            sug.append("Want activity/trip booking statistics or bookings for a specific guest?")
        elif intent == "activity_stats":
            sug.append("Ask for bookings of a specific activity or a guest’s booked activities.")
        elif intent == "business_partner_briefing":
            sug.append("Want me to drill into any watch-out (AR, inventory, occupancy, or expenses)?")
        elif intent == "housekeeping_summary":
            sug.append("Want today’s ops snapshot or arrivals to prioritize readiness?")
        elif intent == "capabilities_help":
            sug.append("Ask me anything above — I’ll answer from real app data.")
    except Exception:
        pass
    # batch/expiry/trace, integrity/health, updates
    try:
        if intent in ("expiring_batches", "get_expiring_batches"):
            n = (data or {}).get("count", 0)
            if n: sug.append("Want to trace a specific lot/barcode to see received date, expiry, and movements?")
            sug.append("I can also list all inventory batches/lots or show inventory alerts.")
        elif intent in ("inventory_batches", "list_inventory_batches"):
            sug.append("Want to trace a barcode/SKU/lot code for full traceability (received/expiry/movements)?")
            sug.append("I can also show batches expiring soon.")
        elif intent in ("trace_barcode", "barcode_trace"):
            sug.append("Want to list all batches for this item or see which batches are expiring soon?")
        elif intent in ("ledger_integrity", "verify_ledger_integrity"):
            ok = (data or {}).get("ok")
            if ok is False: sug.append("Consider reviewing the flagged items in-app (Settings/health checks) before relying on reports.")
            else: sug.append("Want today’s ops or a business partner briefing for a quick health/ops snapshot?")
        elif intent in ("app_update_status", "get_app_update_status", "app_version"):
            sug.append("For git-based installs you can check/apply updates in Settings → App updates (changelog shown; data preserved; restart after apply).")
    except Exception:
        pass
    # dedupe, keep max 2
    out=[]; seen=set()
    for s in sug:
        if s in seen: continue
        seen.add(s); out.append(s)
    return out[:2]

def _fmt_expiring_batches(data: dict) -> str:
    if data.get("error"):
        return f"I couldn’t load expiring batches ({data.get('error')})."
    batches = data.get("batches") or []
    days = data.get("within_days")
    lines = [f"Inventory batches expiring/expired{f' within {days} day(s)' if days is not None else ''} (real, traceable lots):"]
    if not batches:
        lines.append("- None found. Stock looks clear for the selected window.")
        return "\n".join(lines)
    for b in batches[:25]:
        st = b.get('expiry_state') or b.get('status') or ''
        lines.append(f"- **{b.get('item_name') or ('Item #'+str(b.get('item_id')))}** lot `{b.get('lot_code')}`: remaining **{b.get('quantity_remaining')}** (recv {b.get('quantity_received')}), received {b.get('received_at') or '-'}, expiry {b.get('expiry_date') or '-'} [{st}], cost {_fmt_money(b.get('unit_cost',0))}, supplier {b.get('supplier') or '-'}")
    if len(batches) > 25:
        lines.append(f"- …and {len(batches)-25} more batches.")
    return "\n".join(lines)

def _fmt_inventory_batches(data: dict) -> str:
    if data.get("error"):
        return f"I couldn’t load inventory batches ({data.get('error')})."
    batches = data.get("batches") or []
    item_id = data.get("item_id")
    lines = [f"Inventory batches/lots{f' for item #{item_id}' if item_id else ''} (traceable, real data):"]
    if not batches:
        lines.append("- No batches found for the given criteria.")
        return "\n".join(lines)
    for b in batches[:30]:
        lines.append(f"- **{b.get('item_name') or ('Item #'+str(b.get('item_id')))}** lot `{b.get('lot_code')}` (#{b.get('id')}): remaining **{b.get('quantity_remaining')}**/{b.get('quantity_received')}, received {b.get('received_at') or '-'}, expiry {b.get('expiry_date') or '-'} [{b.get('expiry_state') or b.get('status') or ''}], cost {_fmt_money(b.get('unit_cost',0))}, ref {b.get('reference') or '-'}")
    if len(batches) > 30:
        lines.append(f"- …and {len(batches)-30} more.")
    return "\n".join(lines)

def _fmt_trace_barcode(data: dict) -> str:
    if data.get("error"):
        return f"I couldn’t trace that code ({data.get('error')}). { 'Try a different barcode/SKU/lot code.' if 'not found' in str(data.get('error','')).lower() else '' }".strip()
    t = data.get("type")
    if t == "item":
        it = data.get("item") or {}
        lines = [f"Barcode/SKU resolved to inventory item (real data):"]
        lines.append(f"- **{it.get('item_name')}** (#{it.get('id')}), category {it.get('category') or '-'}, stock {it.get('stock_qty')} {it.get('unit') or ''}, sell {_fmt_money(it.get('selling_price',0))}, cost {_fmt_money(it.get('unit_cost',0))}")
        lines.append(f"- SKU: {it.get('sku') or '-'} | Barcode: {it.get('barcode') or '-'} | Batches tracked: {'yes' if it.get('track_batches') else 'no'} | Shelf life: {it.get('shelf_life_days') or '-'} days")
        return "\n".join(lines)
    if t == "batch":
        b = data.get("batch") or {}
        it = data.get("item") or {}
        lines = [f"Barcode/lot resolved to a traceable batch (real data):"]
        if it:
            lines.append(f"- Item: **{it.get('item_name')}** (#{it.get('id')}) SKU {it.get('sku') or '-'}")
        lines.append(f"- Lot: `{b.get('lot_code')}` (batch #{b.get('id')}), status {b.get('status') or '-'} [{b.get('expiry_state') or ''}]")
        lines.append(f"- Received: {b.get('received_at') or '-'} | Expiry: {b.get('expiry_date') or '-'} | Qty: remaining **{b.get('quantity_remaining')}** / received {b.get('quantity_received')}")
        lines.append(f"- Unit cost: {_fmt_money(b.get('unit_cost',0))} | Supplier: {b.get('supplier') or '-'} | Ref: {b.get('reference') or '-'}")
        if b.get('notes'): lines.append(f"- Notes: {_short(b.get('notes'), 140)}")
        moves = data.get("batch_movements") or []
        if moves:
            lines.append("Recent movements:")
            for m in moves[:8]:
                lines.append(f"  - {m.get('at','')}: {m.get('change_type','')} qty {m.get('quantity')} (ref {m.get('reference') or '-'})")
        return "\n".join(lines)
    return _format_data("Trace result", data)


def _fmt_app_update_status(data: dict) -> str:
    if data.get("error"):
        return f"I couldn’t read app update status ({data.get('error')})."
    local = data.get("local") or {}
    lines = ["App version and update status (local, grounded):"]
    lines.append(f"- App version: **{data.get('app_version') or local.get('app_version') or '—'}**")
    lines.append(f"- Installed commit: `{local.get('commit_short') or '—'}` {('— ' + local.get('commit_subject')) if local.get('commit_subject') else ''} {( '('+local.get('commit_date')+')' ) if local.get('commit_date') else ''}")
    lines.append(f"- Branch: {local.get('branch') or '—'} {'(local changes present)' if local.get('dirty') else ''}")
    if local.get('project_root'): lines.append(f"- Project root: {local.get('project_root')}")
    if local.get('data_dir'): lines.append(f"- Data directory (preserved on update/backup/restore): {local.get('data_dir')}")
    how = data.get('how_to_check') or "Use Settings → App updates to check for updates, view changelog, and apply safely (DB/exports/invoices preserved)."
    lines.append(f"- {how}")
    lines.append("- In-app (Web & TUI, git-based installs): check for updates, review changelog/details, and apply a safe fast-forward update.")
    lines.append("- Runtime data (DB, exports, invoices, configured data dir) is preserved; restart after apply. For safety, verify backups before/after updates.")
    return "\n".join(lines)


def _apply_proactive(intent: str, data: dict, answer: str) -> str:
    sugg = _proactive_suggestions(intent, data or {}, answer)
    if not sugg: return answer
    if "Want " in answer or "want " in answer.splitlines()[-1].lower():
        # avoid overdoing
        return answer
    return answer.rstrip() + "\n\n" + " ".join(sugg)

def _format_for_intent(intent: str, data: dict, fallback_title: str = "Result") -> str:
    try:
        if intent == "capabilities_help": return _fmt_capabilities(data)
        if intent == "property_stats": return _fmt_stats(data)
        if intent == "today_ops": return _fmt_today_ops(data)
        if intent == "business_partner_briefing": return _fmt_partner_briefing(data)
        if intent in ("report_pl","report_cash_flow","report_vat","report_payment_methods","report_occupancy","report_daily_ops"): return _fmt_report(data)
        if intent == "occupancy_summary": return _fmt_occupancy(data)
        if intent == "decision_support": return _fmt_decision_support(data)
        if intent == "compare_periods": return _fmt_compare(data)
        if intent == "outstanding_balances": return _fmt_outstanding(data)
        if intent == "inventory_alerts": return _fmt_inventory_alerts(data)
        if intent in ("expiring_batches","get_expiring_batches"): return _fmt_expiring_batches(data)
        if intent in ("inventory_batches","list_inventory_batches"): return _fmt_inventory_batches(data)
        if intent in ("trace_barcode","barcode_trace"): return _fmt_trace_barcode(data)
        if intent in ("app_update_status","get_app_update_status","app_version"): return _fmt_app_update_status(data)
        if intent == "top_selling_items": return _fmt_top_sellers(data)
        if intent == "expenses_summary": return _fmt_expenses(data)
        if intent == "sales_breakdown": return _fmt_sales_breakdown(data)
        if intent == "reservation_pipeline": return _fmt_pipeline(data)
        if intent == "housekeeping_summary": return _fmt_housekeeping(data)
        if intent == "room_availability": return _fmt_availability(data)
        if intent == "menu_info": return _fmt_menu(data)
        if intent == "rate_plans": return _fmt_rate_plans(data)
        if intent == "ledger_integrity": return _fmt_integrity(data)
        if intent in ("health_diagnostics", "doctor_report"): return _fmt_health_diagnostics(data)
        if intent == "recent_activity": return _fmt_recent_activity(data)
        if intent == "list_activities": return _fmt_activities_catalog(data)
        if intent == "activity_stats": return _fmt_activity_stats(data)
        if intent == "guest_activity_bookings": return _fmt_activity_bookings(data)
        if intent in ("reservation_summary","reservation_balance"): return _fmt_reservation(data)
        if intent == "guest_search": return _fmt_guest_search(data)
        if intent == "guest_profile": return _fmt_guest_profile(data)
        if intent == "guest_portfolio": return _fmt_guest_portfolio(data)
        if intent == "guest_last_visit": return _fmt_guest_last_visit(data)
        if intent == "guest_top_items_last_visit": return _fmt_guest_top_items(data)
        if intent == "guest_last_visit_spend": return _fmt_guest_last_visit_spend(data)
    except Exception:
        pass
    return _format_data(fallback_title, data)

# Deterministic local "talking" rephraser (no model, no external calls)
def _local_talking_rephrase(message: str, core_result: dict) -> str:
    """Produce a friendly, grounded natural reply from core result (pure local, deterministic)."""
    intent = (core_result or {}).get("intent") or ""
    data = core_result.get("data") or {}
    core_answer = (core_result.get("core_answer") or core_result.get("answer") or "").strip()
    if core_result.get("needs_clarification") or intent == "ambiguous_needs_clarification":
        # make clarification warm
        if core_answer:
            ans = core_answer
            if not ans.lower().startswith(("sure","of course","i can help","happy to")):
                ans = "Sure — " + ans[0].lower() + ans[1:] if len(ans)>1 else "Sure — " + ans
            return ans
    # Prefer well-formatted core answer (we now generate friendly ones); lightly frame if very terse
    if core_answer:
        ans = core_answer
        # avoid double-wrapping
        return ans
    # fallback: format from data
    return _format_for_intent(intent, data if isinstance(data, dict) else {}, fallback_title="Result")

# ── Feedback & exemplar memory ───────────────────────────────────────────────
def _ensure_nlu_dirs():
    try: INTENT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception: pass

def record_feedback(message: str, predicted_intent: str = "", corrected_intent: str = "", rating: int = 0,
                    confidence: float | None = None, slots: dict | None = None, corrected_slots: dict | None = None,
                    tools_used: list | None = None, answer: str = "", user_note: str = "", source: str = "", status: str = "new"):
    _ensure_nlu_dirs()
    payload = {"created_at": datetime.now().strftime(getattr(services, "DATETIME_FMT", "%Y-%m-%d %H:%M:%S")),
               "message": message or "", "predicted_intent": predicted_intent or "", "corrected_intent": corrected_intent or "",
               "confidence": confidence, "slots": slots or {}, "corrected_slots": corrected_slots or {},
               "tools_used": tools_used or [], "answer_preview": (answer or "")[:500], "rating": int(rating or 0),
               "user_note": user_note or "", "status": status or "new", "source": source or ""}
    fid = None
    try:
        from .db import connect
        cur = connect().execute(
            """INSERT INTO assistant_feedback (message,predicted_intent,corrected_intent,confidence,slots_json,corrected_slots_json,tools_used_json,answer_preview,rating,user_note,status,source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (payload["message"],payload["predicted_intent"],payload["corrected_intent"],payload["confidence"],
             json.dumps(payload["slots"],ensure_ascii=False,default=str), json.dumps(payload["corrected_slots"],ensure_ascii=False,default=str),
             json.dumps(payload["tools_used"],ensure_ascii=False,default=str), payload["answer_preview"], payload["rating"], payload["user_note"], payload["status"], payload["source"]))
        connect().commit(); fid = cur.lastrowid
    except Exception:
        fid = None
    try:
        with FEEDBACK_JSONL.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"id": fid, **payload}, ensure_ascii=False, default=str) + "\n")
    except Exception: pass
    global _EXEMPLAR_CACHE; _EXEMPLAR_CACHE = None
    return fid

def list_feedback(limit: int = 100, status: str | None = None):
    try:
        from .db import connect
        q = "SELECT * FROM assistant_feedback"; args=[]
        if status: q += " WHERE status=?"; args.append(status)
        q += " ORDER BY id DESC LIMIT ?"; args.append(int(limit or 100))
        rows = connect().execute(q,args).fetchall(); out=[]
        for r in rows:
            d={k:r[k] for k in r.keys()}
            for j in ("slots_json","corrected_slots_json","tools_used_json"):
                try: d[j.replace("_json","")] = json.loads(d.get(j) or ("{}" if "slots" in j else "[]"))
                except Exception: d[j.replace("_json","")] = {}
            out.append(d)
        return out
    except Exception:
        return []

def get_correction_exemplars(limit: int = 800):
    out=[]
    try:
        from .db import connect
        rows = connect().execute(
            """SELECT message, corrected_intent, corrected_slots_json, predicted_intent, slots_json
               FROM assistant_feedback WHERE (corrected_intent != '' OR rating = 1) AND message != ''
               ORDER BY id DESC LIMIT ?""", (int(limit or 800),)).fetchall()
        for r in rows:
            intent = r["corrected_intent"] or r["predicted_intent"] or ""
            if not intent: continue
            try: slots = json.loads(r["corrected_slots_json"] or r["slots_json"] or "{}")
            except Exception: slots = {}
            out.append({"text": r["message"], "label": intent, "slots": slots})
    except Exception: pass
    return out

_EXEMPLAR_CACHE = None
def _jaccard(a,b):
    if not a or not b: return 0.0
    inter=len(a&b); union=len(a|b); return inter/union if union else 0.0

def exemplar_match(text: str, threshold: float = 0.70):
    global _EXEMPLAR_CACHE
    if _EXEMPLAR_CACHE is None:
        _EXEMPLAR_CACHE = get_correction_exemplars(limit=1000)
    if not _EXEMPLAR_CACHE: return None
    qn=_norm_text(text); qset=_token_set(qn); best=None; best_score=0.0
    for ex in _EXEMPLAR_CACHE:
        tn=_norm_text(ex.get("text") or "")
        if not tn: continue
        if tn==qn:
            return {"intent":ex["label"],"confidence":0.99,"slots":ex.get("slots") or {},"source":"exemplar_exact"}
        score=max(_jaccard(qset,_token_set(tn)), difflib.SequenceMatcher(None, qn, tn).ratio()*0.95)
        if len(qn)>=8 and (qn in tn or tn in qn): score=max(score,0.9)
        if score>best_score: best_score=score; best=ex
    if best and best_score>=threshold:
        return {"intent":best["label"],"confidence":min(0.96,0.55+best_score*0.45),"slots":best.get("slots") or {},"source":"exemplar_similar","score":best_score}
    return None

# ── ML routers (optional, local/no external API) ─────────────────────────────
_SKLEARN_CACHE = None; _SETFIT_CACHE = None; _LOCAL_LLM_CACHE = None
def _load_sklearn_router():
    global _SKLEARN_CACHE
    if _SKLEARN_CACHE is not None: return _SKLEARN_CACHE
    try:
        import joblib  # type: ignore
        path = Path(os.environ.get("ASSISTANT_SKLEARN_MODEL", str(DEFAULT_SKLEARN_MODEL_PATH)))
        if not path.exists(): _SKLEARN_CACHE=False; return None
        _SKLEARN_CACHE=joblib.load(path); return _SKLEARN_CACHE
    except Exception: _SKLEARN_CACHE=False; return None

def _load_setfit_router():
    global _SETFIT_CACHE
    if _SETFIT_CACHE is not None: return _SETFIT_CACHE
    try:
        from setfit import SetFitModel  # type: ignore
        path = Path(os.environ.get("ASSISTANT_SETFIT_MODEL_DIR", str(DEFAULT_SETFIT_MODEL_DIR)))
        if not path.exists(): _SETFIT_CACHE=False; return None
        _SETFIT_CACHE=SetFitModel.from_pretrained(str(path)); return _SETFIT_CACHE
    except Exception: _SETFIT_CACHE=False; return None

def _predict_intent_ml(text: str):
    mode=(os.environ.get("ASSISTANT_NLU") or "auto").strip().lower()
    if mode in ("auto","setfit"):
        model=_load_setfit_router()
        if model is not None:
            try:
                labels=model.predict([text]); label=labels[0] if isinstance(labels,(list,tuple)) else labels
                conf=0.7
                try:
                    probs=model.predict_proba([text]); p=probs[0] if hasattr(probs,"__len__") else probs
                    arr=list(p.tolist() if hasattr(p,"tolist") else p); conf=float(max(arr)) if arr else conf
                except Exception: pass
                return {"intent":str(label),"confidence":float(conf),"router":"setfit"}
            except Exception: pass
        if mode=="setfit": return None
    if mode in ("auto","sklearn"):
        obj=_load_sklearn_router()
        if obj is not None:
            try:
                vec=obj.get("vectorizer") or obj.get("vec"); clf=obj.get("classifier") or obj.get("clf") or obj.get("model")
                if vec is not None and clf is not None:
                    X=vec.transform([text]); pred=clf.predict(X)[0]; conf=0.65
                    try:
                        if hasattr(clf,"predict_proba"): conf=float(max(clf.predict_proba(X)[0]))
                        elif hasattr(clf,"decision_function"):
                            import math
                            df=clf.decision_function(X); vals=df[0] if getattr(df,"ndim",1)>1 else df
                            exps=[math.exp(float(v)) for v in (vals.tolist() if hasattr(vals,"tolist") else list(vals))]; s=sum(exps) or 1.0
                            conf=float(max(exps)/s)
                    except Exception: pass
                    return {"intent":str(pred),"confidence":float(conf),"router":"sklearn"}
            except Exception: pass
    return None

# ── Weighted rule NLU (no deps) ──────────────────────────────────────────────

def _has_any(tl, keys):
    for k in keys:
        c = (k or "").lower()
        if not c:
            continue
        # avoid substring false positives for short tokens (hi in this, vat in reservation, etc.)
        if re.fullmatch(r"[a-z0-9&/\-']{1,4}", c) or c in ("hi","hey","pl","vat","adr","kpi","occ","pnl","ops","pos"):
            if re.search(r"(?<![a-z0-9])" + re.escape(c) + r"(?![a-z0-9])", tl):
                return True
        elif c in tl:
            return True
    return False

_INTENT_CUES = {
  "greeting_smalltalk": (1.1, ["hi","hello","hey","good morning","good afternoon","good evening","thanks","thank you","thx","how are you","who are you","what's up","whats up"]),
  "capabilities_help": (1.5, ["what can you do","how can you help","capabilities","your abilities","menu of queries","what tools","how do i phrase","boundaries","assist day to day"]),
  "property_stats": (1.4, ["how many guests","number of guests","total guests","guest count","guests do i have","guests do we have","how many customers","how many reservations","number of reservations","total reservations","reservation count","how many rooms","number of rooms","total rooms","how many bookings","quick stats","property stats","overall stats"]),
  "today_ops": (1.4, ["today's ops","todays ops","ops today","today snapshot","morning briefing","what's happening today","whats happening today","arrivals today","departures today","in-house now","in house now","front desk today"]),
  "business_partner_briefing": (1.5, ["business partner","partner briefing","business briefing","executive briefing","how is the business","how's the business","hows the business","advise me","what should i focus on","priorities for me","recommendations","opportunities and risks","partner view"]),
  "decision_support": (1.3, ["decision support","kpi","how are we doing","manager's overview","manager’s overview","executive snapshot","signals","warning signs","strengths and risks","actionable insights","briefing me","performance indicators"]),
  "compare_periods": (1.4, ["compare","vs last","versus last","this month vs","compared to last","trend vs","previous period","month over month"]),
  "outstanding_balances": (1.4, ["who owes","outstanding balance","outstanding balances","unpaid","who hasn't paid","who hasnt paid","amount due overall","receivables","money owed","collectables","overdue","accounts receivable"]),
  "inventory_alerts": (1.4, ["low stock","running low","out of stock","negative stock","overstock","inventory alert","inventory alerts","restock","stock warnings","what's low","whats low"]),
  "top_selling_items": (1.4, ["top selling","best seller","best sellers","most sold","top items sold","popular items","what sells most","top pos items"]),
  "expenses_summary": (1.3, ["expense breakdown","expenses by category","spending breakdown","cost breakdown","operating expenses summary","where did we spend"]),
  "sales_breakdown": (1.3, ["sales breakdown","revenue breakdown","pos breakdown","sales by item","sales by category","revenue mix","how did guests pay breakdown"]),
  "reservation_pipeline": (1.3, ["reservation pipeline","upcoming arrivals","upcoming reservations","bookings pipeline","reservations by status","future bookings"]),
  "housekeeping_summary": (1.3, ["housekeeping","clean rooms","dirty rooms","room status summary","rooms ready","rooms to clean"]),
  "room_availability": (1.3, ["available rooms","room availability","any rooms free","rooms free","vacant rooms","availability for"]),
  "menu_info": (1.2, ["menu","meals","what meals","dishes","meal list","recipes"]),
  "rate_plans": (1.3, ["rate plan","pricing plan","plans/packages","catalog of rate plans"]),
  "ledger_integrity": (1.4, ["health check","integrity","ledger integrity","data integrity","reconcile ledger","orphaned","mismatched records","sanity check","audit the database","consistency","books/data consistency"]),
  "recent_activity": (1.2, ["recent activity","audit log","what changed lately","recent changes"]),
  "expiring_batches": (1.5, ["expiring batch","expiring batches","expired batch","what expires","expires soon","expiry soon","nearing expiry","batches expiring","items expiring"]),
  "inventory_batches": (1.4, ["list batches","show batches","inventory batches","batch list","stock batches","lots for","list lots","inventory lots"]),
  "trace_barcode": (1.5, ["trace barcode","trace lot","trace code","scan barcode","lookup barcode","barcode lookup","lot trace","track lot","batch trace","received when","when received","barcode trace","sku trace"]),
  "app_update_status": (1.5, ["check for updates","check update","app update","update app","new version","latest version","what version","app version","software update","changelog","update status"]),
  "list_activities": (1.4, ["list activities","available activities","available trips","available excursions","available tours","activities catalog","trips catalog","excursions catalog","register activities","registered activities","registered trips","what activities","what trips can","what excursions"]),
  "activity_stats": (1.4, ["activity stats","activity statistics","trip stats","trip statistics","excursion stats","bookings stats","how many booked","most popular activity","most popular trip","most popular excursion","activity revenue","trip bookings"]),
  "guest_portfolio": (1.5, ["full portfolio","guest portfolio","360","360°","all trips","all stays","all reservations","complete history","entire history","all invoices","all his invoices","all her invoices","all their invoices","all pos orders","all orders","all activities for guest","all payments for guest","everything for guest","full history","complete guest record","lifetime","all visits"]),
  "guest_activity_bookings": (1.4, ["booked activities","booked trips","booked excursions","booked tours","activities they ordered","activities he ordered","activities she ordered","trips they booked","excursions booked","tours booked","ordered activities","activity bookings for guest","guest activities","which activities did"]),
  "guest_last_visit_spend": (1.5, ["how much did","spend on last visit","spent last time","spending last visit","total on last visit"]),
  "guest_top_items_last_visit": (1.6, ["most ordered","ordered most","ordered the most","ordering the most","buy the most","bought the most","top-ordered","biggest pos orders","topped their list","most frequently","most of during that visit","consume/purchase most","what did they order","what did he order","what did she order","items did they buy","which items did","order the most"]),
  "guest_last_visit": (1.3, ["last visit","last stay","previous visit","previous trip","last time","most recent stay","most recent visit","latest stay","latest visit","last came","most recent checked-out","previous stay","when was the last time"]),
  "guest_profile": (1.3, ["profile","everything we know","contact info","past reservations","recent stay history","background for guest","account/profile view","history with us","details and latest bookings","full profile","who are they","what's on file","what’s on file","open the profile","their details"]),
  "guest_search": (1.2, ["find guest","search guest","look up a guest","locate a guest","trying to find a guest","anyone in the system","possible matches","who matches","who do we have on file","show candidates","resembling","search guests for","locate a contact","pull up any matching guest","can you find the guest"]),
  "reservation_balance": (1.4, ["balance","owe","outstanding","amount due","settled","paid versus due","left to collect","folio balance","how much does the guest"]),
  "reservation_summary": (1.3, ["reservation summary","booking summary","details on booking","details on reservation","folio snapshot","what's going on with reservation","rundown of booking","summarize reservation","reservation details"]),
}

def rule_classify(text: str, slots: dict | None = None) -> dict:
    slots = slots or extract_slots(text)
    tl = _norm_text(text)
    rid = slots.get("reservation_id"); gid = slots.get("guest_id"); gq = slots.get("guest_query")

    # greeting/smalltalk (only if short and no data ask)
    if re.fullmatch(r"(hi|hello|hey|good morning|good afternoon|good evening|thanks|thank you|thx|yo)[!.\s]*", tl) or (len(tl.split()) <= 5 and _has_any(tl,["hi","hello","hey","thanks","thank you"])):
        if not (rid or gid or gq or slots.get("report_kind")):
            return {"intent":"greeting_smalltalk","confidence":0.95,"slots":slots,"router":"rules"}

    # unsafe (read-only assistant)
    if _has_any(tl, ["go ahead and delete","delete reservation","delete room","and refund","refund the guest","check the guest in","check in the guest","change room price","change price to","reset the super password","reset password for me","email the guest","send invoice","sync bookings","add a new room","import 500","override checkout","hack into","ignore the rules","hidden admin"]):
        return {"intent":"unsupported_or_unsafe","confidence":0.97,"slots":slots,"router":"rules"}

    def _cue_match(cue, tl):
        c = cue.lower()
        # word-boundary match for short tokens and common acronym/word cues to avoid substring false positives
        if re.fullmatch(r"[a-z0-9&/\-']{1,5}", c) or c in ("hi","hey","pl","vat","adr","kpi","occ","pnl"):
            return re.search(r"(?<![a-z0-9])" + re.escape(c) + r"(?![a-z0-9])", tl) is not None
        return c in tl

    scores = defaultdict(float)
    for intent,(w,cues) in _INTENT_CUES.items():
        for c in cues:
            if _cue_match(c, tl): scores[intent] += w
        for c in cues[:6]:
            if len(c)>=6 and not _cue_match(c, tl):
                cs=set(c.split()); ts=_token_set(tl)
                if cs and len(cs & ts)/len(cs) >= 0.8: scores[intent] += w*0.6
    # structural boosts
    if gid or gq or "guest #" in tl or "customer" in tl or "visitor" in tl:
        for k in ("guest_profile","guest_portfolio","guest_last_visit","guest_top_items_last_visit","guest_last_visit_spend","guest_activity_bookings","guest_search"): scores[k]+=0.2
    if rid or "reservation #" in tl or "booking #" in tl or "res #" in tl:
        for k in ("reservation_summary","reservation_balance"): scores[k]+=0.25
    rk = slots.get("report_kind")
    if rk:
        mapk={"pl":"report_pl","cash_flow":"report_cash_flow","vat":"report_vat","payment_methods":"report_payment_methods","occupancy":"report_occupancy","daily_ops":"report_daily_ops"}
        if rk in mapk: scores[mapk[rk]] += 1.2
    if rk=="occupancy":
        if _has_any(tl, ["report","adr","revpar","performance","room-nights","metrics","revenue-per-room"]): scores["report_occupancy"] += 0.6
        else: scores["occupancy_summary"] += 0.4
    if scores.get("guest_top_items_last_visit",0)>0 or scores.get("guest_last_visit_spend",0)>0:
        scores["guest_last_visit"] += 0.1
    if _has_any(tl, ["when did","when was the last time","last time they stayed","last stay with us","previous visit here"]) and scores.get("guest_profile",0)>0 and scores.get("guest_top_items_last_visit",0)==0:
        scores["guest_last_visit"] += 0.5
    # ambiguity
    if _has_any(tl, ["he ordered","she ordered","they ordered","his last visit","her last visit","their last visit","that guest","they owe","do they owe","his portfolio","her portfolio"]) and not gid and not gq and not slots.get("context",{}).get("guest_id"):
        scores["ambiguous_needs_clarification"] += 1.2
    if tl.strip() in {"balance","show me the balance","what's the balance","what is the balance"} and not rid:
        scores["ambiguous_needs_clarification"] += 1.3
    if ("report for" in tl or "give me the report" in tl) and not rk and not _has_any(tl, ["daily ops","arrivals","departures"]):
        scores["ambiguous_needs_clarification"] += 0.8
    # compound detection
    report_hits = sum(1 for k in ["report_pl","report_cash_flow","report_vat","report_payment_methods","report_occupancy","report_daily_ops"] if scores.get(k,0)>0)
    guest_chain = (gid or gq or "guest" in tl) and sum(1 for k in ["guest_profile","guest_portfolio","guest_last_visit","guest_top_items_last_visit","guest_last_visit_spend","guest_activity_bookings"] if scores.get(k,0)>0) >= 2
    res_chain = rid and scores.get("reservation_balance",0)>0 and scores.get("reservation_summary",0)>0
    if report_hits>=2 or guest_chain or res_chain or _has_any(tl, ["and also"," as well as"," plus ","p&l and cash flow","compare this month vs","list rate plans and also"]):
        scores["compound_multi_intent"] += 1.0

    if not scores:
        if _has_any(tl, ["report","revenue","expenses","profit"]): return {"intent":"report_pl","confidence":0.5,"slots":slots,"router":"rules"}
        if "guest" in tl and _has_any(tl, ["find","search","look up"]): return {"intent":"guest_search","confidence":0.5,"slots":slots,"router":"rules"}
        return {"intent":"capabilities_help","confidence":0.2,"slots":slots,"router":"rules","low_confidence":True}

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best, best_s = ranked[0]
    second_s = ranked[1][1] if len(ranked)>1 else 0.0
    margin = best_s - second_s
    conf = min(0.98, 0.45 + best_s*0.18 + margin*0.12)
    if best_s < 0.9: conf = min(conf, 0.55)
    if margin < 0.25 and best_s < 1.4: conf = min(conf, 0.6)
    low = conf < 0.45 and not (rid or gid)
    if best=="capabilities_help" and conf < 0.55: low = True
    return {"intent":best,"confidence":round(conf,3),"slots":slots,"router":"rules","margin":round(margin,3),"low_confidence":low,"candidates":ranked[:3]}

def classify_intent(text: str, history=None) -> dict:
    slots = extract_slots(text, history)
    ex = exemplar_match(text)
    if ex:
        ex_slots = dict(ex.get("slots") or {}); ex_slots.update({k:v for k,v in slots.items() if v not in (None,"",[])})
        ex_slots["context"]=slots.get("context",{})
        return {"intent":ex.get("intent"),"confidence":ex.get("confidence",0.9),"slots":ex_slots,"router":ex.get("source","exemplar")}
    ml = _predict_intent_ml(text)
    rl = rule_classify(text, slots)
    if ml and ml.get("intent") in INTENT_LABELS:
        if rl.get("intent")=="unsupported_or_unsafe": return rl
        if ml.get("confidence",0) >= 0.6 and not rl.get("low_confidence"):
            return {"intent":ml["intent"],"confidence":float(ml.get("confidence",0.7)),"slots":slots,"router":ml.get("router","ml")}
        if rl.get("confidence",0) >= ml.get("confidence",0): return rl
        return {"intent":ml["intent"],"confidence":float(ml.get("confidence",0.6)),"slots":slots,"router":ml.get("router","ml")}
    return rl

# ── Execution ────────────────────────────────────────────────────────────────

def _guest_disambiguation(candidates, q=None):
    data = {"candidates": candidates, "query": q}
    lines = ["I found more than one guest matching that (real records). Which one did you mean? Please reply with the guest ID:"]
    for g in candidates[:8]:
        try:
            name = g.get("name") or g.get("canonical_name") or "Guest"
            lines.append(f"- **#{g['id']} {name}** — phone: {g.get('phone') or '-'}, email: {g.get('email') or '-'}, stays: {g.get('stays', g.get('reservation_count', 0))}, last stay: {g.get('last_stay') or '-'}")
        except Exception:
            lines.append(f"- {g}")
    lines.append("For example: `guest #12 full portfolio`.")
    return {"ok":True,"intent":"ambiguous_needs_clarification","needs_clarification":True,"tools_used":["search_guests"] if q else [],"data":data,"answer":"\n".join(lines)}

def _answer(intent: str, tools_used, data, title="Result", ok=True, **extra):
    ans = _format_for_intent(intent, data if isinstance(data, dict) else {}, fallback_title=title)
    ans = _apply_proactive(intent, data if isinstance(data, dict) else {}, ans)
    res = {"ok": ok, "intent": intent, "tools_used": tools_used or [], "data": data, "answer": ans}
    res.update(extra)
    return res

def _execute_single(intent: str, text: str, slots: dict):
    tools_used=[]; start,end = slots.get("date_start"), slots.get("date_end")
    if not start or not end: start,end = _parse_date_range(text)
    rid = slots.get("reservation_id")

    if intent == "greeting_smalltalk":
        tl = _norm_text(text)
        if any(k in tl for k in ["thank", "thx"]):
            ans = "You're very welcome! If you need anything else — reports, guest portfolios, who owes money, ops, or a business briefing — I'm here to help (all from real app data)."
        else:
            ans = "Hi! I’m your property manager assistant and business partner. I can help with reports, operations, guests (profiles/portfolios/last visits/bookings), reservations, balances, inventory, activities, and more — all grounded in real app data. What would you like to know?"
        return {"ok":True,"intent":intent,"tools_used":[], "data":{}, "answer":ans}

    # quick keyword routing for batch/trace/health/update intents (grounded tools)
    tl = _norm_text(text)
    if any(k in tl for k in ["doctor report", "health check", "health diagnostics", "system health", "backup readiness", "restore readiness", "backup and restore", "printer readiness", "batch stock mismatch", "batch stock consistency", "db health", "database health"]):
        data=execute_tool("get_health_diagnostics",{}); tools_used.append("get_health_diagnostics")
        return _answer("health_diagnostics", tools_used, data)
    if any(k in tl for k in ["expir", "expire soon", "expiring", "expired batch", "what expires", "expiry"]):
        days = None
        import re as _re
        m = _re.search(r"(\d+)\s*day", tl)
        if m:
            try: days = int(m.group(1))
            except Exception: days = None
        data=execute_tool("get_expiring_batches",{"within_days": days, "include_expired": True}); tools_used.append("get_expiring_batches")
        return _answer("expiring_batches", tools_used, data)
    if any(k in tl for k in ["trace barcode", "trace lot", "trace code", "scan barcode", "lookup barcode", "barcode lookup", "lot trace", "track lot", "received when", "when received", "batch trace"]):
        code = slots.get("barcode") or slots.get("sku") or slots.get("code")
        if not code:
            import re as _re
            m = _re.search(r"(?:barcode|sku|lot|code|trace)\s*[:#]?\s*([A-Za-z0-9._\-]+)", text or "", _re.I)
            if m: code = m.group(1)
        if not code:
            import re as _re
            toks = _re.findall(r"[A-Za-z0-9][A-Za-z0-9._\-]{2,}", text or "")
            if toks: code = toks[-1]
        data=execute_tool("trace_barcode",{"code": code}); tools_used.append("trace_barcode")
        return _answer("trace_barcode", tools_used, data)
    if any(k in tl for k in ["list batches", "show batches", "inventory batches", "batch list", "lots for", "stock batches"]):
        data=execute_tool("list_inventory_batches",{"item_id": slots.get("item_id")}); tools_used.append("list_inventory_batches")
        return _answer("inventory_batches", tools_used, data)

    if any(k in tl for k in ["check for updates", "check update", "app update", "update app", "new version", "latest version", "what version", "app version", "software update"]):
        data=execute_tool("get_app_update_status",{}); tools_used.append("get_app_update_status")
        return _answer("app_update_status", tools_used, data)

    if intent=="capabilities_help":
        data=execute_tool("list_capabilities",{}); tools_used.append("list_capabilities")
        return _answer(intent, tools_used, data)

    if intent=="unsupported_or_unsafe":
        ans = ("I can help with **read-only** questions grounded in real app data (reports, guests/portfolios, reservations, balances, inventory, activities, ops, insights). "
               "I can’t perform actions, make changes, or guess/invent information. "
               "If you tell me what you’d like to look up (e.g. a guest, reservation, report, or date range), I’ll fetch it for you.")
        return {"ok":False,"intent":intent,"tools_used":[],"data":{"reason":"unsafe_or_out_of_scope"},"answer":ans}

    if intent=="ambiguous_needs_clarification":
        tl=_norm_text(text)
        if rid and _has_any(tl,["balance","owe","outstanding","due"]):
            return _execute_single("reservation_balance", text, slots)
        if _has_any(tl,["balance","owe","outstanding","due"]) and not rid:
            return {"ok":True,"intent":intent,"needs_clarification":True,"tools_used":[],"data":{"missing":"reservation_id","suggestion":"overall_ar"},
                    "answer":"Do you mean a **specific reservation’s balance** (e.g. `reservation #12 balance`) or **overall outstanding balances** (who owes money across all reservations)? Tell me which one and I’ll pull the real figures."}
        if _has_any(tl,["ordered","last visit","last stay","profile","spend","portfolio","invoice","activity","activities","trip","excursion","when did they","he ","she ","they ","his ","her ","their "]) and not (slots.get("guest_id") or slots.get("guest_query")):
            return {"ok":True,"intent":intent,"needs_clarification":True,"tools_used":[],"data":{"missing":"guest"},
                    "answer":"Which guest do you mean? Please share a **guest ID** (e.g. `guest #3`) or a name/phone fragment so I can look them up in the real records."}
        if (("report" in tl) or tl.startswith("report")) and not slots.get("report_kind") and not rid:
            return {"ok":True,"intent":intent,"needs_clarification":True,"tools_used":[],"data":{"missing":"report_kind"},
                    "answer":"Which report would you like — **P&L, cash flow, VAT, payment methods, occupancy, or daily ops** — and for what date/range (e.g. `P&L last month` or `daily ops today`)?"}
        return {"ok":True,"intent":intent,"needs_clarification":True,"tools_used":[],"data":{"missing":"context"},
                "answer":"I want to make sure I get this right — could you rephrase with a bit more detail? I can help with briefings, property/ops stats, reports & comparisons, who owes money, inventory alerts, top sellers, expenses/sales, expiring batches and batch/lot lists, barcode/lot traceability (received/expiry/movements), guests (search/profile/full portfolio/last visit/orders/spend/activities), reservations/balances/pipeline, housekeeping, availability, activities, menus, rate plans, integrity/doctor/health diagnostics (including backup/restore readiness and batch-stock consistency), and app version/update status."}

    if intent=="property_stats":
        data=execute_tool("get_property_stats",{}); tools_used.append("get_property_stats")
        return _answer(intent, tools_used, data)
    if intent=="today_ops":
        data=execute_tool("get_today_ops",{}); tools_used.append("get_today_ops")
        return _answer(intent, tools_used, data)
    if intent=="business_partner_briefing":
        data=execute_tool("business_partner_briefing",{"start":start,"end":end}); tools_used.append("business_partner_briefing")
        return _answer(intent, tools_used, data)
    if intent=="decision_support":
        data=execute_tool("get_decision_support",{"start":start,"end":end}); tools_used.append("get_decision_support")
        return _answer(intent, tools_used, data, title=f"Decision support ({start} → {end})")
    if intent=="compare_periods":
        data=execute_tool("compare_periods",{"start":start,"end":end}); tools_used.append("compare_periods")
        return _answer(intent, tools_used, data)
    if intent=="outstanding_balances":
        data=execute_tool("get_outstanding_balances",{}); tools_used.append("get_outstanding_balances")
        return _answer(intent, tools_used, data)
    if intent=="inventory_alerts":
        data=execute_tool("get_inventory_alerts",{}); tools_used.append("get_inventory_alerts")
        return _answer(intent, tools_used, data)
    if intent in ("expiring_batches","get_expiring_batches"):
        days = slots.get("days") or slots.get("within_days")
        try: days = int(days) if days not in (None, "") else None
        except Exception: days = None
        data=execute_tool("get_expiring_batches",{"within_days": days, "include_expired": True}); tools_used.append("get_expiring_batches")
        return _answer("expiring_batches", tools_used, data)
    if intent in ("inventory_batches","list_inventory_batches"):
        item_id = slots.get("item_id")
        data=execute_tool("list_inventory_batches",{"item_id": item_id}); tools_used.append("list_inventory_batches")
        return _answer("inventory_batches", tools_used, data)
    if intent in ("trace_barcode","barcode_trace"):
        code = slots.get("barcode") or slots.get("sku") or slots.get("code") or slots.get("lot_code") or slots.get("query") or slots.get("guest_query")
        # try to extract code-like token from text
        if not code:
            import re as _re
            m = _re.search(r"(?:barcode|sku|lot|code|trace)\s*[:#]?\s*([A-Za-z0-9._\-]+)", text, _re.I)
            if m: code = m.group(1)
        if not code:
            # fallback: take last alnum token
            import re as _re
            toks = _re.findall(r"[A-Za-z0-9][A-Za-z0-9._\-]{2,}", text or "")
            if toks: code = toks[-1]
        data=execute_tool("trace_barcode",{"code": code}); tools_used.append("trace_barcode")
        return _answer("trace_barcode", tools_used, data)
    if intent=="top_selling_items":
        data=execute_tool("get_top_selling_items",{"start":start,"end":end,"limit":10}); tools_used.append("get_top_selling_items")
        return _answer(intent, tools_used, data)
    if intent=="expenses_summary":
        data=execute_tool("get_expenses_summary",{"start":start,"end":end}); tools_used.append("get_expenses_summary")
        return _answer(intent, tools_used, data)
    if intent=="sales_breakdown":
        data=execute_tool("get_sales_breakdown",{"start":start,"end":end}); tools_used.append("get_sales_breakdown")
        return _answer(intent, tools_used, data)
    if intent=="reservation_pipeline":
        data=execute_tool("get_reservation_pipeline",{}); tools_used.append("get_reservation_pipeline")
        return _answer(intent, tools_used, data)
    if intent=="housekeeping_summary":
        data=execute_tool("get_housekeeping_summary",{}); tools_used.append("get_housekeeping_summary")
        return _answer(intent, tools_used, data)
    if intent=="room_availability":
        a_start = slots.get("date_start") or start; a_end = slots.get("date_end") or end
        data=execute_tool("get_room_availability",{"start":a_start,"end":a_end}); tools_used.append("get_room_availability")
        return _answer(intent, tools_used, data)
    if intent=="menu_info":
        data=execute_tool("get_menu_info",{}); tools_used.append("get_menu_info")
        return _answer(intent, tools_used, data)
    if intent=="rate_plans":
        data=execute_tool("list_rate_plans",{}); tools_used.append("list_rate_plans")
        return _answer(intent, tools_used, data)
    if intent=="ledger_integrity":
        data=execute_tool("get_health_diagnostics",{}); tools_used.append("get_health_diagnostics")
        return _answer("ledger_integrity", tools_used, data)
    if intent in ("health_diagnostics","doctor_report"):
        data=execute_tool("get_health_diagnostics",{}); tools_used.append("get_health_diagnostics")
        return _answer("health_diagnostics", tools_used, data)
        return _answer(intent, tools_used, data)
    if intent=="recent_activity":
        data=execute_tool("get_recent_activity",{}); tools_used.append("get_recent_activity")
        return _answer(intent, tools_used, data)
    if intent in ("report_pl","report_cash_flow","report_vat","report_payment_methods","report_occupancy","report_daily_ops"):
        kind=intent.replace("report_","")
        data=execute_tool("get_report",{"kind":kind,"start":start,"end":end}); tools_used.append("get_report")
        return _answer(intent, tools_used, data)
    if intent=="occupancy_summary":
        data=execute_tool("get_occupancy_summary",{"start":start,"end":end}); tools_used.append("get_occupancy_summary")
        return _answer(intent, tools_used, data)
    if intent=="list_activities":
        data=execute_tool("list_activities",{}); tools_used.append("list_activities")
        return _answer(intent, tools_used, data)
    if intent=="activity_stats":
        data=execute_tool("get_activity_stats",{}); tools_used.append("get_activity_stats")
        return _answer(intent, tools_used, data)

    # reservations
    if intent in ("reservation_balance","reservation_summary"):
        if not rid: return _execute_single("ambiguous_needs_clarification", text, slots)
        name="get_guest_balance" if intent=="reservation_balance" else "get_reservation_summary"
        data=execute_tool(name,{"reservation_id":rid}); tools_used.append(name)
        return _answer(intent, tools_used, data, title=f"Reservation #{rid}")

    # guests
    if intent in ("guest_search","guest_profile","guest_portfolio","guest_last_visit","guest_top_items_last_visit","guest_last_visit_spend","guest_activity_bookings"):
        guests, explicit_id, q = resolve_guest(text, slots)
        if intent=="guest_search":
            query=q or slots.get("guest_query") or re.sub(r"(?i)\b(find|search|look up|locate|guest|guests|customer|for|named?|called|who do we have|show|matches|matching)\b"," ",text)
            query=re.sub(r"\s+"," ",query).strip(" ?.,")
            data=execute_tool("search_guests",{"query":query,"limit":10}); tools_used.append("search_guests")
            return _answer(intent, tools_used, data)
        if explicit_id and not guests:
            return {"ok":False,"intent":intent,"tools_used":[],"data":{"error":f"Guest #{explicit_id} not found"},
                    "answer":f"I couldn’t find **guest #{explicit_id}** in the real records. Want to try a name/phone search instead?"}
        if len(guests)==1 or (explicit_id and len(guests)==1):
            g=guests[0]; gid=g["id"] if isinstance(g,dict) else explicit_id
            gname=(g.get("name") or g.get("canonical_name") or "") if isinstance(g,dict) else ""
            if intent=="guest_top_items_last_visit":
                data=execute_tool("get_guest_top_items_last_visit",{"guest_id":gid,"limit":5}); tools_used.append("get_guest_top_items_last_visit")
            elif intent=="guest_last_visit_spend":
                data=execute_tool("get_guest_last_visit_spend",{"guest_id":gid}); tools_used.append("get_guest_last_visit_spend")
            elif intent=="guest_last_visit":
                data=execute_tool("get_guest_last_visit",{"guest_id":gid}); tools_used.append("get_guest_last_visit")
            elif intent=="guest_portfolio":
                data=execute_tool("get_guest_portfolio",{"guest_id":gid}); tools_used.append("get_guest_portfolio")
            elif intent=="guest_activity_bookings":
                data=execute_tool("list_activity_bookings",{"guest_id":gid}); tools_used.append("list_activity_bookings")
            else:
                data=execute_tool("get_guest_profile",{"guest_id":gid}); tools_used.append("get_guest_profile")
            res = _answer(intent, tools_used, data)
            res["resolved_guest_id"] = gid
            return res
        if len(guests)>1: return _guest_disambiguation(guests, q or slots.get("guest_query"))
        if slots.get("guest_id") or slots.get("guest_query"):
            return {"ok":False,"intent":intent,"tools_used":[],"data":{"query":slots.get("guest_query"),"guest_id":slots.get("guest_id")},
                    "answer":"I couldn’t find a matching guest in the real records. Try a different name/phone/email fragment or a specific guest ID (e.g. `guest #3`)."}
        return _execute_single("ambiguous_needs_clarification", text, slots)

    # fallback
    data=execute_tool("list_capabilities",{}); tools_used.append("list_capabilities")
    ans = "I couldn’t confidently map that request to a supported, grounded action. " + _fmt_capabilities(data)
    return {"ok":True,"intent":"capabilities_help","tools_used":tools_used,"data":data,"answer":ans}

def _execute_compound(text: str, slots: dict):
    tl=_norm_text(text); subs=[]
    if slots.get("reservation_id") and _has_any(tl,["balance","owe","outstanding","due"]): subs.append(("reservation_balance",slots))
    if slots.get("reservation_id") and _has_any(tl,["summary","details","folio","overview"]): subs.append(("reservation_summary",slots))
    if _has_any(tl,["business partner","partner briefing","executive briefing","advise me","recommendations","what should i focus"]): subs.append(("business_partner_briefing",slots))
    if _has_any(tl,["decision support","kpi","how are we doing","signals"]): subs.append(("decision_support",slots))
    for intent,rk in [("report_pl","pl"),("report_cash_flow","cash_flow"),("report_vat","vat"),("report_payment_methods","payment_methods"),("report_occupancy","occupancy"),("report_daily_ops","daily_ops")]:
        cues={"pl":["p&l","pnl","profit and loss","profit","net profit"],"cash_flow":["cash flow","cashflow"],"vat":["vat","value-added tax","tax report"],"payment_methods":["payment method","payments by method","payment channels"],"occupancy":["occupancy","adr","revpar","how full","how busy"],"daily_ops":["daily ops","arrivals","departures","in-house","in house"]}
        if _has_any(tl,cues[rk]): subs.append((intent,slots))
    if _has_any(tl,["compare","vs last","versus last","previous period"]): subs.append(("compare_periods",slots))
    if _has_any(tl,["who owes","outstanding","unpaid","receivables"]): subs.append(("outstanding_balances",slots))
    if _has_any(tl,["low stock","inventory alert","running low","overstock"]): subs.append(("inventory_alerts",slots))
    if _has_any(tl,["top selling","best seller","most sold"]): subs.append(("top_selling_items",slots))
    if _has_any(tl,["expense breakdown","expenses by category","where did we spend"]): subs.append(("expenses_summary",slots))
    if _has_any(tl,["rate plan","pricing plan"]): subs.append(("rate_plans",slots))
    if _has_any(tl,["doctor report","health check","health diagnostics","backup readiness","restore readiness","printer readiness","batch stock mismatch","batch stock consistency","integrity","ledger integrity"]): subs.append(("health_diagnostics",slots))
    if _has_any(tl,["expiring batch","expired batch","what expires","expires soon","expiry"]): subs.append(("expiring_batches",slots))
    if _has_any(tl,["list batches","show batches","inventory batches","batch list","stock batches","lots for"]): subs.append(("inventory_batches",slots))
    if _has_any(tl,["trace barcode","trace lot","trace code","lookup barcode","barcode lookup","lot trace","track lot","batch trace"]): subs.append(("trace_barcode",slots))
    if _has_any(tl,["check for updates","app update","new version","latest version","app version","changelog","update status"]): subs.append(("app_update_status",slots))
    if _has_any(tl,["housekeeping"]): subs.append(("housekeeping_summary",slots))
    if _has_any(tl,["pipeline","upcoming arrivals"]): subs.append(("reservation_pipeline",slots))
    if _has_any(tl,["list activities","available activities","activities catalog","registered activities","registered trips","available excursions"]): subs.append(("list_activities",slots))
    if _has_any(tl,["activity stats","activity statistics","trip stats","booking statistics"]): subs.append(("activity_stats",slots))
    # guest chain
    guests, explicit_id, q = resolve_guest(text, slots)
    if slots.get("guest_id") or q or _has_any(tl,["find guest","search guest"]):
        if _has_any(tl,["find guest","search guest","search for"]) and not explicit_id and len(guests)!=1:
            subs.append(("guest_search",slots))
        if explicit_id or len(guests)==1:
            g_slots=dict(slots)
            if len(guests)==1 and not explicit_id: g_slots["guest_id"]=guests[0]["id"]
            if _has_any(tl,["full portfolio","360","all trips","all stays","all reservations","all invoices","complete history","entire history","all pos orders"]): subs.append(("guest_portfolio",g_slots))
            if _has_any(tl,["profile","everything we know","history"]): subs.append(("guest_profile",g_slots))
            if _has_any(tl,["last visit","last stay","most recent","when did","last time"]): subs.append(("guest_last_visit",g_slots))
            if _has_any(tl,["ordered most","most ordered","top-ordered","buy the most","topped their list","what did they order"]): subs.append(("guest_top_items_last_visit",g_slots))
            if _has_any(tl,["booked activities","booked excursions","booked tours","activities they ordered","trips they booked"]): subs.append(("guest_activity_bookings",g_slots))
            if _has_any(tl,["spend","how much did"]): subs.append(("guest_last_visit_spend",g_slots))
        elif len(guests)>1 and not explicit_id:
            return _guest_disambiguation(guests,q)
    seen=set(); uniq=[]
    for it,sl in subs:
        key=(it,sl.get("guest_id"),sl.get("reservation_id"),sl.get("report_kind"))
        if key in seen: continue
        seen.add(key); uniq.append((it,sl))
    if not uniq: return _execute_single("ambiguous_needs_clarification", text, slots)
    if len(uniq)==1: return _execute_single(uniq[0][0], text, uniq[0][1])
    tools_used=[]; parts=[]; data_all={}; ok=True
    parts.append("Sure — here’s what I found (grounded in real data):")
    for it,sl in uniq[:6]:
        res=_execute_single(it,text,sl)
        tools_used.extend(res.get("tools_used") or [])
        label = it.replace("_"," ").title()
        parts.append(f"\n### {label}\n{res.get('answer','')}")
        data_all[it]=res.get("data")
        ok = ok and bool(res.get("ok",True))
    return {"ok":ok,"intent":"compound_multi_intent","tools_used":list(dict.fromkeys(tools_used)),"data":data_all,"answer":"\n".join(parts)}

# ── Local-only LLM config & runtime (no external/OpenAI calls) ──────────────

def _llm_config():
    """Local-only config. No external endpoints or OpenAI API calls."""
    try:
        provider=(services.get_setting("assistant_provider","auto") or "auto").strip().lower()
        response_mode=(services.get_setting("assistant_response_mode","talking") or "talking").strip().lower()
        model_path=(services.get_setting("assistant_local_model_path","") or os.environ.get("ASSISTANT_LOCAL_MODEL_PATH","") or "").strip()
        n_ctx=int(services.get_setting("assistant_local_n_ctx","2048") or 2048)
    except Exception:
        provider,response_mode,model_path,n_ctx="auto","talking","",2048
    # normalize: anything "openai"/external -> treated as local/auto (no external)
    if provider in ("openai","openai_compatible","external"):
        provider = "auto"
    local_available = False
    try:
        import llama_cpp  # type: ignore
        if model_path and Path(model_path).expanduser().exists():
            local_available = True
    except Exception:
        local_available = False
    # 'enabled' means local model runtime available; deterministic talking is always available separately
    enabled = provider != "deterministic_only" and local_available
    return {"provider":provider,"response_mode":response_mode,"model_path":model_path,"n_ctx":n_ctx,
            "local_available": local_available, "enabled": enabled, "external": False}

def _get_local_llm():
    global _LOCAL_LLM_CACHE
    cfg = _llm_config()
    if not cfg.get("local_available"): return None
    if _LOCAL_LLM_CACHE is not None: return _LOCAL_LLM_CACHE or None
    try:
        from llama_cpp import Llama  # type: ignore
        mp = str(Path(cfg["model_path"]).expanduser())
        _LOCAL_LLM_CACHE = Llama(model_path=mp, n_ctx=int(cfg.get("n_ctx") or 2048), verbose=False)
        return _LOCAL_LLM_CACHE
    except Exception:
        _LOCAL_LLM_CACHE = False
        return None

def _local_llm_generate(prompt: str, max_tokens: int = 512, temperature: float = 0.2) -> str | None:
    llm = _get_local_llm()
    if llm is None: return None
    try:
        out = llm(prompt, max_tokens=max_tokens, temperature=temperature, stop=["</s>","User:","Manager question:"])
        if isinstance(out, dict):
            txt = ((out.get("choices") or [{}])[0].get("text") or "").strip()
            return txt or None
        return str(out).strip() or None
    except Exception:
        return None

def talking_rephrase(message: str, core_result: dict, config: dict | None = None) -> str | None:
    """Local-only friendly rephrase. Uses local GGUF model if available, else deterministic formatter."""
    config = config or _llm_config()
    core_answer=(core_result or {}).get("answer") or ""
    data=(core_result or {}).get("data")
    # Try local model first (if enabled)
    if config.get("enabled"):
        try:
            data_str=json.dumps(data,ensure_ascii=False,default=str)[:3500]
        except Exception:
            data_str=str(data)[:3500]
        prompt = (
            "You are a friendly, clear property-manager business partner assistant. "
            "Rewrite the grounded answer below for the manager in warm, natural English. "
            "RULES: Use ONLY the provided grounded data/answer. Do NOT invent or guess any facts, numbers, names, dates, IDs, items, or actions. "
            "Keep all IDs/dates/amounts accurate. Be concise, answer-first, use short bullets when helpful, and include 1 helpful follow-up suggestion if relevant.\n\n"
            f"Manager question: {message}\n\nGrounded answer:\n{core_answer}\n\nGrounded data (JSON):\n{data_str}\n\nFriendly reply:"
        )
        txt = _local_llm_generate(prompt, max_tokens=500, temperature=0.2)
        if txt:
            return txt.strip()
    # Deterministic local fallback (always available)
    return _local_talking_rephrase(message, core_result)

def deterministic_route(message: str) -> dict | None:
    text=(message or "").strip()
    if not text: return None
    slots=extract_slots(text)
    cls=rule_classify(text,slots)
    intent=cls.get("intent","capabilities_help"); conf=cls.get("confidence",0.0)
    if cls.get("low_confidence") and conf < 0.4 and not (slots.get("reservation_id") or slots.get("guest_id")):
        return None
    if intent=="compound_multi_intent": res=_execute_compound(text,slots)
    else: res=_execute_single(intent,text,slots)
    res.setdefault("source","core_deterministic"); res.setdefault("ok",True); res.setdefault("tools_used",[])
    res["intent"]=res.get("intent") or intent; res["confidence"]=conf; res["slots"]=slots; res["router"]=cls.get("router","rules")
    return res

def llm_route(message: str, history: list[dict] | None = None, config: dict | None = None) -> dict | None:
    """Local-only 'full' route: uses local GGUF if available for phrasing, else deterministic tool routing. No external calls."""
    config=config or _llm_config()
    # Always produce a grounded deterministic/tool result
    slots=extract_slots(message, history)
    cls=classify_intent(message, history)
    intent=cls.get("intent") or "capabilities_help"
    slots={**slots, **(cls.get("slots") or {})}
    if intent=="compound_multi_intent": core=_execute_compound(message, slots)
    else: core=_execute_single(intent, message, slots)
    core.setdefault("source","local_grounded"); core["intent"]=core.get("intent") or intent
    core["confidence"]=cls.get("confidence",0.0); core["slots"]=slots; core["router"]=cls.get("router","rules")
    # optionally polish with local model
    if config.get("enabled") and core.get("ok") and not core.get("needs_clarification"):
        polished = talking_rephrase(message, {**core, "core_answer": core.get("answer")}, config)
        if polished: core["answer"]=polished; core["source"]="local_llm+"+core.get("source","core"); core["talking_model"]="local_gguf"
    else:
        core["source"]="local_grounded_core"
    return core

def chat(message: str, history: list[dict] | None = None) -> dict:
    message=(message or "").strip()
    if not message:
        return {"ok":False,"source":"none","tools_used":[],"data":None,"answer":"Hi! How can I help? Ask me about reports, guests, reservations, operations, or a business briefing — I’ll answer from real app data."}
    try: services.touch_session()
    except Exception: pass
    config=_llm_config(); mode=config.get("response_mode") or "talking"; provider=config.get("provider") or "auto"

    if mode=="full_llm":
        res=llm_route(message, history=history, config=config)
        if res: res["response_mode"]="full_llm_local"; return res

    slots=extract_slots(message, history)
    cls=classify_intent(message, history)
    intent=cls.get("intent") or "capabilities_help"; confidence=float(cls.get("confidence") or 0.0)
    slots={**slots, **(cls.get("slots") or {})}
    strong = bool(slots.get("reservation_id") or slots.get("guest_id") or confidence>=0.85)

    if intent=="compound_multi_intent":
        core=_execute_compound(message, slots)
    elif confidence < 0.45 and not strong and intent not in ("capabilities_help","unsupported_or_unsafe","greeting_smalltalk"):
        # stay local: clarify rather than calling external llm
        core=_execute_single("ambiguous_needs_clarification", message, slots); core["intent"]=intent
    else:
        if intent=="capabilities_help" and confidence < 0.5 and cls.get("low_confidence"):
            core=_execute_single("ambiguous_needs_clarification", message, slots); core["intent"]="ambiguous_needs_clarification"
        else:
            core=_execute_single(intent, message, slots)

    core.setdefault("source","core_deterministic"); core["intent"]=core.get("intent") or intent
    core["confidence"]=confidence; core["slots"]=slots; core["router"]=cls.get("router","rules")
    core["response_mode"]=mode; core["core_answer"]=core.get("answer"); core["core_source"]=core.get("source")

    # Local "talking" polish (deterministic always, optional local GGUF) — no external calls
    if mode in ("talking","auto","local") and core.get("ok") and not core.get("needs_clarification") and intent not in ("greeting_smalltalk",):
        polished = talking_rephrase(message, core, config)
        if polished and polished.strip():
            # ensure grounded: if model somehow drops key, prefer core; here we trust but keep core data
            core["answer"]=polished
            core["source"]=f"talking_local+{core.get('core_source','core')}"
            if config.get("enabled"): core["talking_model"]="local_gguf"
            else: core["talking_model"]="deterministic"
        else:
            core["source"]=core.get("core_source") or core.get("source")
    elif mode=="core":
        core["source"]=core.get("core_source") or core.get("source")

    # propagate resolved guest for context
    if core.get("resolved_guest_id"):
        try: core.setdefault("slots",{})["guest_id"] = core["resolved_guest_id"]
        except Exception: pass
    return core
