"""Read-only grounded assistant tools and registry."""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from .common import (
    _fmt_money,
    _iso,
    _parse_date_range,
    _prev_period,
    _safe_int,
    _today,
)
from .. import services

logger = logging.getLogger(__name__)

# ── Read-only tools (grounded) ───────────────────────────────────────────────


def tool_list_capabilities(args=None):
    try:
        from .brain import load_brain

        b = load_brain()
        tools = [
            {"id": t.get("id"), "description": t.get("description", ""), "type": t.get("type", "native")}
            for t in b.tools(enabled_only=True)
        ]
        skills = [
            {"id": s.get("id"), "name": s.get("name", s.get("id")), "description": s.get("description", "")}
            for s in b.skills(enabled_only=True)
        ]
        return {
            "agent": {"name": b.name, "boundaries": b.boundaries},
            "tools": tools,
            "skills": skills,
            "capabilities": [t.get("description", "") for t in b.tools(enabled_only=True) if t.get("description")],
            "notes": [
                "Capabilities, tools, skills, and workflows are declared in agent_brain.json (single control file).",
                "All answers grounded in real backend data (read-only) unless an explicit validated action flow is confirmed.",
            ],
        }
    except Exception:
        return {
            "capabilities": ["Manager assistant capabilities unavailable."],
            "notes": ["Failed to load agent_brain.json; check assistant brain configuration."],
        }


def tool_search_guests(args):
    q = (args or {}).get("query") or (args or {}).get("q") or ""
    return {"query": q, "guests": services.assistant_find_guests(q, limit=(args or {}).get("limit") or 10)}


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
    if not gid:
        return {"error": "guest_id is required"}
    return services.assistant_get_top_items_last_visit(gid, limit=(args or {}).get("limit") or 5)


def tool_get_guest_last_visit_spend(args):
    gid = _safe_int((args or {}).get("guest_id") or (args or {}).get("id"))
    if not gid:
        return {"error": "guest_id is required"}
    visit = services.assistant_get_guest_last_visit(gid)
    lv = visit.get("last_visit") or {}
    rid = lv.get("reservation_id") or lv.get("id")
    if not rid:
        return {**visit, "spend": None}
    try:
        bd = services.get_balance_breakdown(rid)
        items = services.assistant_get_top_items_for_reservation(rid, limit=10)
        return {**visit, "balance_breakdown": bd, "top_items": items}
    except Exception as e:
        return {**visit, "spend_error": str(e)}


def tool_get_guest_portfolio(args):
    gid = _safe_int((args or {}).get("guest_id") or (args or {}).get("id"))
    if not gid:
        return {"error": "guest_id is required"}
    try:
        p = services.get_guest_portfolio(gid)
        if not p:
            return {"error": f"Guest #{gid} not found"}
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
    if not rid:
        return {"error": "reservation_id is required"}
    return services.assistant_reservation_summary(rid) or {"error": f"Reservation #{rid} not found"}


def tool_get_guest_balance(args):
    rid = _safe_int((args or {}).get("reservation_id") or (args or {}).get("id"))
    if not rid:
        return {"error": "reservation_id is required"}
    try:
        return {
            "reservation_id": rid,
            "balance": services.calculate_guest_balance(rid),
            "breakdown": services.get_balance_breakdown(rid),
        }
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
            return {
                "kind": "payment_methods",
                "start": start,
                "end": end,
                "report": services.report_payment_methods(start, end),
            }
        if kind in ("occupancy", "occ"):
            return {"kind": "occupancy", "start": start, "end": end, "report": services.report_occupancy(start, end)}
        if kind in ("daily_ops", "daily", "ops"):
            day = end or start
            return {"kind": "daily_ops", "date": day, "report": services.report_daily_ops(day)}
        return {"error": f"Unknown report kind '{kind}'"}
    except Exception as e:
        return {"kind": kind, "start": start, "end": end, "error": str(e)}


def tool_get_decision_support(args):
    start, end = (args or {}).get("start"), (args or {}).get("end")
    if not start or not end:
        start, end = _parse_date_range((args or {}).get("range") or "")
    try:
        return {
            "start": start,
            "end": end,
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
                logger.debug(
                    "assistant.tool_list_rate_plans: failed to count reservations for plan_id=%s",
                    p["id"],
                    exc_info=True,
                )
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
    try:
        from ..db import connect

        c = connect()
        stats = {}
        stats["guests_total"] = c.execute("SELECT COUNT(*) AS n FROM guests").fetchone()["n"]
        stats["rooms_total"] = c.execute("SELECT COUNT(*) AS n FROM rooms").fetchone()["n"]
        rows = c.execute("SELECT status, COUNT(*) AS n FROM reservations GROUP BY status").fetchall()
        stats["reservations_by_status"] = {r["status"]: r["n"] for r in rows}
        stats["reservations_total"] = sum(stats["reservations_by_status"].values())
        stats["in_house"] = stats["reservations_by_status"].get("Checked In", 0)
        today = _iso(_today())
        stats["arrivals_today"] = c.execute(
            "SELECT COUNT(*) AS n FROM reservations WHERE check_in_date=? AND status IN ('Reserved','Checked In')",
            (today,),
        ).fetchone()["n"]
        stats["departures_today"] = c.execute(
            "SELECT COUNT(*) AS n FROM reservations WHERE check_out_date=? AND status IN ('Reserved','Checked In')",
            (today,),
        ).fetchone()["n"]
        return {"stats": stats, "as_of": today}
    except Exception as e:
        return {"error": str(e)}


def tool_get_today_ops(args=None):
    try:
        day = None
        if isinstance(args, dict):
            day = args.get("date") or args.get("day")
        day = day or _iso(_today())
        ops_obj = None
        ops_text = None
        try:
            if hasattr(services, "get_front_desk_ops"):
                ops_obj = services.get_front_desk_ops(day)
        except Exception:
            logger.debug("assistant.tool_get_today_ops: front desk ops unavailable", exc_info=True)
            ops_obj = None
        try:
            ops_text = services.report_daily_ops(day)
        except Exception:
            ops_text = None
        fd = (ops_obj or {}).get("summary") if isinstance(ops_obj, dict) else None
        if fd is None:
            try:
                fd = services.get_front_desk_summary()
            except Exception:
                fd = None
        hk = tool_get_housekeeping_summary({}).get("summary")
        return {"date": day, "daily_ops": ops_text, "ops": ops_obj, "front_desk": fd, "housekeeping": hk}
    except Exception as e:
        return {"error": str(e)}


def tool_get_front_desk_ops(args=None):
    try:
        args = args or {}
        day = args.get("date") or args.get("day") or _iso(_today())
        if not hasattr(services, "get_front_desk_ops"):
            return {"error": "Front desk ops not available"}
        return services.get_front_desk_ops(day)
    except Exception as e:
        return {"error": str(e)}


def tool_get_front_desk_arrivals(args=None):
    try:
        args = args or {}
        day = args.get("date") or args.get("day") or _iso(_today())
        if not hasattr(services, "get_front_desk_arrivals"):
            return {"error": "Front desk arrivals not available"}
        rows = services.get_front_desk_arrivals(day)
        return {"date": day, "arrivals": rows, "count": len(rows)}
    except Exception as e:
        return {"error": str(e)}


def tool_get_front_desk_in_house(args=None):
    try:
        args = args or {}
        day = args.get("date") or args.get("day") or _iso(_today())
        if not hasattr(services, "get_front_desk_in_house"):
            return {"error": "Front desk in-house view not available"}
        rows = services.get_front_desk_in_house(day)
        return {"date": day, "in_house": rows, "count": len(rows)}
    except Exception as e:
        return {"error": str(e)}


def tool_get_front_desk_departures(args=None):
    try:
        args = args or {}
        day = args.get("date") or args.get("day") or _iso(_today())
        if not hasattr(services, "get_front_desk_departures"):
            return {"error": "Front desk departures not available"}
        rows = services.get_front_desk_departures(day)
        return {"date": day, "departures": rows, "count": len(rows)}
    except Exception as e:
        return {"error": str(e)}


def tool_get_housekeeping_summary(args=None):
    try:
        from ..db import connect

        rows = (
            connect()
            .execute("SELECT housekeeping_status, COUNT(*) AS n FROM rooms GROUP BY housekeeping_status")
            .fetchall()
        )
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

        return {
            "alerts": {
                "negative": pack(w.get("negative", []), ["id", "item_name", "stock_qty"]),
                "low": pack(w.get("low", []), ["id", "item_name", "stock_qty", "threshold"]),
                "overstock": pack(w.get("over", []), ["id", "item_name", "stock_qty", "threshold"]),
            }
        }
    except Exception as e:
        return {"error": str(e)}


def tool_get_outstanding_balances(args=None):
    try:
        from ..db import connect

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
                logger.debug(
                    "assistant.tool_get_outstanding_balances: failed to calculate balance for reservation_id=%s",
                    r["id"],
                    exc_info=True,
                )
                bal = 0.0
            if bal and bal > 0.009:
                outstanding.append(
                    {
                        "reservation_id": r["id"],
                        "guest_name": r["guest_name"],
                        "room": r["room_number"],
                        "status": r["status"],
                        "check_in": r["check_in_date"],
                        "check_out": r["check_out_date"],
                        "balance_due": round(bal, 2),
                    }
                )
                total_due += bal
        outstanding.sort(key=lambda x: x["balance_due"], reverse=True)
        return {"outstanding": outstanding[:50], "count": len(outstanding), "total_due": round(total_due, 2)}
    except Exception as e:
        return {"error": str(e)}


def tool_get_top_selling_items(args=None):
    start, end = (args or {}).get("start"), (args or {}).get("end")
    if not start or not end:
        start, end = _parse_date_range((args or {}).get("range") or "")
    limit = int((args or {}).get("limit") or 10)
    try:
        from ..db import connect

        rows = (
            connect()
            .execute(
                """
            SELECT item_description, SUM(quantity) AS qty_sold,
                   SUM((unit_price * quantity) + COALESCE(tax_amount,0)) AS gross, COUNT(*) AS lines
            FROM pos_tabs
            WHERE voided_at IS NULL AND date(created_at) >= ? AND date(created_at) <= ?
            GROUP BY item_description ORDER BY qty_sold DESC, gross DESC LIMIT ?""",
                (start, end, limit),
            )
            .fetchall()
        )
        items = [
            {
                "item": r["item_description"],
                "qty_sold": r["qty_sold"],
                "gross": round(r["gross"] or 0, 2),
                "lines": r["lines"],
            }
            for r in rows
        ]
        return {"start": start, "end": end, "top_items": items}
    except Exception as e:
        return {"start": start, "end": end, "error": str(e)}


def tool_get_expenses_summary(args=None):
    start, end = (args or {}).get("start"), (args or {}).get("end")
    if not start or not end:
        start, end = _parse_date_range((args or {}).get("range") or "")
    try:
        from ..db import connect

        rows = (
            connect()
            .execute(
                """
            SELECT c.category_name, COUNT(*) AS n, SUM(e.net_amount) AS net, SUM(e.vat_amount) AS vat,
                   SUM(e.net_amount + e.vat_amount) AS total
            FROM operational_expenses e JOIN expense_categories c ON e.category_id=c.id
            WHERE e.voided_at IS NULL AND date(e.at) >= ? AND date(e.at) <= ?
            GROUP BY c.category_name ORDER BY total DESC""",
                (start, end),
            )
            .fetchall()
        )
        cats = [
            {
                "category": r["category_name"],
                "count": r["n"],
                "net": round(r["net"] or 0, 2),
                "vat": round(r["vat"] or 0, 2),
                "total": round(r["total"] or 0, 2),
            }
            for r in rows
        ]
        total = round(sum(x["total"] for x in cats), 2)
        return {"start": start, "end": end, "by_category": cats, "total": total}
    except Exception as e:
        return {"start": start, "end": end, "error": str(e)}


def tool_get_sales_breakdown(args=None):
    start, end = (args or {}).get("start"), (args or {}).get("end")
    if not start or not end:
        start, end = _parse_date_range((args or {}).get("range") or "")
    try:
        from ..db import connect

        c = connect()
        by_item = c.execute(
            """
            SELECT item_description, SUM(quantity) AS qty, SUM((unit_price*quantity)+COALESCE(tax_amount,0)) AS gross
            FROM pos_tabs WHERE voided_at IS NULL AND date(created_at)>=? AND date(created_at)<=?
            GROUP BY item_description ORDER BY gross DESC LIMIT 20""",
            (start, end),
        ).fetchall()
        pay = c.execute(
            """
            SELECT method, COUNT(*) AS n, SUM(amount) AS total FROM payments
            WHERE voided_at IS NULL AND date(at)>=? AND date(at)<=?
            GROUP BY method ORDER BY total DESC""",
            (start, end),
        ).fetchall()
        src = c.execute(
            """
            SELECT source, COUNT(*) AS n FROM reservations
            WHERE status NOT IN ('Deleted') AND date(created_at)>=? AND date(created_at)<=?
            GROUP BY source ORDER BY n DESC""",
            (start, end),
        ).fetchall()
        return {
            "start": start,
            "end": end,
            "pos_by_item": [
                {"item": r["item_description"], "qty": r["qty"], "gross": round(r["gross"] or 0, 2)} for r in by_item
            ],
            "payments_by_method": [
                {"method": r["method"], "count": r["n"], "total": round(r["total"] or 0, 2)} for r in pay
            ],
            "reservations_by_source": [{"source": r["source"], "count": r["n"]} for r in src],
        }
    except Exception as e:
        return {"start": start, "end": end, "error": str(e)}


def tool_get_reservation_pipeline(args=None):
    try:
        from ..db import connect

        c = connect()
        rows = c.execute("SELECT status, COUNT(*) AS n FROM reservations GROUP BY status").fetchall()
        by_status = {r["status"]: r["n"] for r in rows}
        today = _iso(_today())
        soon = _iso(_today() + timedelta(days=7))
        upcoming = c.execute(
            """SELECT r.id, r.guest_name, rm.room_number, r.check_in_date, r.check_out_date, r.status
                                FROM reservations r LEFT JOIN rooms rm ON r.room_id=rm.id
                                WHERE r.status='Reserved' AND r.check_in_date>=? AND r.check_in_date<=?
                                ORDER BY r.check_in_date LIMIT 20""",
            (today, soon),
        ).fetchall()
        return {"by_status": by_status, "upcoming_arrivals_7d": [dict(r) for r in upcoming]}
    except Exception as e:
        return {"error": str(e)}


def tool_get_room_availability(args=None):
    start = (args or {}).get("start") or (args or {}).get("check_in") or _iso(_today())
    end = (args or {}).get("end") or (args or {}).get("check_out") or _iso(_today() + timedelta(days=1))
    try:
        rows = services.get_rooms_available_between(start, end)
        return {"start": start, "end": end, "available_count": len(rows), "rooms": [dict(r) for r in rows[:50]]}
    except Exception as e:
        return {"start": start, "end": end, "error": str(e)}


def tool_get_menu_info(args=None):
    try:
        meals = services.list_meals(active_only=False) if hasattr(services, "list_meals") else []
        out = []
        for m in (meals or [])[:80]:
            try:
                d = dict(m) if not isinstance(m, dict) else m
            except Exception:
                d = {"id": getattr(m, "id", None), "name": m.get("name") if hasattr(m, "get") else str(m)}
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
    if not start or not end:
        start, end = _parse_date_range((args or {}).get("range") or "this month")
    p1s, p1e = _prev_period(start, end)

    def snap(s, e):
        out = {"start": s, "end": e}
        try:
            pl = services.report_pl_statement(s, e)
            out["pl"] = pl if isinstance(pl, dict) else {"text": str(pl)[:1000]}
        except Exception as ex:
            out["pl_error"] = str(ex)
        try:
            occ = services.get_occupancy_for_window(s, e)
            out["occupancy"] = occ
        except Exception as ex:
            out["occ_error"] = str(ex)
        try:
            cf = services.report_cash_flow(s, e)
            out["cash_flow"] = cf if isinstance(cf, dict) else {"text": str(cf)[:800]}
        except Exception as ex:
            out["cash_error"] = str(ex)
        return out

    return {"current": snap(start, end), "previous": snap(p1s, p1e)}


def tool_business_partner_briefing(args=None):
    start, end = (args or {}).get("start"), (args or {}).get("end")
    if not start or not end:
        start, end = _parse_date_range((args or {}).get("range") or "this month")
    data = {"start": start, "end": end, "sections": {}}
    insights = {"snapshot": [], "good": [], "watchouts": [], "actions": []}
    try:
        ps = tool_get_property_stats({}).get("stats") or {}
        data["sections"]["property_stats"] = ps
        insights["snapshot"].append(
            f"Guests {ps.get('guests_total', 0)}, rooms {ps.get('rooms_total', 0)}, reservations {ps.get('reservations_total', 0)}, in-house {ps.get('in_house', 0)}."
        )
    except Exception:
        logger.debug("assistant.business_partner_briefing: property stats unavailable", exc_info=True)
    try:
        fd = services.get_front_desk_summary()
        data["sections"]["front_desk"] = fd
        insights["snapshot"].append(
            f"Today: arrivals {fd.get('arrivals_today', 0)}, departures {fd.get('departures_today', 0)}, occupancy {fd.get('occupancy_pct', 0)}% (clean {fd.get('clean', 0)}, dirty {fd.get('dirty', 0)}, maint {fd.get('maint', 0)})."
        )
        if fd.get("dirty", 0) > 0:
            insights["watchouts"].append(f"{fd['dirty']} room(s) marked Dirty — prioritize housekeeping for readiness.")
        if fd.get("maint", 0) > 0:
            insights["watchouts"].append(f"{fd['maint']} room(s) in Maintenance — impacts sellable inventory.")
    except Exception:
        logger.debug("assistant.business_partner_briefing: front desk summary unavailable", exc_info=True)
    try:
        pl = services.report_pl_statement(start, end)
        data["sections"]["pl"] = pl
        if isinstance(pl, dict):
            rev = float(pl.get("revenue", pl.get("total_revenue", pl.get("net_revenue", 0))) or 0)
            exp = float(pl.get("expenses", pl.get("total_expenses", 0)) or 0)
            net = float(pl.get("net_profit", pl.get("net_income", rev - exp)) or (rev - exp))
            insights["snapshot"].append(
                f"Period {start}→{end}: revenue {_fmt_money(rev)}, expenses {_fmt_money(exp)}, net {_fmt_money(net)}."
            )
            if net < 0:
                insights["watchouts"].append("Net result is negative for the period — review costs and pricing/mix.")
            elif rev > 0 and net / rev >= 0.2:
                insights["good"].append("Healthy net margin in the period.")
            if exp > rev and rev > 0:
                insights["watchouts"].append("Expenses exceed revenue in the period.")
        elif isinstance(pl, str):
            insights["snapshot"].append(f"P&L ({start}→{end}) available (text report).")
    except Exception:
        logger.debug("assistant.business_partner_briefing: P&L unavailable for %s..%s", start, end, exc_info=True)
    try:
        occ = services.get_occupancy_for_window(start, end)
        data["sections"]["occupancy"] = occ
        pct = float(occ.get("pct", occ.get("occupancy_pct", 0)) or 0)
        insights["snapshot"].append(
            f"Occupancy {pct}% (range), ADR {_fmt_money(occ.get('adr', 0))}, RevPAR {_fmt_money(occ.get('revpar', 0))}."
        )
        if pct >= 80:
            insights["good"].append("Strong occupancy — consider upsell, protect rate, and optimize channel mix.")
        elif pct <= 35:
            insights["watchouts"].append(
                "Low occupancy — consider promotions, packages, or distribution/channel actions."
            )
    except Exception:
        logger.debug("assistant.business_partner_briefing: occupancy unavailable for %s..%s", start, end, exc_info=True)
    try:
        ar = tool_get_outstanding_balances({})
        data["sections"]["outstanding"] = ar
        if ar.get("count", 0) > 0:
            insights["watchouts"].append(
                f"Outstanding balances: {ar['count']} reservation(s), total due {_fmt_money(ar.get('total_due', 0))} — prioritize collections/follow-up."
            )
        else:
            insights["good"].append("No outstanding guest balances detected on active/checked-out reservations.")
    except Exception:
        logger.debug("assistant.business_partner_briefing: outstanding balances unavailable", exc_info=True)
    try:
        inv = tool_get_inventory_alerts({})["alerts"]
        data["sections"]["inventory_alerts"] = inv
        lowc = len(inv.get("low", [])) + len(inv.get("negative", []))
        overc = len(inv.get("overstock", []))
        if lowc:
            insights["watchouts"].append(
                f"Inventory: {lowc} low/negative item(s) — restock to avoid stockouts/service issues."
            )
        if overc:
            insights["watchouts"].append(
                f"Inventory: {overc} overstock item(s) — consider slowing purchases/promoting usage."
            )
        if not lowc and not overc:
            insights["good"].append("Inventory levels within thresholds (no low/negative/overstock alerts).")
    except Exception:
        logger.debug("assistant.business_partner_briefing: inventory alerts unavailable", exc_info=True)
    try:
        top = tool_get_top_selling_items({"start": start, "end": end, "limit": 5})
        data["sections"]["top_selling_items"] = top
        if top.get("top_items"):
            names = ", ".join([f"{i['item']} (x{i['qty_sold']})" for i in top["top_items"][:3]])
            insights["good"].append(f"Top sellers ({start}→{end}): {names} — keep stocked/feature.")
    except Exception:
        logger.debug(
            "assistant.business_partner_briefing: top sellers unavailable for %s..%s", start, end, exc_info=True
        )
    try:
        integ = services.verify_ledger_integrity()
        data["sections"]["integrity"] = integ
        if integ.get("ok"):
            insights["good"].append("Ledger/data integrity check: OK.")
        else:
            insights["watchouts"].append(
                "Ledger/data integrity reported issues — review health check before relying on reports."
            )
    except Exception:
        logger.debug("assistant.business_partner_briefing: ledger integrity check unavailable", exc_info=True)
    for w in insights["watchouts"][:5]:
        if "Outstanding balances" in w:
            insights["actions"].append("Follow up on unpaid balances (review reservation balances and collect).")
        elif "low/negative" in w:
            insights["actions"].append("Restock low/negative inventory items and verify recipes/ingredient usage.")
        elif "Dirty" in w or "Maintenance" in w:
            insights["actions"].append("Coordinate housekeeping/maintenance to maximize ready rooms.")
        elif "negative for the period" in w or "Expenses exceed revenue" in w:
            insights["actions"].append("Review expense drivers and revenue/pricing mix for the period.")
        elif "Low occupancy" in w:
            insights["actions"].append("Consider targeted offers or channel adjustments to lift occupancy.")
        elif "integrity reported issues" in w:
            insights["actions"].append("Run/open Health Check and resolve flagged inconsistencies.")
    seen = set()
    insights["actions"] = [a for a in insights["actions"] if not (a in seen or seen.add(a))]
    if not insights["actions"]:
        insights["actions"].append("Maintain current performance; monitor occupancy, collections, and stock daily.")
    data["insights"] = insights
    return data


def tool_get_app_update_status(args=None):
    try:
        from .. import updater

        local = updater.get_local_state()
        notes = [
            "Use Settings → App updates → Check for updates (web UI) to see available updates, changelog, and apply safely while preserving data (DB/exports/invoices).",
            "For git-based installs, apply uses fast-forward pull only; restart the app after a successful update.",
            "Create/verify a backup before applying updates in production-like environments; runtime data is preserved.",
        ]
        return {"app_version": local.get("app_version"), "local": local, "how_to_check": notes[0], "notes": notes}
    except Exception as e:
        return {"error": str(e)}


def tool_get_health_diagnostics(args=None):
    """Read-only doctor/health diagnostics (paths, schema, integrity, batch-stock, printer, backup/restore, update)."""
    try:
        from .. import doctor

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
        except (TypeError, ValueError):
            logger.debug("assistant.tool_get_expiring_batches: invalid days=%r", days, exc_info=True)
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
        except (TypeError, ValueError):
            logger.debug("assistant.tool_list_inventory_batches: invalid item_id=%r", item_id, exc_info=True)
            item_id = None
        status = args.get("status") or None
        expiring_within_days = args.get("expiring_within_days") or args.get("within_days")
        try:
            expiring_within_days = int(expiring_within_days) if expiring_within_days not in (None, "") else None
        except (TypeError, ValueError):
            logger.debug(
                "assistant.tool_list_inventory_batches: invalid expiring_within_days=%r",
                expiring_within_days,
                exc_info=True,
            )
            expiring_within_days = None
        include_depleted = bool(args.get("include_depleted", False))
        rows = services.list_inventory_batches(
            item_id=item_id, status=status, expiring_within_days=expiring_within_days, include_depleted=include_depleted
        )
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
                    from ..db import connect

                    logs = (
                        connect()
                        .execute(
                            "SELECT at, change_type, quantity, reference, unit_cost, balance_after FROM inventory_logs WHERE batch_id=? ORDER BY id DESC LIMIT 20",
                            (bid,),
                        )
                        .fetchall()
                    )
                    res["batch_movements"] = [dict(r) for r in logs]
            except Exception:
                logger.debug(
                    "assistant.tool_trace_barcode: failed to load batch movements for code=%r", code, exc_info=True
                )
        return res
    except Exception as e:
        return {"error": str(e)}


def tool_search_reservations(args=None):
    """Search/list reservations by guest name, status, id, source/segment, or date range (read-only)."""
    try:
        from ..db import connect

        args = args or {}
        q = (args.get("query") or args.get("q") or "").strip()
        status = (args.get("status") or "").strip()
        source = (args.get("source") or "").strip()
        market_segment = (args.get("market_segment") or args.get("segment") or "").strip()
        start = (args.get("start") or "").strip()
        end = (args.get("end") or "").strip()
        rid = _safe_int(args.get("reservation_id") or args.get("id"))
        limit = int(args.get("limit") or 30)
        if limit < 1:
            limit = 30
        if limit > 100:
            limit = 100
        where = ["1=1"]
        params = []
        if rid:
            where.append("r.id = ?")
            params.append(rid)
        if q:
            like = f"%{q}%"
            where.append(
                "(r.guest_name LIKE ? OR CAST(r.id AS TEXT) LIKE ? OR COALESCE(r.source,'') LIKE ? OR COALESCE(r.source_detail,'') LIKE ? OR COALESCE(r.market_segment,'') LIKE ? OR COALESCE(rm.room_number,'') LIKE ?)"
            )
            params.extend([like, like, like, like, like, like])
        if status:
            where.append("r.status = ?")
            params.append(status)
        if source:
            where.append("COALESCE(r.source,'') LIKE ?")
            params.append(f"%{source}%")
        if market_segment:
            where.append("COALESCE(r.market_segment,'') LIKE ?")
            params.append(f"%{market_segment}%")
        if start:
            where.append("date(r.check_in_date) >= date(?)")
            params.append(start)
        if end:
            where.append("date(r.check_out_date) <= date(?)")
            params.append(end)
        sql = f"""
            SELECT r.id, r.guest_name, r.status, r.check_in_date, r.check_out_date, r.agreed_nightly_rate,
                   r.source, COALESCE(r.source_detail,'') AS source_detail, COALESCE(r.market_segment,'') AS market_segment,
                   r.created_at, rm.room_number, rm.room_type
            FROM reservations r
            LEFT JOIN rooms rm ON r.room_id = rm.id
            WHERE {" AND ".join(where)}
            ORDER BY r.check_in_date DESC, r.id DESC
            LIMIT ?
        """
        params.append(limit)
        rows = connect().execute(sql, tuple(params)).fetchall()
        out = [dict(r) for r in rows]
        return {
            "reservations": out,
            "count": len(out),
            "filters": {
                "query": q,
                "status": status,
                "source": source,
                "market_segment": market_segment,
                "start": start,
                "end": end,
                "reservation_id": rid,
            },
        }
    except Exception as e:
        return {"error": str(e)}


def tool_search_inventory(args=None):
    """Search/list inventory products with stock, pricing, and category (read-only)."""
    try:
        args = args or {}
        q = (args.get("query") or args.get("q") or "").strip()
        category = (args.get("category") or "").strip()
        stock_filter = (args.get("stock_filter") or "").strip()
        limit = int(args.get("limit") or 40)
        if limit < 1:
            limit = 40
        if limit > 150:
            limit = 150
        rows = services.get_inventory_items(
            query=q, category=category, stock_filter=stock_filter, include_inactive=False
        )
        items = []
        for r in (rows or [])[:limit]:
            d = dict(r) if hasattr(r, "keys") else (r if isinstance(r, dict) else {})
            items.append(
                {
                    "id": d.get("id"),
                    "item_name": d.get("item_name") or d.get("name"),
                    "category": d.get("category"),
                    "unit": d.get("unit"),
                    "stock_qty": d.get("stock_qty"),
                    "selling_price": d.get("selling_price"),
                    "unit_cost": d.get("unit_cost"),
                    "sku": d.get("sku"),
                    "barcode": d.get("barcode"),
                    "active": d.get("active", 1),
                }
            )
        return {"items": items, "count": len(items), "query": q, "category": category, "stock_filter": stock_filter}
    except Exception as e:
        return {"error": str(e)}


def tool_get_inventory_item(args=None):
    """Get a single inventory item/product details by id (read-only)."""
    try:
        item_id = _safe_int((args or {}).get("item_id") or (args or {}).get("id"))
        if not item_id:
            return {"error": "item_id is required"}
        row = services.get_inventory_item(item_id)
        if not row:
            return {"error": f"Inventory item #{item_id} not found"}
        d = dict(row) if hasattr(row, "keys") else row
        return {"item": d}
    except Exception as e:
        return {"error": str(e)}


def tool_get_walkin_sales_summary(args=None):
    """Summarize recent walk-in (non-reservation) POS sales and payments (read-only). Works without rooms/reservations."""
    try:
        args = args or {}
        limit_pos = int(args.get("limit_pos") or args.get("limit") or 30)
        limit_pay = int(args.get("limit_payments") or 20)
        if limit_pos < 1:
            limit_pos = 30
        if limit_pos > 100:
            limit_pos = 100
        if limit_pay < 1:
            limit_pay = 20
        if limit_pay > 100:
            limit_pay = 100
        pos_rows = services.list_walkin_pos(limit=limit_pos) or []
        pay_rows = services.list_walkin_payments(limit=limit_pay) or []

        pos_items = []
        gross_total = 0.0
        qty_total = 0.0
        for r in pos_rows:
            d = dict(r) if hasattr(r, "keys") else r
            qty = float(d.get("quantity") or 0)
            unit = float(d.get("unit_price") or 0)
            tax = float(d.get("tax_amount") or 0)
            gross = unit * qty + tax
            gross_total += gross
            qty_total += qty
            pos_items.append(
                {
                    "id": d.get("id"),
                    "created_at": d.get("created_at"),
                    "item_description": d.get("item_description"),
                    "quantity": qty,
                    "unit_price": unit,
                    "gross": round(gross, 2),
                    "lot_code": d.get("lot_code"),
                }
            )
        payments = []
        paid_total = 0.0
        for r in pay_rows:
            d = dict(r) if hasattr(r, "keys") else r
            amt = float(d.get("amount") or 0)
            if not d.get("voided_at"):
                paid_total += amt
            payments.append(
                {
                    "id": d.get("id"),
                    "at": d.get("at"),
                    "amount": round(amt, 2),
                    "method": d.get("method"),
                    "payment_type": d.get("payment_type"),
                    "notes": d.get("notes"),
                    "voided_at": d.get("voided_at"),
                }
            )
        return {
            "walkin_pos": pos_items,
            "walkin_pos_count": len(pos_items),
            "walkin_pos_gross_total": round(gross_total, 2),
            "walkin_pos_qty_total": round(qty_total, 2),
            "walkin_payments": payments,
            "walkin_payments_count": len(payments),
            "walkin_payments_total": round(paid_total, 2),
            "note": "Walk-in POS/sales are not linked to room reservations and work for product/meal sales.",
        }
    except Exception as e:
        return {"error": str(e)}


def tool_get_payments_summary(args=None):
    """Summarize payments (including walk-ins) for a period by method (read-only)."""
    try:
        args = args or {}
        start, end = args.get("start"), args.get("end")
        if not start or not end:
            start, end = _parse_date_range(args.get("range") or "")
        from ..db import connect

        rows = (
            connect()
            .execute(
                """
            SELECT method, COUNT(*) AS n, SUM(amount) AS total
            FROM payments
            WHERE voided_at IS NULL AND date(at) >= ? AND date(at) <= ?
            GROUP BY method ORDER BY total DESC
        """,
                (start, end),
            )
            .fetchall()
        )
        by_method = [
            {"method": r["method"] or "Unknown", "count": r["n"], "total": round(float(r["total"] or 0), 2)}
            for r in rows
        ]
        total = round(sum(x["total"] for x in by_method), 2)
        walkin_rows = (
            connect()
            .execute(
                """
            SELECT COUNT(*) AS n, COALESCE(SUM(amount),0) AS total
            FROM payments WHERE voided_at IS NULL AND reservation_id IS NULL AND date(at) >= ? AND date(at) <= ?
        """,
                (start, end),
            )
            .fetchone()
        )
        walkin = (
            {"count": int(walkin_rows["n"] or 0), "total": round(float(walkin_rows["total"] or 0), 2)}
            if walkin_rows
            else {"count": 0, "total": 0.0}
        )
        return {"start": start, "end": end, "by_method": by_method, "total": total, "walkin_payments": walkin}
    except Exception as e:
        return {"start": start, "end": end, "error": str(e)}


def tool_list_reservation_groups(args=None):
    try:
        status = (args or {}).get("status", "")
        groups = services.list_reservation_groups(status=status) if hasattr(services, "list_reservation_groups") else []
        # keep payload compact
        out = []
        for g in (groups or [])[:50]:
            members = []
            for m in (g.get("members") or [])[:20]:
                members.append(
                    {
                        "reservation_id": m.get("reservation_id"),
                        "guest_name": m.get("guest_name"),
                        "room_number": m.get("room_number"),
                        "status": m.get("reservation_status"),
                        "check_in_date": m.get("check_in_date"),
                        "check_out_date": m.get("check_out_date"),
                        "balance_due": m.get("balance_due"),
                        "role": m.get("role"),
                    }
                )
            out.append(
                {
                    "id": g.get("id"),
                    "name": g.get("name"),
                    "group_type": g.get("group_type"),
                    "status": g.get("status"),
                    "member_count": g.get("member_count", len(members)),
                    "totals": g.get("totals") or {},
                    "members": members,
                    "contact_name": g.get("contact_name"),
                }
            )
        return {"groups": out, "count": len(out), "status_filter": status}
    except Exception as e:
        return {"error": str(e)}


def tool_list_packages(args=None):
    try:
        active_only = bool((args or {}).get("active_only", False))
        pkgs = services.list_packages(active_only=active_only) if hasattr(services, "list_packages") else []
        out = []
        for p in (pkgs or [])[:50]:
            items = []
            for it in (p.get("items") or [])[:30]:
                items.append(
                    {
                        "id": it.get("id"),
                        "description": it.get("description"),
                        "service_name": it.get("service_name"),
                        "quantity": it.get("quantity"),
                        "unit_price": it.get("unit_price")
                        if it.get("unit_price") is not None
                        else it.get("service_unit_price"),
                    }
                )
            out.append(
                {
                    "id": p.get("id"),
                    "name": p.get("name"),
                    "description": p.get("description"),
                    "active": p.get("active"),
                    "estimated_total": p.get("estimated_total"),
                    "item_count": len(p.get("items") or []),
                    "items": items,
                }
            )
        return {"packages": out, "count": len(out), "active_only": active_only}
    except Exception as e:
        return {"error": str(e)}


def tool_get_cash_shifts(args=None):
    try:
        if not hasattr(services, "list_cash_shifts"):
            return {"error": "cash shift tools unavailable"}
        open_shift = services.get_open_cash_shift() if hasattr(services, "get_open_cash_shift") else None
        shifts = services.list_cash_shifts(limit=int((args or {}).get("limit") or 20))
        movements = (
            services.list_cash_movements(open_shift["id"])
            if (open_shift and hasattr(services, "list_cash_movements"))
            else []
        )
        return {
            "open_shift": open_shift,
            "open_shift_movements": movements[:30],
            "recent_shifts": (shifts or [])[:20],
        }
    except Exception as e:
        return {"error": str(e)}


def tool_list_night_audit_runs(args=None):
    try:
        if not hasattr(services, "list_night_audit_runs"):
            return {"error": "night audit tools unavailable"}
        runs = services.list_night_audit_runs(limit=int((args or {}).get("limit") or 20))
        # parse json fields for readability
        out = []
        for r in (runs or [])[:20]:
            d = dict(r)
            for k in ("summary_json", "exceptions_json"):
                try:
                    if isinstance(d.get(k), str):
                        d[k] = json.loads(d[k])
                except Exception:
                    pass
            out.append(d)
        return {"night_audit_runs": out, "count": len(out)}
    except Exception as e:
        return {"error": str(e)}


def tool_list_room_blocks(args=None):
    try:
        if not hasattr(services, "list_room_blocks"):
            return {"error": "room block tools unavailable"}
        args = args or {}
        room_id = _safe_int(args.get("room_id"))
        status = (args.get("status") or "").strip().lower()
        include_past = bool(args.get("include_past", False))
        # service signature: list_room_blocks(room_id=None, status="active"); empty status => all
        if include_past and not status:
            status = ""
        elif not status:
            status = "active"
        blocks = services.list_room_blocks(room_id=room_id, status=status)
        return {"room_blocks": (blocks or [])[:100], "count": len(blocks or []), "status_filter": status or "all"}
    except Exception as e:
        return {"error": str(e)}


def tool_list_housekeeping_tasks(args=None):
    try:
        if not hasattr(services, "list_housekeeping_tasks"):
            return {"error": "housekeeping task tools unavailable"}
        status = (args or {}).get("status") or ""
        room_id = _safe_int((args or {}).get("room_id"))
        tasks = services.list_housekeeping_tasks(status=status, room_id=room_id)
        return {"housekeeping_tasks": (tasks or [])[:100], "count": len(tasks or []), "status_filter": status}
    except Exception as e:
        return {"error": str(e)}


def tool_list_reservation_packages(args=None):
    """List packages applied to a specific reservation (folio-linked)."""
    try:
        rid = _safe_int((args or {}).get("reservation_id") or (args or {}).get("id"))
        if not rid:
            return {"error": "reservation_id is required"}
        if not hasattr(services, "list_reservation_packages"):
            return {"error": "reservation packages unavailable"}
        rows = services.list_reservation_packages(rid)
        out = []
        for r in (rows or [])[:50]:
            d = dict(r)
            # parse snapshot for compact summary
            try:
                snap = d.get("snapshot_json")
                if isinstance(snap, str):
                    snap = json.loads(snap)
                if isinstance(snap, dict):
                    d["item_count"] = len((snap.get("items") or []))
            except Exception:
                pass
            out.append(d)
        return {"reservation_id": rid, "reservation_packages": out, "count": len(out)}
    except Exception as e:
        return {"error": str(e)}


def tool_get_reservation_group(args=None):
    """Get reservation group details, members, and aggregated folio totals."""
    try:
        gid = _safe_int((args or {}).get("group_id") or (args or {}).get("id"))
        if not gid:
            return {"error": "group_id is required"}
        if not hasattr(services, "get_reservation_group"):
            return {"error": "reservation groups unavailable"}
        g = services.get_reservation_group(gid)
        if not g:
            return {"error": f"Group #{gid} not found"}
        # compact members
        members = []
        for m in (g.get("members") or [])[:50]:
            members.append(
                {
                    "reservation_id": m.get("reservation_id"),
                    "guest_name": m.get("guest_name"),
                    "room_number": m.get("room_number"),
                    "status": m.get("reservation_status"),
                    "check_in_date": m.get("check_in_date"),
                    "check_out_date": m.get("check_out_date"),
                    "total_charges": m.get("total_charges"),
                    "total_paid": m.get("total_paid"),
                    "balance_due": m.get("balance_due"),
                    "role": m.get("role"),
                }
            )
        g2 = {k: v for k, v in g.items() if k != "members"}
        g2["members"] = members
        g2["member_count"] = len(g.get("members") or [])
        return {"group": g2}
    except Exception as e:
        return {"error": str(e)}


def tool_get_housekeeping_ops_report(args=None):
    """Grounded housekeeping operations report (rooms/tasks/blocks)."""
    try:
        if hasattr(services, "report_housekeeping_ops"):
            report = services.report_housekeeping_ops()
            return {"report": report}
        # fallback compose
        return {"error": "housekeeping ops report unavailable"}
    except Exception as e:
        return {"error": str(e)}


def tool_list_rate_rules(args=None):
    try:
        active_only = bool((args or {}).get("active_only", False))
        if not hasattr(services, "list_rate_rules"):
            return {"error": "rate rules unavailable"}
        rules = services.list_rate_rules(active_only=active_only)
        return {"rate_rules": (rules or [])[:100], "count": len(rules or []), "active_only": active_only}
    except Exception as e:
        return {"error": str(e)}


def tool_list_service_catalog(args=None):
    try:
        active_only = bool((args or {}).get("active_only", True))
        if not hasattr(services, "list_service_catalog"):
            return {"error": "service catalog unavailable"}
        items = services.list_service_catalog(active_only=active_only)
        return {"service_catalog": (items or [])[:100], "count": len(items or []), "active_only": active_only}
    except Exception as e:
        return {"error": str(e)}


def tool_list_discount_vouchers(args=None):
    try:
        active_only = bool((args or {}).get("active_only", False))
        if not hasattr(services, "list_discount_vouchers"):
            return {"error": "discount vouchers unavailable"}
        items = services.list_discount_vouchers(active_only=active_only)
        # avoid exposing sensitive internals beyond operational fields
        out = []
        for v in (items or [])[:100]:
            d = dict(v)
            out.append(d)
        return {"discount_vouchers": out, "count": len(out), "active_only": active_only}
    except Exception as e:
        return {"error": str(e)}


def tool_list_approvals(args=None):
    try:
        args = args or {}
        if not hasattr(services, "list_approvals"):
            return {"error": "approvals unavailable"}
        status = (args.get("status") or "").strip()
        limit = min(int(args.get("limit") or 50), 100)
        items = services.list_approvals(status=status, limit=limit)
        return {"approvals": items or [], "count": len(items or []), "status_filter": status or None}
    except Exception as e:
        return {"error": str(e)}


def tool_get_approval(args=None):
    try:
        args = args or {}
        if not hasattr(services, "get_approval"):
            return {"error": "approvals unavailable"}
        aid = int(args.get("approval_id") or args.get("id") or 0)
        if not aid:
            return {"error": "approval_id is required"}
        item = services.get_approval(aid)
        if not item:
            return {"error": f"Approval #{aid} not found"}
        return {"approval": item}
    except Exception as e:
        return {"error": str(e)}


def tool_verify_reservation_financials(args=None):
    try:
        args = args or {}
        rid = _safe_int(args.get("reservation_id") or args.get("id"))
        if not rid:
            return {"error": "reservation_id is required"}
        if not hasattr(services, "verify_reservation_financials"):
            return {"error": "reservation financial integrity check unavailable"}
        return services.verify_reservation_financials(rid)
    except Exception as e:
        return {"error": str(e)}


TOOLS = {
    "list_capabilities": {
        "fn": tool_list_capabilities,
        "description": "List assistant capabilities.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "get_property_stats": {
        "fn": tool_get_property_stats,
        "description": "Property counts/stats (guests, rooms, reservations by status, in-house, arrivals/departures today).",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "get_today_ops": {
        "fn": tool_get_today_ops,
        "description": "Operations snapshot for a day (arrivals/departures/in-house, front-desk exceptions, housekeeping).",
        "parameters": {
            "type": "object",
            "properties": {"date": {"type": "string"}, "day": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "get_front_desk_ops": {
        "fn": tool_get_front_desk_ops,
        "description": "Front-desk operations for a day with arrivals/in-house/departures, room/HK, balances, and exception flags (unpaid, dirty, due to arrive/depart not checked in/out).",
        "parameters": {
            "type": "object",
            "properties": {"date": {"type": "string"}, "day": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "get_front_desk_arrivals": {
        "fn": tool_get_front_desk_arrivals,
        "description": "Arrivals list for a business day with room/HK/balance/exception details.",
        "parameters": {
            "type": "object",
            "properties": {"date": {"type": "string"}, "day": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "get_front_desk_in_house": {
        "fn": tool_get_front_desk_in_house,
        "description": "In-house/stayovers list for a business day with room/HK/balance/exception details.",
        "parameters": {
            "type": "object",
            "properties": {"date": {"type": "string"}, "day": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "get_front_desk_departures": {
        "fn": tool_get_front_desk_departures,
        "description": "Departures list for a business day with room/HK/balance/exception details.",
        "parameters": {
            "type": "object",
            "properties": {"date": {"type": "string"}, "day": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "business_partner_briefing": {
        "fn": tool_business_partner_briefing,
        "description": "Business-partner briefing: KPIs, risks, opportunities, suggested actions for a period.",
        "parameters": {
            "type": "object",
            "properties": {"start": {"type": "string"}, "end": {"type": "string"}, "range": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "get_decision_support": {
        "fn": tool_get_decision_support,
        "description": "Decision-support KPIs/signals + financial summary.",
        "parameters": {
            "type": "object",
            "properties": {"start": {"type": "string"}, "end": {"type": "string"}, "range": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "get_report": {
        "fn": tool_get_report,
        "description": "Run report: pl, cash_flow, vat, payment_methods, occupancy, daily_ops.",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "range": {"type": "string"},
            },
            "required": ["kind"],
            "additionalProperties": False,
        },
    },
    "get_occupancy_summary": {
        "fn": tool_get_occupancy_summary,
        "description": "Occupancy summary for a date range.",
        "parameters": {
            "type": "object",
            "properties": {"start": {"type": "string"}, "end": {"type": "string"}, "range": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "compare_periods": {
        "fn": tool_compare_periods,
        "description": "Compare current period vs previous period (P&L, occupancy, cash flow).",
        "parameters": {
            "type": "object",
            "properties": {"start": {"type": "string"}, "end": {"type": "string"}, "range": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "get_outstanding_balances": {
        "fn": tool_get_outstanding_balances,
        "description": "Reservations with unpaid/outstanding balances (who owes money).",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "get_inventory_alerts": {
        "fn": tool_get_inventory_alerts,
        "description": "Inventory alerts: low/negative/overstock items.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "get_top_selling_items": {
        "fn": tool_get_top_selling_items,
        "description": "Top selling POS items by quantity/revenue in a period.",
        "parameters": {
            "type": "object",
            "properties": {
                "start": {"type": "string"},
                "end": {"type": "string"},
                "range": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
    "get_expenses_summary": {
        "fn": tool_get_expenses_summary,
        "description": "Operational expenses summary by category for a period.",
        "parameters": {
            "type": "object",
            "properties": {"start": {"type": "string"}, "end": {"type": "string"}, "range": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "get_sales_breakdown": {
        "fn": tool_get_sales_breakdown,
        "description": "Sales/POS and payment/reservation-source breakdown for a period.",
        "parameters": {
            "type": "object",
            "properties": {"start": {"type": "string"}, "end": {"type": "string"}, "range": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "get_reservation_pipeline": {
        "fn": tool_get_reservation_pipeline,
        "description": "Reservation counts by status and upcoming arrivals (7 days).",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "get_housekeeping_summary": {
        "fn": tool_get_housekeeping_summary,
        "description": "Room housekeeping status summary.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "get_housekeeping_ops_report": {
        "fn": tool_get_housekeeping_ops_report,
        "description": "Grounded housekeeping operations report: room/task/block status, overdue/open tasks, and active blocks (read-only).",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "list_rate_rules": {
        "fn": tool_list_rate_rules,
        "description": "List pricing/rate rules (conditions and adjustments) (read-only).",
        "parameters": {
            "type": "object",
            "properties": {"active_only": {"type": "boolean"}},
            "additionalProperties": False,
        },
    },
    "list_service_catalog": {
        "fn": tool_list_service_catalog,
        "description": "List extra-service catalog items with pricing/taxable status (read-only).",
        "parameters": {
            "type": "object",
            "properties": {"active_only": {"type": "boolean"}},
            "additionalProperties": False,
        },
    },
    "list_discount_vouchers": {
        "fn": tool_list_discount_vouchers,
        "description": "List discount voucher definitions and usage/validity status (read-only).",
        "parameters": {
            "type": "object",
            "properties": {"active_only": {"type": "boolean"}},
            "additionalProperties": False,
        },
    },
    "list_approvals": {
        "fn": tool_list_approvals,
        "description": "List approval requests and their status/decision/execution metadata (read-only).",
        "parameters": {
            "type": "object",
            "properties": {"status": {"type": "string"}, "limit": {"type": "integer"}},
            "additionalProperties": False,
        },
    },
    "get_approval": {
        "fn": tool_get_approval,
        "description": "Get a specific approval request by id (read-only).",
        "parameters": {
            "type": "object",
            "properties": {"approval_id": {"type": "integer"}},
            "required": ["approval_id"],
            "additionalProperties": False,
        },
    },
    "get_room_availability": {
        "fn": tool_get_room_availability,
        "description": "Available rooms for a date range.",
        "parameters": {
            "type": "object",
            "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "get_menu_info": {
        "fn": tool_get_menu_info,
        "description": "Meals/menu information.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "list_rate_plans": {
        "fn": tool_list_rate_plans,
        "description": "List rate plans and usage.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "verify_ledger_integrity": {
        "fn": tool_verify_ledger_integrity,
        "description": "Read-only ledger/data integrity health check.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "verify_reservation_financials": {
        "fn": tool_verify_reservation_financials,
        "description": "Read-only reservation-level financial integrity/invariant check (charges, payments, allocations, ledger consistency).",
        "parameters": {
            "type": "object",
            "properties": {"reservation_id": {"type": "integer"}},
            "required": ["reservation_id"],
            "additionalProperties": False,
        },
    },
    "search_guests": {
        "fn": tool_search_guests,
        "description": "Search guests by name/phone/email fragment.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "get_guest_profile": {
        "fn": tool_get_guest_profile,
        "description": "Guest profile and recent reservations.",
        "parameters": {
            "type": "object",
            "properties": {"guest_id": {"type": "integer"}},
            "required": ["guest_id"],
            "additionalProperties": False,
        },
    },
    "get_guest_last_visit": {
        "fn": tool_get_guest_last_visit,
        "description": "Guest's last visit (most recent Checked Out, else most recent non-deleted).",
        "parameters": {
            "type": "object",
            "properties": {"guest_id": {"type": "integer"}},
            "required": ["guest_id"],
            "additionalProperties": False,
        },
    },
    "get_guest_top_items_last_visit": {
        "fn": tool_get_guest_top_items_last_visit,
        "description": "Most ordered items on guest's last visit (non-voided POS).",
        "parameters": {
            "type": "object",
            "properties": {"guest_id": {"type": "integer"}, "limit": {"type": "integer"}},
            "required": ["guest_id"],
            "additionalProperties": False,
        },
    },
    "get_guest_last_visit_spend": {
        "fn": tool_get_guest_last_visit_spend,
        "description": "Guest's last visit with spend/balance and top items.",
        "parameters": {
            "type": "object",
            "properties": {"guest_id": {"type": "integer"}},
            "required": ["guest_id"],
            "additionalProperties": False,
        },
    },
    "get_guest_portfolio": {
        "fn": tool_get_guest_portfolio,
        "description": "Full guest 360° portfolio: profile, lifetime stats, all trips/stays with folio/charges/paid/due, all POS/activity orders per trip and overall, all payments, invoices, top items.",
        "parameters": {
            "type": "object",
            "properties": {"guest_id": {"type": "integer"}},
            "required": ["guest_id"],
            "additionalProperties": False,
        },
    },
    "list_activities": {
        "fn": tool_list_activities,
        "description": "List registered guest activities/trips/excursions catalog.",
        "parameters": {
            "type": "object",
            "properties": {"active_only": {"type": "boolean"}},
            "additionalProperties": False,
        },
    },
    "get_activity_stats": {
        "fn": tool_get_activity_stats,
        "description": "Per-activity/trip booking statistics (bookings, qty, revenue) grounded in real bookings.",
        "parameters": {
            "type": "object",
            "properties": {"activity_id": {"type": "integer"}},
            "additionalProperties": False,
        },
    },
    "list_activity_bookings": {
        "fn": tool_list_activity_bookings,
        "description": "List booked/ordered guest activities-trips (POS-linked bookings) with reservation/guest context; filter by guest_id, reservation_id, or activity_id.",
        "parameters": {
            "type": "object",
            "properties": {
                "guest_id": {"type": "integer"},
                "reservation_id": {"type": "integer"},
                "activity_id": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
    "get_reservation_summary": {
        "fn": tool_get_reservation_summary,
        "description": "Reservation details, balance, POS count.",
        "parameters": {
            "type": "object",
            "properties": {"reservation_id": {"type": "integer"}},
            "required": ["reservation_id"],
            "additionalProperties": False,
        },
    },
    "get_guest_balance": {
        "fn": tool_get_guest_balance,
        "description": "Reservation/guest balance and breakdown.",
        "parameters": {
            "type": "object",
            "properties": {"reservation_id": {"type": "integer"}},
            "required": ["reservation_id"],
            "additionalProperties": False,
        },
    },
    "get_recent_activity": {
        "fn": tool_get_recent_activity,
        "description": "Recent audit/activity log.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}, "additionalProperties": False},
    },
    "get_expiring_batches": {
        "fn": tool_get_expiring_batches,
        "description": "List expiring/expired inventory batches (traceable lots) within days.",
        "parameters": {
            "type": "object",
            "properties": {"within_days": {"type": "integer"}, "include_expired": {"type": "boolean"}},
            "additionalProperties": False,
        },
    },
    "list_inventory_batches": {
        "fn": tool_list_inventory_batches,
        "description": "List inventory batches/lots with received/expiry/remaining/cost for an item or all.",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "integer"},
                "status": {"type": "string"},
                "expiring_within_days": {"type": "integer"},
                "include_depleted": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    "trace_barcode": {
        "fn": tool_trace_barcode,
        "description": "Trace a barcode/SKU/lot code to the inventory item or batch (lot, received, expiry, remaining, movements).",
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string"}, "barcode": {"type": "string"}, "sku": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "get_app_update_status": {
        "fn": tool_get_app_update_status,
        "description": "Get local app version/commit/update state and how to check/apply updates (data preserved).",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "get_health_diagnostics": {
        "fn": tool_get_health_diagnostics,
        "description": "Read-only doctor/health diagnostics: schema/integrity, batch-stock consistency, printer/update readiness, and backup/restore guidance.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "search_reservations": {
        "fn": tool_search_reservations,
        "description": "Search/list reservations by guest name, id, status, source/segment, or date range (read-only).",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "reservation_id": {"type": "integer"},
                "status": {"type": "string"},
                "source": {"type": "string"},
                "market_segment": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
    "search_inventory": {
        "fn": tool_search_inventory,
        "description": "Search/list inventory products with stock, pricing, SKU/barcode, and category.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "category": {"type": "string"},
                "stock_filter": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
    "get_inventory_item": {
        "fn": tool_get_inventory_item,
        "description": "Get inventory item/product details and stock by item id.",
        "parameters": {
            "type": "object",
            "properties": {"item_id": {"type": "integer"}},
            "required": ["item_id"],
            "additionalProperties": False,
        },
    },
    "get_walkin_sales_summary": {
        "fn": tool_get_walkin_sales_summary,
        "description": "Recent walk-in (non-reservation) POS sales and payments summary/totals; works without room reservations.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit_pos": {"type": "integer"},
                "limit_payments": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
    "get_payments_summary": {
        "fn": tool_get_payments_summary,
        "description": "Payments summary for a period by method, including walk-in payment totals.",
        "parameters": {
            "type": "object",
            "properties": {"start": {"type": "string"}, "end": {"type": "string"}, "range": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "list_reservation_groups": {
        "fn": tool_list_reservation_groups,
        "description": "List reservation groups with members and aggregated folio totals (read-only).",
        "parameters": {"type": "object", "properties": {"status": {"type": "string"}}, "additionalProperties": False},
    },
    "list_packages": {
        "fn": tool_list_packages,
        "description": "List package/bundle definitions and items (read-only).",
        "parameters": {
            "type": "object",
            "properties": {"active_only": {"type": "boolean"}},
            "additionalProperties": False,
        },
    },
    "get_cash_shifts": {
        "fn": tool_get_cash_shifts,
        "description": "Cash shift/register status: open shift, movements, and recent shifts (read-only).",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}, "additionalProperties": False},
    },
    "list_night_audit_runs": {
        "fn": tool_list_night_audit_runs,
        "description": "List advisory night-audit run snapshots and exceptions (read-only).",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}, "additionalProperties": False},
    },
    "list_room_blocks": {
        "fn": tool_list_room_blocks,
        "description": "List room blocks (OOO/OOS) affecting availability (read-only).",
        "parameters": {
            "type": "object",
            "properties": {
                "room_id": {"type": "integer"},
                "status": {"type": "string"},
                "include_past": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    "list_housekeeping_tasks": {
        "fn": tool_list_housekeeping_tasks,
        "description": "List housekeeping tasks and workflow status (read-only).",
        "parameters": {
            "type": "object",
            "properties": {"status": {"type": "string"}, "room_id": {"type": "integer"}},
            "additionalProperties": False,
        },
    },
    "list_reservation_packages": {
        "fn": tool_list_reservation_packages,
        "description": "List packages/bundles applied to a reservation with posted amounts/status (read-only).",
        "parameters": {
            "type": "object",
            "properties": {"reservation_id": {"type": "integer"}},
            "required": ["reservation_id"],
            "additionalProperties": False,
        },
    },
    "get_reservation_group": {
        "fn": tool_get_reservation_group,
        "description": "Get reservation group details, members, and aggregated folio totals (read-only).",
        "parameters": {
            "type": "object",
            "properties": {"group_id": {"type": "integer"}},
            "required": ["group_id"],
            "additionalProperties": False,
        },
    },
}


def execute_tool(name: str, args: dict | None = None, ctx: dict | None = None) -> dict:
    """Execute a tool by id through the dynamic executor (manifest/registry aware)."""
    from .executor import execute

    return execute(name, args or {}, ctx=ctx)
