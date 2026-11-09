from __future__ import annotations

import os
import secrets
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from itsdangerous import URLSafeSerializer
from starlette.requests import Request

from ui.web import deps


def _make_request(cookie=None, path="/x?y=1"):
    headers = []
    if cookie is not None:
        headers.append((b"cookie", f"{deps.SESSION_COOKIE}={cookie}".encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": path.split("?")[0],
        "query_string": path.split("?", 1)[1].encode() if "?" in path else b"",
        "headers": headers,
    }
    req = Request(scope)
    return req


def test_load_session_variants(monkeypatch):
    req = _make_request()
    assert deps._load_session(req) == {}

    req = _make_request("bad-signature")
    assert deps._load_session(req) == {}

    ser = URLSafeSerializer(os.environ["PM_WEB_SECRET_KEY"], salt="pm-session")
    req = _make_request(ser.dumps(["not", "dict"]))
    assert deps._load_session(req) == {}

    req = _make_request(ser.dumps({"a": 1}))
    assert deps._load_session(req) == {"a": 1}


def test_session_cache_and_helpers():
    req = _make_request()
    s1 = deps.get_session(req)
    s2 = deps.get_session(req)
    assert s1 is s2
    deps.add_flash(req, "m1")
    deps.add_flash(req, "m2", "error")
    for i in range(8):
        deps.add_flash(req, f"x{i}")
    assert len(deps.get_session(req)["flashes"]) == 6
    flashes = deps.pop_flashes(req)
    assert flashes and flashes[-1]["message"] == "x7"
    assert deps.get_session(req)["flashes"] == []


def test_csrf_token_generate_and_reuse():
    req = _make_request()
    t1 = deps.csrf_token(req)
    t2 = deps.csrf_token(req)
    assert t1 and t1 == t2
    deps.verify_csrf(req, t1)
    with pytest.raises(HTTPException) as ei:
        deps.verify_csrf(req, "wrong")
    assert ei.value.status_code == 400
    req2 = _make_request()
    with pytest.raises(HTTPException):
        deps.verify_csrf(req2, None)
    req3 = _make_request()
    req3.state.session = {"csrf": "abc"}
    with pytest.raises(HTTPException):
        deps.verify_csrf(req3, None)


def test_unlock_lock_require_unlock(monkeypatch):
    req = _make_request(path="/secret/page")
    assert deps.is_unlocked(req) is False
    with pytest.raises(HTTPException) as ei:
        deps.require_unlock(req)
    assert ei.value.status_code == 303
    assert "/unlock?next=" in ei.value.headers["Location"]

    # unlock success, unlock_session returns True
    monkeypatch.setattr(deps.services, "verify_password", lambda p: p == "ok")
    monkeypatch.setattr(deps.services, "unlock_session", lambda p: True)
    assert deps.unlock(req, "ok") is True
    assert deps.is_unlocked(req) is True

    # unlock success, unlock_session returns False -> touch_session
    req2 = _make_request()
    touched = []
    monkeypatch.setattr(deps.services, "unlock_session", lambda p: False)
    monkeypatch.setattr(deps.services, "touch_session", lambda: touched.append(1))
    assert deps.unlock(req2, "ok") is True
    assert touched == [1]

    # unlock success but backend alignment raises
    req3 = _make_request()
    monkeypatch.setattr(deps.services, "unlock_session", lambda p: (_ for _ in ()).throw(RuntimeError("x")))
    assert deps.unlock(req3, "ok") is True

    # unlock fail
    monkeypatch.setattr(deps.services, "verify_password", lambda p: False)
    assert deps.unlock(_make_request(), "bad") is False

    deps.lock(req)
    assert deps.is_unlocked(req) is False


def test_save_session_and_redirect():
    class Resp:
        def __init__(self):
            self.cookies = []
        def set_cookie(self, *a, **k):
            self.cookies.append((a, k))

    r = Resp()
    deps.save_session(r, {"a": 1})
    assert r.cookies and r.cookies[0][0][0] == deps.SESSION_COOKIE

    red = deps.redirect("/x")
    assert red.status_code == 303
    assert red.headers["location"] == "/x"
    red2 = deps.redirect("/y", status=302)
    assert red2.status_code == 302


def test_safe_download_path(tmp_path, monkeypatch):
    export_dir = tmp_path / "exports"
    invoice_dir = tmp_path / "invoices"
    export_dir.mkdir()
    invoice_dir.mkdir()
    (export_dir / "a.txt").write_text("x", encoding="utf-8")
    (invoice_dir / "b.txt").write_text("y", encoding="utf-8")
    monkeypatch.setattr(deps.paths, "EXPORT_DIR", export_dir)
    monkeypatch.setattr(deps.paths, "INVOICE_DIR", invoice_dir)

    assert deps.safe_download_path("export", "a.txt").name == "a.txt"
    assert deps.safe_download_path("invoice", "b.txt").name == "b.txt"

    for bad in ["", "../x", "a/b", "a\\b", "..\\x"]:
        with pytest.raises(HTTPException):
            deps.safe_download_path("export", bad)

    with pytest.raises(HTTPException) as ei:
        deps.safe_download_path("export", "missing.txt")
    assert ei.value.status_code == 404

    # wrong base / traversal-like resolved path (simulate by making name valid but path escape unlikely);
    # test base check branch by monkeypatching resolve behavior? easier: use a symlink-like case not portable.
    # Instead hit base comparison by pointing base to another dir and requesting existing file there? no, name has no /.
    # The base check line: if base.resolve() not in p.parents and p != base.resolve(): raise
    # For normal file inside base, p.parent is base so condition false. Hard to trigger without filesystem tricks.
    # We'll cover it by monkeypatching Path.resolve for the constructed path object.
    real_resolve = Path.resolve

    def fake_resolve(self):
        if self.name == "escape.txt":
            return Path("/etc/passwd")
        return real_resolve(self)

    (export_dir / "escape.txt").write_text("z", encoding="utf-8")
    monkeypatch.setattr(Path, "resolve", fake_resolve)
    with pytest.raises(HTTPException):
        deps.safe_download_path("export", "escape.txt")

import importlib
import io
import sys as _sys

def test_deps_import_warning_when_env_missing(monkeypatch):
    # cover import-time warning branch by reloading module without env var
    old = os.environ.pop("PM_WEB_SECRET_KEY", None)
    err = io.StringIO()
    old_stderr = _sys.stderr
    try:
        _sys.stderr = err
        importlib.reload(deps)
        assert "WARNING" in err.getvalue()
        assert deps.SECRET_KEY == "pm-web-local-dev-secret-change-me"
    finally:
        _sys.stderr = old_stderr
        if old is None:
            os.environ["PM_WEB_SECRET_KEY"] = "test-secret"
        else:
            os.environ["PM_WEB_SECRET_KEY"] = old
        importlib.reload(deps)
    assert os.environ["PM_WEB_SECRET_KEY"] == "test-secret"
