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


def _update_notice_key(update_notice: dict | None) -> str:
    if not isinstance(update_notice, dict):
        return ""
    try:
        git = update_notice.get("git") or {}
        latest = git.get("latest") or {}
        for key in (latest.get("commit"), latest.get("commit_short")):
            if key:
                return f"git:{key}"
        rel = update_notice.get("latest_release") or {}
        for key in (rel.get("tag_name"), rel.get("name")):
            if key:
                return f"release:{key}"
        if update_notice.get("message"):
            return f"msg:{update_notice.get('message')}"
        if update_notice.get("url"):
            return f"url:{update_notice.get('url')}"
    except Exception:
        return ""
    return ""


def render(request: Request, name: str, ctx: dict | None = None, status: int = 200) -> HTMLResponse:
    templates = request.app.state.templates
    # Best-effort cached update notice (polling-based). Avoid blocking UI on network issues.
    update_notice = None
    try:
        from app_core import updater
        update_notice = updater.get_cached_update_notice(force=False, timeout=4)
    except Exception:
        update_notice = None
    update_notice_key = _update_notice_key(update_notice)
    show_update_modal = False
    try:
        sess = deps.get_session(request)
        dismissed_key = sess.get("dismissed_update_key") if isinstance(sess, dict) else None
        show_update_modal = bool(
            update_notice and update_notice.get("update_available") and update_notice_key and dismissed_key != update_notice_key
        )
    except Exception:
        show_update_modal = bool(update_notice and update_notice.get("update_available"))
    context = {"request": request, "services": services, "paths": paths, "csrf_token": deps.csrf_token(request),
               "flashes": deps.pop_flashes(request), "unlocked": deps.is_unlocked(request),
               "brand_name": services.get_app_display_name(), "gift": services.get_gift_attribution(),
               "update_notice": update_notice, "update_notice_key": update_notice_key, "show_update_modal": show_update_modal,
               "current_user": deps.get_current_user(request) if hasattr(deps, "get_current_user") else None,
               "current_actor": deps.current_actor(request) if hasattr(deps, "current_actor") else None}
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
