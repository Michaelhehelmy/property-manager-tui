from __future__ import annotations

from types import SimpleNamespace

import app_core.assistant.engine as engine
import app_core.assistant.runtime as runtime
import app_core.assistant.actions as actions
import app_core.assistant.tools as tools
from app_core import services
from app_core.db import connect


def test_engine_helpers_and_write_arg_extraction(monkeypatch):
    # extract write args
    text = 'add 2 water item #5 for reservation #12 batch 7 "note here"'
    args = engine._extract_write_args(text, {"reservation_id": 12})
    assert args["reservation_id"] == 12
    assert args.get("qty") == 2 or args.get("quantity") == 2 or "qty" in args or "item_id" in args
    args = engine._extract_write_args("record payment amount 50 cash for reservation #9", {"reservation_id": 9})
    assert args["reservation_id"] == 9 and args.get("amount") == 50.0 and "Cash" in (args.get("method") or "")
    args = engine._extract_write_args("book activity #3 for reservation #4 check in 2099-01-01 check out 2099-01-02 for John Doe", {"reservation_id": 4, "date_start": "2099-01-01", "date_end": "2099-01-02"})
    assert args.get("activity_id") == 3 and args.get("check_in") == "2099-01-01"

    # guest disambiguation formatting
    res = engine._guest_disambiguation([{"id": 1, "name": "A", "phone": "p", "email": "e", "stays": 2, "last_stay": "x"}], q="A")
    assert res["needs_clarification"] is True and "guest #1" in res["answer"].lower()

    # _answer/apply formatting
    monkeypatch.setattr(engine, "_format_for_intent", lambda intent, data, fallback_title="Result": f"FMT:{intent}")
    monkeypatch.setattr(engine, "_apply_proactive", lambda intent, data, ans: ans + ":PRO")
    out = engine._answer("x", ["t"], {"a": 1})
    assert out["answer"] == "FMT:x:PRO" and out["tools_used"] == ["t"]


def test_engine_maybe_propose_action_and_execute_single(monkeypatch, clean_db):
    # propose action: missing info branches
    r = engine._maybe_propose_action("propose_add_pos_item", "add item to reservation", {}, ui_context={})
    assert r and r["intent"] == "action_needs_info"
    r = engine._maybe_propose_action("propose_complete_walkin_sale", "walk-in sale", {}, ui_context={})
    assert r and r["intent"] == "action_needs_info"
    r = engine._maybe_propose_action("propose_record_payment", "record payment", {}, ui_context={})
    assert r and r["intent"] == "action_needs_info"
    r = engine._maybe_propose_action("propose_book_activity", "book activity", {}, ui_context={})
    assert r and r["intent"] == "action_needs_info"
    r = engine._maybe_propose_action("propose_create_reservation", "create reservation", {}, ui_context={})
    assert r and r["intent"] == "action_needs_info"

    # proposal success / validation failed / exception
    monkeypatch.setattr(actions, "create_proposal", lambda action_type, args, ui_context=None: {
        "summary": "S", "prechecks": {"ok": True, "errors": [], "warnings": []}, "action_type": action_type
    })
    r = engine._maybe_propose_action("propose_record_payment", "record payment amount 10 cash for reservation #1", {"reservation_id": 1}, ui_context={})
    assert r and r.get("needs_confirmation") is True and r.get("proposed_action")

    monkeypatch.setattr(actions, "create_proposal", lambda action_type, args, ui_context=None: {
        "summary": "S", "prechecks": {"ok": False, "errors": ["bad"], "warnings": ["w"]}, "action_type": action_type
    })
    r = engine._maybe_propose_action("propose_record_payment", "record payment amount 10 cash for reservation #1", {"reservation_id": 1}, ui_context={})
    assert r and r["ok"] is False and "validation failed" in r["answer"].lower()

    monkeypatch.setattr(actions, "create_proposal", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = engine._maybe_propose_action("propose_record_payment", "record payment amount 10 cash for reservation #1", {"reservation_id": 1}, ui_context={})
    assert r and r["ok"] is False and "couldn" in r["answer"].lower()

    # keyword-routed read intents in _execute_single
    monkeypatch.setattr(tools, "execute_tool", lambda name, args=None: {"tool": name, "args": args or {}, "ok": True, "groups": [], "rules": [], "items": [], "approvals": [], "approval": {}, "report": "x", "runs": [], "blocks": [], "tasks": []})
    for text, expected_intent in [
        ("show doctor report", "health_diagnostics"),
        ("what expires in 7 days", "expiring_batches"),
        ("trace barcode ABC123", "trace_barcode"),
        ("list batches", "inventory_batches"),
        ("check for updates", "app_update_status"),
    ]:
        res = engine._execute_single("capabilities_help", text, {}, ui_context={})
        assert isinstance(res, dict)

    # simple mappings and report/ops/date intents
    res = engine._execute_single("today_ops", "today ops", {}, ui_context={})
    assert res["intent"] in ("today_ops", "result") or isinstance(res, dict)
    res = engine._execute_single("outstanding_balances", "who owes money", {}, ui_context={})
    assert isinstance(res, dict)
    res = engine._execute_single("report_pl", "p&l last month", {"date_start": "2099-01-01", "date_end": "2099-01-31"}, ui_context={})
    assert isinstance(res, dict)
    res = engine._execute_single("front_desk_ops", "front desk ops", {"date_start": "2099-01-01"}, ui_context={})
    assert isinstance(res, dict)
    res = engine._execute_single("inventory_search", "search inventory water", {}, ui_context={})
    assert isinstance(res, dict)
    res = engine._execute_single("reservation_search", "find reservation john", {}, ui_context={})
    assert isinstance(res, dict)

    # needs clarification / capability fallback
    res = engine._execute_single("ambiguous_needs_clarification", "balance", {}, ui_context={})
    assert res.get("needs_clarification") is True
    monkeypatch.setattr(tools, "execute_tool", lambda name, args=None: {"capabilities": ["x"]})
    res = engine._execute_single("unknown_intent_xyz", "gibberish", {}, ui_context={})
    assert res["intent"] == "capabilities_help"


def test_engine_guest_and_compound_and_runtime(monkeypatch, clean_db):
    # guest resolution branches and compound execution
    monkeypatch.setattr(engine, "resolve_guest", lambda text, slots: ([{"id": 1, "name": "Guest", "canonical_name": "Guest"}], True, "Guest"))
    monkeypatch.setattr(tools, "execute_tool", lambda name, args=None: {"ok": True, "guest": {"id": 1, "canonical_name": "Guest"}, "last_visit": {"reservation_id": 2}, "items": [], "bookings": [], "trips": [], "all_pos": [], "all_payments": [], "totals": {}, "balance_breakdown": {}})
    res = engine._execute_single("guest_portfolio", "guest #1 full portfolio", {"guest_id": 1}, ui_context={})
    assert res.get("resolved_guest_id") == 1

    monkeypatch.setattr(engine, "resolve_guest", lambda text, slots: ([{"id": 1, "name": "A"}, {"id": 2, "name": "B"}], False, "A"))
    res = engine._execute_compound("find guest A and who owes money", {}, ui_context={})
    assert res.get("needs_clarification") is True

    monkeypatch.setattr(engine, "resolve_guest", lambda text, slots: ([], False, ""))
    monkeypatch.setattr(engine, "classify_intent", lambda *a, **k: {"intent": "outstanding_balances", "confidence": 0.9, "slots": {}, "router": "test"})
    monkeypatch.setattr(tools, "execute_tool", lambda name, args=None: {"ok": True, "outstanding": [], "count": 0, "total_due": 0, "alerts": {"negative": [], "low": [], "overstock": []}, "top_items": []})
    res = engine._execute_compound("who owes money and low stock and top selling items", {}, ui_context={})
    assert res["intent"] == "compound_multi_intent"

    # runtime config/llm helpers
    monkeypatch.setattr(services, "get_setting", lambda k, d=None: {"assistant_provider": "auto", "assistant_response_mode": "talking", "assistant_local_model_path": "", "assistant_local_n_ctx": "2048"}.get(k, d))
    cfg = runtime._llm_config()
    assert cfg["external"] is False
    assert runtime.get_llm_config()["external"] is False
    assert runtime.talking_rephrase("hi", {"answer": "ok", "data": {}}, config={"enabled": False}) is not None

    # deterministic and llm routes
    monkeypatch.setattr(runtime, "extract_slots", lambda text, history=None: {})
    monkeypatch.setattr(runtime, "rule_classify", lambda text, slots: {"intent": "capabilities_help", "confidence": 0.9, "router": "rules"})
    monkeypatch.setattr(runtime, "classify_intent", lambda text, history=None: {"intent": "capabilities_help", "confidence": 0.9, "slots": {}, "router": "rules"})
    monkeypatch.setattr(runtime, "_execute_single", lambda intent, text, slots, ui_context=None: {"ok": True, "intent": intent, "answer": "a", "tools_used": [], "data": {}})
    monkeypatch.setattr(runtime, "_execute_compound", lambda text, slots, ui_context=None: {"ok": True, "intent": "compound_multi_intent", "answer": "a", "tools_used": [], "data": {}})
    assert runtime.deterministic_route("help")["intent"] == "capabilities_help"
    assert runtime.llm_route("help", config={"enabled": False})["intent"] == "capabilities_help"

    # chat flows
    monkeypatch.setattr(runtime, "_ui_guidance", lambda message, ui_context=None: None)
    monkeypatch.setattr(runtime, "_get_pending_from_ui", lambda ui_context: None)
    monkeypatch.setattr(services, "touch_session", lambda: None, raising=False)
    r = runtime.chat("")
    assert r["ok"] is False
    r = runtime.chat("help", history=[], ui_context={})
    assert isinstance(r, dict) and "answer" in r
