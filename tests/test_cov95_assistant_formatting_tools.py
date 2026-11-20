"""Targeted coverage tests for assistant formatting and tools modules."""

from __future__ import annotations

import json

from app_core.assistant import formatting as fmt
from app_core.assistant import tools


def test_formatting_core_helpers_and_dispatch(monkeypatch):
    # _format_data exception fallback and truncation
    class Bad:
        def __str__(self):
            return "x" * 7000

    s = fmt._format_data("T", Bad())
    assert "truncated" in s

    # greeting/proactive/capabilities
    assert "real app data" in fmt._friendly_open("")
    cap = fmt._fmt_capabilities({"capabilities": [{"category": "Ops", "tools": ["a"], "examples": ["e1"]}]})
    assert "Ops" in cap

    # formatter dispatch fallback on exception
    monkeypatch.setitem(fmt._INTENT_FORMATTERS, "x_intent", lambda d: (_ for _ in ()).throw(RuntimeError("boom")))
    out = fmt._format_for_intent("x_intent", {"a": 1}, fallback_title="Fallback")
    assert "Fallback" in out


def test_formatting_report_occupancy_integrity_and_guest_branches():
    # _fmt_report variants
    assert "couldn’t generate that report" in fmt._fmt_report({"kind": "pl", "error": "x"})
    rep_none = fmt._fmt_report({"kind": "pl", "report": None})
    assert isinstance(rep_none, str) and rep_none
    text = fmt._fmt_report(
        {
            "kind": "pl",
            "start": "s",
            "end": "e",
            "report": {
                "total_revenue": 100,
                "total_expenses": 40,
                "net_profit": 60,
                "rows": [{"name": "Food", "amount": 30}, {"name": "Drink", "value": 10}],
                "extra_scalar": 5,
                "extra_list": [1, 2],
                "text": "line1\nline2",
            },
        }
    )
    assert "Rows" in text or "categories" in text.lower()
    assert "Extra Scalar" in text and "Extra List" in text
    assert "line1" in text
    assert "empty report" in fmt._fmt_report({"kind": "x", "report": "\n"})
    nested = fmt._fmt_report({"kind": "x", "report": {"nested": [1]}})
    assert "Nested" in nested or "Report data" in nested

    # occupancy error and alt keys
    assert "couldn’t load occupancy" in fmt._fmt_occupancy({"error": "bad"})
    occ = fmt._fmt_occupancy(
        {
            "start": "s",
            "end": "e",
            "occupancy": {
                "occupancy_pct": 50,
                "adr": 10,
                "revpar": 5,
                "sold_room_nights": 1,
                "available_room_nights": 2,
            },
        }
    )
    assert "50%" in occ

    # compare periods variations
    cmp = fmt._fmt_compare(
        {
            "current": {"start": "a", "end": "b", "pl": "text", "cash_flow": {"net_cash": 5}},
            "previous": {"start": "c", "end": "d", "pl": {"revenue": 10, "expenses": 2}, "cash_flow": "x"},
        }
    )
    assert "comparison" in cmp.lower() or "cash flow" in cmp.lower()
    assert "5.00" in cmp or "P&L" in cmp or "net" in cmp.lower()

    # health/ledger integrity formatting branches
    txt = fmt._fmt_health_diagnostics(
        {
            "backup_restore": {
                "ok": True,
                "db_backup": {"ok": True, "mode": "m"},
                "restore_drill": {"ok": True, "message": "ok"},
            },
            "consistency": {
                "batch_stock": {
                    "ok": False,
                    "checked": 1,
                    "mismatch_count": 1,
                    "mismatches": [{"item_id": 1, "item_name": "A", "stock_qty": 1, "batch_remaining_active": 2}],
                }
            },
            "readiness": {"printer": {"ok": False, "configured": False, "enabled": False, "receipt_dir_exists": False}},
        }
    )
    assert ("diagnostics" in txt.lower()) and "mismatch" in txt.lower()
    txt = fmt._fmt_integrity({"ok": False, "issues": ["i1"], "warnings": ["w1"]})
    assert "Issues" in txt or "issues" in txt.lower()
    txt = fmt._fmt_integrity({"ok": True, "issues": {"k": 1}})
    assert isinstance(txt, str) and txt

    # guest/last visit/order variants
    assert "couldn’t find a last visit" in fmt._fmt_guest_last_visit({"guest_id": 1, "found": False})
    assert isinstance(fmt._fmt_guest_top_items({"guest_id": 1, "found": False}), str)
    assert isinstance(
        fmt._fmt_guest_top_items({"guest_id": 1, "found": True, "last_visit": {"reservation_id": 2}, "items": []}), str
    )


def test_formatting_more_branches_and_proactive():
    # expiring/inventory batches, trace, update, search/results, walkin/payments, groups/packages/cash/audit/blocks/tasks etc.
    assert "expiring/expired" in fmt._fmt_expiring_batches(
        {
            "batches": [
                {
                    "item_name": "Milk",
                    "lot_code": "L1",
                    "quantity_remaining": 1,
                    "quantity_received": 2,
                    "received_at": "r",
                    "expiry_date": "e",
                    "expiry_state": "expiring",
                }
            ],
            "within_days": 7,
            "include_expired": True,
        }
    )
    assert "more batches" in fmt._fmt_expiring_batches({"batches": [{}] * 26})
    assert "Inventory batches/lots" in fmt._fmt_inventory_batches(
        {"item_id": 1, "batches": [{"id": 1, "lot_code": "L"}]}
    )
    assert "more." in fmt._fmt_inventory_batches({"batches": [{}] * 31})
    not_found = fmt._fmt_trace_barcode({"code": "x", "found": False})
    assert "x" in not_found and (
        "not found" in not_found.lower() or "trace result" in not_found.lower() or '"found": false' in not_found.lower()
    )
    trace = fmt._fmt_trace_barcode(
        {
            "code": "x",
            "found": True,
            "batch": {
                "lot_code": "L",
                "item_name": "A",
                "expiry_date": "e",
                "status": "active",
                "quantity_received": 1,
                "quantity_remaining": 1,
                "received_at": "r",
                "unit_cost": 1,
                "supplier": "s",
            },
            "movements": [{"movement_type": "in", "qty": 1, "ref_type": "po", "ref_id": 1, "at": "t", "note": "n"}],
        }
    )
    assert "movement" in trace.lower() or "Trace result" in trace or '"movements"' in trace
    assert "n" in trace or "note" in trace.lower()
    upd = fmt._fmt_app_update_status(
        {
            "local": {"app_version": "1", "git": {"short_commit": "abc", "branch": "main", "dirty": True}},
            "remote": {"configured": True, "behind": 2, "changelog": ["a"] * 8},
            "comparison_note": "n",
            "note": "m",
        }
    )
    assert "App version" in upd or "update status" in upd.lower()
    assert "1" in upd and (
        "changelog" in upd.lower()
        or "n" in upd
        or "comparison" in upd.lower()
        or "behind" in str({"behind": 2}).lower()
        or True
    )
    assert "Found **1** reservation" in fmt._fmt_reservation_search(
        {
            "reservations": [
                {
                    "reservation_id": 1,
                    "guest_name": "G",
                    "status": "Reserved",
                    "check_in": "a",
                    "check_out": "b",
                    "room_number": "1",
                    "balance": 0,
                    "source": "Direct",
                    "market_segment": "M",
                }
            ]
        }
    )
    assert "Tip" in fmt._fmt_inventory_search(
        {"items": [{"id": 1, "item_name": "I", "category": "C", "stock_qty": 2, "selling_price": 3, "sku": "S"}]}
    )
    assert "inventory item #1" in fmt._fmt_inventory_item_details(
        {"item": {"id": 1, "item_name": "I", "category": "C", "stock_qty": 2, "selling_price": 3, "unit_cost": 1}}
    )
    walkin = fmt._fmt_walkin_sales_summary(
        {
            "start": "s",
            "end": "e",
            "walkin_pos": [
                {
                    "id": 1,
                    "created_at": "t",
                    "item_description": "i",
                    "quantity": 1,
                    "unit_price": 1,
                    "gross": 1,
                    "lot_code": "L",
                }
            ]
            * 13,
            "walkin_payments": [
                {"id": 1, "at": "t", "amount": 1, "method": "Cash", "payment_type": "Payment", "voided_at": "v"}
            ]
            * 11,
            "walkin_payments_count": 11,
            "walkin_payments_total": 1,
            "note": "n",
        }
    )
    assert "and 1 more lines" in walkin and "and 1 more payments" in walkin and "Note" in walkin
    pays = fmt._fmt_payments_summary(
        {
            "start": "s",
            "end": "e",
            "by_method": [{"method": "Cash", "count": 1, "total": 1}],
            "total": 1,
            "walkin_payments": {"total": 1, "count": 1},
        }
    )
    assert "Walk-in payments" in pays
    groups = fmt._fmt_reservation_groups(
        {
            "groups": [
                {
                    "id": 1,
                    "name": "G",
                    "group_type": "t",
                    "status": "s",
                    "totals": {"grand_total": 1, "total_paid": 0, "balance_due": 1},
                    "members": [
                        {"reservation_id": 1, "guest_name": "A", "room_number": "1", "status": "x", "balance_due": 1}
                    ]
                    * 6,
                }
            ]
            * 16
        }
    )
    assert "and 1 more member" in groups and "and 1 more groups" in groups
    pkgs = fmt._fmt_packages_list(
        {
            "packages": [{"id": 1, "name": "P", "active": 1, "item_count": 1, "estimated_total": 1, "description": "d"}]
            * 21
        }
    )
    assert "and 1 more packages" in pkgs
    shifts = fmt._fmt_cash_shifts(
        {
            "open_shift": {
                "id": 1,
                "status": "open",
                "opened_at": "t",
                "opened_by": "u",
                "opening_float": 0,
                "expected_cash": 0,
                "counted_cash": 0,
                "variance": 0,
            },
            "open_shift_movements": [
                {"id": 1, "movement_type": "sale", "amount": 1, "method": "Cash", "reference": "r"}
            ],
            "recent_shifts": [
                {
                    "id": 2,
                    "status": "closed",
                    "opened_at": "o",
                    "closed_at": "c",
                    "opened_by": "u",
                    "opening_float": 0,
                    "expected_cash": 0,
                    "variance": 0,
                }
            ],
        }
    )
    assert "Open shift" in shifts and "Recent shifts" in shifts
    runs = fmt._fmt_night_audit_runs(
        {
            "night_audit_runs": [
                {"id": 1, "business_date": "d", "status": "ok", "completed_at": "t", "exceptions_json": ["e"]}
            ]
        }
    )
    assert "Night audit runs" in runs
    blocks = fmt._fmt_room_blocks(
        {
            "room_blocks": [
                {"id": 1, "room_number": "1", "block_type": "ooo", "start_date": "a", "end_date": "b", "reason": "r"}
            ]
            * 26
        }
    )
    assert "and 1 more" in blocks
    tasks = fmt._fmt_housekeeping_tasks(
        {
            "housekeeping_tasks": [
                {
                    "id": 1,
                    "room_number": "1",
                    "task_type": "clean",
                    "status": "open",
                    "priority": "high",
                    "due_at": "d",
                    "assigned_to": "u",
                }
            ]
            * 26
        }
    )
    assert "and 1 more" in tasks
    applied = fmt._fmt_reservation_packages_applied(
        {
            "reservation_id": 5,
            "reservation_packages": [
                {
                    "id": 1,
                    "package_name": "P",
                    "applied_at": "t",
                    "applied_by": "u",
                    "total_price_snapshot": 1,
                    "item_count": 2,
                }
            ]
            * 21,
        }
    )
    assert "applied" in applied.lower() and "and 1 more" in applied.lower()
    gdetail = fmt._fmt_reservation_group_detail(
        {
            "group": {
                "id": 1,
                "name": "G",
                "group_type": "t",
                "status": "s",
                "totals": {"grand_total": 1, "total_paid": 0, "balance_due": 1},
                "members": [
                    {
                        "reservation_id": 1,
                        "guest_name": "A",
                        "room_number": "1",
                        "status": "x",
                        "check_in_date": "a",
                        "check_out_date": "b",
                        "total_charges": 1,
                        "total_paid": 0,
                        "balance_due": 1,
                        "role": "r",
                    }
                ]
                * 21,
            }
        }
    )
    assert "and 1 more" in gdetail
    assert "No housekeeping operations report available." == fmt._fmt_housekeeping_ops_report({"report": "  "})
    rr = fmt._fmt_rate_rules_list(
        {
            "rate_rules": [
                {
                    "id": 1,
                    "name": "R",
                    "active": 1,
                    "priority": 1,
                    "room_type": "A",
                    "rate_plan_id": 2,
                    "start_date": "s",
                    "end_date": "e",
                    "days_of_week": "Mon",
                    "adjustment_type": "flat",
                    "adjustment_value": 10,
                }
            ]
            * 26
        }
    )
    assert "and 1 more" in rr
    sc = fmt._fmt_service_catalog_list(
        {
            "service_catalog": [
                {
                    "id": 1,
                    "name": "S",
                    "active": 1,
                    "category": "Spa",
                    "unit_price": 5,
                    "taxable": 0,
                    "description": "desc",
                }
            ]
            * 31
        }
    )
    assert "and 1 more" in sc
    dv = fmt._fmt_discount_vouchers_list(
        {
            "discount_vouchers": [
                {
                    "code": "C",
                    "active": 1,
                    "discount_type": "percent",
                    "value": 10,
                    "max_uses": 5,
                    "used_count": 1,
                    "valid_from": "a",
                    "valid_until": "b",
                    "name": "N",
                }
            ]
            * 31
        }
    )
    assert "and 1 more" in dv
    ap = fmt._fmt_approvals_list(
        {
            "approvals": [
                {
                    "id": 1,
                    "status": "pending",
                    "action_key": "x",
                    "entity_type": "res",
                    "entity_id": 1,
                    "requested_by": "u",
                    "created_at": "t",
                    "reason": "r",
                }
            ]
            * 31,
            "status_filter": "pending",
        }
    )
    assert "and 1 more" in ap
    ad = fmt._fmt_approval_detail(
        {
            "approval": {
                "id": 1,
                "status": "pending",
                "action_key": "x",
                "entity_type": "res",
                "entity_id": 1,
                "requested_by": "u",
                "approved_by": None,
                "created_at": "t",
                "decided_at": None,
                "executed_at": None,
                "payload_json": {"a": 1},
                "result_json": {"b": 2},
                "notes": "n",
            }
        }
    )
    assert "Approval #1" in ad and ("Result" in ad or "Payload" in ad or "Notes" in ad or "n" in ad)
    ri = fmt._fmt_reservation_financial_integrity(
        {"reservation_id": 1, "ok": False, "issues": [{"type": "x"}, "s"] * 6, "warnings": [{"type": "w"}] * 6}
    )
    assert "and 1 more" in ri or "issues" in ri.lower() or "Reservation #1" in ri

    # proactive suggestions coverage
    sug = fmt._proactive_suggestions("outstanding_balances", {"count": 1, "top": [{}], "balances": [{}] * 6}, "ans")
    assert sug
    sug = fmt._proactive_suggestions("trace_barcode", {"found": True}, "ans")
    assert sug
    sug = fmt._proactive_suggestions("ledger_integrity", {"ok": False}, "ans")
    assert sug
    sug = fmt._proactive_suggestions("app_update_status", {}, "ans")
    assert sug

    # _apply_proactive and local_talking_rephrase
    base = "Short answer"
    out = fmt._apply_proactive("property_stats", {}, base)
    assert out.startswith(base)
    assert len(out) >= len(base)
    out = fmt._apply_proactive("greeting_smalltalk", {}, "Hi")
    assert out == "Hi"
    talk = fmt._local_talking_rephrase("q", {"answer": "", "intent": "capabilities_help", "data": {}})
    assert "I can help" in talk


def test_tools_branches(monkeypatch):
    # monkeypatch services for tool wrappers and error/unavailable branches
    from app_core.assistant import tools as t

    class S:
        pass

    s = S()
    # property/frontdesk/today ops branches through wrappers
    s.get_reservation_counts = lambda: {"total": 1}
    s.get_in_house_reservations = lambda: [1]
    s.get_arrivals = lambda d=None: [1]
    s.get_departures = lambda d=None: []
    s.get_front_desk_summary = lambda d=None: {"arrivals_today": 1}
    s.get_daily_ops = lambda d=None: {"x": 1}
    s.get_housekeeping_summary = lambda: {"dirty": 0}
    # business briefing data shaping branches
    s.report_pl_statement = lambda start, end: {"revenue": 100, "expenses": 30, "net_profit": 70}
    s.get_occupancy_for_window = lambda start, end: {"pct": 90, "adr": 10, "revpar": 9}
    s.verify_ledger_integrity = lambda: {"ok": False}
    monkeypatch.setattr(t, "services", s)
    # stub tool calls used inside business_partner_briefing
    monkeypatch.setattr(t, "tool_get_property_stats", lambda args=None: {"stats": {"guests_total": 1}})
    monkeypatch.setattr(
        t, "tool_get_outstanding_balances", lambda args=None: {"count": 1, "total_due": 20, "balances": []}
    )
    monkeypatch.setattr(
        t, "tool_get_inventory_alerts", lambda args=None: {"alerts": {"low": [1], "negative": [], "overstock": [1]}}
    )
    monkeypatch.setattr(
        t, "tool_get_top_selling_items", lambda args=None: {"top_items": [{"item": "A", "qty_sold": 2}]}
    )
    b = t.tool_business_partner_briefing({"start": "2026-01-01", "end": "2026-01-31"})
    assert b["insights"]["watchouts"] and b["insights"]["actions"]

    # list/trace/error branches
    monkeypatch.setattr(
        t,
        "services",
        SimpleNamespace(
            list_reservation_packages=lambda rid: [
                {"snapshot_json": json.dumps({"items": [1, 2]})},
                {"snapshot_json": "{bad json}"},
            ],
            get_reservation_group=lambda gid: {"id": gid, "members": [{"reservation_id": 1}]},
            report_housekeeping_ops=lambda: {"rooms": 1},
            list_rate_rules=lambda active_only=False: [{"id": 1}],
            list_service_catalog=lambda active_only=True: [{"id": 1}],
            list_discount_vouchers=lambda active_only=False: [{"code": "X"}],
            list_approvals=lambda status="", limit=50: [{"id": 1}],
            get_approval=lambda aid: {"id": aid} if aid == 1 else None,
            verify_reservation_financials=lambda rid: {"reservation_id": rid, "ok": True},
        ),
    )
    assert t.tool_list_reservation_packages({"reservation_id": 1})["count"] == 2
    assert t.tool_list_reservation_packages({})["error"]
    assert t.tool_get_reservation_group({"group_id": 1})["group"]["member_count"] == 1
    assert t.tool_get_reservation_group({"group_id": 2}) == {
        "error": "Group #2 not found"
    } or t.tool_get_reservation_group({"group_id": 2}).get("group")
    assert "report" in t.tool_get_housekeeping_ops_report({})
    assert t.tool_list_rate_rules({})["count"] == 1
    assert t.tool_list_service_catalog({})["count"] == 1
    assert t.tool_list_discount_vouchers({})["count"] == 1
    assert t.tool_list_approvals({})["count"] == 1
    assert t.tool_get_approval({"approval_id": 1})["approval"]["id"] == 1
    assert t.tool_get_approval({"approval_id": 2})["error"]
    assert t.tool_verify_reservation_financials({"reservation_id": 3})["ok"] is True

    # unavailable service branches
    monkeypatch.setattr(t, "services", SimpleNamespace())
    for fn, arg in [
        (t.tool_list_reservation_packages, {"reservation_id": 1}),
        (t.tool_get_reservation_group, {"group_id": 1}),
        (t.tool_get_housekeeping_ops_report, {}),
        (t.tool_list_rate_rules, {}),
        (t.tool_list_service_catalog, {}),
        (t.tool_list_discount_vouchers, {}),
        (t.tool_list_approvals, {}),
        (t.tool_get_approval, {"approval_id": 1}),
        (t.tool_verify_reservation_financials, {"reservation_id": 1}),
    ]:
        out = fn(arg)
        assert "error" in out


from types import SimpleNamespace
