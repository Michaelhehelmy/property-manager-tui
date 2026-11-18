from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import app_core.assistant.runtime as runtime
import app_core.assistant.actions as actions
from app_core import services


def test_runtime_config_and_local_llm_helpers(monkeypatch, tmp_path):
    # config normalization and fallbacks
    monkeypatch.setattr(services, "get_setting", lambda k, d=None: (_ for _ in ()).throw(RuntimeError("boom")))
    cfg = runtime._llm_config()
    assert cfg["provider"] == "auto" and cfg["external"] is False

    model = tmp_path / "model.gguf"
    model.write_text("x", encoding="utf-8")
    monkeypatch.setattr(services, "get_setting", lambda k, d=None: {
        "assistant_provider": "openai",
        "assistant_response_mode": "talking",
        "assistant_local_model_path": str(model),
        "assistant_local_n_ctx": "1024",
    }.get(k, d))

    fake_llama_mod = SimpleNamespace(Llama=lambda **kwargs: (lambda prompt, **kw: {"choices": [{"text": " hello "}]}))
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_mod)
    runtime._LOCAL_LLM_CACHE = None
    cfg = runtime._llm_config()
    assert cfg["provider"] == "auto" and cfg["local_available"] is True and cfg["enabled"] is True

    llm = runtime._get_local_llm()
    assert llm is not None
    assert runtime._local_llm_generate("hi") == "hello"

    # cached false path and generation error
    runtime._LOCAL_LLM_CACHE = False
    assert runtime._get_local_llm() is None
    runtime._LOCAL_LLM_CACHE = None
    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(Llama=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("bad"))))
    assert runtime._get_local_llm() is None

    class BadLLM:
        def __call__(self, *a, **k):
            raise RuntimeError("x")
    runtime._LOCAL_LLM_CACHE = BadLLM()
    assert runtime._local_llm_generate("hi") is None

    # enabled talking_rephrase uses local generator via package, fallback to deterministic
    monkeypatch.setattr(runtime, "_pkg", lambda: SimpleNamespace(
        _llm_config=lambda: {"enabled": True},
        talking_rephrase=runtime.talking_rephrase,
        _local_llm_generate=lambda prompt, max_tokens=500, temperature=0.2: "polished",
    ))
    out = runtime.talking_rephrase("q", {"answer": "a", "data": {"x": 1}}, config={"enabled": True})
    assert out == "polished"
    monkeypatch.setattr(runtime, "_pkg", lambda: SimpleNamespace(
        _llm_config=lambda: {"enabled": False},
        talking_rephrase=runtime.talking_rephrase,
        _local_llm_generate=lambda *a, **k: None,
    ))
    assert runtime.talking_rephrase("q", {"answer": "a", "data": {}}, config={"enabled": False}) is not None


def test_runtime_routes_and_ui_guidance(monkeypatch):
    # deterministic_route low confidence -> None
    monkeypatch.setattr(runtime, "extract_slots", lambda text, history=None: {})
    monkeypatch.setattr(runtime, "rule_classify", lambda text, slots: {"intent": "unknown", "confidence": 0.1, "low_confidence": True, "router": "rules"})
    assert runtime.deterministic_route("hmm") is None

    # llm_route polish branch
    monkeypatch.setattr(runtime, "_pkg", lambda: SimpleNamespace(
        _llm_config=lambda: {"enabled": False},
        talking_rephrase=lambda m, c, cfg=None: "polished",
        _local_llm_generate=lambda *a, **k: None,
    ))
    monkeypatch.setattr(runtime, "extract_slots", lambda text, history=None: {})
    monkeypatch.setattr(runtime, "classify_intent", lambda text, history=None: {"intent": "capabilities_help", "confidence": 0.9, "slots": {}, "router": "x"})
    monkeypatch.setattr(runtime, "_execute_single", lambda intent, text, slots, ui_context=None: {"ok": True, "intent": intent, "answer": "core", "tools_used": [], "data": {}, "needs_clarification": False})
    res = runtime.llm_route("help", config={"enabled": True})
    assert res["answer"] == "polished" and "local_llm+" in res["source"]
    res = runtime.llm_route("help", config={"enabled": False})
    assert res["source"] == "local_grounded_core"

    # ui context helpers
    assert runtime._ui_where({"path": "/x/", "tab": "t", "title": "T", "recent_actions": [1]}) == ("/x", "t", "T", [1])
    assert runtime._get_pending_from_ui({"pending_action": {"id": 1}})["id"] == 1
    assert runtime._get_pending_from_ui({"session": {"pending_action": {"id": 2}}})["id"] == 2
    assert runtime._get_pending_from_ui({}) is None

    # ui guidance: where am i
    r = runtime._ui_guidance("where am i?", {"path": "/reservations", "tab": "list", "title": "Reservations", "recent_actions": [{"type": "click", "text": "x", "href": "/y"}]})
    assert r and r["intent"] == "ui_context" and "recent actions" in r["answer"].lower()

    # pending confirm/cancel text
    pending = {"id": "a1"}
    r = runtime._ui_guidance("confirm", {"pending_action": pending})
    assert r and r["intent"] == "action_confirm_request"
    monkeypatch.setattr(actions, "cancel_pending_action", lambda p, reason="": {"ok": True, "status": "cancelled", "action_id": p.get("id")})
    r = runtime._ui_guidance("cancel", {"pending_action": pending})
    assert r and r["intent"] == "action_cancel" and r.get("clear_pending_action") is True
    monkeypatch.setattr(actions, "cancel_pending_action", lambda p, reason="": (_ for _ in ()).throw(RuntimeError("x")))
    r = runtime._ui_guidance("cancel", {"pending_action": pending})
    assert r and r["intent"] == "action_cancel"

    # reservation create guidance, explain fields, nav shortcuts, walk-in how-to
    r = runtime._ui_guidance("how do I create a new reservation?", {"path": "/"})
    assert r and r["intent"] == "ui_navigate_help" and any(a.get("url", "").startswith("/reservations") for a in r["ui_actions"])

    r = runtime._ui_guidance("explain fields", {"path": "/reservations", "tab": "create", "fields": [{"kind": "label", "text": "Guest name"}, {"label": "Source"}]})
    assert r and r["intent"] == "ui_explain_fields" and "guest name" in r["answer"].lower()
    r = runtime._ui_guidance("explain this form", {"path": "/unknown", "tab": "", "fields": []})
    assert r and "detailed field map" in r["answer"].lower()

    r = runtime._ui_guidance("open inventory", {"path": "/"})
    assert r and r["intent"] == "ui_navigate" and r["data"]["target"].startswith("/pos")
    r = runtime._ui_guidance("how to sell walk-in without reservation", {"path": "/"})
    assert r and r["intent"] == "ui_navigate_help" and "walk-in" in r["answer"].lower()

    assert runtime._ui_guidance("hello there", {"path": "/"}) is None


def test_runtime_chat_branches(monkeypatch):
    monkeypatch.setattr(services, "touch_session", lambda: (_ for _ in ()).throw(RuntimeError("ignore")), raising=False)
    monkeypatch.setattr(runtime, "_get_pending_from_ui", lambda ui_context: None)
    monkeypatch.setattr(runtime, "_ui_guidance", lambda message, ui_context=None: (_ for _ in ()).throw(RuntimeError("ignore")))
    monkeypatch.setattr(runtime, "_pkg", lambda: SimpleNamespace(
        _llm_config=lambda: {"provider": "auto", "response_mode": "core", "enabled": False},
        talking_rephrase=lambda m, c, cfg=None: "polished",
    ))
    monkeypatch.setattr(runtime, "extract_slots", lambda text, history=None: {"reservation_id": 1})
    monkeypatch.setattr(runtime, "classify_intent", lambda text, history=None: {"intent": "reservation_summary", "confidence": 0.9, "slots": {}, "router": "rules", "low_confidence": False})
    monkeypatch.setattr(runtime, "_execute_single", lambda intent, text, slots, ui_context=None: {"ok": True, "intent": intent, "answer": "core", "tools_used": [], "data": {}, "resolved_guest_id": 5})
    monkeypatch.setattr(runtime, "_execute_compound", lambda text, slots, ui_context=None: {"ok": True, "intent": "compound_multi_intent", "answer": "c", "tools_used": [], "data": {}})

    r = runtime.chat("", ui_context={})
    assert r["ok"] is False

    # full_llm mode
    monkeypatch.setattr(runtime, "_pkg", lambda: SimpleNamespace(
        _llm_config=lambda: {"provider": "auto", "response_mode": "full_llm", "enabled": False},
        talking_rephrase=lambda m, c, cfg=None: "polished",
    ))
    monkeypatch.setattr(runtime, "llm_route", lambda message, history=None, config=None: {"ok": True, "intent": "x", "answer": "a", "tools_used": [], "data": {}})
    r = runtime.chat("hi", ui_context={})
    assert r["response_mode"] == "full_llm_local"

    # core/talking branches and pending propagation
    monkeypatch.setattr(runtime, "_pkg", lambda: SimpleNamespace(
        _llm_config=lambda: {"provider": "auto", "response_mode": "talking", "enabled": False},
        talking_rephrase=lambda m, c, cfg=None: "polished",
    ))
    monkeypatch.setattr(runtime, "_get_pending_from_ui", lambda ui_context: {"id": "p"})
    r = runtime.chat("reservation summary", ui_context={"session": {"pending_action": {"id": "p"}}})
    assert r["answer"] == "polished" and r.get("pending_action", {}).get("id") == "p" and r["slots"].get("guest_id") == 5

    # clarification branch on low confidence
    monkeypatch.setattr(runtime, "classify_intent", lambda text, history=None: {"intent": "reservation_balance", "confidence": 0.2, "slots": {}, "router": "rules", "low_confidence": True})
    monkeypatch.setattr(runtime, "extract_slots", lambda text, history=None: {})
    calls = {}
    def fake_exec(intent, text, slots, ui_context=None):
        calls["intent"] = intent
        return {"ok": True, "intent": intent, "answer": "clarify", "tools_used": [], "data": {}, "needs_clarification": True}
    monkeypatch.setattr(runtime, "_execute_single", fake_exec)
    r = runtime.chat("balance", ui_context={})
    assert calls["intent"] == "ambiguous_needs_clarification"
