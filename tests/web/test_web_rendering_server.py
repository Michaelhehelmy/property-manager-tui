from __future__ import annotations

import pathlib

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from ui.web import deps
from ui.web.rendering import render
from ui.web.server import create_app


def test_render_saves_session_and_ctx(web_app, client):
    # hit a real page so templates/middleware work
    client.cookies.set(deps.SESSION_COOKIE, deps._serializer.dumps({"csrf": "tok123", "unlocked": False, "flashes": [{"level":"info","message":"hi"}]}))
    r = client.get("/unlock")
    assert r.status_code == 200
    assert "Unlock" in r.text
    assert "hi" in r.text  # flash popped/rendered


def test_render_direct_unit(web_app):
    # unit-call render with a synthetic request that has app.state.templates
    scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b"", "app": web_app}
    req = Request(scope)
    req.state.session = {"csrf": "abc", "unlocked": True, "flashes": []}
    resp = render(req, "unlock.html", {"title": "X", "next": "/"})
    assert isinstance(resp, HTMLResponse)
    assert resp.status_code == 200
    assert deps.SESSION_COOKIE in resp.headers.get("set-cookie", "")


def test_create_app_init_branches(web_env, monkeypatch):
    from app_core import paths, services
    calls = []
    monkeypatch.setattr(paths, "ensure_data_dirs", lambda: calls.append("dirs"))
    monkeypatch.setattr(services, "init_db", lambda: calls.append("init"))
    monkeypatch.setattr(services, "set_language", lambda x: calls.append(("lang", x)))
    monkeypatch.setattr(services, "get_setting", lambda k, d=None: "fr")
    monkeypatch.setattr(services, "get_app_display_name", lambda: "PM")
    app = create_app(init_db=True, run_startup=True)
    assert ("dirs" in calls) and ("init" in calls)
    calls.clear()
    app2 = create_app(init_db=False, run_startup=False)
    assert calls == []


def test_middleware_sets_cookie_when_missing(web_env):
    app = create_app(init_db=False, run_startup=False)

    @app.get("/_plain")
    def plain():
        return {"ok": True}

    with TestClient(app) as c:
        r = c.get("/_plain")
        assert r.status_code == 200
        assert deps.SESSION_COOKIE in r.headers.get("set-cookie", "")


def test_middleware_skips_when_cookie_already_set(web_env):
    from fastapi.responses import JSONResponse
    app = create_app(init_db=False, run_startup=False)

    @app.get("/_preset")
    def preset():
        resp = JSONResponse({"ok": True})
        deps.save_session(resp, {"csrf": "x", "unlocked": False, "flashes": []})
        return resp

    with TestClient(app) as c:
        r = c.get("/_preset")
        # one pm_session cookie set by endpoint; middleware should not add another
        cookies = [v for (k, v) in r.headers.raw if k.lower() == b"set-cookie"]
        pm = [v for v in cookies if b"pm_session=" in v]
        assert len(pm) == 1


def test_static_dir_missing_branch(web_env, monkeypatch):
    orig_exists = pathlib.Path.exists
    def fake_exists(self):
        if self.name == "static" and "ui/web" in str(self).replace("\\", "/"):
            return False
        return orig_exists(self)
    monkeypatch.setattr(pathlib.Path, "exists", fake_exists)
    app = create_app(init_db=False, run_startup=False)
    # /static not mounted; app still works
    assert app is not None


def test_health_probes(web_env):
    app = create_app(init_db=False, run_startup=False)
    with TestClient(app) as c:
        for path in ("/health", "/healthz"):
            r = c.get(path)
            assert r.status_code == 200
            data = r.json()
            assert data.get("ok") is True
            assert data.get("status") == "healthy"

import importlib
import sys

def test_server_sys_path_insert_branch(web_env, monkeypatch):
    import ui.web.server as srv
    src = str((pathlib.Path(srv.__file__).resolve().parents[2] / "src"))
    # ensure branch where SRC not in sys.path is executed
    removed = False
    if src in sys.path:
        sys.path.remove(src)
        removed = True
    try:
        importlib.reload(srv)
        assert src in sys.path
    finally:
        # keep environment sane
        if src not in sys.path:
            sys.path.insert(0, src)
        importlib.reload(srv)
