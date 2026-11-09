from __future__ import annotations
from fastapi import APIRouter, Request
from app_core import services
from ui.web.rendering import render
router = APIRouter()

@router.get("/")
def dashboard(request: Request):
    today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    ctx = {
        "title": "Dashboard",
        "front": services.get_front_desk_summary(),
        "arrivals": [dict(r) for r in services.get_recent_ledger(0)] if False else [],
        "ledger": services.get_general_ledger_summary(),
    }
    # arrivals/departures/in-house via daily ops text parsing not ideal; use direct queries
    from app_core.db import connect
    arr = connect().execute("SELECT id, guest_name, room_id, check_in_date, check_out_date, status FROM reservations WHERE check_in_date=? AND status IN ('Reserved','Checked In') ORDER BY id DESC", (today,)).fetchall()
    dep = connect().execute("SELECT id, guest_name, room_id, check_in_date, check_out_date, status FROM reservations WHERE check_out_date=? AND status IN ('Reserved','Checked In','Checked Out') ORDER BY id DESC", (today,)).fetchall()
    inh = connect().execute("SELECT id, guest_name, room_id, check_in_date, check_out_date, status FROM reservations WHERE status='Checked In' ORDER BY id DESC").fetchall()
    ctx.update({"arrivals": arr, "departures": dep, "in_house": inh})
    return render(request, "dashboard.html", ctx)
