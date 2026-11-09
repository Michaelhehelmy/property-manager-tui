"""FastAPI + HTMX web UI (localhost MVP, single worker)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app_core import paths, services
from ui.web import deps


def create_app(*, init_db: bool = True, run_startup: bool = True) -> FastAPI:
    if init_db and run_startup:
        paths.ensure_data_dirs()
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
        response = await call_next(request)
        if "pm_session" not in response.headers.get("set-cookie", ""):
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
        return {"ok": True, "status": "healthy", "service": "property-manager-web"}

    return app


app = create_app()
