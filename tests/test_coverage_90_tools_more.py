from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import app_core.assistant.tools as tools


class _Row(dict):
    __getattr__ = dict.get


def _connect_rows(rows):
    class C:
        def execute(self, *a, **k):
            return SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: rows[0] if rows else None)
    return C()


def test_tools_exception_and_fallback_branches(monkeypatch):
    # guest spend/portfolio branches
    monkeypatch.setattr(tools.services, "assistant_get_guest_last_visit", lambda gid: {"last_visit": {"reservation_id": 5}})
    monkeypatch.setattr(tools.services, "get_balance_breakdown", lambda rid: (_ for _ in ()).throw(RuntimeError("x")))
    out = tools.tool_get_guest_last_visit_spend({"guest_id": 1})
    assert "spend_error" in out

    monkeypatch.setattr(tools.services, "get_guest_portfolio", lambda gid: None)
    assert tools.tool_get_guest_portfolio({"guest_id": 1}).get("error")
    monkeypatch.setattr(tools.services, "get_guest_portfolio", lambda gid: (_ for _ in ()).throw(RuntimeError("x")))
    assert tools.tool_get_guest_portfolio({"guest_id": 1}).get("error")

    for svc, fn, arg in [
        ("list_activities", tools.tool_list_activities, {}),
        ("get_activity_stats", tools.tool_get_activity_stats, {}),
        ("list_activity_bookings", tools.tool_list_activity_bookings, {}),
    ]:
        monkeypatch.setattr(tools.services, svc, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
        assert fn(arg).get("error")

    # property stats exception
    import app_core.db as db
    monkeypatch.setattr(db, "connect", lambda: (_ for _ in ()).throw(RuntimeError("db")))
    assert tools.tool_get_property_stats({}).get("error")

    # today ops fallbacks and exception
    monkeypatch.setattr(tools.services, "get_front_desk_ops", lambda day: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
    monkeypatch.setattr(tools.services, "report_daily_ops", lambda day: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
    monkeypatch.setattr(tools.services, "get_front_desk_summary", lambda: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
    orig_hk = tools.tool_get_housekeeping_summary
    monkeypatch.setattr(tools, "tool_get_housekeeping_summary", lambda args=None: {"summary": {"dirty": 1}})
    out = tools.tool_get_today_ops({"date": "2026-01-01"})
    assert out.get("date") == "2026-01-01"
    # force outer exception
    monkeypatch.setattr(tools, "tool_get_housekeeping_summary", lambda args=None: (_ for _ in ()).throw(RuntimeError("hk")))
    assert tools.tool_get_today_ops({}).get("error")
    monkeypatch.setattr(tools, "tool_get_housekeeping_summary", orig_hk)

    # front desk unavailable/exceptions
    for name in ("get_front_desk_ops", "get_front_desk_arrivals", "get_front_desk_in_house", "get_front_desk_departures"):
        monkeypatch.delattr(tools.services, name, raising=False)
    assert "error" in tools.tool_get_front_desk_ops({})
    assert "error" in tools.tool_get_front_desk_arrivals({})
    assert "error" in tools.tool_get_front_desk_in_house({})
    assert "error" in tools.tool_get_front_desk_departures({})
    for fn_name, svc_name in [
        ("tool_get_front_desk_ops", "get_front_desk_ops"),
        ("tool_get_front_desk_arrivals", "get_front_desk_arrivals"),
        ("tool_get_front_desk_in_house", "get_front_desk_in_house"),
        ("tool_get_front_desk_departures", "get_front_desk_departures"),
    ]:
        monkeypatch.setattr(tools.services, svc_name, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
        assert getattr(tools, fn_name)({}).get("error")

    # housekeeping/inventory alerts exceptions
    monkeypatch.setattr(db, "connect", lambda: (_ for _ in ()).throw(RuntimeError("db")))
    assert tools.tool_get_housekeeping_summary({}).get("error")
    monkeypatch.setattr(tools.services, "get_inventory_warnings", lambda: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
    assert tools.tool_get_inventory_alerts({}).get("error")

    # outstanding balances: calc error per reservation and outer exception
    rows = [
        _Row(id=1, guest_name="A", status="Reserved", check_in_date="s", check_out_date="e", agreed_nightly_rate=10, room_number="1"),
        _Row(id=2, guest_name="B", status="Reserved", check_in_date="s", check_out_date="e", agreed_nightly_rate=10, room_number="2"),
    ]
    monkeypatch.setattr(db, "connect", lambda: _connect_rows(rows))
    def calc(rid):
        if rid == 1:
            raise RuntimeError("bad")
        return 25.0
    monkeypatch.setattr(tools.services, "calculate_guest_balance", calc, raising=False)
    out = tools.tool_get_outstanding_balances({})
    assert out.get("count") == 1 and out.get("total_due") == 25.0
    monkeypatch.setattr(db, "connect", lambda: (_ for _ in ()).throw(RuntimeError("db")))
    assert tools.tool_get_outstanding_balances({}).get("error")

    # report-like tools exceptions
    monkeypatch.setattr(db, "connect", lambda: (_ for _ in ()).throw(RuntimeError("db")))
    assert tools.tool_get_top_selling_items({"start": "2026-01-01", "end": "2026-01-31"}).get("error")
    assert tools.tool_get_expenses_summary({"start": "2026-01-01", "end": "2026-01-31"}).get("error")
    assert tools.tool_get_sales_breakdown({"start": "2026-01-01", "end": "2026-01-31"}).get("error")
    assert tools.tool_get_reservation_pipeline({}).get("error")
    assert tools.tool_get_recent_activity({}).get("error")

    monkeypatch.setattr(tools.services, "get_rooms_available_between", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
    assert tools.tool_get_room_availability({"start": "2026-01-01", "end": "2026-01-02"}).get("error")
    monkeypatch.setattr(tools.services, "list_meals", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
    assert tools.tool_get_menu_info({}).get("error")

    # compare_periods sub-errors
    monkeypatch.setattr(tools.services, "report_pl_statement", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("pl")), raising=False)
    monkeypatch.setattr(tools.services, "get_occupancy_for_window", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("occ")), raising=False)
    monkeypatch.setattr(tools.services, "report_cash_flow", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cf")), raising=False)
    out = tools.tool_compare_periods({"start": "2026-01-01", "end": "2026-01-31"})
    assert "pl_error" in out["current"] and "occ_error" in out["current"] and "cash_error" in out["current"]

    # business_partner_briefing exception branches
    for name in [
        "get_front_desk_summary", "report_pl_statement", "get_occupancy_for_window", "verify_ledger_integrity",
    ]:
        monkeypatch.setattr(tools.services, name, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
    monkeypatch.setattr(tools, "tool_get_property_stats", lambda args=None: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(tools, "tool_get_outstanding_balances", lambda args=None: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(tools, "tool_get_inventory_alerts", lambda args=None: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(tools, "tool_get_top_selling_items", lambda args=None: (_ for _ in ()).throw(RuntimeError("x")))
    out = tools.tool_business_partner_briefing({"start": "2026-01-01", "end": "2026-01-31"})
    assert isinstance(out, dict) and "insights" in out

    # app update / health diagnostics exceptions
    import app_core.updater as updater
    import app_core.doctor as doctor
    monkeypatch.setattr(updater, "get_local_state", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert tools.tool_get_app_update_status({}).get("error")
    monkeypatch.setattr(doctor, "collect_health", lambda check_updates=False: (_ for _ in ()).throw(RuntimeError("x")))
    assert tools.tool_get_health_diagnostics({}).get("error")

    # expiring/list batches bad parsing and exceptions
    monkeypatch.setattr(tools.services, "get_expiring_batches", lambda *a, **k: [{"id": 1}], raising=False)
    out = tools.tool_get_expiring_batches({"within_days": "bad"})
    assert out.get("within_days") is None
    monkeypatch.setattr(tools.services, "get_expiring_batches", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
    assert tools.tool_get_expiring_batches({}).get("error")

    monkeypatch.setattr(tools.services, "list_inventory_batches", lambda **k: [{"id": 1}], raising=False)
    out = tools.tool_list_inventory_batches({"item_id": "bad", "expiring_within_days": "bad"})
    assert out.get("item_id") is None
    monkeypatch.setattr(tools.services, "list_inventory_batches", lambda **k: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
    assert tools.tool_list_inventory_batches({}).get("error")

    # trace barcode: missing code, batch movements exception, outer exception
    assert "error" in tools.tool_trace_barcode({"code": ""})
    monkeypatch.setattr(tools.services, "resolve_barcode", lambda code: {"type": "batch", "batch": {"id": 7}}, raising=False)
    monkeypatch.setattr(db, "connect", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    out = tools.tool_trace_barcode({"code": "ABC"})
    assert out.get("type") == "batch"  # movement fetch failed but result returned
    monkeypatch.setattr(tools.services, "resolve_barcode", lambda code: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
    assert tools.tool_trace_barcode({"code": "ABC"}).get("error")

    # search reservations filters and exception
    monkeypatch.setattr(db, "connect", lambda: _connect_rows([_Row(id=1, guest_name="G")]))
    out = tools.tool_search_reservations({"query": "G", "status": "Reserved", "source": "web", "market_segment": "corp", "start": "2026-01-01", "end": "2026-01-31", "reservation_id": 1, "limit": 999})
    assert out.get("count") == 1
    monkeypatch.setattr(db, "connect", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert tools.tool_search_reservations({}).get("error")

    # search/get inventory item branches
    monkeypatch.setattr(tools.services, "get_inventory_items", lambda **k: [_Row(id=1, item_name="A", category="C", stock_qty=1, selling_price=2, sku="s", barcode="b")], raising=False)
    out = tools.tool_search_inventory({"query": "A"})
    assert out.get("count") == 1
    monkeypatch.setattr(tools.services, "get_inventory_items", lambda **k: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
    assert tools.tool_search_inventory({}).get("error")

    assert tools.tool_get_inventory_item({}).get("error")
    monkeypatch.setattr(tools.services, "get_inventory_item", lambda iid: None, raising=False)
    assert tools.tool_get_inventory_item({"item_id": 1}).get("error")
    monkeypatch.setattr(tools.services, "get_inventory_item", lambda iid: _Row(id=1, item_name="A"), raising=False)
    out = tools.tool_get_inventory_item({"item_id": 1})
    assert out.get("item", {}).get("id") == 1
    monkeypatch.setattr(tools.services, "get_inventory_item", lambda iid: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
    assert tools.tool_get_inventory_item({"item_id": 1}).get("error")

    # walkin sales summary branches
    monkeypatch.setattr(tools.services, "list_walkin_pos", lambda limit=30: [_Row(id=1, quantity=2, unit_price=3, tax_amount=1, created_at="t", item_description="X", lot_code="L")], raising=False)
    monkeypatch.setattr(tools.services, "list_walkin_payments", lambda limit=20: [_Row(id=1, amount=5, at="t", method="Cash", payment_type="walkin", notes="", voided_at=None), _Row(id=2, amount=1, at="t", method="Cash", voided_at="v")], raising=False)
    out = tools.tool_get_walkin_sales_summary({"limit": 5, "limit_payments": 5})
    assert out.get("walkin_pos_count") == 1 and out.get("walkin_payments_total") == 5.0
    monkeypatch.setattr(tools.services, "list_walkin_pos", lambda limit=30: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
    assert tools.tool_get_walkin_sales_summary({}).get("error")

    # payments summary and exception
    monkeypatch.setattr(db, "connect", lambda: _connect_rows([_Row(method="Cash", n=1, total=10.0)]))
    # connect is used twice; create a smarter fake
    class PConn:
        def execute(self, q, p=None):
            if "GROUP BY method" in q:
                return SimpleNamespace(fetchall=lambda: [_Row(method="Cash", n=1, total=10.0)])
            return SimpleNamespace(fetchone=lambda: _Row(n=1, total=2.0))
    monkeypatch.setattr(db, "connect", lambda: PConn())
    out = tools.tool_get_payments_summary({"start": "2026-01-01", "end": "2026-01-31"})
    assert out.get("total") == 10.0
    monkeypatch.setattr(db, "connect", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert tools.tool_get_payments_summary({"start": "2026-01-01", "end": "2026-01-31"}).get("error")

    # unavailable/exception branches for newer tools
    for attr in ["list_reservation_groups", "list_packages", "list_cash_shifts", "get_open_cash_shift", "list_cash_movements", "list_night_audit_runs", "list_room_blocks", "list_housekeeping_tasks", "list_reservation_packages", "get_reservation_group"]:
        monkeypatch.setattr(tools.services, attr, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
    # also make hasattr checks fail for "unavailable" cases
    for fn, attr in [
        (tools.tool_get_cash_shifts, "list_cash_shifts"),
        (tools.tool_list_night_audit_runs, "list_night_audit_runs"),
        (tools.tool_list_room_blocks, "list_room_blocks"),
        (tools.tool_list_housekeeping_tasks, "list_housekeeping_tasks"),
        (tools.tool_list_reservation_packages, "list_reservation_packages"),
        (tools.tool_get_reservation_group, "get_reservation_group"),
    ]:
        monkeypatch.delattr(tools.services, attr, raising=False)
        assert fn({}).get("error")
    # reattach raising implementations and call to hit exception handlers
    for fn, attr in [
        (tools.tool_list_reservation_groups, "list_reservation_groups"),
        (tools.tool_list_packages, "list_packages"),
        (tools.tool_get_cash_shifts, "list_cash_shifts"),
        (tools.tool_list_night_audit_runs, "list_night_audit_runs"),
        (tools.tool_list_room_blocks, "list_room_blocks"),
        (tools.tool_list_housekeeping_tasks, "list_housekeeping_tasks"),
    ]:
        monkeypatch.setattr(tools.services, attr, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
        if fn is tools.tool_get_cash_shifts:
            monkeypatch.setattr(tools.services, "list_cash_shifts", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
        assert fn({}).get("error")

    # reservation packages/group argument/exception paths
    assert tools.tool_list_reservation_packages({}).get("error")
    monkeypatch.setattr(tools.services, "list_reservation_packages", lambda rid: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
    assert tools.tool_list_reservation_packages({"reservation_id": 1}).get("error")
    assert tools.tool_get_reservation_group({}).get("error")
    monkeypatch.setattr(tools.services, "get_reservation_group", lambda gid: None, raising=False)
    assert tools.tool_get_reservation_group({"group_id": 1}).get("error")
    monkeypatch.setattr(tools.services, "get_reservation_group", lambda gid: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
    assert tools.tool_get_reservation_group({"group_id": 1}).get("error")


def test_tools_more_success_paths(monkeypatch):
    import app_core.db as db
    # housekeeping summary success
    rows = [_Row(housekeeping_status="Dirty", n=2), _Row(housekeeping_status="Clean", n=3)]
    monkeypatch.setattr(db, "connect", lambda: _connect_rows(rows))
    out = tools.tool_get_housekeeping_summary({})
    assert out.get("summary", {}).get("total_rooms") == 5

    # menu info with non-dict/odd objects
    monkeypatch.setattr(tools.services, "list_meals", lambda active_only=False: [{"name": "A"}, "B"], raising=False)
    out = tools.tool_get_menu_info({})
    assert out.get("count") == 2

    # recent activity success
    monkeypatch.setattr(tools.services, "get_audit_log", lambda limit: [_Row(id=1, action="x")], raising=False)
    out = tools.tool_get_recent_activity({"limit": 5})
    assert out.get("recent_audit")

    # trace barcode success with batch movements
    monkeypatch.setattr(tools.services, "resolve_barcode", lambda code: {"type": "batch", "batch": {"id": 7}}, raising=False)
    monkeypatch.setattr(db, "connect", lambda: _connect_rows([_Row(at="t", change_type="add", quantity=1, reference="r", unit_cost=1, balance_after=1)]))
    out = tools.tool_trace_barcode({"code": "ABC"})
    assert out.get("batch_movements")

    # get_inventory_item when row is plain dict-like
    monkeypatch.setattr(tools.services, "get_inventory_item", lambda iid: {"id": iid, "item_name": "A"}, raising=False)
    out = tools.tool_get_inventory_item({"item_id": 3})
    assert out["item"]["id"] == 3

    # reservation packages success compacting
    monkeypatch.setattr(tools.services, "list_reservation_packages", lambda rid: [{"id": 1, "snapshot_json": json.dumps({"items": [1, 2]})}], raising=False)
    out = tools.tool_list_reservation_packages({"reservation_id": 1})
    assert out.get("count") == 1

    # reservation group success
    monkeypatch.setattr(tools.services, "get_reservation_group", lambda gid: {"id": gid, "members": [{"reservation_id": 1, "guest_name": "G", "room_number": "1", "reservation_status": "Reserved", "check_in_date": "s", "check_out_date": "e", "balance_due": 1, "role": "m", "total_charges": 1, "total_paid": 0}]}, raising=False)
    out = tools.tool_get_reservation_group({"group_id": 2})
    assert out.get("id") == 2 or out.get("group", {}).get("id") == 2 or "members" in str(out)

    # night audit json parse branch
    monkeypatch.setattr(tools.services, "list_night_audit_runs", lambda limit=20: [{"id": 1, "summary_json": "{\"a\":1}", "exceptions_json": "bad json"}], raising=False)
    out = tools.tool_list_night_audit_runs({})
    assert out.get("count") == 1

    # room blocks include_past/status logic
    monkeypatch.setattr(tools.services, "list_room_blocks", lambda room_id=None, status="active": [{"id": 1, "status": status}], raising=False)
    out = tools.tool_list_room_blocks({"include_past": True})
    assert out.get("status_filter") == "all"
    out = tools.tool_list_room_blocks({})
    assert out.get("status_filter") == "active"

    # packages/groups/cash shifts/housekeeping tasks basic success
    monkeypatch.setattr(tools.services, "list_reservation_groups", lambda status="": [{"id": 1, "name": "G", "members": []}], raising=False)
    assert tools.tool_list_reservation_groups({}).get("count") == 1
    monkeypatch.setattr(tools.services, "list_packages", lambda active_only=False: [{"id": 1, "name": "P", "items": []}], raising=False)
    assert tools.tool_list_packages({}).get("count") == 1
    monkeypatch.setattr(tools.services, "get_open_cash_shift", lambda: {"id": 9}, raising=False)
    monkeypatch.setattr(tools.services, "list_cash_shifts", lambda limit=20: [{"id": 9}], raising=False)
    monkeypatch.setattr(tools.services, "list_cash_movements", lambda sid: [{"id": 1}], raising=False)
    out = tools.tool_get_cash_shifts({})
    assert out.get("open_shift", {}).get("id") == 9
    monkeypatch.setattr(tools.services, "list_housekeeping_tasks", lambda status="", room_id=None: [{"id": 1}], raising=False)
    assert tools.tool_list_housekeeping_tasks({}).get("count") == 1

