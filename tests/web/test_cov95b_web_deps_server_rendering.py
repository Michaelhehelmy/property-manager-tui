from __future__ import annotations

import os
import types
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from starlette.requests import Request

from ui.web import deps


def _req(path="/", method="GET", headers=None, query_string=b"", cookies=None):
    raw_headers = []
    for k, v in (headers or {}).items():
        raw_headers.append((str(k).lower().encode(), str(v).encode()))
    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        raw_headers.append((b"cookie", cookie_str.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path.split("?")[0],
        "raw_path": path.split("?")[0].encode(),
        "query_string": query_string,
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


def test_deps_privilege_and_rbac_misc_branches(monkeypatch):
    # is_privileged_user role handling (305,306)
    req = _req()
    deps.get_session(req)[deps._WEB_USER_KEY] = {"id": 1, "username": "admin", "role": "admin"}
    assert deps.is_privileged_user(req) is True
    deps.get_session(req)[deps._WEB_USER_KEY] = {"id": 2, "username": "staff", "role": "staff"}
    assert deps.is_privileged_user(req) is False

    # has_permission legacy/hybrid permissive for logged-in non-privileged user (340)
    monkeypatch.setenv("PM_WEB_AUTH_MODE", "legacy")
    req2 = _req()
    deps.get_session(req2)[deps._WEB_USER_KEY] = {"id": 3, "username": "s", "role": "staff"}
    assert deps.has_permission(req2, "settings.manage") is True

    # has_privileged_access hybrid/strict + privileged user (486)
    monkeypatch.setenv("PM_WEB_AUTH_MODE", "hybrid")
    req3 = _req()
    deps.get_session(req3)[deps._WEB_USER_KEY] = {"id": 4, "username": "mgr", "role": "manager"}
    assert deps.has_privileged_access(req3) is True
    monkeypatch.delenv("PM_WEB_AUTH_MODE", raising=False)


def test_deps_unlock_expiry_parse_and_sync_lock_exceptions(monkeypatch):
    # is_unlocked invalid last timestamp parse exception (400-403)
    req = _req()
    deps.get_session(req)[deps._WEB_UNLOCK_LAST] = "not-an-int"
    assert deps.is_unlocked(req) is False
    assert deps._WEB_UNLOCK_LAST not in deps.get_session(req)

    # sync_backend_session_if_possible exception swallowed (525,526)
    monkeypatch.setattr(deps.services, "touch_session", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    deps.sync_backend_session_if_possible()

    # lock services.lock_session exception swallowed (554,555)
    req2 = _req()
    deps.get_session(req2)[deps._WEB_UNLOCK_LAST] = "123"
    monkeypatch.setattr(deps.services, "lock_session", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    deps.lock(req2)
    assert deps.is_unlocked(req2) is False


def test_deps_require_unlock_redirect_edge_cases(monkeypatch):
    # GET with path-like value that normalizes to unsafe target -> sanitization fallback (515)
    req = _req(path="//evil", method="GET")
    req.scope["path"] = "//evil"
    req.scope["raw_path"] = b"//evil"

    # Recreate request so .url reflects scope path
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    req = Request(req.scope, receive)
    deps.get_session(req)
    with pytest.raises(HTTPException) as ei:
        deps.require_unlock(req)
    assert ei.value.status_code == 303
    assert ei.value.headers.get("Location") == "/unlock?next=/"

    # POST with referer parsing exception -> nxt_raw "/" (512,513)
    req2 = _req(path="/do", method="POST", headers={"referer": "http://testserver/from"})
    req2.scope["server"] = ("testserver", 80)
    deps.get_session(req2)
    monkeypatch.setattr("urllib.parse.urlsplit", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")))
    with pytest.raises(HTTPException) as ei:
        deps.require_unlock(req2)
    assert ei.value.status_code == 303
    assert ei.value.headers.get("Location", "").endswith("next=/")


def test_deps_safe_download_and_redirect_branches(tmp_path, monkeypatch):
    export_dir = tmp_path / "exports"
    invoice_dir = tmp_path / "invoices"
    export_dir.mkdir()
    invoice_dir.mkdir()
    f = export_dir / "ok.txt"
    f.write_text("x", encoding="utf-8")
    monkeypatch.setattr(deps.paths, "EXPORT_DIR", export_dir)
    monkeypatch.setattr(deps.paths, "INVOICE_DIR", invoice_dir)

    # invalid kind (568)
    with pytest.raises(HTTPException) as ei:
        deps.safe_download_path("other", "ok.txt")
    assert ei.value.status_code == 400

    # commonpath mismatch branch (583,584)
    real_commonpath = os.path.commonpath

    def bad_commonpath(paths):
        # force mismatch for our case
        if any("exports" in str(p) for p in paths):
            return "/definitely-different"
        return real_commonpath(paths)

    monkeypatch.setattr(os.path, "commonpath", bad_commonpath)
    with pytest.raises(HTTPException) as ei:
        deps.safe_download_path("export", "ok.txt")
    assert ei.value.status_code == 400
    monkeypatch.setattr(os.path, "commonpath", real_commonpath)

    # outer exception fallback (586)
    monkeypatch.setattr(Path, "relative_to", lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("rel")))
    with pytest.raises(HTTPException) as ei:
        deps.safe_download_path("export", "ok.txt")
    assert ei.value.status_code == 400

    # redirect branches including query/fragment (591,598,605,609-614)
    r = deps.redirect("/path?q=1#frag")
    assert r.status_code == 303
    assert r.headers["location"] == "/path?q=1#frag"


def test_rendering_update_notice_and_fallback_branches(web_app, monkeypatch):
    from ui.web import rendering

    # _update_notice_key branches (34-44)
    assert rendering._update_notice_key({"latest_release": {"tag_name": "v1.2.3"}}) == "release:v1.2.3"
    assert rendering._update_notice_key({"message": "hello"}) == "msg:hello"
    assert rendering._update_notice_key({"url": "https://x/y"}) == "url:https://x/y"

    class BadNotice(dict):
        def get(self, key, default=None):
            if key == "git":
                return {}
            if key == "latest_release":
                return {}
            raise RuntimeError("boom")

    assert rendering._update_notice_key(BadNotice()) == ""
    assert rendering._update_notice_key({}) == ""

    # render fallback when session access raises during modal computation (64,65)
    scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b"", "app": web_app}
    req = Request(scope)

    class BadSession(dict):
        def get(self, key, default=None):
            if key == "dismissed_update_key":
                raise RuntimeError("sess")
            return super().get(key, default)

    req.state.session = BadSession({"csrf": "abc", "unlocked": False, "flashes": []})
    notice = {"update_available": True}
    monkeypatch.setattr("app_core.updater.get_cached_update_notice", lambda *a, **k: notice)
    resp = rendering.render(req, "unlock.html", {"title": "X", "next": "/"})
    assert resp.status_code == 200
    assert isinstance(resp, HTMLResponse)


def test_server_middleware_exception_and_probe_branches(web_env, monkeypatch):
    from ui.web.server import create_app

    # 144,151 HTTPException re-raised/logged path
    app_http = create_app(init_db=False, run_startup=False)

    @app_http.get("/__http_exc_only")
    def _http_exc_only():
        raise HTTPException(status_code=418, detail="teapot")

    with TestClient(app_http, raise_server_exceptions=False) as c:
        r = c.get("/__http_exc_only")
        assert r.status_code == 418

    # 192,193: actor reset exception swallowed in finally
    app = create_app(init_db=False, run_startup=False)
    monkeypatch.setattr("ui.web.server.services.set_current_actor", lambda *a, **k: "tok")
    monkeypatch.setattr(
        "ui.web.server.services.reset_current_actor", lambda tok: (_ for _ in ()).throw(RuntimeError("reset"))
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/health")
        assert r.status_code == 200

    # 157,158 redact fallback, 182,183 render fallback
    app2 = create_app(init_db=False, run_startup=False)

    @app2.get("/__http_exc")
    def _http_exc():
        raise HTTPException(status_code=418, detail="teapot")

    @app2.get("/__err_redact")
    def _err_redact():
        raise RuntimeError("boom")

    @app2.get("/__err_render")
    def _err_render():
        raise RuntimeError("boom2")

    # redact fallback (157,158): force redact_sensitive to raise
    import ui.web.server as srv

    monkeypatch.setattr(srv.paths, "redact_sensitive", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("redact")))
    with TestClient(app2, raise_server_exceptions=False) as c:
        r = c.get("/__http_exc")
        assert r.status_code == 418

        # make 500 html rendering fail to hit 182,183
        import ui.web.rendering as rendering_mod

        real_render = rendering_mod.render

        def bad_render(*a, **k):
            raise RuntimeError("render-fail")

        monkeypatch.setattr(rendering_mod, "render", bad_render)
        r = c.get("/__err_render", headers={"accept": "text/html"})
        assert r.status_code == 500
        assert r.headers.get("content-type", "").startswith("application/json")
        # redact fallback path still exercised on previous/other exceptions; call json accept too
        monkeypatch.setattr(rendering_mod, "render", real_render)
        r = c.get("/__err_redact", headers={"accept": "application/json"})
        assert r.status_code == 500
        assert r.json().get("error_ref")

    # 256,261 probes
    app3 = create_app(init_db=False, run_startup=False)
    with TestClient(app3, raise_server_exceptions=False, follow_redirects=False) as c:
        r = c.get("/favicon.ico")
        assert r.status_code in (307, 302)
        assert "/static/icons/icon-192.png" in (r.headers.get("location") or "")
        r = c.get("/v1/models")
        assert r.status_code == 404
        assert "error" in r.json()
