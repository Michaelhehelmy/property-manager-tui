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

from app_core import paths, services
from ui.web import deps

logger = logging.getLogger(__name__)


def _runtime_readiness(include_checks: bool = False) -> dict:
    """Read-only readiness summary for health probes (no secrets, no sensitive data)."""
    checks = {}
    try:
        checks = paths.check_runtime_permissions() if hasattr(paths, "check_runtime_permissions") else {}
    except Exception:
        checks = {}
    data_dir = checks.get("data_dir") or {}
    db_info = checks.get("db_path") or {}
    export_dir = checks.get("export_dir") or {}
    invoice_dir = checks.get("invoice_dir") or {}
    data_writable = bool(data_dir.get("writable"))
    db_writable = bool(db_info.get("writable", data_writable))
    ready = bool(data_writable and db_writable and export_dir.get("writable", False) and invoice_dir.get("writable", False))
    out = {
        "ready": ready,
        "db_initialized": bool(getattr(services, "db", None) and getattr(services.db, "_conn", None)),
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
            if not os.environ.get("PM_WEB_SECRET_KEY"):
                host_hint = (os.environ.get("HOST") or os.environ.get("PM_WEB_HOST") or "127.0.0.1")
                if host_hint not in ("127.0.0.1", "localhost", "::1"):
                    logger.warning("PM_WEB_SECRET_KEY is not set for non-local host hint; set a strong random secret.")
            perm = paths.check_runtime_permissions() if hasattr(paths, "check_runtime_permissions") else {}
            if perm and not ((perm.get("data_dir") or {}).get("writable")):
                logger.warning("Runtime data directory is not writable; web operations may fail.")
        except Exception:
            pass
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
                    resp = JSONResponse({"detail": e.detail}, status_code=e.status_code)
                    deps.save_session(resp, deps.get_session(request))
                    return resp
        response = await call_next(request)
        deps.save_session(response, deps.get_session(request))
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
