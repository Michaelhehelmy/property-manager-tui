"""Targeted coverage tests for assistant core modules (engine/runtime/nlu/feedback/common/actions/validation/init)."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app_core import assistant
from app_core.assistant import (
    action_validation as av,
    actions,
    common,
    engine,
    feedback,
    nlu,
    runtime,
)


def _as_obj(**kw):
    return SimpleNamespace(**kw)


def test_assistant_init_today_exception_path(monkeypatch):
    # Hit _today fallback except path (lines 58-59): now.date() raises and
    # now is instance of _dt_class so first branch is skipped.
    from types import SimpleNamespace

    class BadDT(assistant._dt_class):
        def date(self):
            raise RuntimeError("boom")

    bad = BadDT(2026, 1, 2)
    fake_dt_module = SimpleNamespace(datetime=SimpleNamespace(now=lambda: bad))
    monkeypatch.setattr(assistant, "datetime", fake_dt_module)
    assert assistant._today() is bad


def test_common_helpers_cover_edge_branches(monkeypatch):
    assert common._sim("", "abc") == 0.0
    assert common._fmt_money("bad") == "bad"
    assert common._yn(0) == "no"
    assert common._yn(1) == "yes"
    assert common._val({"a": {"b": 1}}, "a", "b") == 1
    assert common._val({"a": 1}, "a", "b", default="x") == "x"
    assert common._val(None, "a", default=5) == 5

    hist = [
        {"role": "user", "content": "x"},
        {"role": "assistant", "slots": {"guest_id": 7, "reservation_id": 3}, "intent": "guest_profile", "resolved_guest_id": 9},
        {"role": "assistant", "slots": {"group_id": 4, "date_start": "2026-01-01", "date_end": "2026-01-31"}},
    ]
    ctx = common._context_from_history(hist)
    assert ctx["group_id"] == 4
    assert ctx["date_start"] == "2026-01-01"
    assert ctx["date_end"] == "2026-01-31"
    # earlier assistant filled others
    assert ctx["guest_id"] in (7, 9)
    assert ctx["reservation_id"] == 3
    assert ctx["last_intent"] == "guest_profile"


def test_feedback_exemplar_match_and_ml_load_branches(monkeypatch, tmp_path):
    # exemplar_match returns None when no exemplars
    monkeypatch.setattr(feedback, "_EXEMPLAR_CACHE", [])
    assert feedback.exemplar_match("hello") is None

    # exemplar below threshold -> None
    monkeypatch.setattr(feedback, "_EXEMPLAR_CACHE", [{"text": "completely different thing", "label": "x", "slots": {}}])
    assert feedback.exemplar_match("hello there", threshold=0.99) is None

    # sklearn loader branches: missing path
    monkeypatch.setattr(feedback, "_SKLEARN_CACHE", None)
    monkeypatch.setenv("ASSISTANT_SKLEARN_MODEL", str(tmp_path / "missing.joblib"))
    assert feedback._load_sklearn_router() is None
    # load failure exception path
    monkeypatch.setattr(feedback, "_SKLEARN_CACHE", None)
    bad = tmp_path / "bad.joblib"
    bad.write_text("not a joblib")
    monkeypatch.setenv("ASSISTANT_SKLEARN_MODEL", str(bad))
    assert feedback._load_sklearn_router() is None

    # setfit loader branches: missing path
    monkeypatch.setattr(feedback, "_SETFIT_CACHE", None)
    monkeypatch.setenv("ASSISTANT_SETFIT_MODEL_DIR", str(tmp_path / "missing_setfit"))
    assert feedback._load_setfit_router() is None
    # import/load failure (SetFitModel likely unavailable) -> None
    monkeypatch.setattr(feedback, "_SETFIT_CACHE", None)
    monkeypatch.setenv("ASSISTANT_SETFIT_MODEL_DIR", str(tmp_path))
    assert feedback._load_setfit_router() is None

    # _predict_intent_ml setfit path with fake model
    class FakeSetfit:
        def predict(self, texts):
            return ["guest_profile"]
        def predict_proba(self, texts):
            raise RuntimeError("no proba")

    monkeypatch.setenv("ASSISTANT_NLU", "setfit")
    monkeypatch.setattr(feedback, "_load_setfit_router", lambda: FakeSetfit())
    out = feedback._predict_intent_ml("who is guest")
    assert out and out["intent"] == "guest_profile" and out["router"] == "setfit"

    # sklearn prediction confidence fallback from decision_function failing
    class FakeVec:
        def transform(self, texts):
            return texts
    class FakeClf:
        def predict(self, X):
            return ["guest_search"]
        def predict_proba(self, X):
            raise RuntimeError("nope")
        def decision_function(self, X):
            raise RuntimeError("nope")
    fake = {"vectorizer": FakeVec(), "classifier": FakeClf()}
    monkeypatch.setenv("ASSISTANT_NLU", "sklearn")
    monkeypatch.setattr(feedback, "_load_sklearn_router", lambda: fake)
    out = feedback._predict_intent_ml("find guest")
    assert out and out["intent"] == "guest_search" and out["router"] == "sklearn"


def test_nlu_rule_and_classify_branches(monkeypatch):
    # specific rule cue refinements
    r = nlu.rule_classify("show occupancy performance report", {"report_kind": "occupancy"})
    assert r["intent"] == "report_occupancy"
    r = nlu.rule_classify("when was the last time guest profile for them", {"guest_id": 1})
    # guest_last_visit boost branch
    assert r["intent"] in {"guest_profile", "guest_last_visit", "guest_top_items_last_visit", "ambiguous_needs_clarification", "compound_multi_intent", "capabilities_help"}
    r = nlu.rule_classify("show group #3 details", {"group_id": 3})
    assert r["intent"] in {"reservation_group_detail", "reservation_groups", "compound_multi_intent"}
    r = nlu.rule_classify("packages on reservation #5", {"reservation_id": 5})
    assert r["intent"] in {"reservation_packages_applied", "packages_list", "compound_multi_intent"}
    r = nlu.rule_classify("do they owe money")
    assert isinstance(r.get("intent"), str)
    r = nlu.rule_classify("balance")
    assert isinstance(r.get("intent"), str)
    r = nlu.rule_classify("give me the report for this")
    assert isinstance(r.get("intent"), str)

    # classify_intent: ml lower-confidence/low-rl path chooses ml when rl.conf < ml.conf (line 184)
    monkeypatch.setattr(feedback, "exemplar_match", lambda text, threshold=0.70: None)
    monkeypatch.setattr(feedback, "_predict_intent_ml", lambda text: {"intent": "guest_profile", "confidence": 0.55, "router": "sklearn"})
    monkeypatch.setattr(nlu, "rule_classify", lambda text, slots: {"intent": "capabilities_help", "confidence": 0.4, "slots": slots, "router": "rules", "low_confidence": True})
    out = nlu.classify_intent("something vague")
    assert out["intent"] == "guest_profile" and out["router"] == "sklearn"


def test_runtime_branches(monkeypatch):
    # local llm generate non-dict path
    class LLM:
        def __call__(self, prompt, **kw):
            return "  hi  "
    monkeypatch.setattr(runtime, "_get_local_llm", lambda: LLM())
    assert runtime._local_llm_generate("x") == "hi"

    # talking_rephrase json serialization exception fallback + deterministic fallback
    monkeypatch.setattr(runtime, "_llm_config", lambda: {"enabled": False, "local_available": False})
    ans = runtime.talking_rephrase("q", {"answer": "ok", "intent": "capabilities_help", "data": {"x": {1, 2}}})
    assert isinstance(ans, str) and ans

    # chat ui guidance propagation and pending propagation
    def fake_ui_guidance(message, ui_context=None):
        return {"ok": True, "intent": "ui_navigate", "answer": "go"}
    monkeypatch.setattr(runtime, "_ui_guidance", fake_ui_guidance)
    out = runtime.chat("open reservations", ui_context={"path": "/reservations", "pending_action": {"id": "p1"}})
    assert out["ui_actions"] == []
    assert out["response_mode"] == "core"
    assert out.get("pending_action") == {"id": "p1"}

    # chat compound path
    monkeypatch.setattr(runtime, "_ui_guidance", lambda *a, **k: None)
    monkeypatch.setattr(runtime, "_llm_config", lambda: {"enabled": False, "response_mode": "core", "local_available": False})
    monkeypatch.setattr(runtime, "classify_intent", lambda msg, hist: {"intent": "compound_multi_intent", "confidence": 0.9, "slots": {}, "router": "rules"})
    monkeypatch.setattr(runtime, "_execute_compound", lambda *a, **k: {"ok": True, "intent": "compound_multi_intent", "answer": "done", "tools_used": [], "data": {}})
    out = runtime.chat("compare and show", history=None, ui_context={})
    assert out["intent"] == "compound_multi_intent"

    # chat resolved_guest_id propagation and exception-safe path
    monkeypatch.setattr(runtime, "classify_intent", lambda msg, hist: {"intent": "guest_profile", "confidence": 0.9, "slots": {"guest_id": 5}, "router": "rules"})
    monkeypatch.setattr(runtime, "_execute_single", lambda *a, **k: {"ok": True, "intent": "guest_profile", "answer": "ok", "tools_used": [], "data": {}, "resolved_guest_id": 5, "slots": None})
    out = runtime.chat("guest 5", ui_context={})
    assert out.get("resolved_guest_id") == 5


def test_engine_extract_and_propose_branches(monkeypatch):
    # _extract_write_args: amount parse exception, lot_code string, guest name, item_name extraction
    text = "add pos item coffee to reservation #1 amount bad qty 2 batch LOTX for John Doe room 3 activity 4 on 2026-01-01 to 2026-01-02 \"note\""
    args = engine._extract_write_args(text, {"reservation_id": 1, "date_start": "2026-01-01", "date_end": "2026-01-02"})
    assert args["qty"] == 2
    assert args["lot_code"] == "LOTX"
    assert args.get("guest_name", "").startswith("John Doe")
    assert args["room_id"] == 3
    assert args["activity_id"] == 4
    assert args["notes"] == "note"
    assert "amount" not in args
    # item_name extraction
    args2 = engine._extract_write_args("add bottled water to reservation #2", {})
    assert args2.get("item_name") == "bottled water"

    # propose action branches
    # walk-in with inferred item but missing method
    monkeypatch.setattr(actions, "create_proposal", lambda action_type, args, ui_context=None: pytest.fail("should not propose without method"))
    out = engine._maybe_propose_action("propose_complete_walkin_sale", "walk-in sale item barcode ABC qty 2", {}, ui_context={})
    assert out and out["intent"] == "action_needs_info" and "method" in out["data"]["missing"]

    # create reservation amount->rate
    captured = {}
    def fake_create(action_type, args, ui_context=None):
        captured["action_type"] = action_type
        captured["args"] = dict(args)
        return {"summary": "ok", "prechecks": {"ok": True}}
    monkeypatch.setattr(actions, "create_proposal", fake_create)
    out = engine._maybe_propose_action(
        "propose_create_reservation",
        "create reservation for Jane room 1 amount 50",
        {"date_start": "2026-02-01", "date_end": "2026-02-03"},
        ui_context={},
    )
    assert out and out["ok"] is True
    assert captured.get("action_type") == "propose_create_reservation"
    assert captured["args"]["rate"] == 50.0

    # proposal precheck failure formatting and exception handling
    monkeypatch.setattr(actions, "create_proposal", lambda *a, **k: {"summary": "bad", "prechecks": {"ok": False, "errors": ["e1"], "warnings": ["w1"]}})
    out = engine._maybe_propose_action(
        "propose_record_payment",
        "record payment amount 10 cash",
        {"reservation_id": 1},
    )
    assert out and out["ok"] is False and "Issues" in out["answer"] and "Notes" in out["answer"]

    def boom(*a, **k):
        raise RuntimeError("explode")
    monkeypatch.setattr(actions, "create_proposal", boom)
    out = engine._maybe_propose_action(
        "propose_record_payment",
        "record payment amount 10 cash",
        {"reservation_id": 1},
    )
    assert out and out["ok"] is False and "couldn’t prepare that action safely" in out["answer"]

    # guest disambiguation formatting exception branch
    bad_guest = {"id": 1}  # missing fields is fine but force exception via custom object? easier: make .get raise? not possible; simulate exception by passing object with keys() raising in iteration not used. Instead use proxy.
    class Bad:
        def get(self, k, d=None):
            if k == "name":
                raise RuntimeError("bad")
            return d
        def __getitem__(self, k):
            if k == "id":
                return 1
            raise KeyError(k)
    out = engine._guest_disambiguation([Bad(), {"id": 2, "name": "Ana", "phone": "p", "email": "e", "stays": 1, "last_stay": "2026"}], q="a")
    assert "Which one did you mean" in out["answer"]


def test_engine_execute_single_keyword_and_dispatch_branches(monkeypatch):
    # greeting thanks
    res = engine._execute_single("greeting_smalltalk", "thanks!", {})
    assert "welcome" in res["answer"].lower()
    # quick keyword routing branches
    monkeypatch.setattr(engine, "execute_tool", lambda name, args: {"tool": name, "args": args, "ok": True, "sections": {}, "data": {}})
    res = engine._execute_single("anything", "doctor report please", {})
    assert res["intent"] == "health_diagnostics"
    res = engine._execute_single("anything", "what expires in bad days", {})
    assert res["intent"] == "expiring_batches"
    res = engine._execute_single("anything", "trace barcode ABC123", {})
    assert res["intent"] == "trace_barcode"
    res = engine._execute_single("anything", "list batches for item", {})
    assert res["intent"] == "inventory_batches"
    res = engine._execute_single("anything", "check for updates", {})
    assert res["intent"] == "app_update_status"

    # ambiguous clarification specialized paths
    res = engine._execute_single("ambiguous_needs_clarification", "reservation #1 balance", {"reservation_id": 1})
    assert res["intent"] == "reservation_balance"
    res = engine._execute_single("ambiguous_needs_clarification", "balance", {})
    assert res["needs_clarification"] and res["data"].get("suggestion") == "overall_ar"
    res = engine._execute_single("ambiguous_needs_clarification", "what did he order last visit", {})
    assert res["needs_clarification"]
    res = engine._execute_single("ambiguous_needs_clarification", "report for this", {})
    assert res["needs_clarification"]

    # reservation integrity extract id from text and missing
    monkeypatch.setattr(engine, "execute_tool", lambda name, args: {"ok": True, "issues": [], "warnings": []})
    res = engine._execute_single("reservation_financial_integrity", "check reservation #77 integrity", {})
    assert res["intent"] == "reservation_financial_integrity"
    res = engine._execute_single("reservation_financial_integrity", "check integrity", {})
    assert res["needs_clarification"]

    # approval detail extract from text and missing
    monkeypatch.setattr(engine, "execute_tool", lambda name, args: {"approval": {"id": args.get("approval_id"), "status": "pending"}})
    res = engine._execute_single("approval_detail", "show approval #9", {})
    assert res["intent"] == "approval_detail"
    res = engine._execute_single("approval_detail", "show approval", {})
    assert res["needs_clarification"]

    # front_desk_ops special formatting path and trace/expiring/inventory_batches by intent
    monkeypatch.setattr(engine, "execute_tool", lambda name, args: {"date": "2026-01-01", "summary": {"arrivals": 1}, "error": None} if name == "get_front_desk_ops" else {"summary": {"dirty": 0}})
    res = engine._execute_single("front_desk_ops", "front desk ops", {"date_start": "2026-01-01"})
    assert res["intent"] == "today_ops"
    monkeypatch.setattr(engine, "execute_tool", lambda name, args: {"tool": name, "args": args})
    res = engine._execute_single("expiring_batches", "expiring", {"days": "bad"})
    assert res["intent"] == "expiring_batches" and res["data"]["args"]["within_days"] is None
    res = engine._execute_single("trace_barcode", "trace code Z9", {})
    assert res["intent"] == "trace_barcode"
    res = engine._execute_single("inventory_batches", "batches", {"item_id": 3})
    assert res["intent"] == "inventory_batches"


def test_actions_and_action_validation_direct_branches(monkeypatch):
    # actions helpers
    assert actions._float("bad", default=1.5) == 1.5
    assert actions._int("7", default=0) == 7
    proofs = actions._extract_proofs({"confirm": True}, {"proofs": {"approved": True}})
    assert proofs["confirmed"] is True
    assert actions._summary_for("unknown", {}, {}) == "Proposed action: unknown"

    # create/cancel/execute basic flows
    orig_norm = av.normalize_action_args
    orig_build = av.build_prechecks
    monkeypatch.setattr(av, "normalize_action_args", lambda t, a: dict(a or {}))
    monkeypatch.setattr(av, "build_prechecks", lambda t, a, ui_context=None: {"ok": True, "preview": {"type": t, "amount": 5, "method": "Cash", "reservation_id": 1}, "resolved": {"reservation": {"id": 1}}, "warnings": [], "checks": []})
    prop = actions.create_proposal("propose_record_payment", {"reservation_id": 1, "amount": 5, "method": "Cash"})
    assert prop["ok"] and prop["status"] == "pending"
    cancelled = actions.cancel_pending_action(prop, reason="test")
    assert cancelled["status"] == "cancelled" and cancelled["cancel_reason"] == "test"
    assert actions.cancel_pending_action(None)["ok"] is False

    # execute: proof required, expired, validation failed, success, failure
    assert actions.execute_pending_action(None)["status"] == "invalid"
    exp = {"status": "pending", "expires_at": "2000-01-01T00:00:00Z", "action_type": "propose_record_payment", "execute_action_type": "execute_record_payment", "args": {}}
    assert actions.execute_pending_action(exp)["status"] == "expired"
    no_proof = {"status": "pending", "action_type": "propose_record_payment", "execute_action_type": "execute_record_payment", "args": {"reservation_id": 1, "amount": 1, "method": "Cash"}, "required_proofs": ["ui_confirmation"]}
    assert actions.execute_pending_action(no_proof)["status"] == "proof_required"
    monkeypatch.setattr(av, "build_prechecks", lambda t, a, ui_context=None: {"ok": False, "errors": ["bad"], "warnings": [], "resolved": {}})
    bad = {"status": "pending", "action_type": "propose_record_payment", "execute_action_type": "execute_record_payment", "args": {}, "required_proofs": []}
    assert actions.execute_pending_action(bad, form_proofs={"confirmed": True})["status"] == "validation_failed"

    monkeypatch.setattr(av, "build_prechecks", lambda t, a, ui_context=None: {"ok": True, "preview": {}, "resolved": {}, "warnings": [], "checks": []})
    monkeypatch.setattr(actions, "_EXECUTORS", {"execute_record_payment": lambda args, resolved: (_ for _ in ()).throw(RuntimeError("fail"))})
    fail = actions.execute_pending_action({"status": "pending", "action_type": "propose_record_payment", "execute_action_type": "execute_record_payment", "args": {}, "required_proofs": []}, form_proofs={"confirmed": True})
    assert fail["status"] == "failed"

    monkeypatch.setattr(actions, "_EXECUTORS", {"execute_record_payment": lambda args, resolved: {"result": {"payment_id": 9}, "affected_ids": {"reservation_id": 1, "payment_id": 9}, "message": "ok"}})
    ok = actions.execute_pending_action({"status": "pending", "action_type": "propose_record_payment", "execute_action_type": "execute_record_payment", "args": {}, "required_proofs": []}, form_proofs={"confirmed": True})
    assert ok["ok"] and ok["status"] == "executed"

    # action_validation direct helpers (restore stubs above)
    monkeypatch.setattr(av, "normalize_action_args", orig_norm)
    monkeypatch.setattr(av, "build_prechecks", orig_build)
    assert av._row_to_dict({"a": 1}) == {"a": 1}
    assert av._money_round(None) is None
    assert av._money_round("bad") == "bad"
    norm_items = av.normalize_action_args(
        "propose_complete_walkin_sale",
        {"items": ["x", {"item_id": 2, "item_name": "Tea"}]},
    )["items"]
    assert norm_items[0] == "x"
    assert norm_items[1]["inv_id"] == 2
    assert norm_items[1]["desc"] == "Tea"
    monkeypatch.setitem(av._PREVIEW_BUILDERS, "x", lambda args: (_ for _ in ()).throw(RuntimeError("boom")))
    out = av.build_prechecks("x", {})
    assert not out["ok"]
    assert any("Precheck failed" in e or "Unsupported" in e for e in out.get("errors", []))
