from __future__ import annotations

import importlib
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.requests import Request

from tests.web.conftest import set_session


def _req(path="/", method="GET", headers=None, query_string=b""):
    hdrs = []
    for k, v in (headers or {}).items():
        hdrs.append((str(k).lower().encode(), str(v).encode()))
    scope = {
        "type": "http",
        "method": method,
        "path": path.split("?")[0],
        "query_string": query_string,
        "headers": hdrs,
    }
    return Request(scope)


def test_deps_import_nonlocal_requires_secret(monkeypatch):
    import ui.web.deps as deps
    old = os.environ.pop("PM_WEB_SECRET_KEY", None)
    old_host = os.environ.get("HOST")
    old_allow = os.environ.get("PM_WEB_ALLOW_INSECURE_DEV")
    try:
        os.environ["HOST"] = "0.0.0.0"
        os.environ.pop("PM_WEB_ALLOW_INSECURE_DEV", None)
        with pytest.raises(RuntimeError):
            importlib.reload(deps)
        # allowed override path (covers fallback branch lines incl. 38 and allow_insecure warning path)
        os.environ["PM_WEB_ALLOW_INSECURE_DEV"] = "1"
        importlib.reload(deps)
        assert deps.SECRET_KEY
    finally:
        if old is None:
            os.environ["PM_WEB_SECRET_KEY"] = "test-secret"
        else:
            os.environ["PM_WEB_SECRET_KEY"] = old
        if old_host is None:
            os.environ.pop("HOST", None)
        else:
            os.environ["HOST"] = old_host
        if old_allow is None:
            os.environ.pop("PM_WEB_ALLOW_INSECURE_DEV", None)
        else:
            os.environ["PM_WEB_ALLOW_INSECURE_DEV"] = old_allow
        importlib.reload(deps)


def test_deps_auth_rbac_and_redirect_helpers(monkeypatch):
    import ui.web.deps as deps

    # _client_ip (411-412+) and fallbacks
    req = _req()
    req.scope["client"] = ("1.2.3.4", 1234)
    assert deps._client_ip(req) == "1.2.3.4"
    req2 = _req()
    req2.scope["client"] = None
    assert deps._client_ip(req2) == "unknown"

    # require_authenticated_user strict mode denies anonymous (314-315)
    monkeypatch.setenv("PM_WEB_AUTH_MODE", "strict")
    req3 = _req()
    deps.get_session(req3)
    with pytest.raises(HTTPException) as ei:
        deps.require_authenticated_user(req3)
    assert ei.value.status_code == 403
    monkeypatch.setenv("PM_WEB_AUTH_MODE", "legacy")

    # has_permission: empty permission allowed path (349)
    req4 = _req()
    deps.get_session(req4)
    assert deps.has_permission(req4, "") is True

    # require_unlock GET/HEAD preserves path+query (495-497)
    req5 = _req(path="/secret", query_string=b"a=1&b=2")
    deps.get_session(req5)
    with pytest.raises(HTTPException) as ei:
        deps.require_unlock(req5)
    assert ei.value.status_code == 303
    loc = ei.value.headers.get("Location") or ""
    assert loc.startswith("/unlock?next=")
    assert "secret%3Fa%3D1%26b%3D2" in loc or "secret%3Fa%3D1%26b%3D2" in loc.replace("%2F", "/")

    # require_unlock unsafe method uses same-origin referer (510-511)
    req6 = _req(path="/do", method="POST", headers={"referer": "http://testserver/from?x=1"})
    req6.scope["scheme"] = "http"
    req6.scope["server"] = ("testserver", 80)
    deps.get_session(req6)
    with pytest.raises(HTTPException) as ei:
        deps.require_unlock(req6)
    loc = ei.value.headers.get("Location") or ""
    assert loc.startswith("/unlock?next=")
    assert "from%3Fx%3D1" in loc or "/from%3Fx%3D1" in loc

    # unlock backend alignment exception swallowed (539-540) - explicit
    monkeypatch.setattr(deps.services, "verify_password", lambda p: True)
    monkeypatch.setattr(deps.services, "unlock_session", lambda p: (_ for _ in ()).throw(RuntimeError("x")))
    req7 = _req()
    deps.get_session(req7)
    assert deps.unlock(req7, "ok") is True


def test_server_middleware_http_exception_and_unhandled_paths(web_env):
    from ui.web.server import create_app

    app = create_app(init_db=False, run_startup=False)

    @app.get("/__raise_http")
    def raise_http():
        raise HTTPException(status_code=418, detail="teapot")

    @app.get("/__raise_err_html")
    def raise_err_html():
        raise RuntimeError("boom-html")

    @app.get("/__raise_err_json")
    def raise_err_json():
        raise RuntimeError("boom-json")

    @app.get("/__preset_cookie")
    def preset_cookie():
        # middleware should detect existing session cookie in raw headers (195-196 path)
        resp = JSONResponse({"ok": True})
        # set cookie using deps-compatible name; simplest via header
        resp.set_cookie("pm_session", "preset")
        return resp

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/__raise_http")
        assert r.status_code == 418
        assert "teapot" in r.text

        r = c.get("/__raise_err_html", headers={"accept": "text/html"})
        assert r.status_code == 500

        r = c.get("/__raise_err_json", headers={"accept": "application/json"})
        assert r.status_code == 500
        assert r.json().get("error_ref")

        r = c.get("/__preset_cookie")
        assert r.status_code == 200
        # ensure endpoint works; duplicate-cookie avoidance is internal behavior
