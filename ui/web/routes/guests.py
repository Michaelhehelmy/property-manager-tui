from __future__ import annotations
from fastapi import APIRouter, Form, Request
from app_core import services
from ui.web import deps
from ui.web.rendering import render
router = APIRouter()

@router.get("/guests")
def guests(request: Request, q: str = ""):
    rows = services.get_all_guests(q)
    return render(request, "guests/list.html", {"title": "Guests", "q": q, "rows": rows})

@router.get("/guests/{gid}")
def guest_detail(gid: int, request: Request):
    g = services.get_guest_by_id(gid)
    if not g:
        deps.add_flash(request, "Guest not found", "error"); return deps.redirect("/guests")
    if isinstance(g, dict) and "canonical_name" not in g and "name" in g:
        g = {**g, "canonical_name": g["name"]}
    hist = services.get_guest_history_by_id(gid)
    guest_name = g.get("canonical_name") or g.get("name") or ""
    return render(request, "guests/detail.html", {"title": f"Guest {guest_name}", "g": g, "hist": hist})

@router.post("/guests/{gid}/update")
async def guest_update(gid: int, request: Request):
    deps.require_unlock(request)
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        services.update_guest(gid, form.get("name",""), form.get("phone",""), form.get("email",""), form.get("notes",""), form.get("password",""))
        deps.add_flash(request, "Guest updated", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect(f"/guests/{gid}"); deps.save_session(r, deps.get_session(request)); return r
