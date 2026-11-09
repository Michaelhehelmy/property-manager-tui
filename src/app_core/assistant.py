"""Manager assistant: CORE (grounded, read-only tools + deterministic router) + optional TALKING LLM.

- Core AI (backend): deterministic routing + real app_core.services/db tools. Always grounded.
- Talking AI (optional): rephrases grounded core results for nicer user interaction via an
  OpenAI-compatible endpoint. Never adds facts; falls back to core text on failure.
- Full LLM mode (optional): LLM tool-calling conversation using the same real tools.

Works smoothly with no LLM configured (deterministic core). Uses `requests` when available.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from . import services

DATE_FMT = getattr(services, "DATE_FMT", "%Y-%m-%d")


def _today():
    return datetime.now().date()


def _iso(d):
    return d.strftime(DATE_FMT)


def _parse_date_range(text: str):
    t = (text or "").lower()
    today = _today()
    dates = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", text or "")
    if len(dates) >= 2:
        return dates[0], dates[1]
    if len(dates) == 1 and any(k in t for k in ["on ", "for "]):
        return dates[0], dates[0]
    if "today" in t:
        return _iso(today), _iso(today)
    if "yesterday" in t:
        y = today - timedelta(days=1)
        return _iso(y), _iso(y)
    if "this week" in t:
        start = today - timedelta(days=today.weekday())
        return _iso(start), _iso(today)
    if "last 7 days" in t or "past 7 days" in t:
        return _iso(today - timedelta(days=6)), _iso(today)
    if "last 30 days" in t or "past 30 days" in t:
        return _iso(today - timedelta(days=29)), _iso(today)
    if "this month" in t:
        start = today.replace(day=1)
        return _iso(start), _iso(today)
    if "last month" in t:
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        start_prev = last_prev.replace(day=1)
        return _iso(start_prev), _iso(last_prev)
    if len(dates) == 1:
        return dates[0], dates[0]
    start = today.replace(day=1)
    return _iso(start), _iso(today)


def _safe_int(v):
    try:
        return int(str(v).strip())
    except Exception:
        return None


# ── Read-only tools (real backend / CORE) ────────────────────────────────────

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
        return {
            "start": start, "end": end,
            "decision_support": services.dataset_decision_support_v1(start, end),
            "financial_summary": services.dataset_financial_summary(start, end),
        }
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


def _resolve_guest_from_text(text: str):
    m = re.search(r"\bguest\s*#?\s*(\d+)\b", text, re.I)
    if m:
        gid = int(m.group(1))
        g = services.get_guest_by_id(gid)
        return ([g] if g else []), gid
    name = None
    mq = re.search(r"[\"""']([^\"""']{2,})[\"""']", text)
    if mq:
        name = mq.group(1).strip()
    if not name:
        mf = re.search(r"\b(?:for|of|guest|customer)\s+([A-Za-z\u0600-\u06FF][A-Za-z\u0600-\u06FF\s'.-]{1,60}?)(?:\s+(?:last|most|ordered|visit|stayed|reservation|balance)\b|[?.!,]|$)", text, re.I)
        if mf:
            name = mf.group(1).strip(" ?.,!")
    if not name:
        return [], None
    if name.lower() in {"last visit", "most", "ordered", "the guest", "a guest", "guest"}:
        return [], None
    return services.assistant_find_guests(name, limit=8), None


# ── CORE deterministic router (backend) ──────────────────────────────────────

def deterministic_route(message: str) -> dict | None:
    text = (message or "").strip()
    t = text.lower()
    tools_used = []
    if not text:
        return None

    if any(k in t for k in ["what can you do", "help", "capabilities", "tools", "how can you help"]):
        data = execute_tool("list_capabilities", {})
        tools_used.append("list_capabilities")
        caps = data.get("capabilities", [])
        return {"ok": True, "source": "core_deterministic", "tools_used": tools_used, "data": data,
                "answer": "I can answer using real backend data (read-only):\n- " + "\n- ".join(caps) +
                          "\n\nExamples: 'P&L for last month', 'VAT 2026-01-01 to 2026-01-31', 'find guest Ahmed', 'what did guest #3 order most on their last visit?', 'reservation #12 balance'."}

    mr = re.search(r"\breservation\s*#?\s*(\d+)\b", t)
    if mr and any(k in t for k in ["balance", "summary", "detail", "folio", "status", "reservation"]):
        rid = int(mr.group(1))
        if "balance" in t and "summary" not in t and "detail" not in t:
            data = execute_tool("get_guest_balance", {"reservation_id": rid}); tools_used.append("get_guest_balance")
        else:
            data = execute_tool("get_reservation_summary", {"reservation_id": rid}); tools_used.append("get_reservation_summary")
        return {"ok": True, "source": "core_deterministic", "tools_used": tools_used, "data": data, "answer": _format_data(f"Reservation #{rid}", data)}

    if "guest" in t or "ordered" in t or "last visit" in t or "last stay" in t:
        guests, explicit_id = _resolve_guest_from_text(text)
        if explicit_id and not guests:
            return {"ok": False, "source": "core_deterministic", "tools_used": [], "data": {"error": f"Guest #{explicit_id} not found"}, "answer": f"I couldn't find guest #{explicit_id} (real backend lookup)."}
        if len(guests) == 1 or (explicit_id and len(guests) == 1):
            g = guests[0]
            gid = g["id"] if isinstance(g, dict) else explicit_id
            if any(k in t for k in ["most ordered", "ordered most", "what did", "what he ordered", "what she ordered", "items", "order last", "orders last", "bought"]):
                data = execute_tool("get_guest_top_items_last_visit", {"guest_id": gid, "limit": 5}); tools_used.append("get_guest_top_items_last_visit")
                title = f"Most ordered items — last visit (guest #{gid} {g.get('name','') if isinstance(g, dict) else ''})"
            elif "last visit" in t or "last stay" in t:
                data = execute_tool("get_guest_last_visit", {"guest_id": gid}); tools_used.append("get_guest_last_visit")
                title = f"Last visit (guest #{gid})"
            else:
                data = execute_tool("get_guest_profile", {"guest_id": gid}); tools_used.append("get_guest_profile")
                title = f"Guest #{gid} profile"
            return {"ok": True, "source": "core_deterministic", "tools_used": tools_used, "data": data, "answer": _format_data(title, data)}
        if len(guests) > 1:
            data = {"candidates": guests}
            return {"ok": True, "source": "core_deterministic", "tools_used": ["search_guests"], "data": data,
                    "answer": "I found multiple matching guests (real backend search). Please specify the guest ID:\n" + _format_data("Candidates", guests)}
        if any(k in t for k in ["find guest", "search guest", "look up guest"]):
            q = re.sub(r"(?i).*\b(find|search|look up)\s+guest\s*", "", text).strip(" ?.")
            if q:
                data = execute_tool("search_guests", {"query": q}); tools_used.append("search_guests")
                return {"ok": True, "source": "core_deterministic", "tools_used": tools_used, "data": data, "answer": _format_data(f"Guest search: {q}", data)}

    start, end = _parse_date_range(text)
    if any(k in t for k in ["decision support", "signals", "kpi", "how are we doing", "management summary", "manager summary"]):
        data = execute_tool("get_decision_support", {"start": start, "end": end}); tools_used.append("get_decision_support")
        return {"ok": True, "source": "core_deterministic", "tools_used": tools_used, "data": data, "answer": _format_data(f"Decision support ({start} to {end})", data)}
    report_map = [("vat", ["vat", "tax report"]), ("cash_flow", ["cash flow", "cashflow"]), ("payment_methods", ["payment method", "payments by method"]),
                  ("occupancy", ["occupancy", "adr", "revpar"]), ("pl", ["p&l", "pnl", "profit and loss", "profit", "revenue", "expenses", "net profit"])]
    for kind, keys in report_map:
        if any(k in t for k in keys):
            data = execute_tool("get_report", {"kind": kind, "start": start, "end": end}); tools_used.append("get_report")
            return {"ok": True, "source": "core_deterministic", "tools_used": tools_used, "data": data, "answer": _format_data(f"Report {kind} ({start} to {end})", data)}
    if "daily ops" in t or "arrivals" in t or "departures" in t or "in house" in t:
        data = execute_tool("get_report", {"kind": "daily_ops", "start": end, "end": end}); tools_used.append("get_report")
        return {"ok": True, "source": "core_deterministic", "tools_used": tools_used, "data": data, "answer": _format_data(f"Daily ops ({end})", data)}
    if "rate plan" in t:
        data = execute_tool("list_rate_plans", {}); tools_used.append("list_rate_plans")
        return {"ok": True, "source": "core_deterministic", "tools_used": tools_used, "data": data, "answer": _format_data("Rate plans", data)}
    if any(k in t for k in ["health check", "integrity", "ledger integrity", "data integrity", "reconcile ledger"]):
        data = execute_tool("verify_ledger_integrity", {}); tools_used.append("verify_ledger_integrity")
        status = "OK" if data.get("ok") else "ISSUES FOUND"
        return {"ok": True, "source": "core_deterministic", "tools_used": tools_used, "data": data, "answer": _format_data(f"Ledger/data integrity health check: {status}", data)}
    if re.search(r"\b(find|search)\b.*\bguest\b|\bguest\b.*\b(find|search)\b", t):
        q = re.sub(r"(?i)\b(find|search|guest|for|named?)\b", " ", text)
        q = re.sub(r"\s+", " ", q).strip(" ?.")
        if len(q) >= 2:
            data = execute_tool("search_guests", {"query": q}); tools_used.append("search_guests")
            return {"ok": True, "source": "core_deterministic", "tools_used": tools_used, "data": data, "answer": _format_data(f"Guest search: {q}", data)}
    return None


# ── LLM config / transport (optional) ────────────────────────────────────────

def _llm_config():
    provider = (services.get_setting("assistant_provider", "auto") or "auto").strip()
    response_mode = (services.get_setting("assistant_response_mode", "talking") or "talking").strip()
    base = (services.get_setting("assistant_llm_base_url", "") or "").strip().rstrip("/")
    model = (services.get_setting("assistant_llm_model", "") or "").strip()
    key = (services.get_setting("assistant_llm_api_key", "") or "").strip()
    enabled = provider != "deterministic_only" and bool(base and model)
    return {"provider": provider, "response_mode": response_mode, "base_url": base, "model": model, "api_key": key, "enabled": enabled}


def _openai_chat(messages, config, tools_schema=None, temperature=0.1, timeout=60):
    try:
        import requests
    except Exception as e:
        return None, f"requests not available: {e}"
    url = config["base_url"] + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if config.get("api_key"):
        headers["Authorization"] = f"Bearer {config['api_key']}"
    payload = {"model": config["model"], "messages": messages, "temperature": temperature}
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


# ── TALKING layer (rephrase grounded core results only) ──────────────────────

def talking_rephrase(message: str, core_result: dict, config: dict) -> str | None:
    """Use a talking model to polish a grounded core answer. Returns None on failure."""
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


# ── FULL LLM tool-calling route (optional) ───────────────────────────────────

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


# ── Public entrypoint ────────────────────────────────────────────────────────

def chat(message: str, history: list[dict] | None = None) -> dict:
    """Answer a manager question. Core is grounded/read-only; talking LLM is optional polish."""
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

    # Full LLM tool-calling mode (explicit)
    if mode == "full_llm" and config.get("enabled"):
        res = llm_route(message, history=history, config=config)
        if res:
            res["response_mode"] = mode
            return res

    # CORE (backend): deterministic grounded routing
    core = deterministic_route(message)

    # If core missed and LLM is available in auto/full contexts, try full_llm as assisted retrieval
    if not core and config.get("enabled") and provider in ("auto", "openai_compatible") and mode != "core":
        res = llm_route(message, history=history, config=config)
        if res:
            res["response_mode"] = mode
            return res

    if not core:
        caps = execute_tool("list_capabilities", {})
        core = {"ok": True, "source": "core_fallback", "tools_used": ["list_capabilities"], "data": caps,
                "answer": "I couldn't confidently match that with the core backend router. "
                          "I can help with reports, decision support, guest search, last visit, most-ordered items on a guest's last visit, and reservation/balance lookups (all real backend data).\n\n"
                          + _format_data("Capabilities", caps)}

    core["response_mode"] = mode
    core["core_answer"] = core.get("answer")
    core["core_source"] = core.get("source")

    # TALKING layer: rephrase grounded core output for user interaction
    if mode == "talking" and core.get("ok") and config.get("enabled"):
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
