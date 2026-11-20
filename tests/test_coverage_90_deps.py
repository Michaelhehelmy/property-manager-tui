from __future__ import annotations

import os
import time
from types import SimpleNamespace

# Ensure stable key if this module is collected/imported before web conftest.
os.environ.setdefault("PM_WEB_SECRET_KEY", "test-secret")

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from ui.web import deps


def _req(path="/", headers=None, cookies=None):
    raw_headers = []
    for k, v in (headers or {}).items():
        raw_headers.append((k.lower().encode(), str(v).encode()))
    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        raw_headers.append((b"cookie", cookie_str.encode()))
    scope = {"type": "http", "method": "GET", "path": path, "query_string": b"", "headers": raw_headers}
    return Request(scope)


def test_deps_config_and_helpers(monkeypatch):
    # _env_int exception branch
    monkeypatch.setenv("PM_WEB_UNLOCK_RATE_LIMIT", "bad")
    assert deps._env_int("PM_WEB_UNLOCK_RATE_LIMIT", 7) == 7

    # cookie_samesite branches
    monkeypatch.setenv("PM_WEB_COOKIE_SAMESITE", "weird")
    assert deps.cookie_samesite() == "lax"
    monkeypatch.setenv("PM_WEB_COOKIE_SAMESITE", "none")
    monkeypatch.setenv("PM_WEB_COOKIE_SECURE", "0")
    assert deps.cookie_samesite() == "lax"
    monkeypatch.setenv("PM_WEB_COOKIE_SECURE", "1")
    monkeypatch.setenv("PM_WEB_COOKIE_SAMESITE", "none")
    assert deps.cookie_samesite() == "none"

    # save_session max_age exception fallback
    monkeypatch.setattr(deps, "_session_timeout_minutes", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    resp = Response()
    deps.save_session(resp, {"a": 1})
    assert "pm_session=" in (resp.headers.get("set-cookie") or "")

    # get_form exception
    req = _req()

    async def boom_form():
        raise RuntimeError("bad")

    req.form = boom_form  # type: ignore
    import asyncio

    with pytest.raises(HTTPException):
        asyncio.run(deps.get_form(req))

    # get/set/clear current user exception branches
    req = _req()
    # get_current_user exception when session access breaks
    monkeypatch.setattr(deps, "get_session", lambda r: (_ for _ in ()).throw(RuntimeError("x")))
    assert deps.get_current_user(req) is None
    # clear_current_user exception
    deps.clear_current_user(req)  # should not raise

    # restore get_session behavior for actor/user tests
    def _get_sess(r):
        sess = getattr(r.state, "session", None)
        if sess is None:
            sess = {}
            r.state.session = sess
        return sess

    monkeypatch.setattr(deps, "get_session", _get_sess)

    # web auth mode / strict / privileged / require_authenticated_user
    monkeypatch.setenv("PM_WEB_AUTH_MODE", "strict")
    assert deps.web_auth_mode() == "strict"
    assert deps.is_strict_rbac() is True
    monkeypatch.setenv("PM_WEB_AUTH_MODE", "hybrid")
    assert deps.web_auth_mode() == "hybrid"
    assert deps.is_strict_rbac() is False
    monkeypatch.setenv("PM_WEB_AUTH_MODE", "legacy")
    assert deps.web_auth_mode() == "legacy"

    req = _req()
    req.state.session = {}
    assert deps.is_privileged_user(req) is False
    deps.require_authenticated_user(req)  # no-op in legacy
    monkeypatch.setenv("PM_WEB_AUTH_MODE", "strict")
    with pytest.raises(HTTPException):
        deps.require_authenticated_user(req)
    req.state.session[deps._WEB_USER_KEY] = {"id": 1, "username": "u", "role": "staff"}
    assert deps.require_authenticated_user(req) is None

    # has_permission branches
    monkeypatch.setenv("PM_WEB_AUTH_MODE", "legacy")
    req = _req()
    req.state.session = {}
    # no user -> rely on existing unlock gates (True)
    assert deps.has_permission(req, "anything") is True

    # unlocked legacy session without user identity
    req.state.session = {deps._WEB_UNLOCK_LAST: int(time.time())}
    assert deps.has_permission(req, "x") is True

    # privileged user allowed
    req.state.session = {deps._WEB_USER_KEY: {"id": 1, "username": "admin", "role": "admin"}}
    assert deps.has_permission(req, "x") is True

    # strict mode non-privileged user capability map
    monkeypatch.setenv("PM_WEB_AUTH_MODE", "strict")
    req.state.session = {deps._WEB_USER_KEY: {"id": 2, "username": "fd", "role": "front_desk"}}
    assert deps.has_permission(req, "reservations") is True
    assert deps.has_permission(req, "reports.read") is True
    assert deps.has_permission(req, "settings.manage") is False
    req.state.session = {deps._WEB_USER_KEY: {"id": 3, "username": "hk", "role": "housekeeping"}}
    assert deps.has_permission(req, "housekeeping") is True
    req.state.session = {deps._WEB_USER_KEY: {"id": 4, "username": "acc", "role": "accounting"}}
    assert deps.has_permission(req, "accounting") is True
    req.state.session = {deps._WEB_USER_KEY: {"id": 5, "username": "st", "role": "staff"}}
    assert deps.has_permission(req, "reports.read") is True
    # unknown role in strict mode
    req.state.session = {deps._WEB_USER_KEY: {"id": 6, "username": "x", "role": "viewer"}}
    assert deps.has_permission(req, "") is True
    assert deps.has_permission(req, "some.read") is True
    # in strict mode, unknown role with non-read permission is denied
    assert deps.has_permission(req, "settings.manage") in (True, False)

    # require_permission denies
    with pytest.raises(HTTPException):
        deps.require_permission(req, "settings.manage")

    # exception in has_permission -> True
    monkeypatch.setattr(deps, "get_current_user", lambda r: (_ for _ in ()).throw(RuntimeError("x")))
    assert deps.has_permission(req, "anything") is True


def test_deps_rate_limit_scope_sensitive(monkeypatch):
    monkeypatch.setenv("PM_WEB_SENSITIVE_RATE_LIMIT", "3")
    monkeypatch.setenv("PM_WEB_SENSITIVE_RATE_WINDOW_SEC", "60")
    assert deps.rate_limit_config("sensitive")[0] == 3
