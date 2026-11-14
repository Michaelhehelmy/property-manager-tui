from __future__ import annotations

import sqlite3

from fastapi import Request
from fastapi.responses import HTMLResponse

from app_core import paths, services
from ui.web import deps


def _to_template_data(obj):
    """Recursively convert sqlite3.Row (and Row-like mappings) to plain dicts for Jinja."""
    if isinstance(obj, sqlite3.Row):
        return {k: _to_template_data(obj[k]) for k in obj.keys()}
    if isinstance(obj, dict):
        return {k: _to_template_data(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_template_data(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_to_template_data(v) for v in obj)
    return obj


def render(request: Request, name: str, ctx: dict | None = None, status: int = 200) -> HTMLResponse:
    templates = request.app.state.templates
    # Best-effort cached update notice (polling-based). Avoid blocking UI on network issues.
    update_notice = None
    try:
        from app_core import updater
        update_notice = updater.get_cached_update_notice(force=False, timeout=4)
    except Exception:
        update_notice = None
    context = {"request": request, "services": services, "paths": paths, "csrf_token": deps.csrf_token(request),
               "flashes": deps.pop_flashes(request), "unlocked": deps.is_unlocked(request),
               "brand_name": services.get_app_display_name(), "gift": services.get_gift_attribution(),
               "update_notice": update_notice}
    if ctx:
        context.update(ctx)
    context = _to_template_data(context)
    # Preserve request/services/paths objects (converted dict wrapper would break), re-inject originals
    context["request"] = request
    context["services"] = services
    context["paths"] = paths
    response = templates.TemplateResponse(request=request, name=name, context=context, status_code=status)
    deps.save_session(response, deps.get_session(request))
    return response
