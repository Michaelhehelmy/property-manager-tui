"""Intent execution and deterministic routing composition helpers."""
from __future__ import annotations

import logging
import re

from .common import (
    _norm_text, _parse_date_range, resolve_guest, _safe_int,
)
from .formatting import _apply_proactive, _fmt_capabilities, _format_for_intent
from .nlu import _has_any, classify_intent  # noqa: F401
from .tools import execute_tool

logger = logging.getLogger(__name__)


def _extract_write_args(text: str, slots: dict, ui_context=None):
    t = (text or "").strip()
    tl = _norm_text(text)
    args = {}
    # reservation
    rid = slots.get("reservation_id") or _safe_int(slots.get("id"))
    if rid:
        args["reservation_id"] = rid
    # qty
    m = re.search(r"\b(?:qty|quantity|x)\s*[:#-]?\s*(\d+)\b", t, re.I) or re.search(r"\b(\d+)\s*(?:pcs|units?|items?|qty)\b", t, re.I)
    if m:
        args["qty"] = int(m.group(1))
    # amount/price
    m = re.search(r"\b(?:amount|price|total|sum)\s*(?:of|=|:)?\s*([0-9]+(?:\.[0-9]+)?)\b", t, re.I)
    if m:
        try: args["amount"] = float(m.group(1))
        except Exception: pass
    # payment method
    for meth in ["cash", "card", "bank", "transfer", "mobile money", "momo", "credit"]:
        if re.search(rf"\b{re.escape(meth)}\b", tl):
            args["method"] = "Cash" if meth == "cash" else ("Card" if meth == "card" else ("Bank Transfer" if meth in ("bank","transfer") else ("Mobile Money" if meth in ("mobile money","momo") else meth.title())))
            break
    # item id / sku / barcode hints
    m = re.search(r"\b(?:item|product|inv(?:entory)?(?:\s*item)?|sku|barcode|code)\s*#?\s*([A-Za-z0-9._\-]+)\b", t, re.I)
    if m:
        val = m.group(1)
        ival = _safe_int(val)
        if ival is not None:
            args["item_id"] = ival
        else:
            args["sku"] = val; args["barcode"] = val
    # lot/batch
    m = re.search(r"\b(?:batch|lot)\s*#?\s*([A-Za-z0-9._\-]+)\b", t, re.I)
    if m:
        val = m.group(1); ival = _safe_int(val)
        if ival is not None: args["batch_id"] = ival
        else: args["lot_code"] = val
    # guest name
    m = re.search(r"\b(?:for|guest)\s+([A-Za-z][A-Za-z .'-]{1,60})\b", t, re.I)
    if m and not args.get("guest_name"):
        gn = m.group(1).strip().strip(".,")
        if gn.lower() not in {"reservation","room","item","product","activity"}:
            args["guest_name"] = gn
    # room id
    m = re.search(r"\broom\s*#?\s*(\d+)\b", t, re.I)
    if m: args["room_id"] = int(m.group(1))
    # activity id
    m = re.search(r"\bactivity\s*#?\s*(\d+)\b", t, re.I)
    if m: args["activity_id"] = int(m.group(1))
    # dates
    if slots.get("date_start"): args["check_in"] = slots.get("date_start")
    if slots.get("date_end"): args["check_out"] = slots.get("date_end")
    # notes (quoted)
    m = re.search(r'"([^"]{2,120})"', t) or re.search(r"'([^']{2,120})'", t)
    if m: args["notes"] = m.group(1).strip()
    # try to extract item name for "add <name> to reservation ..."
    if "item_name" not in args and "item_id" not in args and "sku" not in args and "barcode" not in args:
        m = re.search(r"\b(?:add|sell|charge)\s+(.+?)\s+\b(?:to\s+reservation|for\s+reservation|reservation\s*#)\b", t, re.I)
        if m:
            name = m.group(1).strip().strip(".,")
            name = re.sub(r"^(?:pos\s+item|item|product)\s+", "", name, flags=re.I).strip()
            if 1 <= len(name) <= 80:
                args["item_name"] = name
    return args


def _maybe_propose_action(intent, text, slots, ui_context=None):
    """Return a proposed-action chat result for supported write intents, else None."""
    tl = _norm_text(text)
    args = _extract_write_args(text, slots, ui_context=ui_context)
    action_type = None

    # add/sell/charge POS item to reservation
    if (re.search(r"\b(add|sell|charge|post)\b", tl) and re.search(r"\b(reservation|booking|folio)\b", tl) and re.search(r"\b(item|product|pos|meal|drink|food|inventory)\b", tl)) or intent in {"propose_add_pos_item","add_pos_item"}:
        action_type = "propose_add_pos_item"
        if not args.get("reservation_id"):
            return {"ok": True, "intent": "action_needs_info", "tools_used": [], "data": {"action": "propose_add_pos_item", "missing": ["reservation_id"], "args": args},
                    "answer": "To add a POS item, I need the **reservation** (e.g. `reservation #12`) and the **item** (name/SKU/barcode/item ID), plus quantity if not 1. I’ll verify the reservation and item exist before proposing the action."}
    # complete walk-in sale
    elif (re.search(r"\b(walk[- ]?in|counter)\b", tl) and re.search(r"\b(sale|sell|checkout|charge)\b", tl)) or intent in {"propose_complete_walkin_sale","complete_walkin_sale"}:
        action_type = "propose_complete_walkin_sale"
        # best-effort single-item payload
        if not args.get("items"):
            if args.get("item_id") or args.get("item_name") or args.get("sku") or args.get("barcode"):
                it = {"inv_id": args.get("item_id"), "item_id": args.get("item_id"), "desc": args.get("item_name") or args.get("desc"), "qty": args.get("qty") or 1, "price": args.get("amount") or args.get("price"), "batch_id": args.get("batch_id"), "lot_code": args.get("lot_code")}
                args["items"] = [it]
        if not args.get("items"):
            return {"ok": True, "intent": "action_needs_info", "tools_used": [], "data": {"action": "propose_complete_walkin_sale", "missing": ["items"], "args": args},
                    "answer": "To prepare a **walk-in sale** (no reservation), tell me the **item(s)** (name/SKU/barcode/ID), quantity, and payment method. I’ll verify each item exists and is sellable before proposing."}
        if not args.get("method"):
            return {"ok": True, "intent": "action_needs_info", "tools_used": [], "data": {"action": "propose_complete_walkin_sale", "missing": ["method"], "args": args},
                    "answer": "I can prepare that walk-in sale — which **payment method** should I use (Cash, Card, Bank Transfer, Mobile Money, etc.)? I’ll validate the item(s) first."}
    # record payment
    elif (re.search(r"\b(record|receive|take|add)\b", tl) and re.search(r"\bpayment\b", tl)) or intent in {"propose_record_payment","record_payment"}:
        action_type = "propose_record_payment"
        if not args.get("reservation_id") or args.get("amount") is None or not args.get("method"):
            missing = [k for k in ("reservation_id","amount","method") if not args.get(k) and not (k=="amount" and args.get("amount") is not None)]
            return {"ok": True, "intent": "action_needs_info", "tools_used": [], "data": {"action": "propose_record_payment", "missing": missing, "args": args},
                    "answer": "To record a payment, I need the **reservation** (e.g. `reservation #12`), **amount**, and **method** (Cash/Card/etc.). I’ll verify the reservation exists and show the balance impact before proposing."}
    # book activity
    elif (re.search(r"\b(book|schedule|add)\b", tl) and re.search(r"\b(activity|trip|tour|excursion)\b", tl)) or intent in {"propose_book_activity","book_activity"}:
        action_type = "propose_book_activity"
        if not args.get("reservation_id") or not args.get("activity_id"):
            missing = []
            if not args.get("reservation_id"): missing.append("reservation_id")
            if not args.get("activity_id"): missing.append("activity_id")
            return {"ok": True, "intent": "action_needs_info", "tools_used": [], "data": {"action": "propose_book_activity", "missing": missing, "args": args},
                    "answer": "To book an activity, I need the **reservation** (e.g. `reservation #12`) and **activity ID**. I’ll verify both exist and the reservation is eligible before proposing."}
    # create reservation
    elif (re.search(r"\b(create|make|add|book)\b", tl) and re.search(r"\breservation\b", tl) and not re.search(r"\b(item|product|pos|payment|activity|trip|tour|excursion)\b", tl)) or intent in {"propose_create_reservation","create_reservation"}:
        action_type = "propose_create_reservation"
        required = []
        if not args.get("guest_name") and not args.get("guest_id"): required.append("guest_name")
        if not args.get("room_id"): required.append("room_id")
        if not args.get("check_in"): required.append("check_in")
        if not args.get("check_out"): required.append("check_out")
        if not args.get("rate") and args.get("amount") is None: required.append("rate")
        if required:
            return {"ok": True, "intent": "action_needs_info", "tools_used": [], "data": {"action": "propose_create_reservation", "missing": required, "args": args},
                    "answer": "To create a reservation, I need **guest name/ID, room, check-in, check-out, and nightly rate** (and source/rate plan/deposit if applicable). I’ll verify guest/room/rate plan exist and check availability before proposing."}
        if args.get("amount") is not None and not args.get("rate"):
            args["rate"] = args.get("amount")

    if not action_type:
        return None

    try:
        from .actions import create_proposal
        proposal = create_proposal(action_type, args, ui_context=ui_context or {})
        pre = (proposal.get("prechecks") or {}) if isinstance(proposal, dict) else {}
        ok = bool(pre.get("ok")) if pre else False
        summary = proposal.get("summary") or "Proposed action"
        if ok:
            ans = f"**Proposed action (requires your confirmation):** {summary}\n\nI’ve checked the referenced entities exist and validated the request as much as possible. Review the details and click **Confirm** to execute, or **Cancel**. No changes are made until you confirm."
        else:
            errs = pre.get("errors") or []
            warns = pre.get("warnings") or []
            parts = [f"**I can’t propose this action yet (validation failed):** {summary}"]
            if errs:
                parts.append("**Issues:**\n" + "\n".join([f"- {e}" for e in errs[:8]]))
            if warns:
                parts.append("**Notes:**\n" + "\n".join([f"- {w}" for w in warns[:6]]))
            parts.append("Please correct the request (ensure the item/reservation/IDs exist and are valid) and I’ll re-check.")
            ans = "\n\n".join(parts)
        return {
            "ok": ok,
            "intent": action_type,
            "tools_used": ["validate_entities"],
            "data": {"proposal": proposal, "prechecks": pre, "args": args},
            "answer": ans,
            "proposed_action": proposal if ok else None,
            "source": "action_proposal",
            "needs_confirmation": ok,
        }
    except Exception as e:
        logger.exception("assistant._maybe_propose_action failed")
        return {"ok": False, "intent": action_type or "action_error", "tools_used": [], "data": {"error": str(e), "args": args},
                "answer": f"I couldn’t prepare that action safely: {e}", "proposed_action": None}

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

def _execute_single(intent: str, text: str, slots: dict, ui_context=None):
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

    if intent == "capabilities_help":
        data = execute_tool("list_capabilities", {}); tools_used.append("list_capabilities")
        return _answer(intent, tools_used, data)

    if intent == "unsupported_or_unsafe":
        ans = ("I can help with **read-only** questions grounded in real app data (reports, guests/portfolios, reservations, balances, inventory, activities, ops, insights). "
               "I can’t perform actions, make changes, or guess/invent information. "
               "If you tell me what you’d like to look up (e.g. a guest, reservation, report, or date range), I’ll fetch it for you.")
        return {"ok": False, "intent": intent, "tools_used": [], "data": {"reason": "unsafe_or_out_of_scope"}, "answer": ans}

    if intent == "ambiguous_needs_clarification":
        tl = _norm_text(text)
        if rid and _has_any(tl, ["balance", "owe", "outstanding", "due"]):
            return _execute_single("reservation_balance", text, slots)
        if _has_any(tl, ["balance", "owe", "outstanding", "due"]) and not rid:
            return {"ok": True, "intent": intent, "needs_clarification": True, "tools_used": [], "data": {"missing": "reservation_id", "suggestion": "overall_ar"},
                    "answer": "Do you mean a **specific reservation’s balance** (e.g. `reservation #12 balance`) or **overall outstanding balances** (who owes money across all reservations)? Tell me which one and I’ll pull the real figures."}
        if _has_any(tl, ["ordered", "last visit", "last stay", "profile", "spend", "portfolio", "invoice", "activity", "activities", "trip", "excursion", "when did they", "he ", "she ", "they ", "his ", "her ", "their "]) and not (slots.get("guest_id") or slots.get("guest_query")):
            return {"ok": True, "intent": intent, "needs_clarification": True, "tools_used": [], "data": {"missing": "guest"},
                    "answer": "Which guest do you mean? Please share a **guest ID** (e.g. `guest #3`) or a name/phone fragment so I can look them up in the real records."}
        if (("report" in tl) or tl.startswith("report")) and not slots.get("report_kind") and not rid:
            return {"ok": True, "intent": intent, "needs_clarification": True, "tools_used": [], "data": {"missing": "report_kind"},
                    "answer": "Which report would you like — **P&L, cash flow, VAT, payment methods, occupancy, or daily ops** — and for what date/range (e.g. `P&L last month` or `daily ops today`)?"}
        return {"ok": True, "intent": intent, "needs_clarification": True, "tools_used": [], "data": {"missing": "context"},
                "answer": "I want to make sure I get this right — could you rephrase with a bit more detail? I can help with briefings, property/ops stats, reports & comparisons, who owes money, inventory alerts, top sellers, expenses/sales, expiring batches and batch/lot lists, barcode/lot traceability (received/expiry/movements), guests (search/profile/full portfolio/last visit/orders/spend/activities), reservations/balances/pipeline/groups, packages/bundles, housekeeping/tasks/room blocks, availability, activities, menus, rate plans, cash shifts, night-audit runs, integrity/doctor/health diagnostics (including backup/restore readiness and batch-stock consistency), and app version/update status."}

    if intent == "reservation_group_detail":
        gidq = slots.get("group_id") or slots.get("context", {}).get("group_id")
        if not gidq:
            return {"ok": True, "intent": intent, "needs_clarification": True, "tools_used": [], "data": {"missing": "group_id"},
                    "answer": "Which reservation group should I show? Please share a group ID (e.g. `group #3 details` or `group folio for group 3`) so I can fetch grounded members and totals."}
        data = execute_tool("get_reservation_group", {"group_id": gidq}); tools_used.append("get_reservation_group")
        return _answer(intent, tools_used, data)


    if intent == "reservation_financial_integrity":
        ridq = slots.get("reservation_id") or slots.get("context", {}).get("reservation_id")
        if not ridq:
            m = re.search(r"(?:reservation|res|booking)\s*#?\s*(\d+)", text or "", re.I)
            if m:
                try: ridq = int(m.group(1))
                except Exception: ridq = None
        if not ridq:
            return _need("Which reservation ID should I check for financial integrity?", intent, slots, missing=["reservation_id"])
        data = execute_tool("verify_reservation_financials", {"reservation_id": _safe_int(ridq)})
        tools_used.append("verify_reservation_financials")
        return _answer(intent, tools_used, data)

    if intent == "reservation_packages_applied":
        ridq = slots.get("reservation_id") or slots.get("context", {}).get("reservation_id")
        if not ridq:
            return {"ok": True, "intent": intent, "needs_clarification": True, "tools_used": [], "data": {"missing": "reservation_id"},
                    "answer": "Which reservation’s applied packages should I show? Please include a reservation ID (e.g. `packages on reservation #12`)."}
        data = execute_tool("list_reservation_packages", {"reservation_id": ridq}); tools_used.append("list_reservation_packages")
        return _answer(intent, tools_used, data)

    if intent == "approval_detail":
        aid = _safe_int(slots.get("approval_id") or slots.get("id") or slots.get("context", {}).get("approval_id"))
        if not aid:
            m = re.search(r"approval\s*#?\s*(\d+)", text or "", re.I)
            if m:
                try: aid = int(m.group(1))
                except Exception: aid = None
        if not aid:
            return _need("Which approval ID should I look up?", intent, slots, missing=["approval_id"])
        data = execute_tool("get_approval", {"approval_id": aid})
        tools_used.append("get_approval")
        return _answer(intent, tools_used, data)

    # Simple read-only intent -> tool mappings (behavior-preserving consolidation)
    if intent in ("property_stats", "today_ops", "outstanding_balances", "inventory_alerts", "reservation_pipeline",
                  "housekeeping_summary", "menu_info", "rate_plans", "rate_rules_list", "service_catalog_list", "discount_vouchers_list", "approvals_list", "recent_activity", "list_activities",
                  "activity_stats", "walkin_sales_summary", "reservation_groups", "packages_list", "cash_shifts",
                  "night_audit_runs", "room_blocks", "housekeeping_tasks", "housekeeping_ops_report"):
        tool = {
            "property_stats": "get_property_stats",
            "today_ops": "get_today_ops",
            "outstanding_balances": "get_outstanding_balances",
            "inventory_alerts": "get_inventory_alerts",
            "reservation_pipeline": "get_reservation_pipeline",
            "housekeeping_summary": "get_housekeeping_summary",
            "menu_info": "get_menu_info",
            "rate_plans": "list_rate_plans",
            "rate_rules_list": "list_rate_rules",
            "service_catalog_list": "list_service_catalog",
            "discount_vouchers_list": "list_discount_vouchers",
            "approvals_list": "list_approvals",
            "recent_activity": "get_recent_activity",
            "list_activities": "list_activities",
            "activity_stats": "get_activity_stats",
            "walkin_sales_summary": "get_walkin_sales_summary",
            "reservation_groups": "list_reservation_groups",
            "packages_list": "list_packages",
            "cash_shifts": "get_cash_shifts",
            "night_audit_runs": "list_night_audit_runs",
            "room_blocks": "list_room_blocks",
            "housekeeping_tasks": "list_housekeeping_tasks",
            "housekeeping_ops_report": "get_housekeeping_ops_report",
        }[intent]
        args = {}
        data = execute_tool(tool, args); tools_used.append(tool)
        return _answer(intent, tools_used, data)

    if intent in ("business_partner_briefing", "decision_support", "compare_periods", "occupancy_summary",
                  "expenses_summary", "sales_breakdown", "payments_summary"):
        tool = {
            "business_partner_briefing": "business_partner_briefing",
            "decision_support": "get_decision_support",
            "compare_periods": "compare_periods",
            "occupancy_summary": "get_occupancy_summary",
            "expenses_summary": "get_expenses_summary",
            "sales_breakdown": "get_sales_breakdown",
            "payments_summary": "get_payments_summary",
        }[intent]
        data = execute_tool(tool, {"start": start, "end": end}); tools_used.append(tool)
        title = f"Decision support ({start} → {end})" if intent == "decision_support" else "Result"
        return _answer(intent, tools_used, data, title=title)

    if intent == "top_selling_items":
        data = execute_tool("get_top_selling_items", {"start": start, "end": end, "limit": 10}); tools_used.append("get_top_selling_items")
        return _answer(intent, tools_used, data)

    if intent in ("front_desk_ops", "front_desk_arrivals", "front_desk_departures", "front_desk_in_house"):
        day = slots.get("date_start") or slots.get("date_end") or end or start
        tool = {
            "front_desk_ops": "get_front_desk_ops",
            "front_desk_arrivals": "get_front_desk_arrivals",
            "front_desk_departures": "get_front_desk_departures",
            "front_desk_in_house": "get_front_desk_in_house",
        }[intent]
        data = execute_tool(tool, {"date": day} if day else {})
        tools_used.append(tool)
        # reuse today_ops formatting for ops snapshot where helpful
        if intent == "front_desk_ops":
            return {"ok": not bool(data.get("error")), "intent": "today_ops", "tools_used": tools_used, "data": {"date": data.get("date"), "ops": data, "front_desk": data.get("summary"), "housekeeping": execute_tool("get_housekeeping_summary", {}).get("summary")}, "answer": _format_for_intent("today_ops", {"date": data.get("date"), "ops": data, "front_desk": data.get("summary"), "housekeeping": execute_tool("get_housekeeping_summary", {}).get("summary")})}
        return _answer(intent, tools_used, data)

    if intent in ("report_pl", "report_cash_flow", "report_vat", "report_payment_methods", "report_occupancy", "report_daily_ops"):
        kind = intent.replace("report_", "")
        data = execute_tool("get_report", {"kind": kind, "start": start, "end": end}); tools_used.append("get_report")
        return _answer(intent, tools_used, data)

    if intent == "room_availability":
        a_start = slots.get("date_start") or start; a_end = slots.get("date_end") or end
        data = execute_tool("get_room_availability", {"start": a_start, "end": a_end}); tools_used.append("get_room_availability")
        return _answer(intent, tools_used, data)

    if intent in ("ledger_integrity", "health_diagnostics", "doctor_report"):
        data = execute_tool("get_health_diagnostics", {}); tools_used.append("get_health_diagnostics")
        out_intent = "ledger_integrity" if intent == "ledger_integrity" else "health_diagnostics"
        return _answer(out_intent, tools_used, data)

    if intent in ("expiring_batches", "get_expiring_batches"):
        days = slots.get("days") or slots.get("within_days")
        try:
            days = int(days) if days not in (None, "") else None
        except (TypeError, ValueError):
            logger.debug("assistant._execute_single: invalid within_days=%r", days, exc_info=True)
            days = None
        data = execute_tool("get_expiring_batches", {"within_days": days, "include_expired": True}); tools_used.append("get_expiring_batches")
        return _answer("expiring_batches", tools_used, data)

    if intent in ("inventory_batches", "list_inventory_batches"):
        item_id = slots.get("item_id")
        data = execute_tool("list_inventory_batches", {"item_id": item_id}); tools_used.append("list_inventory_batches")
        return _answer("inventory_batches", tools_used, data)

    if intent in ("trace_barcode", "barcode_trace"):
        code = slots.get("barcode") or slots.get("sku") or slots.get("code") or slots.get("lot_code") or slots.get("query") or slots.get("guest_query")
        if not code:
            m = re.search(r"(?:barcode|sku|lot|code|trace)\s*[:#]?\s*([A-Za-z0-9._\-]+)", text or "", re.I)
            if m: code = m.group(1)
        if not code:
            toks = re.findall(r"[A-Za-z0-9][A-Za-z0-9._\-]{2,}", text or "")
            if toks: code = toks[-1]
        data = execute_tool("trace_barcode", {"code": code}); tools_used.append("trace_barcode")
        return _answer("trace_barcode", tools_used, data)

    if intent == "reservation_search":
        q = slots.get("guest_query") or slots.get("query") or ""
        if not q:
            q = re.sub(r"(?i)\b(find|search|show|list|look up|reservation|reservations|booking|bookings|for|of)\b", " ", text)
            q = re.sub(r"\s+", " ", q).strip(" ?.,#:") or ""
        data = execute_tool("search_reservations", {"query": q, "reservation_id": rid, "status": slots.get("status") or "", "source": slots.get("source") or "", "market_segment": slots.get("market_segment") or slots.get("segment") or "", "start": start, "end": end})
        tools_used.append("search_reservations")
        return _answer(intent, tools_used, data)

    if intent == "inventory_search":
        q = slots.get("query") or slots.get("guest_query") or ""
        if not q:
            q = re.sub(r"(?i)\b(search|find|list|show|inventory|product|products|item|items|stock|for|of)\b", " ", text)
            q = re.sub(r"\s+", " ", q).strip(" ?.,#:") or ""
        data = execute_tool("search_inventory", {"query": q, "category": slots.get("category") or "", "stock_filter": slots.get("stock_filter") or ""})
        tools_used.append("search_inventory")
        return _answer(intent, tools_used, data)

    if intent == "inventory_item_details":
        item_id = slots.get("item_id") or slots.get("inventory_item_id")
        data = execute_tool("get_inventory_item", {"item_id": item_id})
        tools_used.append("get_inventory_item")
        return _answer(intent, tools_used, data)

    # reservations
    if intent in ("reservation_balance","reservation_summary"):
        if not rid: return _execute_single("ambiguous_needs_clarification", text, slots, ui_context=ui_context)
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
        return _execute_single("ambiguous_needs_clarification", text, slots, ui_context=ui_context)

    # fallback
    data=execute_tool("list_capabilities",{}); tools_used.append("list_capabilities")
    ans = "I couldn’t confidently map that request to a supported, grounded action. " + _fmt_capabilities(data)
    return {"ok":True,"intent":"capabilities_help","tools_used":tools_used,"data":data,"answer":ans}

def _execute_compound(text: str, slots: dict, ui_context=None):
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
    if _has_any(tl,["walk-in sales","walkin sales","walk in sales","counter sales","non-reservation sales"]): subs.append(("walkin_sales_summary",slots))
    if _has_any(tl,["payments summary","payment totals","total payments","money received"]): subs.append(("payments_summary",slots))
    if _has_any(tl,["search inventory","find product","search product","list products","inventory search"]): subs.append(("inventory_search",slots))
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
    if not uniq: return _execute_single("ambiguous_needs_clarification", text, slots, ui_context=ui_context)
    if len(uniq)==1: return _execute_single(uniq[0][0], text, uniq[0][1], ui_context=ui_context)
    tools_used=[]; parts=[]; data_all={}; ok=True
    parts.append("Sure — here’s what I found (grounded in real data):")
    for it,sl in uniq[:6]:
        res=_execute_single(it,text,sl,ui_context=ui_context)
        tools_used.extend(res.get("tools_used") or [])
        label = it.replace("_"," ").title()
        parts.append(f"\n### {label}\n{res.get('answer','')}")
        data_all[it]=res.get("data")
        ok = ok and bool(res.get("ok",True))
    return {"ok":ok,"intent":"compound_multi_intent","tools_used":list(dict.fromkeys(tools_used)),"data":data_all,"answer":"\n".join(parts)}

