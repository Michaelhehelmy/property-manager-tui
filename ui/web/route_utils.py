"""Shared response helpers for web route handlers."""
from __future__ import annotations

from typing import Any, Mapping

from fastapi import Request

from ui.web import deps


def flash_redirect(request: Request, url: str, message: str | None = None, level: str = "info", *, status: int = 303):
    """Add optional flash, create redirect, persist session cookie, and return it."""
    if message is not None:
        deps.add_flash(request, message, level)
    response = deps.redirect(url, status=status)
    deps.save_session(response, deps.get_session(request))
    return response


def guarded_unlock_redirect(request: Request, next_url: str, message: str, level: str = "error", *, status: int = 303):
    """Flash a message and redirect to unlock flow for guarded actions/pages."""
    return flash_redirect(request, next_url, message, level, status=status)


def htmx_partial_or_redirect(
    request: Request,
    *,
    partial_template: str,
    partial_context: Mapping[str, Any],
    redirect_url: str,
    redirect_status: int = 303,
):
    """Return HTMX partial TemplateResponse or a redirect, persisting session in both cases."""
    if request.headers.get("HX-Request"):
        templates = request.app.state.templates
        response = templates.TemplateResponse(request=request, name=partial_template, context=dict(partial_context))
        deps.save_session(response, deps.get_session(request))
        return response
    return flash_redirect(request, redirect_url, status=redirect_status)

