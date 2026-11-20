from __future__ import annotations

import time

from tests.web.conftest import get_csrf, set_session


def _patch_base(monkeypatch):
    import ui.web.routes.assistant as ar

    monkeypatch.setattr(
        ar.assistant,
        "get_llm_config",
        lambda: {
            "provider": "auto",
            "response_mode": "talking",
            "local_available": False,
            "enabled": False,
            "external": False,
        },
    )
    monkeypatch.setattr(ar.assistant, "INTENT_LABELS", {"capabilities_help": "Help"}, raising=False)
    monkeypatch.setattr(ar.services, "get_setting", lambda k, d=None: "true" if k == "assistant_require_unlock" else d)
    return ar


def test_assistant_ask_json_pending_and_ttl(client, monkeypatch):
    ar = _patch_base(monkeypatch)

    proposed = {
        "id": "pa1",
        "type": "test_action",
        "summary": "Do thing",
        "args": {"x": 1},
        "resolved": {},
        "preview": {"note": "preview"},
    }

    def fake_chat(msg, history=None, ui_context=None):
        return {
            "ok": True,
            "answer": f"reply:{msg}",
            "intent": "capabilities_help",
            "confidence": 0.9,
            "tools_used": [],
            "source": "mock",
            "ui_actions": [{"type": "toast", "message": "hi"}],
            "proposed_action": dict(proposed),
            "slots": {"a": 1},
        }

    monkeypatch.setattr(ar.assistant, "chat", fake_chat)

    token = set_session(client, unlocked=True)
    r = client.post(
        "/assistant/ask",
        data={"csrf": token, "message": "do it", "widget": "1", "ui_context": '{"page":"x"}'},
        headers={"Accept": "application/json", "X-Requested-With": "assistant-widget"},
    )
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["answer"] == "reply:do it"
    assert j["pending_action"]["id"] == "pa1"
    assert j["proposed_action"]["id"] == "pa1"
    assert j["pending_action"]["ui_context"]["page"] == "x"
    assert j["pending_action"]["expires_at_ts"] > int(time.time()) - 5

    # follow-up ask without proposed action keeps pending action in session/json
    monkeypatch.setattr(
        ar.assistant,
        "chat",
        lambda msg, history=None, ui_context=None: {
            "ok": True,
            "answer": "follow",
            "intent": "capabilities_help",
            "confidence": 0.5,
            "tools_used": [],
            "source": "mock",
            "ui_actions": [],
        },
    )
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    r = client.post(
        "/assistant/ask", data={"csrf": csrf, "message": "follow up"}, headers={"Accept": "application/json"}
    )
    assert r.status_code == 200
    j = r.json()
    assert j.get("pending_action", {}).get("id") == "pa1" or j.get("proposed_action", {}).get("id") == "pa1"

    # expired pending action cleared
    expired = {"id": "old", "type": "x", "expires_at_ts": int(time.time()) - 10}
    token = set_session(client, unlocked=True, extra={"assistant_history": [], ar.PENDING_ACTION_SESSION_KEY: expired})
    page = client.get("/assistant")
    assert page.status_code == 200
    # JSON ask should not surface expired pending
    csrf = get_csrf(page.text)
    monkeypatch.setattr(
        ar.assistant,
        "chat",
        lambda msg, history=None, ui_context=None: {
            "ok": True,
            "answer": "ok",
            "intent": "i",
            "confidence": 1,
            "tools_used": [],
            "source": "s",
            "ui_actions": [],
        },
    )
    r = client.post("/assistant/ask", data={"csrf": csrf, "message": "hi"}, headers={"Accept": "application/json"})
    assert r.status_code == 200
    assert r.json().get("pending_action") is None


def test_assistant_json_locked_and_chat_fallback(client, monkeypatch):
    ar = _patch_base(monkeypatch)

    # locked JSON ask
    token = set_session(client, unlocked=False)
    r = client.post("/assistant/ask", data={"csrf": token, "message": "hi"}, headers={"Accept": "application/json"})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is False
    assert any(a.get("type") == "navigate" for a in j.get("ui_actions") or [])

    # unlocked: TypeError fallback to legacy chat signature and non-dict result normalization
    token = set_session(client, unlocked=True)

    def legacy_chat(msg, history=None):
        return "plain-text-answer"

    monkeypatch.setattr(ar.assistant, "chat", legacy_chat)
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    r = client.post("/assistant/ask", data={"csrf": csrf, "message": "legacy"}, headers={"Accept": "application/json"})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["answer"] == "plain-text-answer"

    # chat exception path
    def boom(msg, history=None, ui_context=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(ar.assistant, "chat", boom)
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    r = client.post("/assistant/ask", data={"csrf": csrf, "message": "err"}, headers={"Accept": "application/json"})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is False
    assert "encountered an error" in j["answer"].lower()
    assert "boom" not in j["answer"]
    assert "RuntimeError" not in j["answer"]


def test_assistant_action_confirm_and_cancel_json(client, monkeypatch):
    ar = _patch_base(monkeypatch)

    pending = {
        "id": "act1",
        "type": "demo",
        "summary": "Demo",
        "args": {},
        "resolved": {},
        "preview": {},
        "expires_at_ts": int(time.time()) + 600,
    }
    token = set_session(
        client, unlocked=True, extra={ar.PENDING_ACTION_SESSION_KEY: dict(pending), "assistant_history": []}
    )

    calls = {}

    def exec_ok(action, request=None, password=None, proof=None):
        calls["action"] = action
        calls["proof"] = proof or password
        return {"ok": True, "answer": "Done", "ui_actions": [{"type": "toast", "message": "ok"}], "tools_used": ["x"]}

    monkeypatch.setattr(ar, "execute_pending_action", exec_ok)
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    r = client.post(
        "/assistant/action/confirm",
        data={"csrf": csrf, "action_id": "act1", "widget": "1"},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["result"]["ok"] is True
    assert j.get("pending_action") is None
    assert calls.get("action", {}).get("id") == "act1"

    # mismatch
    pending = {"id": "act2", "type": "demo", "summary": "Demo", "expires_at_ts": int(time.time()) + 600}
    token = set_session(client, unlocked=True, extra={ar.PENDING_ACTION_SESSION_KEY: dict(pending)})
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    r = client.post(
        "/assistant/action/confirm", data={"csrf": csrf, "action_id": "wrong"}, headers={"Accept": "application/json"}
    )
    assert r.status_code == 200 and r.json()["ok"] is False

    # no pending
    token = set_session(client, unlocked=True, extra={})
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    r = client.post(
        "/assistant/action/confirm", data={"csrf": csrf, "action_id": "x"}, headers={"Accept": "application/json"}
    )
    assert r.status_code == 200 and r.json()["ok"] is False

    # failure result keeps/clears based on terminal behavior; route treats not ok as terminal by default
    pending = {"id": "act3", "type": "demo", "summary": "Demo", "expires_at_ts": int(time.time()) + 600}
    token = set_session(client, unlocked=True, extra={ar.PENDING_ACTION_SESSION_KEY: dict(pending)})
    monkeypatch.setattr(ar, "execute_pending_action", lambda *a, **k: {"ok": False, "answer": "bad", "ui_actions": []})
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    r = client.post(
        "/assistant/action/confirm", data={"csrf": csrf, "action_id": "act3"}, headers={"Accept": "application/json"}
    )
    assert r.status_code == 200 and r.json()["ok"] is False

    # failed execution is treated as terminal by route logic (clears pending)
    pending = {"id": "act4", "type": "demo", "summary": "Demo", "expires_at_ts": int(time.time()) + 600}
    token = set_session(client, unlocked=True, extra={ar.PENDING_ACTION_SESSION_KEY: dict(pending)})
    monkeypatch.setattr(
        ar,
        "execute_pending_action",
        lambda *a, **k: {"ok": False, "answer": "retry", "terminal": False, "ui_actions": []},
    )
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    r = client.post(
        "/assistant/action/confirm", data={"csrf": csrf, "action_id": "act4"}, headers={"Accept": "application/json"}
    )
    j = r.json()
    assert r.status_code == 200 and j["ok"] is False
    assert j.get("pending_action") is None

    # cancel success
    pending = {"id": "act5", "type": "demo", "summary": "Demo", "expires_at_ts": int(time.time()) + 600}
    token = set_session(client, unlocked=True, extra={ar.PENDING_ACTION_SESSION_KEY: dict(pending)})
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    r = client.post(
        "/assistant/action/cancel", data={"csrf": csrf, "action_id": "act5"}, headers={"Accept": "application/json"}
    )
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json().get("pending_action") is None

    # cancel mismatch and no pending
    pending = {"id": "act6", "type": "demo", "summary": "Demo", "expires_at_ts": int(time.time()) + 600}
    token = set_session(client, unlocked=True, extra={ar.PENDING_ACTION_SESSION_KEY: dict(pending)})
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    r = client.post(
        "/assistant/action/cancel", data={"csrf": csrf, "action_id": "nope"}, headers={"Accept": "application/json"}
    )
    assert r.status_code == 200 and r.json()["ok"] is False
    token = set_session(client, unlocked=True, extra={})
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    r = client.post("/assistant/action/cancel", data={"csrf": csrf}, headers={"Accept": "application/json"})
    assert r.status_code == 200 and r.json()["ok"] is True

    # locked confirm/cancel JSON
    token = set_session(client, unlocked=False)
    r = client.post(
        "/assistant/action/confirm", data={"csrf": token, "action_id": "x"}, headers={"Accept": "application/json"}
    )
    assert r.status_code == 200 and r.json()["ok"] is False
    r = client.post(
        "/assistant/action/cancel", data={"csrf": token, "action_id": "x"}, headers={"Accept": "application/json"}
    )
    assert r.status_code == 200 and r.json()["ok"] is False


def test_assistant_feedback_and_helpers(client, monkeypatch):
    ar = _patch_base(monkeypatch)
    feedback_calls = []
    monkeypatch.setattr(ar.assistant, "record_feedback", lambda **kw: feedback_calls.append(kw))

    hist = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "a1",
            "intent": "i1",
            "confidence": 0.2,
            "tools_used": ["t"],
            "slots": {"s": 1},
            "user_message": "hi",
            "source": "mock",
        },
        {
            "role": "assistant",
            "content": "a2",
            "intent": "i2",
            "confidence": 0.3,
            "tools_used": [],
            "slots": {},
            "user_message": "hi2",
            "source": "mock",
        },
    ]
    token = set_session(client, unlocked=True, extra={"assistant_history": list(hist)})
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    # target specific assistant index
    r = client.post(
        "/assistant/feedback",
        data={
            "csrf": csrf,
            "msg_index": "1",
            "rating": "1",
            "corrected_intent": "fixed",
            "user_note": "n",
            "corrected_slots_json": '{"k":2}',
        },
    )
    assert r.status_code == 303
    assert feedback_calls and feedback_calls[-1]["corrected_intent"] == "fixed"
    assert feedback_calls[-1]["corrected_slots"] == {"k": 2}

    # fallback to last assistant and bad slots json
    token = set_session(client, unlocked=True, extra={"assistant_history": list(hist)})
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    r = client.post(
        "/assistant/feedback", data={"csrf": csrf, "msg_index": "bad", "rating": "0", "corrected_slots_json": "{bad"}
    )
    assert r.status_code == 303
    assert feedback_calls[-1]["corrected_slots"] == {}

    # no assistant message found
    token = set_session(client, unlocked=True, extra={"assistant_history": [{"role": "user", "content": "x"}]})
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    r = client.post("/assistant/feedback", data={"csrf": csrf, "msg_index": "0", "rating": "1"})
    assert r.status_code == 303

    # locked feedback
    token = set_session(client, unlocked=False)
    r = client.post("/assistant/feedback", data={"csrf": token, "rating": "1"})
    assert r.status_code == 303 and "/assistant" in r.headers.get("location", "/assistant")

    # helper normalization branches
    assert ar._normalize_pending_action(None) is None
    # raw hint without id/type gets normalized via create_proposal
    monkeypatch.setattr(
        ar,
        "create_proposal",
        lambda a: {"id": "gen", "type": a.get("type", "x"), "summary": "s", "args": {}, "resolved": {}, "preview": {}},
    )
    norm = ar._normalize_pending_action({"type": "x"})
    assert norm and norm["id"] == "gen" and norm["expires_at_ts"] >= int(time.time())

    # parse body fields helper
    assert ar._parse_body_fields(None, {"a": 1, "b": 2}, "a", "c") == {"a": 1}
