"""Coverage-focused tests for assistant web routes."""

from __future__ import annotations

import json
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


def test_assistant_route_helper_and_request_branches(client, monkeypatch):
    ar = _patch_base(monkeypatch)

    # _json_safe_value sanitization branches
    tb = None
    try:
        raise ValueError("x")
    except ValueError as e:
        tb = e
    safe = ar._json_safe_value(
        {
            "ok": True,
            "tb": "x",
            "traceback": "y",
            "exc_info": "z",
            "exception": tb,
            "__traceback__": "t",
            1: {2, 3},
        }
    )
    assert "tb" not in safe and "traceback" not in safe and "exc_info" not in safe and "exception" not in safe
    assert safe["1"] in ([2, 3], [3, 2])

    # _get_request_data JSON path and invalid JSON fallback (via HTTP behavior)
    token = set_session(client, unlocked=True)
    page = client.get("/assistant")
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

    r = client.post(
        "/assistant/ask",
        data={"csrf": csrf, "message": "json", "widget": "1"},
        headers={"Accept": "application/json", "X-Requested-With": "assistant-widget"},
    )
    assert r.status_code == 200 and r.json()["answer"] == "ok"

    # _history initializes non-list, _parse_body_fields from form
    assert ar._assistant_history_context([{"role": "user"}, {"role": "system"}, {"role": "assistant"}], limit=10) == [
        {"role": "user"},
        {"role": "assistant"},
    ]

    # normalize proposed action from raw dict without id/type (create_proposal failure fallback path)
    monkeypatch.setattr(ar, "create_proposal", lambda a: (_ for _ in ()).throw(RuntimeError("boom")))
    norm = ar._normalize_pending_action({"raw": 1}, ui_context={"p": 1})
    assert norm and norm.get("ui_context") == {"p": 1} and "expires_at_ts" in norm


def test_assistant_route_htmx_and_non_json_paths(client, monkeypatch):
    ar = _patch_base(monkeypatch)
    token = set_session(client, unlocked=True)
    page = client.get("/assistant")
    csrf = get_csrf(page.text)

    # unlocked ask via HTMX partial path (non-json)
    monkeypatch.setattr(
        ar.assistant,
        "chat",
        lambda msg, history=None, ui_context=None: {
            "ok": True,
            "answer": "hello",
            "intent": "capabilities_help",
            "confidence": 0.5,
            "tools_used": [],
            "source": "mock",
            "ui_actions": [],
        },
    )
    r = client.post("/assistant/ask", data={"csrf": csrf, "message": "hi"}, headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert (
        "assistant" in r.text.lower()
        or "hello" in r.text
        or "hx" in r.headers.get("content-type", "")
        or r.headers.get("content-type", "").startswith("text/html")
    )

    # locked non-json ask redirects/guard
    token = set_session(client, unlocked=False)
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    r = client.post("/assistant/ask", data={"csrf": csrf, "message": "hi"})
    assert r.status_code in (302, 303)

    # clear endpoint
    token = set_session(
        client,
        unlocked=True,
        extra={ar.PENDING_ACTION_SESSION_KEY: {"id": "x", "expires_at_ts": int(time.time()) + 100}},
    )
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    r = client.post("/assistant/clear", data={"csrf": csrf}, headers={"HX-Request": "true"})
    assert r.status_code == 200

    # feedback locked redirect
    token = set_session(client, unlocked=False)
    r = client.post("/assistant/feedback", data={"csrf": token, "rating": "1"})
    assert r.status_code == 303


def test_assistant_route_action_confirm_cancel_htmx_and_error_paths(client, monkeypatch):
    ar = _patch_base(monkeypatch)

    # confirm locked non-json
    token = set_session(client, unlocked=False)
    r = client.post("/assistant/action/confirm", data={"csrf": token, "action_id": "x"})
    assert r.status_code == 303

    # cancel locked non-json
    token = set_session(client, unlocked=False)
    r = client.post("/assistant/action/cancel", data={"csrf": token, "action_id": "x"})
    assert r.status_code == 303

    # confirm non-json no pending
    token = set_session(client, unlocked=True, extra={})
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    r = client.post("/assistant/action/confirm", data={"csrf": csrf, "action_id": "x"}, headers={"HX-Request": "true"})
    assert r.status_code == 200

    # confirm mismatch non-json
    pending = {"id": "a1", "type": "demo", "summary": "Demo", "expires_at_ts": int(time.time()) + 600}
    token = set_session(client, unlocked=True, extra={ar.PENDING_ACTION_SESSION_KEY: dict(pending)})
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    r = client.post(
        "/assistant/action/confirm", data={"csrf": csrf, "action_id": "wrong"}, headers={"HX-Request": "true"}
    )
    assert r.status_code == 200

    # confirm success non-json and exception path
    token = set_session(
        client, unlocked=True, extra={ar.PENDING_ACTION_SESSION_KEY: dict(pending), "assistant_history": []}
    )
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    monkeypatch.setattr(ar, "execute_pending_action", lambda *a, **k: {"ok": True, "answer": "ok", "ui_actions": []})
    r = client.post("/assistant/action/confirm", data={"csrf": csrf, "action_id": "a1"}, headers={"HX-Request": "true"})
    assert r.status_code == 200

    token = set_session(
        client, unlocked=True, extra={ar.PENDING_ACTION_SESSION_KEY: dict(pending), "assistant_history": []}
    )
    page = client.get("/assistant")
    csrf = get_csrf(page.text)

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(ar, "execute_pending_action", boom)
    r = client.post(
        "/assistant/action/confirm", data={"csrf": csrf, "action_id": "a1"}, headers={"Accept": "application/json"}
    )
    assert r.status_code == 200 and r.json()["ok"] is False

    # cancel mismatch non-json, cancel success non-json, cancel no-pending non-json
    pending = {"id": "c1", "type": "demo", "summary": "Demo", "expires_at_ts": int(time.time()) + 600}
    token = set_session(
        client, unlocked=True, extra={ar.PENDING_ACTION_SESSION_KEY: dict(pending), "assistant_history": []}
    )
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    r = client.post("/assistant/action/cancel", data={"csrf": csrf, "action_id": "bad"}, headers={"HX-Request": "true"})
    assert r.status_code == 200
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    r = client.post("/assistant/action/cancel", data={"csrf": csrf, "action_id": "c1"}, headers={"HX-Request": "true"})
    assert r.status_code == 200
    page = client.get("/assistant")
    csrf = get_csrf(page.text)
    r = client.post("/assistant/action/cancel", data={"csrf": csrf}, headers={"HX-Request": "true"})
    assert r.status_code == 200
