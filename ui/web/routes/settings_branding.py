from __future__ import annotations
from pathlib import Path
from fastapi import APIRouter, Request
from app_core import services
from ui.web import deps
from ui.web.rendering import render
router = APIRouter()

@router.get("/unlock")
def unlock_page(request: Request, next: str = "/"):
    return render(request, "unlock.html", {"title":"Unlock", "next": next})

@router.post("/unlock")
async def unlock_post(request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    if deps.unlock(request, form.get("password","")):
        deps.add_flash(request, "Unlocked", "success")
        r=deps.redirect(form.get("next") or "/")
    else:
        deps.add_flash(request, "Invalid super password", "error")
        r=deps.redirect("/unlock")
    deps.save_session(r, deps.get_session(request)); return r

@router.post("/lock")
async def lock_post(request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    deps.lock(request); deps.add_flash(request, "Locked", "info")
    r=deps.redirect("/"); deps.save_session(r, deps.get_session(request)); return r

@router.get("/settings")
def settings(request: Request):
    return render(request, "settings/index.html", {"title":"Settings / Branding",
        "catalog": services.SETTINGS_CATALOG, "values": services.get_all_settings(),
        "brand": {"name": services.get_app_display_name(), "tagline": services.get_app_tagline(),
                  "animation": services.get_loading_animation(), "configured": services.is_branding_configured(),
                  "gift": services.get_gift_attribution()}})

@router.post("/settings/branding")
async def branding(request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        services.apply_branding_settings(form.get("display_name",""), form.get("tagline",""), form.get("animation","reveal"))
        deps.add_flash(request, "Branding updated", "success")
    except Exception as e: deps.add_flash(request, str(e), "error")
    r=deps.redirect("/settings"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/settings/save")
async def save_settings(request: Request):
    deps.require_unlock(request)
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    changes = {}
    for k in services.SETTINGS_CATALOG.keys():
        if k in form and k != "schema_version":
            changes[k] = form.get(k)
    try:
        count, errs = services.save_settings(changes, form.get("password",""))
        if errs: deps.add_flash(request, "; ".join(errs), "error")
        else: deps.add_flash(request, f"Saved {count} settings", "success")
    except Exception as e: deps.add_flash(request, str(e), "error")
    r=deps.redirect("/settings"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/settings/backup")
async def backup(request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        path = services.backup_now(); deps.add_flash(request, f"Backup: {Path(path).name}", "success")
    except Exception as e: deps.add_flash(request, str(e), "error")
    r=deps.redirect("/settings"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/settings/demo-preset")
async def demo_preset(request: Request):
    deps.require_unlock(request)
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        res = services.apply_demo_data_preset(form.get("preset","analysis_small"), form.get("password",""), form.get("confirm",""), allow_existing=False)
        deps.add_flash(request, f"Demo preset applied: {res['created']}", "success")
    except Exception as e: deps.add_flash(request, str(e), "error")
    r=deps.redirect("/settings"); deps.save_session(r, deps.get_session(request)); return r
