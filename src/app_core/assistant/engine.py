"""Intent execution and deterministic routing composition helpers."""
from __future__ import annotations

import logging
import re

from .common import (
    _norm_text, _parse_date_range, extract_slots, resolve_guest,
)
from .formatting import _apply_proactive, _fmt_capabilities, _format_for_intent
from .nlu import _has_any, classify_intent, rule_classify
from .tools import execute_tool

logger = logging.getLogger(__name__)

# ── Execution ────────────────────────────────────────────────────────────────

def _guest_disambiguation(candidates, q=None):
    data = {"candidates": candidates, "query": q}
    lines = ["I found more than one guest matching that (real records). Which one did you mean? Please reply with the guest ID:"]
    for g in candidates[:8]:
        try:
            name = g.get("name") or g.get("canonical_name") or "Guest"
            lines.append(f"- **#{g['id']} {name}** — phone: {g.get('phone') or '-'}, email: {g.get('email') or '-'}, stays: {g.get('stays', g.get('reservation_count', 0))}, last stay: {g.get('last_stay') or '-'}")
        except Exception:
            logger.debug("assistant._guest_disambiguation: failed to format candidate %r", g, exc_info=True)
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
            try:
                days = int(m.group(1))
            except (TypeError, ValueError):
                logger.debug("assistant._execute_single: failed to parse expiring-batches day count from %r", m.group(1), exc_info=True)
                days = None
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
        try:
            days = int(days) if days not in (None, "") else None
        except (TypeError, ValueError):
            logger.debug("assistant._execute_single: invalid within_days=%r", days, exc_info=True)
            days = None
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

