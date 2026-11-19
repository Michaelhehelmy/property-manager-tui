"""Web dependencies: sessions, CSRF, auth, flash messages, safe file paths."""
from __future__ import annotations

import logging
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeSerializer

from app_core import paths, services

SESSION_COOKIE = "pm_session"
logger = logging.getLogger(__name__)
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
    # Never use a hardcoded fallback secret. Generate an ephemeral per-process
    # secret (sessions invalidate on restart) for local/dev and explicit override cases.
    SECRET_KEY = secrets.token_urlsafe(48)
    if os.environ.get(_ALLOW_INSECURE_ENV) == "1":
        msg = (
            f"[pm-web] WARNING: {_SECRET_ENV} not set and {_ALLOW_INSECURE_ENV}=1; "
            "using an insecure ephemeral development secret. Use only for explicit local/dev scenarios."
        )
    else:
        msg = (
            f"[pm-web] WARNING: {_SECRET_ENV} not set; using an ephemeral per-process development secret. "
            "Web sessions will not survive restart. Set PM_WEB_SECRET_KEY to a strong random value for any non-local/shared deployment."
        )
    _insecure_dev = True
    print(msg, file=sys.stderr)
    logger.warning(
        "web_secret_config_warning code=insecure_dev_secret host_hint=%s non_local=%s explicit_insecure_override=%s ephemeral=%s",
        host_hint,
        bool(non_local),
        os.environ.get(_ALLOW_INSECURE_ENV) == "1",
        True,
    )
elif len(SECRET_KEY) < 32:
    msg = f"[pm-web] WARNING: {_SECRET_ENV} is short (<32 chars); use a strong random secret in production."
    print(msg, file=sys.stderr)
    logger.warning("web_secret_config_warning code=short_secret_key min_recommended=32")

_serializer = URLSafeSerializer(SECRET_KEY, salt="pm-session")


def _env_int(name: str, default: int, *, min_v: int = 1, max_v: int = 100000) -> int:
    try:
        v = int(os.environ.get(name, str(default)))
        return max(min_v, min(max_v, v))
    except Exception:
        return default


def rate_limit_config(scope: str = "unlock") -> tuple[int, int]:
    """(limit, window_sec) configurable via env for in-process best-effort throttling."""
    s = (scope or "unlock").lower()
    if s == "recovery":
        return (
            _env_int("PM_WEB_RECOVERY_RATE_LIMIT", 5, min_v=1, max_v=1000),
            _env_int("PM_WEB_RECOVERY_RATE_WINDOW_SEC", 900, min_v=10, max_v=86400),
        )
    if s == "sensitive":
        return (
            _env_int("PM_WEB_SENSITIVE_RATE_LIMIT", 20, min_v=1, max_v=5000),
            _env_int("PM_WEB_SENSITIVE_RATE_WINDOW_SEC", 300, min_v=10, max_v=86400),
        )
    return (
        _env_int("PM_WEB_UNLOCK_RATE_LIMIT", 8, min_v=1, max_v=1000),
        _env_int("PM_WEB_UNLOCK_RATE_WINDOW_SEC", 300, min_v=10, max_v=86400),
    )

_WEB_UNLOCK_LAST = "web_unlock_last"
_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
_SENSITIVE_ATTEMPTS: dict[str, list[float]] = {}


def cookie_secure_enabled() -> bool:
    """PM_WEB_COOKIE_SECURE=1/true/yes/on enables Secure cookie flag (required for HTTPS)."""
    return os.environ.get("PM_WEB_COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes", "on")


def cookie_samesite() -> str:
    """PM_WEB_COOKIE_SAMESITE=lax|strict|none (defaults to lax). 'none' requires Secure."""
    v = os.environ.get("PM_WEB_COOKIE_SAMESITE", "lax").strip().lower()
    if v not in ("lax", "strict", "none"):
        v = "lax"
    if v == "none" and not cookie_secure_enabled():
        # Avoid invalid/insecure cookie config in browsers; fall back safely.
        return "lax"
    return v


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
    # HttpOnly always; SameSite/Secure configurable for HTTPS deployments.
    # max_age aligns cookie lifetime with unlock/session timeout window (+grace) while
    # server-side expiry is still enforced on each request from web_unlock_last.
    try:
        max_age = max(300, _session_timeout_minutes() * 60 + 300)
    except Exception:
        max_age = 3600
    response.set_cookie(
        SESSION_COOKIE,
        _serializer.dumps(data),
        httponly=True,
        samesite=cookie_samesite(),
        secure=cookie_secure_enabled(),
        max_age=max_age,
        path="/",
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


async def get_form(request: Request):
    """Return parsed form, reusing middleware-cached form when available."""
    cached = getattr(request.state, "cached_form", None)
    if cached is not None:
        return cached
    try:
        form = await request.form()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid form submission")
    request.state.cached_form = form
    return form


async def require_csrf_form(request: Request):
    """Centralized form CSRF verification helper. Returns parsed form."""
    form = await get_form(request)
    verify_csrf(request, form.get("csrf"))
    return form


async def require_unlocked_form(request: Request, permission: str | None = None):
    """Return cached/parsed form and enforce per-browser unlock for sensitive mutations.

    CSRF for browser form posts is enforced centrally by middleware; this helper
    normalizes unlock+form access for route handlers.

    Optional permission enables incremental RBAC enforcement for migrated routes
    (legacy unlocked behavior remains compatible via has_permission defaults).
    """
    form = await get_form(request)
    require_unlock(request)
    if permission:
        require_permission(request, permission)
    return form


def service_password(request: Request, form, *, allow_session_sentinel: bool = False) -> str:
    """Resolve password/proof for app_core service calls.

    - Prefer an explicit password supplied by the form (never weakened).
    - When the browser session is unlocked and the target service explicitly
      supports the "__session__" sentinel, pass "__session__" as a best-effort
      compatibility proof for single-worker flows (with backend-session sync).
    - Otherwise return the provided value (possibly empty) so the service enforces
      its own password requirements.
    """
    pw = str(form.get("password") or "")
    if pw:
        return pw
    if allow_session_sentinel and is_unlocked(request):
        sync_backend_session_if_possible()
        return "__session__"
    return pw



_WEB_USER_KEY = "auth_user"


def get_current_user(request: Request) -> dict[str, Any] | None:
    """Optional local user identity (RBAC groundwork). Independent from unlock gate."""
    try:
        sess = get_session(request)
        u = sess.get(_WEB_USER_KEY)
        if isinstance(u, dict) and u.get("id") and u.get("username"):
            # normalize minimal shape
            return {
                "id": u.get("id"),
                "username": u.get("username"),
                "display_name": u.get("display_name") or u.get("username"),
                "role": u.get("role") or "staff",
            }
    except Exception:
        return None
    return None


def set_current_user(request: Request, user: dict[str, Any]) -> None:
    sess = get_session(request)
    sess[_WEB_USER_KEY] = {
        "id": user.get("id"),
        "username": user.get("username"),
        "display_name": user.get("display_name") or user.get("username"),
        "role": user.get("role") or "staff",
    }


def clear_current_user(request: Request) -> None:
    try:
        sess = get_session(request)
        sess.pop(_WEB_USER_KEY, None)
    except Exception:
        pass


def current_actor(request: Request) -> dict[str, Any]:
    u = get_current_user(request)
    if u:
        return {
            "actor": (u.get("username") or "user"),
            "actor_user_id": u.get("id"),
            "actor_role": u.get("role") or "",
            "display_name": u.get("display_name") or u.get("username"),
        }
    # preserve existing behavior: unlocked/admin-equivalent manager context
    return {
        "actor": "manager",
        "actor_user_id": None,
        "actor_role": "admin" if is_unlocked(request) else "",
        "display_name": "Manager",
    }


# --- RBAC/auth migration scaffolding (P4, additive, non-breaking defaults) ---

def web_auth_mode() -> str:
    """Auth enforcement mode for migration path.
    - legacy (default): preserve existing unlock/session behavior.
    - hybrid: prefer user identity/roles when present, keep unlock fallback.
    - strict/rbac: intended for stricter enforcement (opt-in; route adoption incremental).
    """
    v = os.environ.get("PM_WEB_AUTH_MODE", "legacy").strip().lower()
    if v in ("strict", "rbac", "enforce"):
        return "strict"
    if v in ("hybrid", "mixed"):
        return "hybrid"
    return "legacy"


def is_strict_rbac() -> bool:
    return web_auth_mode() == "strict"


def is_privileged_user(request: Request) -> bool:
    u = get_current_user(request)
    if not u:
        return False
    role = (u.get("role") or "").strip().lower()
    return role in ("admin", "owner", "manager")


def require_authenticated_user(request: Request) -> None:
    """Strict-mode helper (not enforced globally by default)."""
    if get_current_user(request):
        return
    if is_strict_rbac():
        raise HTTPException(status_code=403, detail="Authenticated user session required (strict auth mode)")
    # legacy/hybrid: no-op



def has_permission(request: Request, permission: str) -> bool:
    """RBAC scaffold/migration helper. Defaults preserve existing unlock behavior.
    - Legacy unlocked session without user identity remains admin-equivalent.
    - Logged-in privileged roles (admin/owner/manager) are allowed.
    - In strict auth mode, logged-in non-privileged users are evaluated against a
      minimal role capability map for common permission namespaces; unknown permissions
      remain allowed to avoid accidental lockout during incremental migration.
    - In legacy/hybrid modes, behavior remains permissive for compatibility.
    """
    try:
        u = get_current_user(request)
        if is_unlocked(request) and not u:
            return True
        if not u:
            # no explicit user; rely on existing unlock gates elsewhere
            return True
        role = (u.get("role") or "").strip().lower()
        if role in ("admin", "owner", "manager"):
            return True
        if not is_strict_rbac():
            # permissive groundwork in legacy/hybrid for backward compatibility
            return True
        # strict-mode minimal capability map (incremental, non-exhaustive)
        perm = (permission or "").strip().lower()
        caps = {
            "front_desk": {"reservations", "guests", "housekeeping.read", "reports.read", "pos.read"},
            "housekeeping": {"housekeeping", "reservations.read", "rooms.read"},
            "accounting": {"accounting", "reports.read", "payments", "reservations.read"},
            "staff": {"reservations.read", "reports.read", "housekeeping.read"},
        }.get(role, set())
        if not perm or "*" in caps:
            return True
        # allow if namespace/action prefix matches
        for c in caps:
            if perm == c or perm.startswith(c.rstrip("*")) or (c.endswith(".*") and perm.startswith(c[:-1])) or (c.endswith(".read") and perm == c):
                return True
        # also allow generic read-only checks to be safe
        if perm.endswith(".read") or perm.endswith(":read"):
            return True
        # unknown/unsupported permission in strict mode for non-privileged role: deny
        return False
    except Exception:
        return True


def require_permission(request: Request, permission: str) -> None:
    # groundwork: keep behavior compatible; only block if future strict checks deny
    if not has_permission(request, permission):
        raise HTTPException(status_code=403, detail="Permission denied")



def _session_timeout_minutes() -> int:
    try:
        timeout = int(services.get_setting("session_timeout_min", "5"))
        return max(1, timeout)
    except Exception:
        return 5


def touch_web_unlock(request: Request) -> None:
    """Slide per-browser web unlock expiry on activity."""
    sess = get_session(request)
    if sess.get(_WEB_UNLOCK_LAST):
        sess[_WEB_UNLOCK_LAST] = int(time.time())


def is_unlocked(request: Request) -> bool:
    """Per-browser web unlock state with timeout/expiry (no global services._session)."""
    sess = get_session(request)
    last = sess.get(_WEB_UNLOCK_LAST)
    if not last:
        # Backward compat: honor legacy boolean flag but migrate to timed model.
        if sess.get("unlocked"):
            sess[_WEB_UNLOCK_LAST] = int(time.time())
            sess.pop("unlocked", None)
            last = sess[_WEB_UNLOCK_LAST]
        else:
            return False
    try:
        last_i = int(last)
    except Exception:
        sess.pop(_WEB_UNLOCK_LAST, None)
        sess.pop("unlocked", None)
        return False
    if (int(time.time()) - last_i) > (_session_timeout_minutes() * 60):
        sess.pop(_WEB_UNLOCK_LAST, None)
        sess.pop("unlocked", None)
        return False
    return True


def _client_ip(request: Request) -> str:
    try:
        if request.client and request.client.host:
            return str(request.client.host)
    except Exception:
        pass
    return "unknown"


def _prune_attempts(bucket: dict[str, list[float]], key: str, window_sec: int) -> list[float]:
    now = time.time()
    arr = [t for t in bucket.get(key, []) if now - t <= window_sec]
    bucket[key] = arr
    return arr


def check_rate_limit(bucket: dict[str, list[float]], key: str, *, limit: int, window_sec: int) -> tuple[bool, int]:
    """Return (allowed, retry_after_seconds). Lightweight in-process limiter (--workers 1)."""
    arr = _prune_attempts(bucket, key, window_sec)
    if len(arr) >= limit:
        retry = max(1, int(window_sec - (time.time() - arr[0])) + 1)
        return False, retry
    return True, 0


def record_attempt(bucket: dict[str, list[float]], key: str, window_sec: int) -> None:
    arr = _prune_attempts(bucket, key, window_sec)
    arr.append(time.time())
    bucket[key] = arr


def clear_attempts(bucket: dict[str, list[float]], key: str) -> None:
    bucket.pop(key, None)


def enforce_auth_rate_limit(request: Request, scope: str = "unlock", *, limit: int | None = None, window_sec: int | None = None) -> None:
    """Mitigate brute-force on auth/unlock-sensitive endpoints (best-effort, in-process, configurable)."""
    d_limit, d_window = rate_limit_config(scope)
    lim = d_limit if limit is None else max(1, int(limit))
    win = d_window if window_sec is None else max(10, int(window_sec))
    key = f"{scope}:{_client_ip(request)}"
    bucket = _LOGIN_ATTEMPTS if scope in ("unlock", "recovery") else _SENSITIVE_ATTEMPTS
    ok, retry = check_rate_limit(bucket, key, limit=lim, window_sec=win)
    if not ok:
        logger.warning(
            "web_rate_limit_block scope=%s path=%s retry_after_s=%s",
            scope,
            getattr(getattr(request, "url", None), "path", ""),
            retry,
        )
        raise HTTPException(status_code=429, detail=f"Too many attempts. Try again in about {retry}s.")


def record_auth_failure(request: Request, scope: str = "unlock", *, window_sec: int = 300) -> None:
    key = f"{scope}:{_client_ip(request)}"
    bucket = _LOGIN_ATTEMPTS if scope in ("unlock", "recovery") else _SENSITIVE_ATTEMPTS
    record_attempt(bucket, key, window_sec)


def clear_auth_failures(request: Request, scope: str = "unlock") -> None:
    key = f"{scope}:{_client_ip(request)}"
    bucket = _LOGIN_ATTEMPTS if scope in ("unlock", "recovery") else _SENSITIVE_ATTEMPTS
    clear_attempts(bucket, key)


def has_privileged_access(request: Request) -> bool:
    """Privilege check for sensitive actions with safe migration behavior.
    - legacy: unlocked session required (current behavior)
    - hybrid/strict: authenticated privileged user (admin/owner/manager) OR unlocked session
      provides privileged access, enabling login-session integration without breaking unlock flows.
    """
    if is_unlocked(request):
        return True
    mode = web_auth_mode()
    if mode in ("hybrid", "strict") and is_privileged_user(request):
        return True
    return False


def require_unlock(request: Request) -> None:
    if not has_privileged_access(request):
        method = (getattr(request, "method", "GET") or "GET").upper()
        if method in ("GET", "HEAD"):
            # Return to the same page after unlock (preserve query string).
            path = str(getattr(getattr(request, "url", None), "path", "") or "/")
            query = str(getattr(getattr(request, "url", None), "query", "") or "")
            nxt_raw = path + (("?" + query) if query else "")
        else:
            # For unsafe methods, redirecting back to the same endpoint would be replayed as GET (405).
            # Prefer same-origin referer page; fall back to home so the user can retry the action.
            nxt_raw = "/"
            ref = (request.headers.get("referer") or "").strip() if getattr(request, "headers", None) else ""
            if ref:
                try:
                    from urllib.parse import urlsplit
                    parts = urlsplit(ref)
                    req_netloc = str(getattr(getattr(request, "url", None), "netloc", "") or "")
                    if parts.scheme in ("", "http", "https") and parts.netloc in ("", req_netloc):
                        cand = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
                        if cand.startswith("/") and not cand.startswith("//"):
                            nxt_raw = cand
                except Exception:
                    nxt_raw = "/"
        if not nxt_raw.startswith("/") or nxt_raw.startswith("//"):
            nxt_raw = "/"
        nxt = quote(nxt_raw, safe="/")
        raise HTTPException(status_code=303, headers={"Location": f"/unlock?next={nxt}"})


def sync_backend_session_if_possible() -> None:
    """Best-effort compatibility for single-worker flows that may consult process-global
    session state internally. Web authorization decisions MUST NOT rely on this."""
    try:
        services.touch_session()
    except Exception:
        pass


def unlock(request: Request, password: str) -> bool:
    if services.verify_password(password):
        sess = get_session(request)
        sess[_WEB_UNLOCK_LAST] = int(time.time())
        sess.pop("unlocked", None)
        clear_auth_failures(request, "unlock")
        # Best-effort in-process alignment for legacy service internals (single-worker MVP).
        try:
            if not services.unlock_session(password):
                services.touch_session()
        except Exception:
            pass
        logger.info("web_auth_event event=unlock_success scope=unlock")
        return True
    record_auth_failure(request, "unlock")
    logger.warning("web_auth_event event=unlock_failure scope=unlock")
    return False


def lock(request: Request) -> None:
    sess = get_session(request)
    sess.pop(_WEB_UNLOCK_LAST, None)
    sess.pop("unlocked", None)
    try:
        services.lock_session()
    except Exception:
        pass
    logger.info("web_auth_event event=lock")


def safe_download_path(kind: str, name: str) -> Path:
    # Confine downloads to approved directories and prevent traversal/symlink escape.
    if not name or "/" in name or "\\" in name or ".." in name or "\x00" in name:
        raise HTTPException(status_code=400, detail="Invalid file")
    if kind not in ("export", "invoice"):
        raise HTTPException(status_code=400, detail="Invalid file")
    base = paths.EXPORT_DIR if kind == "export" else paths.INVOICE_DIR
    p = (base / name).resolve()
    base_r = base.resolve()
    if base_r != p and base_r not in p.parents:
        raise HTTPException(status_code=400, detail="Invalid file")
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return p


def redirect(url: str, status: int = 303) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=status)
