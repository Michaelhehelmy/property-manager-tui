from __future__ import annotations

import time
from tests.web.conftest import get_csrf, set_session


def _patch_assistant(monkeypatch):
    import ui.web.routes.assistant as ar
    monkeypatch.setattr(ar.assistant, "get_llm_config", lambda: {"provider": "auto", "response_mode": "talking", "local_available": False, "enabled": False, "external": False})
    monkeypatch.setattr(ar.assistant, "INTENT_LABELS", {"capabilities_help": "Help"}, raising=False)
    monkeypatch.setattr(ar.services, "get_setting", lambda k, d=None: "true" if k == "assistant_require_unlock" else d)
    return ar


def test_assistant_routes_more_edges(client, monkeypatch):
    ar = _patch_assistant(monkeypatch)

    # _get_request_data: JSON content-type but non-dict payload -> treated as {}
    token = set_session(client, unlocked=True)
    # send JSON list
    r = client.post('/assistant/ask', content='[1,2,3]', headers={'Content-Type': 'application/json', 'Accept': 'application/json', 'Cookie': f'pm_session={token}'})
    # need valid csrf? middleware enforces csrf for JSON via header; easier to include header token from session
    # build cookie with known csrf
    token = set_session(client, unlocked=True, csrf='tok')
    r = client.post('/assistant/ask', content='[1,2,3]', headers={'Content-Type': 'application/json', 'Accept': 'application/json', 'x-csrf-token': 'tok'})
    # may be 200 with ok false because message missing or 400? should pass parsing
    assert r.status_code in (200, 400)

    # ask with empty message (HTML partial path)
    monkeypatch.setattr(ar.assistant, "chat", lambda *a, **k: {"ok": True, "answer": "x", "intent": "i", "confidence": 1, "tools_used": [], "source": "s", "ui_actions": []})
    page = client.get('/assistant'); csrf = get_csrf(page.text)
    r = client.post('/assistant/ask', data={'csrf': csrf, 'message': '   '})
    assert r.status_code in (200, 303)

    # locked non-json confirm/cancel paths
    token = set_session(client, unlocked=False)
    r = client.post('/assistant/action/confirm', data={'csrf': token, 'action_id': 'x'})
    assert r.status_code in (200, 303)
    r = client.post('/assistant/action/cancel', data={'csrf': token, 'action_id': 'x'})
    assert r.status_code in (200, 303)

    # unlocked, no pending confirm
    token = set_session(client, unlocked=True, csrf='tok2')
    r = client.post('/assistant/action/confirm', data={'csrf': 'tok2', 'action_id': 'x'}, headers={'Accept': 'application/json'})
    assert r.status_code == 200 and r.json().get('ok') is False

    # pending mismatch confirm/cancel
    pending = {'id': 'a1', 'type': 't', 'summary': 's', 'args': {}, 'resolved': {}, 'preview': {}, 'expires_at_ts': int(time.time()) + 600}
    token = set_session(client, unlocked=True, csrf='tok3', extra={ar.PENDING_ACTION_SESSION_KEY: pending})
    r = client.post('/assistant/action/confirm', data={'csrf': 'tok3', 'action_id': 'wrong'}, headers={'Accept': 'application/json'})
    assert r.status_code == 200 and r.json().get('ok') is False
    r = client.post('/assistant/action/cancel', data={'csrf': 'tok3', 'action_id': 'wrong'}, headers={'Accept': 'application/json'})
    assert r.status_code == 200 and r.json().get('ok') is False

    # confirm success path with execute returning non-dict and string/exception paths
    pending = {'id': 'a2', 'type': 't', 'summary': 's', 'args': {}, 'resolved': {}, 'preview': {}, 'expires_at_ts': int(time.time()) + 600}
    token = set_session(client, unlocked=True, csrf='tok4', extra={ar.PENDING_ACTION_SESSION_KEY: pending})
    monkeypatch.setattr(ar, 'execute_pending_action', lambda *a, **k: 'done-str')
    r = client.post('/assistant/action/confirm', data={'csrf': 'tok4', 'action_id': 'a2'}, headers={'Accept': 'application/json'})
    assert r.status_code == 200 and r.json().get('ok') is True

    pending = {'id': 'a3', 'type': 't', 'summary': 's', 'args': {}, 'resolved': {}, 'preview': {}, 'expires_at_ts': int(time.time()) + 600}
    token = set_session(client, unlocked=True, csrf='tok5', extra={ar.PENDING_ACTION_SESSION_KEY: pending})
    monkeypatch.setattr(ar, 'execute_pending_action', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
    r = client.post('/assistant/action/confirm', data={'csrf': 'tok5', 'action_id': 'a3'}, headers={'Accept': 'application/json'})
    assert r.status_code == 200 and r.json().get('ok') is False

    # non-terminal failure keeps pending? terminal False and ok False
    pending = {'id': 'a4', 'type': 't', 'summary': 's', 'args': {}, 'resolved': {}, 'preview': {}, 'expires_at_ts': int(time.time()) + 600}
    token = set_session(client, unlocked=True, csrf='tok6', extra={ar.PENDING_ACTION_SESSION_KEY: pending})
    monkeypatch.setattr(ar, 'execute_pending_action', lambda *a, **k: {'ok': False, 'terminal': False, 'answer': 'retry'})
    r = client.post('/assistant/action/confirm', data={'csrf': 'tok6', 'action_id': 'a4'}, headers={'Accept': 'application/json'})
    assert r.status_code == 200 and r.json().get('ok') is False

    # cancel with pending success and no pending
    pending = {'id': 'a5', 'type': 't', 'summary': 's', 'args': {}, 'resolved': {}, 'preview': {}, 'expires_at_ts': int(time.time()) + 600}
    token = set_session(client, unlocked=True, csrf='tok7', extra={ar.PENDING_ACTION_SESSION_KEY: pending})
    r = client.post('/assistant/action/cancel', data={'csrf': 'tok7', 'action_id': 'a5'}, headers={'Accept': 'application/json'})
    assert r.status_code == 200 and r.json().get('ok') is True
    token = set_session(client, unlocked=True, csrf='tok8')
    r = client.post('/assistant/action/cancel', data={'csrf': 'tok8'}, headers={'Accept': 'application/json'})
    assert r.status_code == 200 and r.json().get('ok') is True

    # clear
    token = set_session(client, unlocked=True, csrf='tok9', extra={ar.PENDING_ACTION_SESSION_KEY: {'id': 'x', 'type': 't', 'expires_at_ts': int(time.time())+100}, 'assistant_history': [{'role':'user','content':'hi'}]})
    r = client.post('/assistant/clear', data={'csrf': 'tok9'})
    assert r.status_code in (200, 303)

    # feedback with bad corrected_slots json and missing msg index
    token = set_session(client, unlocked=True, csrf='tok10', extra={'assistant_history': [{'role':'assistant','content':'a','intent':'i','confidence':1,'tools_used':[],'slots':{},'source':'s','user_message':'q'}]})
    monkeypatch.setattr(ar.assistant, 'record_feedback', lambda **k: 1)
    r = client.post('/assistant/feedback', data={'csrf': 'tok10', 'message_index': '999', 'corrected_slots_json': '{bad', 'corrected_intent': 'x', 'rating': '1'})
    assert r.status_code in (200, 303)
    r = client.post('/assistant/feedback', data={'csrf': 'tok10', 'message_index': '0', 'corrected_slots_json': '[1,2]', 'corrected_intent': 'x', 'rating': '1'})
    assert r.status_code in (200, 303)

    # _normalize_pending_action helper branches via module
    assert ar._normalize_pending_action(None) is None
    monkeypatch.setattr(ar, 'create_proposal', lambda action: (_ for _ in ()).throw(RuntimeError('x')))
    out = ar._normalize_pending_action({'foo': 'bar'})
    assert isinstance(out, dict) and out.get('foo') == 'bar'
    out2 = ar._normalize_pending_action({'id': 'i', 'type': 't', 'expires_at_ts': 'bad'})
    assert out2['expires_at_ts'] > 0

    # get_pending_action expired with clear_if_expired False
    # build a fake request object is easier via set_session state in integration, but we can use app's dependency behavior by hitting endpoint
    expired = {'id': 'old2', 'type': 'x', 'expires_at_ts': int(time.time()) - 10}
    token = set_session(client, unlocked=True, csrf='tok11', extra={ar.PENDING_ACTION_SESSION_KEY: expired})
    # calling get_pending_action through an endpoint that reads it without clearing in a specific way is indirect; but clear_if_expired False branch is internal
    # exercise via direct call with constructed request
    from starlette.requests import Request
    scope = {'type':'http','method':'GET','path':'/','headers':[], 'app': client.app}
    req = Request(scope)
    req.state.session = {ar.PENDING_ACTION_SESSION_KEY: {'id':'z','type':'t','expires_at_ts': int(time.time())-5}}
    assert ar.get_pending_action(req, clear_if_expired=False) is None
    assert ar.PENDING_ACTION_SESSION_KEY in req.state.session
