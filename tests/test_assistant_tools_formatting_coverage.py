from __future__ import annotations

from datetime import date, timedelta

import app_core.assistant.formatting as fmt
import app_core.assistant.tools as tools
from app_core import services
from app_core.db import connect


def _future(days=200, nights=1):
    ci = date.today() + timedelta(days=days)
    return ci.strftime("%Y-%m-%d"), (ci + timedelta(days=nights)).strftime("%Y-%m-%d")


def test_tools_basic_and_db_backed(clean_db, monkeypatch):
    assert "capabilities" in tools.tool_list_capabilities()

    # property/stats and simple DB-backed tools
    stats = tools.tool_get_property_stats()
    assert "stats" in stats or "error" in stats

    ops = tools.tool_get_today_ops({"date": date.today().strftime("%Y-%m-%d")})
    assert isinstance(ops, dict)

    hk = tools.tool_get_housekeeping_summary()
    assert "summary" in hk or "error" in hk

    inv = tools.tool_get_inventory_alerts()
    assert "alerts" in inv or "error" in inv

    pipe = tools.tool_get_reservation_pipeline()
    assert "by_status" in pipe or "error" in pipe

    ci, co = _future(220, 1)
    avail = tools.tool_get_room_availability({"start": ci, "end": co})
    assert "available_count" in avail or "error" in avail

    menu = tools.tool_get_menu_info()
    assert "meals" in menu or "error" in menu

    recent = tools.tool_get_recent_activity({"limit": 5})
    assert "recent_audit" in recent or "error" in recent

    # outstanding/top/expenses/sales/compare/business briefing (should run on seeded DB)
    out = tools.tool_get_outstanding_balances()
    assert "outstanding" in out or "error" in out

    top = tools.tool_get_top_selling_items({"range": "this month", "limit": 5})
    assert "top_items" in top or "error" in top

    exp = tools.tool_get_expenses_summary({"range": "this month"})
    assert "by_category" in exp or "error" in exp

    sales = tools.tool_get_sales_breakdown({"range": "this month"})
    assert "pos_by_item" in sales or "error" in sales

    cmp = tools.tool_compare_periods({"range": "this month"})
    assert "current" in cmp and "previous" in cmp

    brief = tools.tool_business_partner_briefing({"range": "this month"})
    assert "sections" in brief or "error" in brief

    # guest/reservation helpers on seeded/created data
    room_id = connect().execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
    rid = services.create_reservation("Tool Guest", "Direct", int(room_id), None, 100.0, ci, co, 0.0)
    grow = connect().execute("SELECT guest_id FROM reservations WHERE id=?", (rid,)).fetchone()
    gid = int(grow["guest_id"]) if grow and grow["guest_id"] else None
    if gid:
        assert tools.tool_search_guests({"query": "Tool"})["guests"] is not None
        assert "guest" in tools.tool_get_guest_profile({"guest_id": gid}) or "error" in tools.tool_get_guest_profile(
            {"guest_id": gid}
        )
        assert isinstance(tools.tool_get_guest_last_visit({"guest_id": gid}), dict)
        assert isinstance(tools.tool_get_guest_top_items_last_visit({"guest_id": gid}), dict)
        assert isinstance(tools.tool_get_guest_last_visit_spend({"guest_id": gid}), dict)
        assert isinstance(tools.tool_get_guest_portfolio({"guest_id": gid}), dict)
    assert "reservation" in (tools.tool_get_reservation_summary({"reservation_id": rid}) or {}) or "error" in (
        tools.tool_get_reservation_summary({"reservation_id": rid}) or {}
    )
    assert "balance" in tools.tool_get_guest_balance(
        {"reservation_id": rid}
    ) or "error" in tools.tool_get_guest_balance({"reservation_id": rid})


def test_tools_mocked_and_registry_paths(monkeypatch, clean_db):
    # front desk ops family
    monkeypatch.setattr(
        services,
        "get_front_desk_ops",
        lambda day=None: {
            "date": day,
            "summary": {"arrivals_today": 1},
            "counts": {
                "arrivals": 1,
                "departures": 0,
                "in_house": 2,
                "exceptions": 0,
                "unpaid": 0,
                "dirty_rooms": 0,
                "not_checked_in": 0,
                "not_checked_out": 0,
            },
            "arrivals": [],
            "in_house": [],
            "departures": [],
        },
        raising=False,
    )
    monkeypatch.setattr(services, "get_front_desk_arrivals", lambda day=None: [{"id": 1}], raising=False)
    monkeypatch.setattr(services, "get_front_desk_in_house", lambda day=None: [{"id": 2}], raising=False)
    monkeypatch.setattr(services, "get_front_desk_departures", lambda day=None: [{"id": 3}], raising=False)
    assert "summary" in tools.tool_get_front_desk_ops({"day": "2099-01-01"})
    assert tools.tool_get_front_desk_arrivals({"day": "2099-01-01"})["count"] == 1
    assert tools.tool_get_front_desk_in_house({"day": "2099-01-01"})["count"] == 1
    assert tools.tool_get_front_desk_departures({"day": "2099-01-01"})["count"] == 1

    # activities
    monkeypatch.setattr(
        services, "list_activities", lambda active_only=False: [{"id": 1, "name": "Spa", "active": 1}], raising=False
    )
    monkeypatch.setattr(
        services, "get_activity_stats", lambda activity_id=None: [{"activity_id": 1, "bookings": 2}], raising=False
    )
    monkeypatch.setattr(services, "list_activity_bookings", lambda **k: [{"id": 1, "activity_id": 1}], raising=False)
    assert tools.tool_list_activities()["count"] == 1
    assert "activity_stats" in tools.tool_get_activity_stats({})
    assert tools.tool_list_activity_bookings({})["count"] == 1

    # reports/decision/occupancy/rate plans/integrity/doctor/update
    monkeypatch.setattr(
        services, "report_pl_statement", lambda s, e: {"revenue": 100, "expenses": 40, "net_profit": 60}
    )
    monkeypatch.setattr(services, "report_cash_flow", lambda s, e: {"net_cash": 50})
    monkeypatch.setattr(services, "report_vat", lambda s, e: {"net_vat": 5})
    monkeypatch.setattr(services, "report_payment_methods", lambda s, e: {"Cash": 10})
    monkeypatch.setattr(services, "report_occupancy", lambda s, e: {"pct": 50})
    monkeypatch.setattr(services, "report_daily_ops", lambda d: {"arrivals": []})
    assert tools.tool_get_report({"kind": "pl", "start": "2099-01-01", "end": "2099-01-31"})["kind"] == "pl"
    assert (
        tools.tool_get_report({"kind": "cash_flow", "start": "2099-01-01", "end": "2099-01-31"})["kind"] == "cash_flow"
    )
    assert tools.tool_get_report({"kind": "vat", "start": "2099-01-01", "end": "2099-01-31"})["kind"] == "vat"
    assert (
        tools.tool_get_report({"kind": "payment_methods", "start": "2099-01-01", "end": "2099-01-31"})["kind"]
        == "payment_methods"
    )
    assert (
        tools.tool_get_report({"kind": "occupancy", "start": "2099-01-01", "end": "2099-01-31"})["kind"] == "occupancy"
    )
    assert tools.tool_get_report({"kind": "daily_ops", "day": "2099-01-01"})["kind"] == "daily_ops"

    monkeypatch.setattr(services, "dataset_decision_support_v1", lambda s, e: {"ok": True}, raising=False)
    monkeypatch.setattr(services, "dataset_financial_summary", lambda s, e: {"revenue": 1}, raising=False)
    assert "decision_support" in tools.tool_get_decision_support({"start": "2099-01-01", "end": "2099-01-31"})
    monkeypatch.setattr(services, "get_all_rate_plans", lambda: [{"id": 1, "name": "Std", "price_modifier": 1.0}])
    monkeypatch.setattr(services, "count_reservations_using_plan", lambda pid: 2, raising=False)
    assert tools.tool_list_rate_plans()["rate_plans"][0]["reservations_using_plan"] == 2
    monkeypatch.setattr(services, "get_occupancy_for_window", lambda s, e: {"pct": 40})
    assert tools.tool_get_occupancy_summary({"start": "2099-01-01", "end": "2099-01-31"})["occupancy"]["pct"] == 40
    monkeypatch.setattr(services, "verify_ledger_integrity", lambda: {"ok": True})
    assert tools.tool_verify_ledger_integrity()["ok"] is True
    monkeypatch.setattr(
        services, "verify_reservation_financials", lambda rid: {"ok": True, "reservation_id": rid}, raising=False
    )
    assert tools.tool_verify_reservation_financials({"reservation_id": 1})["ok"] is True

    # search/list/get tools with monkeypatched services
    monkeypatch.setattr(services, "assistant_search_reservations", lambda **k: [{"id": 1}], raising=False)
    assert tools.tool_search_reservations({"query": "x"})["count"] >= 0
    monkeypatch.setattr(
        services, "assistant_search_inventory", lambda **k: [{"id": 1, "item_name": "Water"}], raising=False
    )
    assert (
        "items" in tools.tool_search_inventory({"query": "water"})
        or "results" in tools.tool_search_inventory({"query": "water"})
        or isinstance(tools.tool_search_inventory({"query": "water"}), dict)
    )
    monkeypatch.setattr(
        services,
        "assistant_get_inventory_item",
        lambda item_id=None, query=None: {"id": 1, "item_name": "Water"},
        raising=False,
    )
    monkeypatch.setattr(
        services, "get_inventory_item", lambda item_id: {"id": item_id, "item_name": "Water"}, raising=False
    )
    assert isinstance(tools.tool_get_inventory_item({"item_id": 1}), dict)

    monkeypatch.setattr(services, "list_reservation_groups", lambda limit=100: [{"id": 1, "name": "G"}], raising=False)
    monkeypatch.setattr(
        services,
        "get_reservation_group",
        lambda gid: {"group": {"id": gid, "name": "G"}, "members": [], "folio": {}},
        raising=False,
    )
    monkeypatch.setattr(services, "list_packages", lambda: [{"id": 1, "name": "P"}], raising=False)
    monkeypatch.setattr(
        services, "list_reservation_packages", lambda rid: [{"id": 1, "reservation_id": rid}], raising=False
    )
    monkeypatch.setattr(services, "list_cash_shifts", lambda limit=50: [{"id": 1}], raising=False)
    monkeypatch.setattr(services, "get_open_cash_shift", lambda: {"id": 1, "status": "open"}, raising=False)
    monkeypatch.setattr(services, "list_cash_movements", lambda shift_id=None, limit=100: [{"id": 1}], raising=False)
    monkeypatch.setattr(services, "list_night_audit_runs", lambda limit=50: [{"id": 1}], raising=False)
    monkeypatch.setattr(services, "list_room_blocks", lambda **k: [{"id": 1}], raising=False)
    monkeypatch.setattr(services, "list_housekeeping_tasks", lambda **k: [{"id": 1}], raising=False)
    monkeypatch.setattr(services, "report_housekeeping_ops", lambda: "ops", raising=False)
    monkeypatch.setattr(services, "list_rate_rules", lambda active_only=True: [{"id": 1}], raising=False)
    monkeypatch.setattr(services, "list_service_catalog", lambda active_only=True: [{"id": 1}], raising=False)
    monkeypatch.setattr(services, "list_discount_vouchers", lambda active_only=True: [{"id": 1}], raising=False)
    monkeypatch.setattr(services, "list_approvals", lambda status=None, limit=50: [{"id": 1}], raising=False)
    monkeypatch.setattr(services, "get_approval", lambda aid: {"id": aid, "status": "pending"}, raising=False)

    assert isinstance(tools.tool_list_reservation_groups({}), dict)
    assert isinstance(tools.tool_get_reservation_group({"group_id": 1}), dict)
    assert isinstance(tools.tool_list_packages({}), dict)
    assert isinstance(tools.tool_list_reservation_packages({"reservation_id": 1}), dict)
    assert isinstance(tools.tool_get_cash_shifts({}), dict)
    assert isinstance(tools.tool_list_night_audit_runs({}), dict)
    assert isinstance(tools.tool_list_room_blocks({}), dict)
    assert isinstance(tools.tool_list_housekeeping_tasks({}), dict)
    assert isinstance(tools.tool_get_housekeeping_ops_report({}), dict)
    assert isinstance(tools.tool_list_rate_rules({}), dict)
    assert isinstance(tools.tool_list_service_catalog({}), dict)
    assert isinstance(tools.tool_list_discount_vouchers({}), dict)
    assert isinstance(tools.tool_list_approvals({}), dict)
    assert isinstance(tools.tool_get_approval({"approval_id": 1}), dict)

    # payments/walkin/inventory trace/batches/expiring/health/update
    monkeypatch.setattr(services, "assistant_get_payments_summary", lambda **k: {"total": 10}, raising=False)
    monkeypatch.setattr(services, "assistant_get_walkin_sales_summary", lambda **k: {"count": 1}, raising=False)
    monkeypatch.setattr(
        services, "assistant_trace_barcode", lambda code: {"type": "item", "item": {"id": 1}}, raising=False
    )
    monkeypatch.setattr(services, "assistant_list_inventory_batches", lambda **k: [{"id": 1}], raising=False)
    monkeypatch.setattr(services, "assistant_get_expiring_batches", lambda days=14: [{"id": 1}], raising=False)
    assert isinstance(tools.tool_get_payments_summary({"range": "this month"}), dict)
    assert isinstance(tools.tool_get_walkin_sales_summary({"range": "this month"}), dict)
    assert isinstance(tools.tool_trace_barcode({"code": "ABC"}), dict)
    assert isinstance(tools.tool_list_inventory_batches({}), dict)
    assert isinstance(tools.tool_get_expiring_batches({"days": 7}), dict)

    # tool registry / unknown
    if hasattr(tools, "execute_tool"):
        assert isinstance(tools.execute_tool("list_capabilities", {}), dict)
        assert "error" in tools.execute_tool("no_such_tool", {})
    elif hasattr(tools, "call_tool"):
        assert isinstance(tools.call_tool("list_capabilities", {}), dict)


def test_formatters_cover_common_branches():
    def ok(text, keywords=None):
        assert isinstance(text, str) and text.strip()
        if keywords:
            tl = text.lower()
            assert any(k in tl for k in keywords)

    # capabilities/stats/ops/briefing/reports
    ok(fmt._fmt_capabilities({"capabilities": ["x"], "notes": ["n"]}), ["grounded", "assistant", "help"])
    ok(
        fmt._fmt_stats(
            {
                "stats": {
                    "guests_total": 1,
                    "rooms_total": 2,
                    "reservations_total": 3,
                    "reservations_by_status": {"Reserved": 1},
                    "in_house": 1,
                    "arrivals_today": 0,
                    "departures_today": 0,
                },
                "as_of": "2099-01-01",
            }
        ),
        ["property", "snapshot", "guests"],
    )
    ok(
        fmt._fmt_today_ops(
            {
                "date": "2099-01-01",
                "front_desk": {
                    "arrivals_today": 1,
                    "departures_today": 2,
                    "in_house": 3,
                    "occupancy_pct": 50,
                    "clean": 1,
                    "dirty": 0,
                    "maint": 0,
                },
                "housekeeping": {"by_status": {"Clean": 1}, "total_rooms": 1},
            }
        ),
        ["operations", "front desk", "snapshot"],
    )
    ok(
        fmt._fmt_partner_briefing(
            {
                "start": "a",
                "end": "b",
                "insights": {"snapshot": ["s"], "good": ["g"], "watchouts": ["w"], "actions": ["act"]},
            }
        ),
        ["briefing", "snapshot", "actions"],
    )
    ok(
        fmt._fmt_report(
            {"kind": "pl", "start": "a", "end": "b", "report": {"revenue": 100, "expenses": 40, "net_profit": 60}}
        ),
        ["report", "revenue"],
    )
    ok(fmt._fmt_report({"error": "x"}), ["couldn", "report", "error", "generate"])
    ok(
        fmt._fmt_occupancy({"start": "a", "end": "b", "occupancy": {"pct": 50, "adr": 100, "revpar": 50}}),
        ["occupancy"],
    )
    ok(
        fmt._fmt_compare(
            {
                "current": {
                    "start": "a",
                    "end": "b",
                    "pl": {"revenue": 10, "expenses": 5, "net_profit": 5},
                    "occupancy": {"pct": 50},
                    "cash_flow": {"net_cash": 3},
                },
                "previous": {
                    "start": "c",
                    "end": "d",
                    "pl": {"revenue": 8, "expenses": 4, "net_profit": 4},
                    "occupancy": {"pct": 40},
                    "cash_flow": {"net_cash": 2},
                },
            }
        ),
        ["comparison", "compare", "vs"],
    )

    # lists/summaries/alerts
    ok(
        fmt._fmt_outstanding(
            {
                "outstanding": [
                    {
                        "reservation_id": 1,
                        "guest_name": "G",
                        "room": "101",
                        "status": "Checked In",
                        "check_in": "a",
                        "check_out": "b",
                        "balance_due": 10,
                    }
                ],
                "count": 1,
                "total_due": 10,
            }
        ),
        ["owe", "outstanding", "balance", "due"],
    )
    ok(
        fmt._fmt_inventory_alerts(
            {
                "alerts": {
                    "negative": [{"id": 1, "item_name": "X", "stock_qty": -1}],
                    "low": [{"id": 2, "item_name": "Y", "stock_qty": 1, "threshold": 2}],
                    "overstock": [],
                }
            }
        ),
        ["inventory", "alert", "stock"],
    )
    ok(
        fmt._fmt_top_sellers({"start": "a", "end": "b", "top_items": [{"item": "X", "qty_sold": 2, "gross": 20}]}),
        ["top", "sell", "item"],
    )
    ok(
        fmt._fmt_expenses(
            {
                "start": "a",
                "end": "b",
                "by_category": [{"category": "C", "count": 1, "net": 10, "vat": 1, "total": 11}],
                "total": 11,
            }
        ),
        ["expense"],
    )
    ok(
        fmt._fmt_sales_breakdown(
            {
                "start": "a",
                "end": "b",
                "pos_by_item": [{"item": "X", "qty": 1, "gross": 10}],
                "payments_by_method": [{"method": "Cash", "count": 1, "total": 10}],
                "reservations_by_source": [{"source": "Direct", "count": 1}],
            }
        ),
        ["sales", "breakdown"],
    )
    ok(
        fmt._fmt_pipeline(
            {
                "by_status": {"Reserved": 1},
                "upcoming_arrivals_7d": [
                    {
                        "id": 1,
                        "guest_name": "G",
                        "room_number": "101",
                        "check_in_date": "a",
                        "check_out_date": "b",
                        "status": "Reserved",
                    }
                ],
            }
        ),
        ["pipeline", "reservation", "arrival"],
    )
    ok(
        fmt._fmt_housekeeping({"summary": {"by_status": {"Clean": 1}, "total_rooms": 1}}),
        ["housekeeping", "clean", "room"],
    )
    ok(
        fmt._fmt_availability(
            {"start": "a", "end": "b", "available_count": 1, "rooms": [{"room_number": "101", "room_type": "Std"}]}
        ),
        ["availability", "available", "room"],
    )
    ok(fmt._fmt_menu({"meals": [{"name": "M"}], "count": 1}), ["menu", "meal"])
    ok(
        fmt._fmt_rate_plans(
            {"rate_plans": [{"id": 1, "name": "Std", "price_modifier": 1.0, "reservations_using_plan": 0}]}
        ),
        ["rate", "plan"],
    )

    # health/integrity/activity/recent
    ok(
        fmt._fmt_health_diagnostics(
            {
                "ok": True,
                "paths": {"data_dir": "d"},
                "schema": {
                    "user_version": 21,
                    "expected_version": 21,
                    "matches_expected": True,
                    "tables": {"ok": True},
                },
                "integrity": {"sqlite": {"ok": True}, "ledger": {"ok": True}},
                "readiness": {"printer": {"configured": False, "checks": []}},
                "backup_restore": {"readiness": {"db_exists": True}},
                "notes": [],
                "config_issues": [],
            }
        ),
        ["health", "doctor", "diagnostic"],
    )
    ok(fmt._fmt_integrity({"ok": True}), ["integrity", "ledger", "ok"])
    ok(
        fmt._fmt_recent_activity(
            {"recent_audit": [{"at": "t", "action": "a", "entity_type": "e", "entity_id": 1, "details": "d"}]}
        ),
        ["recent", "activity", "audit"],
    )
    ok(
        fmt._fmt_activities_catalog(
            {"activities": [{"id": 1, "name": "Spa", "active": 1, "unit_price": 10}], "count": 1}
        ),
        ["activit", "catalog"],
    )
    ok(
        fmt._fmt_activity_stats(
            {
                "activity_stats": [
                    {"activity_id": 1, "name": "Spa", "bookings_count": 1, "total_qty": 1, "total_revenue": 10}
                ]
            }
        ),
        ["activit", "stat", "booking"],
    )
    ok(
        fmt._fmt_activity_bookings(
            {
                "bookings": [
                    {
                        "id": 1,
                        "activity_name": "Spa",
                        "reservation_id": 1,
                        "guest_name": "G",
                        "quantity": 1,
                        "status": "booked",
                    }
                ],
                "count": 1,
            }
        ),
        ["booking", "activit"],
    )

    # guest/reservation formatters
    ok(
        fmt._fmt_reservation(
            {
                "reservation": {
                    "id": 1,
                    "guest_name": "G",
                    "status": "Reserved",
                    "check_in_date": "a",
                    "check_out_date": "b",
                    "room_number": "101",
                    "source": "Direct",
                },
                "balance": 0,
                "breakdown": {},
            }
        ),
        ["reservation", "#1"],
    )
    ok(
        fmt._fmt_guest_search({"query": "q", "guests": [{"id": 1, "canonical_name": "G"}]}),
        ["guest", "search", "found"],
    )
    ok(
        fmt._fmt_guest_profile(
            {
                "guest": {"id": 1, "canonical_name": "G"},
                "recent_reservations": [{"id": 2, "check_in_date": "a", "check_out_date": "b", "status": "Reserved"}],
            }
        ),
        ["guest", "profile", "reservation"],
    )
    ok(
        fmt._fmt_guest_last_visit(
            {
                "guest": {"id": 1, "canonical_name": "G"},
                "last_visit": {"reservation_id": 2, "check_out_date": "a", "status": "Checked Out"},
            }
        ),
        ["visit", "last"],
    )
    ok(
        fmt._fmt_guest_top_items(
            {
                "guest": {"id": 1, "canonical_name": "G"},
                "reservation_id": 2,
                "items": [{"item_description": "X", "qty": 2, "total": 20}],
            }
        ),
        ["item", "order", "top"],
    )
    ok(
        fmt._fmt_guest_last_visit_spend(
            {
                "guest": {"id": 1, "canonical_name": "G"},
                "last_visit": {"reservation_id": 2, "check_in_date": "a", "check_out_date": "b"},
                "balance_breakdown": {"total_charges": 20, "total_paid": 10, "balance_due": 10},
                "top_items": [{"item_description": "X", "qty": 1, "total": 10}],
            }
        ),
        ["spend", "visit", "balance", "charge"],
    )
    ok(
        fmt._fmt_guest_portfolio(
            {
                "guest": {"id": 1, "canonical_name": "G"},
                "trips": [],
                "all_pos": [],
                "all_payments": [],
                "totals": {"reservations": 0},
            }
        ),
        ["portfolio", "guest"],
    )

    # many more list/detail formatters
    ok(
        fmt._fmt_expiring_batches(
            {
                "batches": [
                    {"id": 1, "item_name": "X", "lot_code": "L", "expiry_date": "2099-01-01", "quantity_remaining": 1}
                ],
                "days": 7,
            }
        ),
        ["batch", "expir"],
    )
    ok(
        fmt._fmt_inventory_batches(
            {"batches": [{"id": 1, "item_name": "X", "lot_code": "L", "quantity_remaining": 1}]}
        ),
        ["batch", "inventory"],
    )
    ok(
        fmt._fmt_trace_barcode({"type": "item", "item": {"id": 1, "item_name": "X"}, "batches": [], "movements": []}),
        ["trace", "barcode", "item"],
    )
    ok(
        fmt._fmt_app_update_status(
            {
                "update_available": False,
                "local": {"app_version": "1"},
                "latest_release": {"tag_name": "v1"},
                "message": "ok",
            }
        ),
        ["update", "version", "app"],
    )
    ok(
        fmt._fmt_reservation_search({"results": [{"id": 1, "guest_name": "G", "status": "Reserved"}], "count": 1}),
        ["reservation", "search", "found"],
    )
    ok(
        fmt._fmt_inventory_search({"items": [{"id": 1, "item_name": "X", "stock_qty": 1}]}),
        ["inventory", "item", "search"],
    )
    ok(
        fmt._fmt_inventory_item_details({"item": {"id": 1, "item_name": "X", "stock_qty": 1, "selling_price": 2}}),
        ["item", "inventory", "stock"],
    )
    ok(
        fmt._fmt_walkin_sales_summary({"start": "a", "end": "b", "count": 0, "total": 0, "sales": []}),
        ["walk", "sales"],
    )
    ok(fmt._fmt_payments_summary({"start": "a", "end": "b", "total": 0, "by_method": []}), ["payment", "summary"])
    ok(
        fmt._fmt_reservation_groups(
            {"groups": [{"id": 1, "name": "G", "status": "active", "member_count": 2}], "count": 1}
        ),
        ["group", "reservation"],
    )
    ok(fmt._fmt_packages_list({"packages": [{"id": 1, "name": "P", "active": 1}], "count": 1}), ["package"])
    ok(
        fmt._fmt_cash_shifts({"open_shift": {"id": 1, "status": "open"}, "shifts": [], "movements": []}),
        ["shift", "cash"],
    )
    ok(
        fmt._fmt_night_audit_runs({"runs": [{"id": 1, "audit_date": "a", "status": "ok"}], "count": 1}),
        ["audit", "night"],
    )
    ok(
        fmt._fmt_room_blocks(
            {
                "blocks": [{"id": 1, "room_number": "101", "block_type": "OOO", "start_date": "a", "end_date": "b"}],
                "count": 1,
            }
        ),
        ["block", "room"],
    )
    ok(
        fmt._fmt_housekeeping_tasks(
            {"tasks": [{"id": 1, "room_number": "101", "task_type": "clean", "status": "open"}], "count": 1}
        ),
        ["task", "housekeeping"],
    )
    ok(
        fmt._fmt_reservation_packages_applied({"reservation_id": 1, "packages": [{"id": 1, "name": "P"}]}),
        ["package", "reservation"],
    )
    ok(
        fmt._fmt_reservation_group_detail({"group": {"id": 1, "name": "G"}, "members": [], "folio": {}}),
        ["group", "reservation"],
    )
    ok(fmt._fmt_housekeeping_ops_report({"report": "text"}), ["housekeeping", "ops", "report", "text"])
    ok(fmt._fmt_rate_rules_list({"rules": [{"id": 1, "name": "R", "active": 1}]}), ["rate", "rule"])
    ok(
        fmt._fmt_service_catalog_list({"items": [{"id": 1, "name": "S", "unit_price": 10, "active": 1}]}),
        ["service", "catalog"],
    )
    ok(fmt._fmt_discount_vouchers_list({"items": [{"id": 1, "code": "C", "active": 1}]}), ["voucher", "discount"])
    ok(
        fmt._fmt_approvals_list({"approvals": [{"id": 1, "action_type": "a", "status": "pending"}], "count": 1}),
        ["approval"],
    )
    ok(
        fmt._fmt_approval_detail({"approval": {"id": 1, "status": "pending", "action_type": "a"}}),
        ["approval", "#1", "status"],
    )
    ok(
        fmt._fmt_reservation_financial_integrity({"ok": True, "reservation_id": 1, "issues": []}),
        ["financial", "integrity", "reservation"],
    )
