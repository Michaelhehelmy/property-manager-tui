from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request

from app_core import services
from ui.web.rendering import render

router = APIRouter()


@router.get("/")
def dashboard(request: Request, day: str | None = None):
    if not services.is_branding_configured():
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url="/setup", status_code=303)
    ops_day = (day or "").strip() or datetime.now().strftime(services.DATE_FMT)
    try:
        ops = services.get_front_desk_ops(ops_day)
    except Exception:
        ops = {"date": ops_day, "arrivals": [], "in_house": [], "departures": [], "counts": {}, "exceptions": []}
    ctx = {
        "title": "Dashboard",
        "front": services.get_front_desk_summary(),
        "ledger": services.get_general_ledger_summary(),
        "ops": ops,
        "ops_day": ops_day,
        "arrivals": ops.get("arrivals", []),
        "departures": ops.get("departures", []),
        "in_house": ops.get("in_house", []),
    }
    return render(request, "dashboard.html", ctx)
