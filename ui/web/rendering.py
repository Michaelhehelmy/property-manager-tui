from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse

from app_core import paths, services
from ui.web import deps


def render(request: Request, name: str, ctx: dict | None = None, status: int = 200) -> HTMLResponse:
    templates = request.app.state.templates
    context = {"request": request, "services": services, "paths": paths, "csrf_token": deps.csrf_token(request),
               "flashes": deps.pop_flashes(request), "unlocked": deps.is_unlocked(request),
               "brand_name": services.get_app_display_name(), "gift": services.get_gift_attribution()}
    if ctx:
        context.update(ctx)
    response = templates.TemplateResponse(request=request, name=name, context=context, status_code=status)
    deps.save_session(response, deps.get_session(request))
    return response
