"""Web dependencies: sessions, CSRF, auth, flash messages, safe file paths."""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeSerializer

import os
import sys
from app_core import paths, services

SESSION_COOKIE = "pm_session"
# Secret/cookie hardening
_SECRET_ENV = "PM_WEB_SECRET_KEY"
_ALLOW_INSECURE_ENV = "PM_WEB_ALLOW_INSECURE_DEV"
SECRET_KEY = os.environ.get(_SECRET_ENV)
_insecure_dev = False
if not SECRET_KEY:
    host_hint = (os.environ.get("HOST") or os.environ.get("PM_WEB_HOST") or "127.0.0.1")
    non_local = host_hint not in ("127.0.0.1", "localhost", "::1")
    if non_local and os.environ.get(_ALLOW_INSECURE_ENV) != "1":
        raise RuntimeError(
            f"{_SECRET_ENV} must be set for non-local web deployment (HOST={host_hint}). "
            f"Set {_ALLOW_INSECURE_ENV}=1 only for explicit local/dev override."
        )
    SECRET_KEY = "pm-web-local-dev-secret-change-me"
    _insecure_dev = True
    print(
        f"[pm-web] WARNING: {_SECRET_ENV} not set; using insecure development secret. "
        "Set PM_WEB_SECRET_KEY for any non-local/shared deployment.",
        file=sys.stderr,
    )

_COOKIE_SECURE = os.environ.get("PM_WEB_COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes", "on")
_serializer = URLSafeSerializer(SECRET_KEY, salt="pm-session")


def _load_session(request: Request) -> dict[str, Any]:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return {}
    try:
        data = _serializer.loads(raw)
        return data if isinstance(data, dict) else {}
    except BadSignature:
        return {}


def save_session(response, data: dict[str, Any]) -> None:
    secure = _COOKIE_SECURE
    response.set_cookie(
        SESSION_COOKIE,
        _serializer.dumps(data),
        httponly=True,
        samesite="lax",
        secure=secure,
    )


def get_session(request: Request) -> dict[str, Any]:
    if not hasattr(request.state, "session"):
        request.state.session = _load_session(request)
    return request.state.session


def add_flash(request: Request, message: str, level: str = "info") -> None:
    sess = get_session(request)
    flashes = list(sess.get("flashes") or [])
    flashes.append({"level": level, "message": str(message)})
    sess["flashes"] = flashes[-6:]


def pop_flashes(request: Request) -> list[dict[str, str]]:
    sess = get_session(request)
    flashes = list(sess.get("flashes") or [])
    sess["flashes"] = []
    return flashes


def csrf_token(request: Request) -> str:
    sess = get_session(request)
    tok = sess.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(24)
        sess["csrf"] = tok
    return tok


def verify_csrf(request: Request, token: str | None) -> None:
    sess = get_session(request)
    ok = token and sess.get("csrf") and secrets.compare_digest(str(token), str(sess.get("csrf")))
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid CSRF token")


async def require_csrf_form(request: Request):
    """Centralized form CSRF verification helper. Returns parsed form."""
    try:
        form = await request.form()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid form submission")
    verify_csrf(request, form.get("csrf"))
    return form


def is_unlocked(request: Request) -> bool:
    return bool(get_session(request).get("unlocked"))


def require_unlock(request: Request) -> None:
    if not is_unlocked(request):
        nxt = quote(str(request.url.path) or "/")
        raise HTTPException(status_code=303, headers={"Location": f"/unlock?next={nxt}"})


def unlock(request: Request, password: str) -> bool:
    if services.verify_password(password):
        get_session(request)["unlocked"] = True
        try:
            # Keep backend in-process session aligned when available (single-worker MVP).
            if not services.unlock_session(password):
                services.touch_session()
        except Exception:
            pass
        return True
    return False


def lock(request: Request) -> None:
    get_session(request)["unlocked"] = False


def safe_download_path(kind: str, name: str) -> Path:
    if not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid file")
    base = paths.EXPORT_DIR if kind == "export" else paths.INVOICE_DIR
    p = (base / name).resolve()
    if base.resolve() not in p.parents and p != base.resolve():
        raise HTTPException(status_code=400, detail="Invalid file")
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return p




def enforce_csrf_for_unsafe(request: Request) -> None:
    """Call from middleware/dependency for state-changing methods."""
    if request.method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
        # form token or header
        token = request.headers.get("x-csrf-token")
        if token is None:
            # try cached form? cannot read body here safely; routes should verify.
            return
        verify_csrf(request, token)


async def csrf_middleware(request: Request, call_next):
    # Best-effort: enforce header token for unsafe API-style requests; form posts
    # are validated in route handlers via verify_csrf(request, csrf_token_form).
    if request.method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
        ct = (request.headers.get("content-type") or "").lower()
        if "application/json" in ct or request.headers.get("x-csrf-token"):
            token = request.headers.get("x-csrf-token")
            if token is None:
                # JSON unsafe requests must provide header
                raise HTTPException(status_code=400, detail="Invalid CSRF token")
            verify_csrf(request, token)
    return await call_next(request)


def redirect(url: str, status: int = 303) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=status)
