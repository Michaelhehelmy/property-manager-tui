from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app_core.assistant import action_validation as av
from app_core.assistant import actions
from app_core.assistant import common
from app_core.assistant import engine
from app_core.assistant import feedback
from app_core.assistant import formatting
from app_core.assistant import nlu
from app_core.assistant import runtime


# ---------------- common ----------------

def test_common_resolve_guest_compat_and_sim():
    # line 72 in common is in _sim body (return SequenceMatcher ratio) - ensure called
    assert common._sim("abc", "abc") == 1.0
    # _resolve_guest_from_text compat wrapper
    guests, explicit = common._resolve_guest_from_text("find guest")
    assert isinstance(guests, list)
    assert explicit is None or isinstance(explicit, int)
    # history context user branch no-op (178,180)
    ctx = common._context_from_history([
        {"role": "user", "content": "hello"},
        {"role": "assistant", "slots": {"guest_id": 5}, "intent": "guest_profile"},
    ])
    assert ctx.get("guest_id") == 5


# ---------------- nlu ----------------

def test_nlu_rule_refinements_branches():
    # occupancy summary branch (130)
    r = nlu.rule_classify("how occupied were we", {"report_kind": "occupancy"})
    assert r["intent"] in {"occupancy_summary", "report_occupancy", "compound_multi_intent", "ambiguous_needs_clarification"}

    # guest last visit refinement from profile cues (134)
    r = nlu.rule_classify("when was the last time guest #1 stayed profile history", {"guest_id": 1})
    assert isinstance(r["intent"], str)

    # reservation group detail boost (137)
    r = nlu.rule_classify("show group #3 members and folio details", {"group_id": 3})
    # should prefer reservation_group_detail or at least exercise branch
    assert r["intent"] in {"reservation_group_detail", "reservation_groups", "compound_multi_intent"}

    # packages applied on reservation boost (139)
    r = nlu.rule_classify("what packages are on reservation #7", {"reservation_id": 7})
    assert r["intent"] in {"reservation_packages_applied", "packages_list", "compound_multi_intent"}


# ---------------- runtime ----------------

def test_runtime_talking_rephrase_data_str_exception_and_chat_paths(monkeypatch):
    # lines 89-90: json.dumps of data fails -> str(data)
    monkeypatch.setattr(runtime, "_llm_config", lambda: {"enabled": True, "local_available": True, "response_mode": "talking"})
    # exercise data serialization exception fallback (lines 89-90)
    seen = {"boom": 0}
    orig_dumps = runtime.json.dumps
    def boom(*a, **k):
        seen["boom"] += 1
        raise TypeError("boom")
    monkeypatch.setattr(runtime.json, "dumps", boom)
    try:
        # patch both local binding and package-level getter target
        monkeypatch.setattr(runtime, "_local_llm_generate", lambda *a, **k: "polished")
        try:
            import app_core.assistant as _pkg
            monkeypatch.setattr(_pkg, "_local_llm_generate", lambda *a, **k: "polished", raising=False)
        except Exception:
            pass
        out = runtime.talking_rephrase("q", {"answer": "a", "data": {"x": 1}}, config={"enabled": True})
        assert out in ("polished", "a")  # polished if local gen path used, else fallback
        assert seen["boom"] >= 1
    finally:
        monkeypatch.setattr(runtime.json, "dumps", orig_dumps)

    # chat: ui_context None, resolved_guest_id propagation exception path (385-386)
    monkeypatch.setattr(runtime, "_ui_guidance", lambda *a, **k: None)
    monkeypatch.setattr(runtime, "_llm_config", lambda: {"enabled": False, "response_mode": "core", "local_available": False})
    monkeypatch.setattr(runtime, "extract_slots", lambda *a, **k: {})
    monkeypatch.setattr(runtime, "classify_intent", lambda *a, **k: {"intent": "guest_profile", "confidence": 0.9, "slots": {}})
    # return core with resolved_guest_id but slots as non-dict-ish? make setitem fail
    class BadSlots(dict):
        def setdefault(self, k, v=None):
            raise RuntimeError("bad")
    core = {"ok": True, "intent": "guest_profile", "answer": "ok", "tools_used": [], "data": {}, "resolved_guest_id": 5, "slots": BadSlots()}
    monkeypatch.setattr(runtime, "_execute_single", lambda *a, **k: core)
    out = runtime.chat("guest", history=None, ui_context=None)
    assert out.get("resolved_guest_id") == 5

    # chat: deterministic rephrase polishing path when mode talking and not clarification -> sets answer/source/model
    monkeypatch.setattr(runtime, "_llm_config", lambda: {"enabled": False, "response_mode": "talking", "local_available": False})
    monkeypatch.setattr(runtime, "extract_slots", lambda *a, **k: {})
    monkeypatch.setattr(runtime, "classify_intent", lambda *a, **k: {"intent": "property_stats", "confidence": 0.9, "slots": {}})
    monkeypatch.setattr(runtime, "_execute_single", lambda *a, **k: {"ok": True, "intent": "property_stats", "answer": "Core", "tools_used": [], "data": {}})
    try:
        import app_core.assistant as _pkgmod
        monkeypatch.setattr(_pkgmod, "talking_rephrase", lambda *a, **k: "Friendly", raising=False)
    except Exception:
        pass
    monkeypatch.setattr(runtime, "talking_rephrase", lambda *a, **k: "Friendly")
    out = runtime.chat("stats", ui_context={})
    assert out["answer"] == "Friendly"
    assert out["talking_model"] == "deterministic"
    # line 377: else branch (not polished / other modes) can be hit when mode core
    try:
        import app_core.assistant as _pkgmod
        monkeypatch.setattr(_pkgmod, "_llm_config", lambda: {"enabled": False, "response_mode": "core", "local_available": False}, raising=False)
    except Exception:
        pass
    monkeypatch.setattr(runtime, "_llm_config", lambda: {"enabled": False, "response_mode": "core", "local_available": False})
    out = runtime.chat("stats", ui_context={})
    assert out["source"] in ("core_deterministic", out.get("core_source"), "local_grounded_core")


# ---------------- feedback ----------------

def test_feedback_loader_and_ml_branches(monkeypatch, tmp_path):
    # sklearn path success (123-125)
    model_path = tmp_path / "m.joblib"
    model_path.write_bytes(b"x")
    fake_joblib = types.ModuleType("joblib")
    fake_joblib.load = lambda p: {"ok": True}
    monkeypatch.setitem(sys.modules, "joblib", fake_joblib)
    monkeypatch.setattr(feedback, "_SKLEARN_CACHE", None)
    monkeypatch.setenv("ASSISTANT_SKLEARN_MODEL", str(model_path))
    assert feedback._load_sklearn_router() == {"ok": True}

    # missing path already cached False behavior separate; ensure missing -> (124)
    monkeypatch.setattr(feedback, "_SKLEARN_CACHE", None)
    monkeypatch.setenv("ASSISTANT_SKLEARN_MODEL", str(tmp_path / "nope.joblib"))
    assert feedback._load_sklearn_router() is None

    # setfit success (133-135)
    setfit_dir = tmp_path / "setfit"
    setfit_dir.mkdir()
    fake_setfit_mod = types.ModuleType("setfit")
    class FakeModel:
        @classmethod
        def from_pretrained(cls, p):
            return {"model": p}
    fake_setfit_mod.SetFitModel = FakeModel
    monkeypatch.setitem(sys.modules, "setfit", fake_setfit_mod)
    monkeypatch.setattr(feedback, "_SETFIT_CACHE", None)
    monkeypatch.setenv("ASSISTANT_SETFIT_MODEL_DIR", str(setfit_dir))
    assert feedback._load_setfit_router() == {"model": str(setfit_dir)}

    # setfit missing -> 134
    monkeypatch.setattr(feedback, "_SETFIT_CACHE", None)
    monkeypatch.setenv("ASSISTANT_SETFIT_MODEL_DIR", str(tmp_path / "missing_setfit"))
    assert feedback._load_setfit_router() is None

    # predict_intent_ml setfit proba list/tolist/conf assignment (148)
    class Probs:
        def tolist(self):
            return [0.1, 0.9]
    class SF:
        def predict(self, texts): return ["capabilities_help"]
        def predict_proba(self, texts): return [Probs()]
    monkeypatch.setenv("ASSISTANT_NLU", "setfit")
    monkeypatch.setattr(feedback, "_load_setfit_router", lambda: SF())
    res = feedback._predict_intent_ml("hi")
    assert res and res["intent"] == "capabilities_help" and res["confidence"] == 0.9

    # sklearn decision_function confidence branch (168)
    class Vec:
        def transform(self, t): return t
    class Clf:
        def predict(self, X): return ["guest_search"]
        def predict_proba(self, X): raise RuntimeError("no")
        def decision_function(self, X): return [[0.0, 1.0]]
    monkeypatch.setenv("ASSISTANT_NLU", "sklearn")
    monkeypatch.setattr(feedback, "_load_sklearn_router", lambda: {"vectorizer": Vec(), "classifier": Clf()})
    res = feedback._predict_intent_ml("find guest")
    assert res and res["intent"] == "guest_search" and 0.0 < res["confidence"] <= 1.0


# ---------------- engine ----------------

def test_engine_extract_amount_parse_exception_and_more(monkeypatch):
    # line 33: amount parse exception in _extract_write_args
    args = engine._extract_write_args("record payment amount notanumber for reservation #1 cash", {"reservation_id": 1})
    assert "amount" not in args

    # line 141/223/268/271/398/416-439 branches in _execute_single
    calls = []
    def fake_tool(name, args=None):
        calls.append((name, args or {}))
        return {"ok": True, "name": name}
    monkeypatch.setattr(engine, "execute_tool", fake_tool)

    # none/no proposal path (141)
    assert engine._maybe_propose_action("greeting_smalltalk", "hello", {}) is None

    # hi (else greeting) 223
    out = engine._execute_single("greeting_smalltalk", "hello there", {})
    assert out["ok"] is True and "property manager assistant" in out["answer"].lower()

    # unsupported_or_unsafe answer/return 268,271
    out = engine._execute_single("unsupported_or_unsafe", "delete everything", {})
    assert out["ok"] is False and out["intent"] == "unsupported_or_unsafe"
    assert "read-only" in out["answer"].lower()

    # expiring batches via intent with invalid days slots path 416-423
    out = engine._execute_single("expiring_batches", "anything", {"days": "bad"})
    assert out["intent"] == "expiring_batches"
    assert calls[-1][0] == "get_expiring_batches" and calls[-1][1]["within_days"] is None

    # inventory_batches/list tool via intent 398? (simple mapping returns _answer)
    out = engine._execute_single("inventory_batches", "batches", {"item_id": 7})
    assert out["intent"] == "inventory_batches"
    assert calls[-1] == ("list_inventory_batches", {"item_id": 7})

    # trace_barcode intent with code extraction fallbacks 431-439
    out = engine._execute_single("trace_barcode", "trace: ABC-123", {})
    assert out["intent"] == "trace_barcode"
    assert calls[-1][1]["code"] == "ABC-123"
    out = engine._execute_single("barcode_trace", "trace LOTZ99 now", {})
    assert calls[-1][1]["code"] == "LOTZ99"
    out = engine._execute_single("trace_barcode", "trace zxy", {})
    assert calls[-1][1]["code"] in ("zxy", "trace")

    # keyword trace fallback token extraction 250-252 in early keyword block
    out = engine._execute_single("capabilities_help", "lookup barcode", {})
    assert calls[-1][0] == "trace_barcode"
    assert calls[-1][1]["code"] == "barcode"

    # expiring keyword parse exception 238-240
    out = engine._execute_single("capabilities_help", "what expires in xx day", {})
    assert ("get_expiring_batches", {"within_days": None, "include_expired": True}) in calls

    # app update status keyword branch
    out = engine._execute_single("capabilities_help", "check for updates", {})
    assert any(c[0] == "get_app_update_status" for c in calls)

    # reservation financial integrity parse error fallback 304
    out = engine._execute_single("reservation_financial_integrity", "check reservation integrity", {})
    assert out.get("intent") == "reservation_financial_integrity"

    # approval detail parse error fallback 325
    out = engine._execute_single("approval_detail", "show approval", {})
    assert out.get("intent") == "approval_detail"


# ---------------- actions ----------------

def test_actions_branches(monkeypatch):
    # _map_propose_to_execute execute passthrough and unknown (135-137)
    assert actions._map_propose_to_execute("execute_record_payment") == "execute_record_payment"
    assert actions._map_propose_to_execute("unknown_action") is None

    # _exec_add_pos_item: missing args, batch_id in affected, pos_ids (224,228,232,237)
    with pytest.raises(ValueError):
        actions._exec_add_pos_item({}, {})
    monkeypatch.setattr(actions.services, "add_pos_item", lambda *a, **k: {"pos_id": 11, "pos_ids": [11, 12]})
    out = actions._exec_add_pos_item({"reservation_id": 1, "item_id": 2, "desc": "X", "qty": 1, "price": 5.0, "batch_id": 9}, {"item": {"id": 2, "item_name": "X"}})
    assert out["affected_ids"]["batch_id"] == 9 and out["affected_ids"]["pos_ids"] == [11, 12]

    # _exec_complete_walkin_sale validation (247,249,261)
    with pytest.raises(ValueError):
        actions._exec_complete_walkin_sale({"items": [], "method": "Cash"}, {})
    with pytest.raises(ValueError):
        actions._exec_complete_walkin_sale({"items": [{"inv_id": 1, "desc": "a", "qty": 1, "price": 1}]}, {})
    with pytest.raises(ValueError):
        actions._exec_complete_walkin_sale({"items": [{"inv_id": 1, "desc": "a", "qty": 1}], "method": "Cash"}, {})

    # _exec_record_payment validation (288)
    with pytest.raises(ValueError):
        actions._exec_record_payment({"reservation_id": 1, "amount": 5}, {})

    # _exec_book_activity validation and pos_ids (303,310)
    with pytest.raises(ValueError):
        actions._exec_book_activity({"reservation_id": 1}, {})
    monkeypatch.setattr(actions.services, "book_activity", lambda *a, **k: {"pos_id": 3, "pos_ids": [3]})
    out = actions._exec_book_activity({"reservation_id": 1, "activity_id": 2}, {"activity": {"id": 2}})
    assert out["affected_ids"]["pos_ids"] == [3]

    # _exec_create_reservation fallbacks/validation/affected guest_id (318,328,330,336,338,355)
    with pytest.raises(ValueError):
        actions._exec_create_reservation({}, {})
    with pytest.raises(ValueError):
        actions._exec_create_reservation({"guest_name": "A"}, {})
    monkeypatch.setattr(actions.services, "create_reservation", lambda **k: 77)
    out = actions._exec_create_reservation(
        {"guest_id": 4, "room_id": 2, "check_in": "2026-01-01", "check_out": "2026-01-02", "amount": 12.0},
        {"guest_resolution": {"guest": {"id": 4, "name": "A"}}, "room": {"id": 2, "base_price": 12.0}},
    )
    assert out["affected_ids"]["reservation_id"] == 77 and out["affected_ids"]["guest_id"] == 4

    # execute_pending_action invalid pending (405)
    assert actions.execute_pending_action(None)["status"] == "invalid"


# ---------------- action_validation ----------------

class RowLike:
    def __init__(self, d):
        self._d = d
    def keys(self):
        return self._d.keys()
    def __getitem__(self, k):
        return self._d[k]

def test_action_validation_branches(monkeypatch):
    # _row_to_dict row.keys path (70)
    assert av._row_to_dict(RowLike({"a": 1})) == {"a": 1}

    # _is_expired parse exception -> False (241-242) and bad format
    assert av._is_expired("not-a-date") is False

    # resolve_inventory_item: code/batch hint path, lookup exception fallback, name not found, inactive item (279-291,306)
    monkeypatch.setattr(av.services, "resolve_barcode", lambda code: {"type": "batch", "item": {"id": 1, "item_name": "I", "active": 1}, "batch": {"id": 9}})
    out = av.resolve_inventory_item({"barcode": "B1"})
    assert out["resolved"].get("resolved_batch_hint", {}).get("id") == 9

    def bad_search(*a, **k): raise RuntimeError("x")
    def fallback_items(*a, **k): return [{"id": 2, "item_name": "Tea", "active": 1}]
    monkeypatch.setattr(av.services, "search_inventory", bad_search)
    monkeypatch.setattr(av.services, "get_inventory_items", fallback_items)
    out = av.resolve_inventory_item({"item_name": "tea"})
    assert out["resolved"]["item"]["id"] == 2

    monkeypatch.setattr(av.services, "search_inventory", lambda *a, **k: [])
    monkeypatch.setattr(av.services, "get_inventory_items", lambda *a, **k: [])
    out = av.resolve_inventory_item({"item_name": "none"})
    assert not out["ok"]

    monkeypatch.setattr(av.services, "get_inventory_item", lambda iid: {"id": iid, "item_name": "I", "active": 0})
    out = av.resolve_inventory_item({"item_id": 1})
    assert not out["ok"]

    # resolve_batch_or_lot early return (313)
    assert av.resolve_batch_or_lot(None, {})["ok"] is True

    # check_availability exception (489-492)
    monkeypatch.setattr(av.services, "check_room_availability", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = av.check_availability({"room_id": 1, "check_in": "2026-01-01", "check_out": "2026-01-02"}, room_id=1)
    assert not out["ok"]

    # preview_add_pos_item various branches (558,559,577,579,580,588,589,603,604)
    monkeypatch.setattr(av, "resolve_reservation", lambda *a, **k: {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {"reservation": {"id": 1}}, "preview": {}})
    monkeypatch.setattr(av, "resolve_inventory_item", lambda *a, **k: {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {"item": {"id": 5, "item_name": "Item", "selling_price": 10.0, "stock_qty": 1, "active": 1}}, "preview": {}})
    monkeypatch.setattr(av, "resolve_batch_or_lot", lambda *a, **k: {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {}, "preview": {}})
    monkeypatch.setattr(av.services, "get_setting", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    out = av.preview_add_pos_item({"reservation_id": 1, "item_id": 5, "qty": 2, "desc": "Item"})
    assert not out["ok"]  # insufficient stock
    monkeypatch.setattr(av.services, "get_setting", lambda *a, **k: "true")
    monkeypatch.setattr(av.services, "calculate_tax", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("tax")))
    out = av.preview_add_pos_item({"reservation_id": 1, "item_id": 5, "qty": 1, "price": "bad", "desc": "Item"})
    assert "estimated_subtotal" in out["preview"]
    # valid price path for subtotal/tax body
    monkeypatch.setattr(av.services, "calculate_tax", lambda net, z: {"net": net, "service": 0, "vat": 0, "city_tax": 0, "gross": net})
    out = av.preview_add_pos_item({"reservation_id": 1, "item_id": 5, "qty": 1, "price": 10.0, "desc": "Item"})
    assert out["preview"]["estimated_subtotal"] == 10.0

    # preview_complete_walkin_sale branches (632-643,668-676,705)
    monkeypatch.setattr(av, "resolve_inventory_item", lambda *a, **k: {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {"item": {"id": 1, "item_name": "X", "selling_price": 3.0}}, "preview": {}})
    monkeypatch.setattr(av, "resolve_batch_or_lot", lambda *a, **k: {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {"batch": {"id": 2, "lot_code": "L"}}, "preview": {}})
    out = av.preview_complete_walkin_sale({"items": [], "method": ""})
    assert not out["ok"]
    out = av.preview_complete_walkin_sale({"items": [{"inv_id": 1, "qty": 0, "price": None}], "method": "Weird"})
    assert not out["ok"] and any("uncommon" in w for w in out["warnings"])

    # preview_record_payment branches (730,731,735,736,746-755)
    monkeypatch.setattr(av, "resolve_reservation", lambda *a, **k: {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {"reservation": {"id": 1}}, "preview": {}})
    monkeypatch.setattr(av.services, "get_balance_breakdown", lambda rid: {"balance_due": 5.0})
    out = av.preview_record_payment({"reservation_id": 1, "amount": 0, "method": ""})
    assert not out["ok"]
    monkeypatch.setattr(av.services, "get_balance_breakdown", lambda rid: 5.0)
    out = av.preview_record_payment({"reservation_id": 1, "amount": 10, "method": "Cash"})
    assert any("exceeds" in w for w in out["warnings"])
    monkeypatch.setattr(av.services, "get_balance_breakdown", lambda rid: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(av.services, "calculate_guest_balance", lambda rid: (_ for _ in ()).throw(RuntimeError("y")))
    out = av.preview_record_payment({"reservation_id": 1, "amount": 1, "method": "Cash"})
    assert out["ok"] is True

    # preview_book_activity quantity invalid (784,785)
    monkeypatch.setattr(av, "resolve_reservation", lambda *a, **k: {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {}, "preview": {}})
    monkeypatch.setattr(av, "resolve_activity", lambda *a, **k: {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {"activity": {"id": 1, "name": "A", "unit_price": 10}}, "preview": {}})
    out = av.preview_book_activity({"reservation_id": 1, "activity_id": 1, "quantity": -1})
    assert not out["ok"] and any("quantity must be positive" in e for e in out.get("errors", []))

    # preview_create_reservation branches (820,821,830,831,837-841,843,844,852,854,856)
    monkeypatch.setattr(av, "resolve_guest", lambda *a, **k: {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {"guest": {"id": 5}}, "preview": {}})
    monkeypatch.setattr(av, "resolve_room", lambda *a, **k: {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {"room": {"id": 2, "room_number": "101", "base_price": 55.0}}, "preview": {}})
    monkeypatch.setattr(av, "resolve_rate_plan", lambda *a, **k: {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {}, "preview": {}})
    monkeypatch.setattr(av, "check_availability", lambda *a, **k: {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {}, "preview": {"nights": 2}})
    out = av.preview_create_reservation({"guest_name": "A", "source": "Direct", "check_in": "2026-01-01", "check_out": "2026-01-03"})
    assert out["ok"] is True and out["preview"]["room_id"] == 2 and out["preview"]["rate"] == 55.0
    out = av.preview_create_reservation({"guest_name": "", "guest_id": None, "room_id": 1, "check_in": "bad", "check_out": "bad"})
    assert not out["ok"]
    # rate missing with no room base
    monkeypatch.setattr(av, "resolve_room", lambda *a, **k: {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {"room": {"id": 2}}, "preview": {}})
    monkeypatch.setattr(av, "check_availability", lambda *a, **k: {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {}, "preview": {"nights": 1}})
    out = av.preview_create_reservation({"guest_name": "A", "room_id": 2, "check_in": "2026-01-01", "check_out": "2026-01-02"})
    assert not out["ok"]

    # build_prechecks ui_context merge (925)
    monkeypatch.setattr(av, "preview_add_pos_item", lambda args: {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {}, "preview": {}})
    out = av.build_prechecks("preview_add_pos_item", {"reservation_id": 1}, ui_context={"screen": "pos"})
    assert out["resolved"]["ui_context"]["screen"] == "pos"

    # normalize_action_args guest_id alias branch (939)
    n = av.normalize_action_args("propose_create_reservation", {"id": 9})
    assert n["guest_id"] == 9


# ---------------- formatting ----------------

def test_formatting_missing_branches():
    # _format_data exception (17,18) and _friendly_open (27)
    orig = formatting.json.dumps
    try:
        formatting.json.dumps = lambda *a, **k: (_ for _ in ()).throw(TypeError("x"))
        assert formatting._format_data("T", {"a": 1}).startswith("T\n")
    finally:
        formatting.json.dumps = orig
    assert formatting._friendly_open("") .startswith("Sure")

    # _fmt_today_ops detailed counts/list rows (98-100)
    txt = formatting._fmt_today_ops({
        "date": "2026-01-01",
        "ops": {"arrivals": [{"id": 1, "guest_name": "A", "room_number": "101"}], "in_house": [], "departures": []},
        "front_desk": {"arrivals_today": 1, "departures_today": 0, "in_house": 0},
        "housekeeping": {},
    })
    assert "Arrivals: **1**" in txt and "#1 A" in txt

    # _fmt_report scalar other fields (159)
    txt = formatting._fmt_report({"kind": "pl", "start": "s", "end": "e", "report": {"misc_note": "ok"}})
    assert "Misc Note" in txt

    # _fmt_compare fallback text (227)
    txt = formatting._fmt_compare({"current": {"pl": "x", "occupancy": {}, "cash_flow": None}, "previous": {"pl": "y", "occupancy": {}, "cash_flow": None}})
    assert "Detailed period data" in txt

    # _fmt_outstanding more-than-15 (240)
    txt = formatting._fmt_outstanding({"count": 16, "total_due": 100, "outstanding": [{"reservation_id": i, "guest_name": "g", "room": "1", "status": "Checked In", "check_in": "a", "check_out": "b", "balance_due": 1} for i in range(16)]})
    assert "and 1 more" in txt

    # _fmt_integrity branches (412,420,421,423-426)
    txt = formatting._fmt_integrity({"backup_restore": {}})
    assert "Doctor/health diagnostics" in txt or "read-only" in txt.lower()
    txt = formatting._fmt_integrity({"ok": False, "issues": "bad"})
    assert "issues found" in txt.lower()
    txt = formatting._fmt_integrity({"ok": False, "foo": [{"x": 1}]})
    assert "foo" in txt

    # guest portfolio top items (610)
    txt = formatting._fmt_guest_portfolio({"guest": {"id": 1, "name": "A"}, "stats": {}, "top_items_overall": [{"item": "Tea", "gross": 10}]})
    assert "Top items overall" in txt

    # decision support string and scalar branches (632,633,637,638,640)
    txt = formatting._fmt_decision_support({"start": "s", "end": "e", "decision_support": {"summary": "All good", "custom_metric": 5, "pct_metric": 10}})
    assert "Summary" in txt and "Custom Metric" in txt and "Pct Metric" in txt
    txt = formatting._fmt_decision_support({"start": "s", "end": "e", "decision_support": "line1\nline2"})
    assert "line1" in txt

    # proactive suggestions specific branches and exception logs (665,686,688,690,692,695,696,711,714,715)
    s = formatting._proactive_suggestions("compare_periods", {}, "")
    assert s
    s = formatting._proactive_suggestions("list_activities", {}, "")
    assert s
    s = formatting._proactive_suggestions("activity_stats", {}, "")
    assert s
    s = formatting._proactive_suggestions("business_partner_briefing", {}, "")
    assert s
    s = formatting._proactive_suggestions("housekeeping_summary", {}, "")
    assert s
    s = formatting._proactive_suggestions("ledger_integrity", {"ok": True}, "")
    assert s

    # formatters with error/empty/specific branches
    assert "couldn’t load expiring batches" in formatting._fmt_expiring_batches({"error": "x"})
    assert "couldn’t load inventory batches" in formatting._fmt_inventory_batches({"error": "x"})
    assert "couldn’t read app update status" in formatting._fmt_app_update_status({"error": "x"})
    txt = formatting._fmt_reservation_search({"reservations": [ {"id": i, "guest_name": "g"} for i in range(21)]})
    assert "and 1 more" in txt
    assert "didn’t find matching inventory items" in formatting._fmt_inventory_search({"items": []})
    txt = formatting._fmt_inventory_search({"items": [{"id": i, "item_name": "x"} for i in range(26)]})
    assert "and 1 more" in txt
    assert "No inventory item details found" in formatting._fmt_inventory_item_details({"item": {}})
    txt = formatting._fmt_inventory_item_details({"item": {"id": 1, "item_name": "A", "selling_price": object()}})
    assert "Selling price" in txt
    assert "No reservation groups found" in formatting._fmt_reservation_groups({"groups": []})
    assert "No packages/bundles found" in formatting._fmt_packages_list({"packages": []})
    assert "No open cash shift" in formatting._fmt_cash_shifts({"recent_shifts": []})
    txt = formatting._fmt_night_audit_runs({"night_audit_runs": [{"id": 1, "business_date": "d", "status": "ok", "exceptions_json": object()}]})
    assert "exceptions **0**" in txt
    assert "Reservation group not found" in formatting._fmt_reservation_group_detail({"group": {}})
    assert "No approval requests found" in formatting._fmt_approvals_list({"approvals": []})
    assert "No approval details available" in formatting._fmt_approval_detail({"approval": {}})
    txt = formatting._fmt_reservation_financial_integrity({"reservation_id": 1, "ok": False, "issues": [{"type": "x"}], "warnings": ["w"]})
    assert "ISSUES FOUND" in txt

    # _local_talking_rephrase clarification warm prefix (1287-1291)
    ans = formatting._local_talking_rephrase("q", {"intent": "ambiguous_needs_clarification", "answer": "which one?"})
    assert ans.lower().startswith("sure") and "which one" in ans.lower()
    ans = formatting._local_talking_rephrase("q", {"intent": "ambiguous_needs_clarification", "answer": ""})
    assert isinstance(ans, str)

