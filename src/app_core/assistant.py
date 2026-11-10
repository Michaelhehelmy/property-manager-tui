"""Manager assistant: grounded read-only core + no-dep NLU + business-partner insights.

- Facts come ONLY from real backend tools/services/DB (read-only). No hallucinations.
- No new dependencies: stdlib + existing app modules only.
- Tiered routing: instant exemplar corrections > weighted rule/slot scoring > optional sklearn/setfit if available.
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
    "capabilities_help", "property_stats", "today_ops", "business_partner_briefing", "decision_support",
    "report_pl", "report_cash_flow", "report_vat", "report_payment_methods", "report_occupancy", "report_daily_ops",
    "occupancy_summary", "compare_periods", "outstanding_balances", "inventory_alerts", "top_selling_items",
    "expenses_summary", "sales_breakdown", "reservation_pipeline", "housekeeping_summary", "room_availability",
    "menu_info", "rate_plans", "ledger_integrity", "guest_search", "guest_profile", "guest_last_visit",
    "guest_top_items_last_visit", "guest_last_visit_spend", "reservation_summary", "reservation_balance",
    "recent_activity", "ambiguous_needs_clarification", "unsupported_or_unsafe", "compound_multi_intent",
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
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()

def _fmt_money(x):
    try:
        return f"{float(x):,.2f}"
    except Exception:
        return str(x)

def _parse_iso(s):
    try:
        return datetime.strptime(s, DATE_FMT).date()
    except Exception:
        return None

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
    if not isinstance(history, list):
        return ctx
    for h in reversed(history):
        if h.get("role") == "assistant":
            slots = h.get("slots") or {}
            for k in ("guest_id", "reservation_id", "date_start", "date_end"):
                if slots.get(k) and k not in ctx:
                    ctx[k] = slots.get(k)
            if h.get("intent") and "last_intent" not in ctx:
                ctx["last_intent"] = h.get("intent")
        if len(ctx) >= 5:
            break
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
    # report kind hints
    rk = None
    if any(k in tl for k in ["p&l", "pnl", "profit and loss", "profit-wise", "bottom line", "net profit", "income versus outgoings", "earnings and costs", "revenue beat expenses", "financial performance", "sales in, costs out"]):
        rk = "pl"
    elif any(k in tl for k in ["cash flow", "cashflow", "cash in", "cash out", "money movement", "cash-positive", "liquidity", "ins and outs of cash"]):
        rk = "cash_flow"
    elif any(k in tl for k in ["vat", "value-added tax", "tax report", "output", "input tax"]):
        rk = "vat"
    elif any(k in tl for k in ["payment method", "payments by method", "paid us", "by cash, card", "split by cash", "payment channels", "settlement methods", "how did guests pay", "collections by method", "mix of settlement", "bank transfers or cash"]):
        rk = "payment_methods"
    elif any(k in tl for k in ["daily ops", "arrivals", "departures", "in-house", "in house", "check-ins", "checkouts", "check outs", "handover", "front-desk style ops", "stayovers", "ops picture", "ops summary", "operational view"]):
        rk = "daily_ops"
    elif any(k in tl for k in ["occupancy", "adr", "revpar", "room-nights", "fill rate", "how full", "how busy", "utilized", "how occupied"]):
        rk = "occupancy"
    if rk: slots["report_kind"] = rk
    # guest query
    mq = re.search(r"[\"""'`]([^\"""'`]{2,})[\"""'`]", t)
    if mq:
        slots["guest_query"] = mq.group(1).strip()
    if "guest_query" not in slots:
        mf = re.search(r"\b(?:find|search|look up|locate|named?|called|for|about)\s+(?:guest\s+|customer\s+)?([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s'.-]{1,50}?)(?:\s+(?:last|most|ordered|visit|stayed|reservation|balance|profile|history|check|who|and)\b|[?.!,]|$)", t, re.I)
        if mf:
            cand = mf.group(1).strip(" ?.,!")
            bad = {"id","number","#","the","a","someone","anyone","he","she","they","him","her","them","last","visit","most","ordered","me","us","today","yesterday","month","week"}
            if cand.lower() not in bad and not re.fullmatch(r"\d+", cand):
                slots["guest_query"] = cand
    # carryover (only if not explicit and message is a follow-up)
    ctx = _context_from_history(history)
    followup = any(k in tl for k in ["same guest", "that guest", "his ", "her ", "their ", "same reservation", "that reservation", "for him", "for her", "for them", "and for", "what about", "same period", "same range"])
    if followup or (len(t.split()) <= 6 and not slots.get("guest_id") and not slots.get("reservation_id")):
        for k in ("guest_id", "reservation_id"):
            if not slots.get(k) and ctx.get(k):
                slots[k] = ctx[k]
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
        q2 = re.sub(r"(?i)\b(find|search|look up|locate|guest|customer|named?|called|for|about|profile|someone|anyone|matching|matches|resembles?|show|possible|candidates)\b", " ", q)
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
        "Guests: search, profile, last visit, most-ordered items and spend on last visit",
        "Reservations: summary/details, balance, outstanding balances/AR, pipeline",
        "Inventory: alerts (low/over/negative), top-selling items; menu/meals",
        "Expenses and sales breakdown; rate plans; room availability; ledger/data integrity",
    ], "notes": ["All answers grounded in real backend data (read-only).", "Last visit = most recent Checked Out, else most recent non-deleted."]}

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
    try:
        return services.verify_ledger_integrity()
    except Exception as e:
        return {"ok": False, "error": str(e)}

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
        # make json-friendly
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
            try:
                bal = services.calculate_guest_balance(r["id"])
            except Exception:
                bal = 0.0
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
            SELECT item_description,
                   SUM(quantity) AS qty_sold,
                   SUM((unit_price * quantity) + COALESCE(tax_amount,0)) AS gross,
                   COUNT(*) AS lines
            FROM pos_tabs
            WHERE voided_at IS NULL AND date(created_at) >= ? AND date(created_at) <= ?
            GROUP BY item_description
            ORDER BY qty_sold DESC, gross DESC
            LIMIT ?""", (start, end, limit)).fetchall()
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
        for m in (meals or [])[:50]:
            try:
                d = dict(m) if not isinstance(m, dict) else m
            except Exception:
                d = {"id": m["id"], "name": m.get("name")}
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
    return {"current": snap(start,end), "previous": snap(p1s,p1e), "note": "Compare key metrics (P&L, occupancy, cash flow) across the two periods."}

def tool_business_partner_briefing(args=None):
    start, end = (args or {}).get("start"), (args or {}).get("end")
    if not start or not end: start, end = _parse_date_range((args or {}).get("range") or "this month")
    data = {"start": start, "end": end, "sections": {}}
    insights = {"snapshot": [], "good": [], "watchouts": [], "actions": []}
    try:
        ps = tool_get_property_stats({})["stats"]
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
    # actions from watchouts
    for w in insights["watchouts"][:5]:
        if "Outstanding balances" in w: insights["actions"].append("Follow up on unpaid balances (review reservation balances and collect).")
        elif "low/negative" in w: insights["actions"].append("Restock low/negative inventory items and verify recipes/ingredient usage.")
        elif "Dirty" in w or "Maintenance" in w: insights["actions"].append("Coordinate housekeeping/maintenance to maximize ready rooms.")
        elif "negative for the period" in w or "Expenses exceed revenue" in w: insights["actions"].append("Review expense drivers and revenue/pricing mix for the period.")
        elif "Low occupancy" in w: insights["actions"].append("Consider targeted offers or channel adjustments to lift occupancy.")
        elif "integrity reported issues" in w: insights["actions"].append("Run/open Health Check and resolve flagged inconsistencies.")
    # dedupe actions
    seen=set(); insights["actions"]=[a for a in insights["actions"] if not (a in seen or seen.add(a))]
    if not insights["actions"]: insights["actions"].append("Maintain current performance; monitor occupancy, collections, and stock daily.")
    data["insights"] = insights
    return data


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
    "get_reservation_summary": {"fn": tool_get_reservation_summary, "description": "Reservation details, balance, POS count.", "parameters": {"type":"object","properties":{"reservation_id":{"type":"integer"}},"required":["reservation_id"],"additionalProperties":False}},
    "get_guest_balance": {"fn": tool_get_guest_balance, "description": "Reservation/guest balance and breakdown.", "parameters": {"type":"object","properties":{"reservation_id":{"type":"integer"}},"required":["reservation_id"],"additionalProperties":False}},
    "get_recent_activity": {"fn": tool_get_recent_activity, "description": "Recent audit/activity log.", "parameters": {"type":"object","properties":{"limit":{"type":"integer"}},"additionalProperties":False}},
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
    if len(body) > 7000:
        body = body[:7000] + "\n... (truncated)"
    return f"{title}\n{body}"

def _fmt_stats_answer(stats: dict, as_of=None):
    if not stats: return ""
    lines = [f"Property stats (as of {as_of or _iso(_today())}, real backend):"]
    if "guests_total" in stats: lines.append(f"- Guests (total): {stats['guests_total']}")
    if "rooms_total" in stats: lines.append(f"- Rooms (total): {stats['rooms_total']}")
    if "reservations_total" in stats: lines.append(f"- Reservations (total): {stats['reservations_total']}")
    if "reservations_by_status" in stats: lines.append(f"- By status: {json.dumps(stats['reservations_by_status'], ensure_ascii=False)}")
    if "in_house" in stats: lines.append(f"- In-house (Checked In): {stats['in_house']}")
    if "arrivals_today" in stats: lines.append(f"- Arrivals today: {stats['arrivals_today']}")
    if "departures_today" in stats: lines.append(f"- Departures today: {stats['departures_today']}")
    return "\n".join(lines)

def _fmt_partner_briefing(data: dict) -> str:
    ins = (data or {}).get("insights") or {}
    start, end = data.get("start"), data.get("end")
    out = [f"Business partner briefing ({start} → {end}) — grounded in real data"]
    if ins.get("snapshot"):
        out.append("\nSnapshot:"); out += [f"- {x}" for x in ins["snapshot"]]
    if ins.get("good"):
        out.append("\nWhat's going well:"); out += [f"- {x}" for x in ins["good"]]
    if ins.get("watchouts"):
        out.append("\nWatch-outs:"); out += [f"- {x}" for x in ins["watchouts"]]
    if ins.get("actions"):
        out.append("\nSuggested actions (data-informed):"); out += [f"- {x}" for x in ins["actions"]]
    out.append("\n(Data trace: property/front-desk stats, P&L, occupancy, AR, inventory alerts, top sellers, ledger integrity for the period.)")
    return "\n".join(out)

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

# ── ML routers (optional, no-dep to run without) ─────────────────────────────
_SKLEARN_CACHE = None; _SETFIT_CACHE = None
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

def _has_any(tl, keys): return any(k in tl for k in keys)

_INTENT_CUES = {
  "capabilities_help": (1.5, ["what can you do","how can you help","capabilities","your abilities","menu of queries","what tools","how do i phrase","boundaries","assist day to day"]),
  "property_stats": (1.4, ["how many guests","number of guests","total guests","guest count","guests do i have","guests do we have","how many customers","how many reservations","number of reservations","total reservations","reservation count","how many rooms","number of rooms","total rooms","how many bookings","quick stats","property stats","overall stats"]),
  "today_ops": (1.4, ["today's ops","todays ops","ops today","today snapshot","morning briefing","what's happening today","whats happening today","arrivals today","departures today","in-house now","in house now"]),
  "business_partner_briefing": (1.5, ["business partner","partner briefing","business briefing","executive briefing","how is the business","how's the business","hows the business","advise me","what should i focus on","priorities for me","recommendations","opportunities and risks","partner view"]),
  "decision_support": (1.3, ["decision support","kpi","how are we doing","manager's overview","manager’s overview","executive snapshot","signals","warning signs","strengths and risks","actionable insights","briefing me","performance indicators"]),
  "compare_periods": (1.4, ["compare","vs last","versus last","this month vs","compared to last","trend vs","previous period"]),
  "outstanding_balances": (1.4, ["who owes","outstanding balance","outstanding balances","unpaid","who hasn't paid","who hasnt paid","amount due overall","receivables","money owed","collectables","overdue"]),
  "inventory_alerts": (1.4, ["low stock","running low","out of stock","negative stock","overstock","inventory alert","inventory alerts","restock","stock warnings","what's low","whats low"]),
  "top_selling_items": (1.4, ["top selling","best seller","best sellers","most sold","top items sold","popular items","what sells most","top pos items"]),
  "expenses_summary": (1.3, ["expense breakdown","expenses by category","spending breakdown","cost breakdown","operating expenses summary","where did we spend"]),
  "sales_breakdown": (1.3, ["sales breakdown","revenue breakdown","pos breakdown","sales by item","sales by category","revenue mix"]),
  "reservation_pipeline": (1.3, ["reservation pipeline","upcoming arrivals","upcoming reservations","bookings pipeline","reservations by status","future bookings"]),
  "housekeeping_summary": (1.3, ["housekeeping","clean rooms","dirty rooms","room status summary","rooms ready"]),
  "room_availability": (1.3, ["available rooms","room availability","any rooms free","rooms free","vacant rooms"]),
  "menu_info": (1.2, ["menu","meals","what meals","dishes","meal list","recipes"]),
  "rate_plans": (1.3, ["rate plan","pricing plan","plans/packages","catalog of rate plans"]),
  "ledger_integrity": (1.4, ["health check","integrity","ledger integrity","data integrity","reconcile ledger","orphaned","mismatched records","sanity check","audit the database","consistency","books/data consistency"]),
  "recent_activity": (1.2, ["recent activity","audit log","what changed lately","recent changes"]),
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

    # unsafe (read-only assistant)
    if _has_any(tl, ["go ahead and delete","delete reservation","delete room","and refund","refund the guest","check the guest in","check in the guest","change room price","change price to","reset the super password","reset password for me","email the guest","send invoice","sync bookings","add a new room","import 500","override checkout","hack into","ignore the rules","hidden admin"]):
        return {"intent":"unsupported_or_unsafe","confidence":0.97,"slots":slots,"router":"rules"}

    scores = defaultdict(float)
    for intent,(w,cues) in _INTENT_CUES.items():
        for c in cues:
            if c in tl: scores[intent] += w
        # fuzzy cue boost
        for c in cues[:6]:
            if len(c)>=6 and c not in tl:
                # token overlap approximation
                cs=set(c.split()); ts=_token_set(tl)
                if cs and len(cs & ts)/len(cs) >= 0.8: scores[intent] += w*0.6
    # structural boosts
    if gid or gq or "guest #" in tl or "customer" in tl or "visitor" in tl:
        for k in ("guest_profile","guest_last_visit","guest_top_items_last_visit","guest_last_visit_spend","guest_search"): scores[k]+=0.2
    if rid or "reservation #" in tl or "booking #" in tl or "res #" in tl:
        for k in ("reservation_summary","reservation_balance"): scores[k]+=0.25
    rk = slots.get("report_kind")
    if rk:
        mapk={"pl":"report_pl","cash_flow":"report_cash_flow","vat":"report_vat","payment_methods":"report_payment_methods","occupancy":"report_occupancy","daily_ops":"report_daily_ops"}
        if rk in mapk: scores[mapk[rk]] += 1.2
    # disambiguate occupancy summary vs report
    if rk=="occupancy":
        if _has_any(tl, ["report","adr","revpar","performance","room-nights","metrics","revenue-per-room"]): scores["report_occupancy"] += 0.6
        else: scores["occupancy_summary"] += 0.4
    # guest intent priority adjustments
    if scores.get("guest_top_items_last_visit",0)>0 or scores.get("guest_last_visit_spend",0)>0:
        scores["guest_last_visit"] += 0.1
    if _has_any(tl, ["when did","when was the last time","last time they stayed","last stay with us","previous visit here"]) and scores.get("guest_profile",0)>0 and scores.get("guest_top_items_last_visit",0)==0:
        scores["guest_last_visit"] += 0.5
    # ambiguity
    if _has_any(tl, ["he ordered","she ordered","they ordered","his last visit","her last visit","their last visit","that guest","they owe","do they owe"]) and not gid and not gq and not slots.get("context",{}).get("guest_id"):
        scores["ambiguous_needs_clarification"] += 1.2
    if tl.strip() in {"balance","show me the balance","what's the balance","what is the balance"} and not rid:
        scores["ambiguous_needs_clarification"] += 1.3
    if ("report for" in tl or "give me the report" in tl) and not rk and not _has_any(tl, ["daily ops","arrivals","departures"]):
        scores["ambiguous_needs_clarification"] += 0.8
    # compound detection
    report_hits = sum(1 for k in ["report_pl","report_cash_flow","report_vat","report_payment_methods","report_occupancy","report_daily_ops"] if scores.get(k,0)>0)
    guest_chain = (gid or gq or "guest" in tl) and sum(1 for k in ["guest_profile","guest_last_visit","guest_top_items_last_visit","guest_last_visit_spend"] if scores.get(k,0)>0 or k.replace("guest_","") in tl) >= 2
    res_chain = rid and scores.get("reservation_balance",0)>0 and scores.get("reservation_summary",0)>0
    if report_hits>=2 or guest_chain or res_chain or _has_any(tl, ["and also"," as well as"," plus ","p&l and cash flow","compare this month vs","list rate plans and also"]):
        scores["compound_multi_intent"] += 1.0

    if not scores:
        # weak fallbacks
        if _has_any(tl, ["report","revenue","expenses","profit"]): return {"intent":"report_pl","confidence":0.5,"slots":slots,"router":"rules"}
        if "guest" in tl and _has_any(tl, ["find","search","look up"]): return {"intent":"guest_search","confidence":0.5,"slots":slots,"router":"rules"}
        return {"intent":"capabilities_help","confidence":0.2,"slots":slots,"router":"rules","low_confidence":True}

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best, best_s = ranked[0]
    second_s = ranked[1][1] if len(ranked)>1 else 0.0
    margin = best_s - second_s
    # confidence from score/margin
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
    lines = ["I found multiple matching guests (real backend search). Please specify the guest ID:"]
    for g in candidates[:8]:
        try:
            lines.append(f"- #{g['id']}: {g.get('name','')} (phone: {g.get('phone','') or '-'}, reservations: {g.get('reservation_count',0)})")
        except Exception: lines.append(f"- {g}")
    return {"ok":True,"intent":"ambiguous_needs_clarification","needs_clarification":True,"tools_used":["search_guests"] if q else [],"data":data,"answer":"\n".join(lines)}

def _execute_single(intent: str, text: str, slots: dict):
    tools_used=[]; start,end = slots.get("date_start"), slots.get("date_end")
    if not start or not end: start,end = _parse_date_range(text)
    rid = slots.get("reservation_id")

    if intent=="capabilities_help":
        data=execute_tool("list_capabilities",{}); tools_used.append("list_capabilities")
        caps=data.get("capabilities",[])
        ans="I can help as a grounded, read-only manager/business partner using real backend data:\n- "+"\n- ".join(caps)+\
            "\n\nExamples: 'business partner briefing for this month', 'who owes money?', 'top selling items last month', 'compare this month vs last month', 'what did guest #3 order most on their last visit?'"
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":ans}

    if intent=="unsupported_or_unsafe":
        return {"ok":False,"intent":intent,"tools_used":[],"data":{"reason":"unsafe_or_out_of_scope"},
                "answer":"I can only help with read-only questions grounded in real app data (reports, guests, reservations, balances, inventory, ops, insights). I can’t perform actions/changes or invent data. Tell me what you want to look up and I’ll fetch it."}

    if intent=="ambiguous_needs_clarification":
        tl=_norm_text(text)
        if rid and _has_any(tl,["balance","owe","outstanding"]):
            return _execute_single("reservation_balance", text, slots)
        if _has_any(tl,["balance","owe","outstanding"]) and not rid:
            return {"ok":True,"intent":intent,"needs_clarification":True,"tools_used":[],"data":{"missing":"reservation_id","suggestion":"overall_ar"},
                    "answer":"Do you mean a specific reservation's balance (e.g. 'reservation #12 balance') or overall outstanding balances/who owes money? (Try: 'who owes money?')"}
        if _has_any(tl,["ordered","last visit","last stay","profile","spend","when did they","he ","she ","they ","his ","her ","their "]) and not (slots.get("guest_id") or slots.get("guest_query")):
            return {"ok":True,"intent":intent,"needs_clarification":True,"tools_used":[],"data":{"missing":"guest"},
                    "answer":"Which guest do you mean? Please provide a guest ID (e.g. guest #3) or a name/phone fragment so I can look them up from real data."}
        if ("report" in tl or tl.startswith("report")) and not slots.get("report_kind") and not rid:
            return {"ok":True,"intent":intent,"needs_clarification":True,"tools_used":[],"data":{"missing":"report_kind"},
                    "answer":"Which report do you want (P&L, cash flow, VAT, payment methods, occupancy, or daily ops) and for what date range?"}
        return {"ok":True,"intent":intent,"needs_clarification":True,"tools_used":[],"data":{"missing":"context"},
                "answer":"I’m not confident I understood. I can fetch reports, business briefing, stats/counts, who owes money, inventory alerts, top sellers, guests (search/profile/last visit/most-ordered/spend), reservations/balances, rate plans, availability, and more. Please rephrase with specifics (ID, name, report type, or date range)."}

    if intent=="property_stats":
        data=execute_tool("get_property_stats",{}); tools_used.append("get_property_stats")
        ans=_fmt_stats_answer((data or {}).get("stats") or {}, (data or {}).get("as_of"))
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":ans or _format_data("Property stats",data)}

    if intent=="today_ops":
        data=execute_tool("get_today_ops",{}); tools_used.append("get_today_ops")
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":_format_data(f"Today's ops ({(data or {}).get('date', _iso(_today()))})", data)}

    if intent=="business_partner_briefing":
        data=execute_tool("business_partner_briefing",{"start":start,"end":end}); tools_used.append("business_partner_briefing")
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":_fmt_partner_briefing(data)}

    if intent=="decision_support":
        data=execute_tool("get_decision_support",{"start":start,"end":end}); tools_used.append("get_decision_support")
        # add short insight header
        ans=_format_data(f"Decision support ({start} → {end})", data)
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":ans}

    if intent=="compare_periods":
        data=execute_tool("compare_periods",{"start":start,"end":end}); tools_used.append("compare_periods")
        cur=data.get("current",{}); prev=data.get("previous",{})
        lines=[f"Period comparison (grounded):\n- Current: {cur.get('start')} → {cur.get('end')}\n- Previous: {prev.get('start')} → {prev.get('end')}"]
        def g(d,*keys):
            x=d
            for k in keys:
                if isinstance(x,dict): x=x.get(k)
                else: return None
            return x
        for label, key in [("PL","pl"),("Occupancy","occupancy"),("Cash flow","cash_flow")]:
            lines.append(f"\n{label} (current): {json.dumps(cur.get(key,{}),ensure_ascii=False,default=str)[:600]}")
            lines.append(f"{label} (previous): {json.dumps(prev.get(key,{}),ensure_ascii=False,default=str)[:600]}")
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":"\n".join(lines)}

    if intent=="outstanding_balances":
        data=execute_tool("get_outstanding_balances",{}); tools_used.append("get_outstanding_balances")
        if data.get("error"): ans=_format_data("Outstanding balances",data)
        else:
            lines=[f"Outstanding balances (real data): {data.get('count',0)} reservation(s), total due {_fmt_money(data.get('total_due',0))}"]
            for r in (data.get('outstanding') or [])[:15]:
                lines.append(f"- Res #{r['reservation_id']} {r.get('guest_name','')} (room {r.get('room','')}, {r.get('status','')}): due {_fmt_money(r['balance_due'])}")
            if (data.get('count',0) or 0)==0: lines.append("- No outstanding balances on active/checked-out reservations.")
            ans="\n".join(lines)
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":ans}

    if intent=="inventory_alerts":
        data=execute_tool("get_inventory_alerts",{}); tools_used.append("get_inventory_alerts")
        a=(data or {}).get("alerts") or {}
        lines=["Inventory alerts (real data):"]
        for label,key in [("Negative","negative"),("Low","low"),("Overstock","overstock")]:
            items=a.get(key) or []
            lines.append(f"- {label}: {len(items)}")
            for it in items[:10]: lines.append(f"  • #{it.get('id')} {it.get('item_name')} qty={it.get('stock_qty')}"+(f" (thr {it.get('threshold')})" if it.get('threshold') is not None else ""))
        if not (a.get("negative") or a.get("low") or a.get("overstock")): lines.append("- All clear: no low/negative/overstock alerts.")
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":"\n".join(lines)}

    if intent=="top_selling_items":
        data=execute_tool("get_top_selling_items",{"start":start,"end":end,"limit":10}); tools_used.append("get_top_selling_items")
        items=(data or {}).get("top_items") or []
        lines=[f"Top selling items ({start} → {end}) — grounded in POS data:"]
        for i,it in enumerate(items[:10],1): lines.append(f"{i}. {it['item']}: qty {it['qty_sold']}, gross {_fmt_money(it['gross'])} ({it['lines']} lines)")
        if not items: lines.append("- No non-voided POS sales found in this period.")
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":"\n".join(lines)}

    if intent=="expenses_summary":
        data=execute_tool("get_expenses_summary",{"start":start,"end":end}); tools_used.append("get_expenses_summary")
        cats=(data or {}).get("by_category") or []
        lines=[f"Expenses summary ({start} → {end}): total {_fmt_money((data or {}).get('total',0))}"]
        for c in cats[:15]: lines.append(f"- {c['category']}: {_fmt_money(c['total'])} (net {_fmt_money(c['net'])}, vat {_fmt_money(c['vat'])}, {c['count']} entries)")
        if not cats: lines.append("- No expenses recorded in this period.")
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":"\n".join(lines)}

    if intent=="sales_breakdown":
        data=execute_tool("get_sales_breakdown",{"start":start,"end":end}); tools_used.append("get_sales_breakdown")
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":_format_data(f"Sales breakdown ({start} → {end})",data)}

    if intent=="reservation_pipeline":
        data=execute_tool("get_reservation_pipeline",{}); tools_used.append("get_reservation_pipeline")
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":_format_data("Reservation pipeline",data)}

    if intent=="housekeeping_summary":
        data=execute_tool("get_housekeeping_summary",{}); tools_used.append("get_housekeeping_summary")
        s=(data or {}).get("summary") or {}
        lines=[f"Housekeeping summary (total rooms {s.get('total_rooms',0)}):"]+[f"- {k}: {v}" for k,v in (s.get("by_status") or {}).items()]
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":"\n".join(lines)}

    if intent=="room_availability":
        a_start = slots.get("date_start") or start; a_end = slots.get("date_end") or end
        data=execute_tool("get_room_availability",{"start":a_start,"end":a_end}); tools_used.append("get_room_availability")
        rooms=(data or {}).get("rooms") or []
        lines=[f"Room availability ({a_start} → {a_end}): {data.get('available_count',len(rooms))} room(s) available"]
        for r in rooms[:20]: lines.append(f"- {r.get('room_number')} ({r.get('room_type','')}) base {_fmt_money(r.get('base_price',0))} [{r.get('housekeeping_status','')}]")
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":"\n".join(lines)}

    if intent=="menu_info":
        data=execute_tool("get_menu_info",{}); tools_used.append("get_menu_info")
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":_format_data("Menu / meals",data)}

    if intent=="rate_plans":
        data=execute_tool("list_rate_plans",{}); tools_used.append("list_rate_plans")
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":_format_data("Rate plans",data)}

    if intent=="ledger_integrity":
        data=execute_tool("verify_ledger_integrity",{}); tools_used.append("verify_ledger_integrity")
        status="OK" if (data or {}).get("ok") else "ISSUES FOUND"
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":_format_data(f"Ledger/data integrity: {status}",data)}

    if intent=="recent_activity":
        data=execute_tool("get_recent_activity",{}); tools_used.append("get_recent_activity")
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":_format_data("Recent activity (audit)",data)}

    # reports
    if intent in ("report_pl","report_cash_flow","report_vat","report_payment_methods","report_occupancy","report_daily_ops"):
        kind=intent.replace("report_","")
        data=execute_tool("get_report",{"kind":kind,"start":start,"end":end}); tools_used.append("get_report")
        label = end if kind=="daily_ops" else f"{start} → {end}"
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":_format_data(f"Report {kind} ({label})",data)}

    if intent=="occupancy_summary":
        data=execute_tool("get_occupancy_summary",{"start":start,"end":end}); tools_used.append("get_occupancy_summary")
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":_format_data(f"Occupancy summary ({start} → {end})",data)}

    # reservations
    if intent in ("reservation_balance","reservation_summary"):
        if not rid: return _execute_single("ambiguous_needs_clarification", text, slots)
        name="get_guest_balance" if intent=="reservation_balance" else "get_reservation_summary"
        data=execute_tool(name,{"reservation_id":rid}); tools_used.append(name)
        return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":_format_data(f"Reservation #{rid}",data)}

    # guests
    if intent in ("guest_search","guest_profile","guest_last_visit","guest_top_items_last_visit","guest_last_visit_spend"):
        guests, explicit_id, q = resolve_guest(text, slots)
        if intent=="guest_search":
            query=q or slots.get("guest_query") or re.sub(r"(?i)\b(find|search|look up|locate|guest|guests|customer|for|named?|called|who do we have|show|matches|matching)\b"," ",text)
            query=re.sub(r"\s+"," ",query).strip(" ?.,")
            data=execute_tool("search_guests",{"query":query,"limit":10}); tools_used.append("search_guests")
            return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":_format_data(f"Guest search: {data.get('query','')}",data)}
        if explicit_id and not guests:
            return {"ok":False,"intent":intent,"tools_used":[],"data":{"error":f"Guest #{explicit_id} not found"},"answer":f"I couldn't find guest #{explicit_id} (real backend lookup)."}
        if len(guests)==1 or (explicit_id and len(guests)==1):
            g=guests[0]; gid=g["id"] if isinstance(g,dict) else explicit_id
            gname=g.get("name","") if isinstance(g,dict) else ""
            if intent=="guest_top_items_last_visit":
                data=execute_tool("get_guest_top_items_last_visit",{"guest_id":gid,"limit":5}); tools_used.append("get_guest_top_items_last_visit")
                title=f"Most ordered items — last visit (guest #{gid} {gname})"
            elif intent=="guest_last_visit_spend":
                data=execute_tool("get_guest_last_visit_spend",{"guest_id":gid}); tools_used.append("get_guest_last_visit_spend")
                title=f"Last visit spend (guest #{gid} {gname})"
            elif intent=="guest_last_visit":
                data=execute_tool("get_guest_last_visit",{"guest_id":gid}); tools_used.append("get_guest_last_visit")
                title=f"Last visit (guest #{gid} {gname})"
            else:
                data=execute_tool("get_guest_profile",{"guest_id":gid}); tools_used.append("get_guest_profile")
                title=f"Guest #{gid} profile"
            return {"ok":True,"intent":intent,"tools_used":tools_used,"data":data,"answer":_format_data(title,data)}
        if len(guests)>1: return _guest_disambiguation(guests, q or slots.get("guest_query"))
        if slots.get("guest_id") or slots.get("guest_query"):
            return {"ok":False,"intent":intent,"tools_used":[],"data":{"query":slots.get("guest_query"),"guest_id":slots.get("guest_id")},"answer":"I couldn't find a matching guest in real backend data. Please try a different name/phone fragment or a guest ID."}
        return _execute_single("ambiguous_needs_clarification", text, slots)

    # fallback
    data=execute_tool("list_capabilities",{}); tools_used.append("list_capabilities")
    return {"ok":True,"intent":"capabilities_help","tools_used":tools_used,"data":data,"answer":"I couldn't confidently map that request. "+_format_data("Capabilities",data)}

def _execute_compound(text: str, slots: dict):
    tl=_norm_text(text); subs=[]
    if slots.get("reservation_id") and _has_any(tl,["balance","owe","outstanding"]): subs.append(("reservation_balance",slots))
    if slots.get("reservation_id") and _has_any(tl,["summary","details","folio","overview"]): subs.append(("reservation_summary",slots))
    if _has_any(tl,["business partner","partner briefing","executive briefing","advise me","recommendations","what should i focus"]): subs.append(("business_partner_briefing",slots))
    if _has_any(tl,["decision support","kpi","how are we doing","signals"]): subs.append(("decision_support",slots))
    for intent,rk in [("report_pl","pl"),("report_cash_flow","cash_flow"),("report_vat","vat"),("report_payment_methods","payment_methods"),("report_occupancy","occupancy"),("report_daily_ops","daily_ops")]:
        cues={"pl":["p&l","pnl","profit and loss","profit","net profit"],"cash_flow":["cash flow","cashflow"],"vat":["vat","value-added tax","tax report"],"payment_methods":["payment method","payments by method","payment channels"],"occupancy":["occupancy","adr","revpar","how full","how busy"],"daily_ops":["daily ops","arrivals","departures","in-house","in house"]}
        if _has_any(tl,cues[rk]): subs.append((intent,slots))
    if _has_any(tl,["compare","vs last","versus last"]): subs.append(("compare_periods",slots))
    if _has_any(tl,["who owes","outstanding","unpaid"]): subs.append(("outstanding_balances",slots))
    if _has_any(tl,["low stock","inventory alert","running low","overstock"]): subs.append(("inventory_alerts",slots))
    if _has_any(tl,["top selling","best seller","most sold"]): subs.append(("top_selling_items",slots))
    if _has_any(tl,["expense breakdown","expenses by category","where did we spend"]): subs.append(("expenses_summary",slots))
    if _has_any(tl,["rate plan","pricing plan"]): subs.append(("rate_plans",slots))
    if _has_any(tl,["health check","integrity","ledger integrity"]): subs.append(("ledger_integrity",slots))
    if _has_any(tl,["housekeeping"]): subs.append(("housekeeping_summary",slots))
    if _has_any(tl,["pipeline","upcoming arrivals"]): subs.append(("reservation_pipeline",slots))
    # guest chain
    guests, explicit_id, q = resolve_guest(text, slots)
    if slots.get("guest_id") or q or _has_any(tl,["find guest","search guest"]):
        if _has_any(tl,["find guest","search guest","search for"]) and not explicit_id and len(guests)!=1:
            subs.append(("guest_search",slots))
        if explicit_id or len(guests)==1:
            g_slots=dict(slots)
            if len(guests)==1 and not explicit_id: g_slots["guest_id"]=guests[0]["id"]
            if _has_any(tl,["profile","everything we know","history"]): subs.append(("guest_profile",g_slots))
            if _has_any(tl,["last visit","last stay","most recent","when did","last time"]): subs.append(("guest_last_visit",g_slots))
            if _has_any(tl,["ordered most","most ordered","top-ordered","buy the most","topped their list","what did they order"]): subs.append(("guest_top_items_last_visit",g_slots))
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
    for it,sl in uniq[:6]:
        res=_execute_single(it,text,sl)
        tools_used.extend(res.get("tools_used") or [])
        parts.append(f"## {it}\n{res.get('answer','')}")
        data_all[it]=res.get("data")
        ok = ok and bool(res.get("ok",True))
    return {"ok":ok,"intent":"compound_multi_intent","tools_used":list(dict.fromkeys(tools_used)),"data":data_all,"answer":"\n\n".join(parts)}

# ── Public routing / chat / LLM compat ───────────────────────────────────────

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

def _llm_config():
    try:
        provider=(services.get_setting("assistant_provider","auto") or "auto").strip()
        response_mode=(services.get_setting("assistant_response_mode","talking") or "talking").strip()
        base=(services.get_setting("assistant_llm_base_url","") or "").strip().rstrip("/")
        model=(services.get_setting("assistant_llm_model","") or "").strip()
        key=(services.get_setting("assistant_llm_api_key","") or "").strip()
    except Exception:
        provider,response_mode,base,model,key="auto","talking","","",""
    enabled = provider!="deterministic_only" and bool(base and model)
    return {"provider":provider,"response_mode":response_mode,"base_url":base,"model":model,"api_key":key,"enabled":enabled}

def _openai_chat(messages, config, tools_schema=None, temperature=0.1, timeout=60):
    try:
        import requests
    except Exception as e:
        return None, f"requests not available: {e}"
    url=(config.get("base_url") or "")+"/chat/completions"
    headers={"Content-Type":"application/json"}
    if config.get("api_key"): headers["Authorization"]=f"Bearer {config['api_key']}"
    payload={"model":config.get("model"),"messages":messages,"temperature":temperature}
    if tools_schema: payload["tools"]=tools_schema; payload["tool_choice"]="auto"
    try:
        r=requests.post(url,headers=headers,json=payload,timeout=timeout)
        if r.status_code>=400: return None, f"LLM HTTP {r.status_code}: {r.text[:300]}"
        return r.json(), None
    except Exception as e:
        return None, str(e)

def talking_rephrase(message: str, core_result: dict, config: dict) -> str | None:
    if not config or not config.get("enabled"): return None
    core_answer=(core_result or {}).get("answer") or ""
    data=(core_result or {}).get("data")
    try: data_str=json.dumps(data,ensure_ascii=False,default=str)[:5000]
    except Exception: data_str=str(data)[:5000]
    system=("You are a friendly property-manager business partner assistant. Answer in clear English. "
            "You are given GROUNDED BACKEND DATA and a core answer. Rephrase/summarize it nicely and highlight key insights/actions. "
            "Do NOT invent facts, numbers, names, dates, IDs, or items. Keep IDs/dates/amounts accurate.")
    user=f"Manager question: {message}\n\nCore grounded answer:\n{core_answer}\n\nGrounded data (JSON):\n{data_str}"
    resp, err = _openai_chat([{"role":"system","content":system},{"role":"user","content":user}], config, tools_schema=None, temperature=0.2, timeout=45)
    if err or not resp: return None
    msg=((resp.get("choices") or [{}])[0].get("message") or {})
    text=(msg.get("content") or "").strip()
    return text or None

def llm_route(message: str, history: list[dict] | None = None, config: dict | None = None) -> dict | None:
    config=config or _llm_config()
    if not config.get("enabled"): return None
    tools_schema=[{"type":"function","function":{"name":n,"description":s["description"],"parameters":s["parameters"]}} for n,s in TOOLS.items()]
    system=("You are a property-manager business partner assistant. Use ONLY the provided tools for facts/numbers/guests/reports. "
            "Never invent data. If ambiguous, ask to clarify. Cite key IDs/dates. Answer in clear English. "
            "Last visit = most recent Checked Out else most recent non-deleted. Most ordered last visit = get_guest_top_items_last_visit.")
    msgs=[{"role":"system","content":system}]
    for h in (history or [])[-8:]:
        if h.get("role") in ("user","assistant") and h.get("content"): msgs.append({"role":h["role"],"content":str(h["content"])[:4000]})
    msgs.append({"role":"user","content":message})
    tools_used=[]; last_data=None
    for _ in range(6):
        resp, err = _openai_chat(msgs, config, tools_schema=tools_schema, temperature=0.1, timeout=60)
        if err:
            det=deterministic_route(message)
            return {"ok":bool(det),"source":"full_llm_error_fallback_core" if det else "full_llm_error",
                    "tools_used":(det or {}).get("tools_used") or tools_used,"data":(det or {}).get("data") or {"error":err},
                    "answer":((det or {}).get("answer") or "") or f"LLM unavailable ({err}). Try a grounded query (reports, stats, guests, reservations).","llm_error":err}
        choice=(resp.get("choices") or [{}])[0]; msg=choice.get("message") or {}; tool_calls=msg.get("tool_calls") or []
        if tool_calls:
            msgs.append({"role":"assistant","content":msg.get("content") or "","tool_calls":tool_calls})
            for tc in tool_calls:
                fn=(tc.get("function") or {}); name=fn.get("name")
                try: args=json.loads(fn.get("arguments") or "{}")
                except Exception: args={}
                result=execute_tool(name,args); tools_used.append(name); last_data=result
                msgs.append({"role":"tool","tool_call_id":tc.get("id"),"name":name,"content":json.dumps(result,ensure_ascii=False,default=str)[:8000]})
            continue
        answer=(msg.get("content") or "").strip() or _format_data("Result", last_data)
        return {"ok":True,"source":"full_llm_tools","tools_used":tools_used,"data":last_data,"answer":answer}
    return {"ok":True,"source":"full_llm_tools","tools_used":tools_used,"data":last_data,"answer":_format_data("Result (max tool rounds reached)", last_data)}

def chat(message: str, history: list[dict] | None = None) -> dict:
    message=(message or "").strip()
    if not message: return {"ok":False,"source":"none","tools_used":[],"data":None,"answer":"Please enter a question."}
    try: services.touch_session()
    except Exception: pass
    config=_llm_config(); mode=config.get("response_mode") or "talking"; provider=config.get("provider") or "auto"
    if mode=="full_llm" and config.get("enabled"):
        res=llm_route(message, history=history, config=config)
        if res: res["response_mode"]=mode; return res

    slots=extract_slots(message, history)
    cls=classify_intent(message, history)
    intent=cls.get("intent") or "capabilities_help"; confidence=float(cls.get("confidence") or 0.0)
    slots={**slots, **(cls.get("slots") or {})}
    strong = bool(slots.get("reservation_id") or slots.get("guest_id") or confidence>=0.85)

    if intent=="compound_multi_intent":
        core=_execute_compound(message, slots)
    elif confidence < 0.45 and not strong and intent not in ("capabilities_help","unsupported_or_unsafe"):
        if config.get("enabled") and provider in ("auto","openai_compatible") and mode!="core":
            res=llm_route(message, history=history, config=config)
            if res: res["response_mode"]=mode; res["intent"]=intent; res["confidence"]=confidence; return res
        core=_execute_single("ambiguous_needs_clarification", message, slots); core["intent"]=intent
    else:
        if intent=="capabilities_help" and confidence < 0.5 and cls.get("low_confidence"):
            core={"ok":True,"intent":"ambiguous_needs_clarification","needs_clarification":True,"tools_used":[],"data":{"router_confidence":confidence},
                  "answer":"I’m not confident I understood that request. I can help with: business partner briefing, property/ops stats, reports (P&L/cash/VAT/payments/occupancy/daily ops) and period comparisons, who owes money, inventory alerts, top sellers, expenses/sales breakdown, guests (search/profile/last visit/most-ordered/spend), reservations/balances/pipeline, housekeeping, availability, menus, rate plans, integrity. Please rephrase with specifics (ID, name, report, or date range)."}
        else:
            core=_execute_single(intent, message, slots)

    core.setdefault("source","core_deterministic"); core["intent"]=core.get("intent") or intent
    core["confidence"]=confidence; core["slots"]=slots; core["router"]=cls.get("router","rules")
    core["response_mode"]=mode; core["core_answer"]=core.get("answer"); core["core_source"]=core.get("source")

    if mode=="talking" and core.get("ok") and config.get("enabled") and not core.get("needs_clarification"):
        polished=talking_rephrase(message, core, config)
        if polished: core["answer"]=polished; core["source"]=f"talking+{core.get('core_source')}"; core["talking_model"]=config.get("model")
        else: core["source"]=f"talking_unavailable+{core.get('core_source')}"
    elif mode=="core":
        core["source"]=core.get("core_source") or core.get("source")
    return core
