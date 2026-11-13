"""FastAPI + HTMX web UI (localhost MVP, single worker)."""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app_core import db, paths, services
from ui.web import deps

logger = logging.getLogger(__name__)


def _safe_detail(detail: object) -> str:
    text = str(detail or "")
    return text if len(text) <= 240 else (text[:237] + "...")


def _runtime_readiness(include_checks: bool = False) -> dict:
    """Read-only readiness summary for health probes (no secrets, no sensitive data)."""
    checks = {}
    try:
        checks = paths.check_runtime_permissions() if hasattr(paths, "check_runtime_permissions") else {}
    except Exception as exc:
        logger.warning("web_readiness_permission_check_failed error=%s", _safe_detail(exc))
        checks = {}
    data_dir = checks.get("data_dir") or {}
    db_info = checks.get("db_path") or {}
    export_dir = checks.get("export_dir") or {}
    invoice_dir = checks.get("invoice_dir") or {}
    data_writable = bool(data_dir.get("writable"))
    db_writable = bool(db_info.get("writable", data_writable))
    ready = bool(data_writable and db_writable and export_dir.get("writable", False) and invoice_dir.get("writable", False))
    try:
        db_initialized = bool(db.is_initialized())
    except Exception as exc:
        logger.warning("web_readiness_db_check_failed error=%s", _safe_detail(exc))
        db_initialized = False
    out = {
        "ready": ready,
        "db_initialized": db_initialized,
        "data_dir_writable": data_writable,
        "db_writable": db_writable,
    }
    if include_checks:
        out["checks"] = {
            "data_dir_exists": bool(data_dir.get("exists")),
            "export_dir_writable": bool(export_dir.get("writable")),
            "invoice_dir_writable": bool(invoice_dir.get("writable")),
        }
    return out


def create_app(*, init_db: bool = True, run_startup: bool = True) -> FastAPI:
    if init_db and run_startup:
        paths.ensure_data_dirs()
        # Best-effort startup hygiene warnings (no secrets logged)
        try:
            secret_set = bool(os.environ.get("PM_WEB_SECRET_KEY"))
            host_hint = (os.environ.get("HOST") or os.environ.get("PM_WEB_HOST") or "127.0.0.1")
            if not secret_set and host_hint not in ("127.0.0.1", "localhost", "::1"):
                logger.warning(
                    "web_startup_config_warning code=missing_pm_web_secret_key host_hint=%s",
                    host_hint,
                )
            perm = paths.check_runtime_permissions() if hasattr(paths, "check_runtime_permissions") else {}
            if perm and not ((perm.get("data_dir") or {}).get("writable")):
                logger.warning("web_startup_permission_warning code=data_dir_not_writable")
            logger.info(
                "web_startup event=init_complete db_initialized=%s secret_key_env_set=%s",
                bool(db.is_initialized()) if hasattr(db, "is_initialized") else False,
                secret_set,
            )
        except Exception as exc:
            logger.exception("web_startup_warning_failed error=%s", _safe_detail(exc))
        services.init_db()
        services.set_language(services.get_setting("language", "en"))

    app = FastAPI(title=f"{services.get_app_display_name()} Web UI")
    static_dir = Path(__file__).resolve().parent / "static"
    templates_dir = Path(__file__).resolve().parent / "templates"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    templates = Jinja2Templates(directory=str(templates_dir))
    app.state.templates = templates

    @app.middleware("http")
    async def session_middleware(request: Request, call_next):
        start = time.monotonic()
        deps.get_session(request)
        deps.csrf_token(request)
        # Evaluate per-browser unlock expiry and slide activity for unlocked sessions.
        if deps.is_unlocked(request):
            deps.touch_web_unlock(request)
            deps.sync_backend_session_if_possible()
        # Enforce CSRF for state-changing requests. Form posts must include a form token
        # (or header fallback); JSON/unsafe API-style requests must include x-csrf-token.
        if request.method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
            path = request.url.path
            if path not in ("/health", "/healthz"):
                try:
                    ct = (request.headers.get("content-type") or "").lower()
                    token = request.headers.get("x-csrf-token")
                    if "application/json" in ct or token is not None:
                        deps.verify_csrf(request, token)
                    elif "multipart/form-data" in ct or "application/x-www-form-urlencoded" in ct or ct == "":
                        form = await request.form()
                        deps.verify_csrf(request, form.get("csrf"))
                        request.state.cached_form = form
                except HTTPException as e:
                    logger.warning(
                        "web_csrf_reject method=%s path=%s status=%s detail=%s",
                        request.method,
                        request.url.path,
                        e.status_code,
                        _safe_detail(getattr(e, "detail", "invalid")),
                    )
                    resp = JSONResponse({"detail": e.detail}, status_code=e.status_code)
                    deps.save_session(resp, deps.get_session(request))
                    return resp
        try:
            response = await call_next(request)
        except HTTPException as e:
            logger.info(
                "web_http_exception method=%s path=%s status=%s detail=%s",
                request.method,
                request.url.path,
                e.status_code,
                _safe_detail(getattr(e, "detail", "")),
            )
            raise
        except Exception:
            logger.exception(
                "web_unhandled_exception method=%s path=%s",
                request.method,
                request.url.path,
            )
            resp = JSONResponse({"detail": "Internal Server Error"}, status_code=500)
            deps.save_session(resp, deps.get_session(request))
            return resp
        # Avoid duplicating session cookie if endpoint/earlier handler already set it.
        has_session_cookie = any(
            k.lower() == b"set-cookie" and v.startswith((deps.SESSION_COOKIE + "=").encode())
            for k, v in response.headers.raw
        )
        if not has_session_cookie:
            deps.save_session(response, deps.get_session(request))
        try:
            dur_ms = int((time.monotonic() - start) * 1000)
            if request.url.path not in ("/health", "/healthz"):
                logger.info(
                    "web_request method=%s path=%s status=%s dur_ms=%s unlocked=%s",
                    request.method,
                    request.url.path,
                    getattr(response, "status_code", 0),
                    dur_ms,
                    bool(deps.is_unlocked(request)),
                )
        except Exception:
            logger.debug("web_request_log_failed", exc_info=True)
        return response

    from ui.web.routes import accounting, assistant, dashboard, files, guests, pos_inventory, reports_analytics, reservations, settings_branding

    app.include_router(dashboard.router)
    app.include_router(reservations.router)
    app.include_router(guests.router)
    app.include_router(pos_inventory.router)
    app.include_router(accounting.router)
    app.include_router(reports_analytics.router)
    app.include_router(assistant.router)
    app.include_router(settings_branding.router)
    app.include_router(files.router)

    @app.get("/health")
    @app.get("/healthz")
    def health_probe():
        readiness = _runtime_readiness(include_checks=False)
        payload = {
            "ok": bool(readiness.get("ready")),
            "status": "healthy" if readiness.get("ready") else "degraded",
            "service": "property-manager-web",
            "time": int(time.time()),
            "readiness": readiness,
        }
        return payload

    @app.get("/readyz")
    def readiness_probe():
        readiness = _runtime_readiness(include_checks=True)
        return {
            "ok": bool(readiness.get("ready")),
            "status": "ready" if readiness.get("ready") else "not_ready",
            "service": "property-manager-web",
            "readiness": readiness,
        }

    return app


app = create_app()
