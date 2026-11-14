from __future__ import annotations

from fastapi import APIRouter, Request

from app_core import services
from ui.web.rendering import render

router = APIRouter()


@router.get("/")
def dashboard(request: Request):
    if not services.is_branding_configured():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/setup", status_code=303)
    day = services.get_dashboard_day_snapshot()
    ctx = {
        "title": "Dashboard",
        "front": services.get_front_desk_summary(),
        "ledger": services.get_general_ledger_summary(),
        "arrivals": day.get("arrivals", []),
        "departures": day.get("departures", []),
        "in_house": day.get("in_house", []),
    }
    return render(request, "dashboard.html", ctx)
