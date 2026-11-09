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
# Phase 0.1: load secret from env for deployment; keep explicit dev fallback for localhost MVP.
_SECRET_ENV = "PM_WEB_SECRET_KEY"
SECRET_KEY = os.environ.get(_SECRET_ENV) or "pm-web-local-dev-secret-change-me"
if not os.environ.get(_SECRET_ENV):
    print(
        f"[pm-web] WARNING: {_SECRET_ENV} not set; using insecure development secret. "
        "Set PM_WEB_SECRET_KEY for any non-local/shared deployment.",
        file=sys.stderr,
    )
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
    response.set_cookie(SESSION_COOKIE, _serializer.dumps(data), httponly=True, samesite="lax")


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


def redirect(url: str, status: int = 303) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=status)
