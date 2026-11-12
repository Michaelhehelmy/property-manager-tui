from __future__ import annotations
from fastapi import APIRouter, Request
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
    portfolio = services.get_guest_portfolio(gid)
    if not portfolio:
        deps.add_flash(request, "Guest not found", "error")
        return deps.redirect("/guests")
    g = portfolio.get("guest") or {}
    if isinstance(g, dict) and "canonical_name" not in g and "name" in g:
        g = {**g, "canonical_name": g["name"]}
    return render(request, "guests/detail.html", {
        "title": f"Guest {g.get('canonical_name') or g.get('name') or gid}",
        "g": g,
        "p": portfolio,
    })

@router.post("/guests/{gid}/update")
async def guest_update(gid: int, request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        services.update_guest(gid, form.get("name",""), form.get("phone",""), form.get("email",""), form.get("notes",""), deps.service_password(request, form))
        deps.add_flash(request, "Guest updated", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect(f"/guests/{gid}"); deps.save_session(r, deps.get_session(request)); return r
