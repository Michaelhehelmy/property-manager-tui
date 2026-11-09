from __future__ import annotations
from pathlib import Path
from datetime import datetime, timedelta
from fastapi import APIRouter, Form, Request
from app_core import services
from app_core.db import connect
from ui.web import deps
from ui.web.rendering import render
router = APIRouter()

def _rooms(): return connect().execute("SELECT id, room_number, room_type, base_price FROM rooms ORDER BY room_number").fetchall()
def _plans(): return connect().execute("SELECT id, name, price_modifier FROM rate_plans ORDER BY name").fetchall()

@router.get("/reservations")
def list_res(request: Request, q: str = ""):
    ql = f"%{q}%" if q else "%"
    rows = connect().execute("""
        SELECT r.*, rm.room_number, rp.name as rate_plan_name
        FROM reservations r LEFT JOIN rooms rm ON r.room_id=rm.id
        LEFT JOIN rate_plans rp ON r.rate_plan_id=rp.id
        WHERE r.status NOT IN ('Deleted') AND (r.guest_name LIKE ? OR r.id LIKE ? OR rm.room_number LIKE ?)
        ORDER BY r.id DESC LIMIT 200""", (ql, ql, ql)).fetchall()
    now = datetime.now()
    return render(request, "reservations/list.html", {"title":"Reservations","rows":rows,"q":q,"rooms":_rooms(),"plans":_plans(),
                  "today":now.strftime("%Y-%m-%d"), "tomorrow":(now+timedelta(days=1)).strftime("%Y-%m-%d")})

@router.post("/reservations/create")
async def create(request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        rid = services.create_reservation(form.get("guest_name",""), form.get("source","Direct"), int(form.get("room_id")),
              int(form["rate_plan_id"]) if form.get("rate_plan_id") else None, float(form.get("rate") or 0),
              form.get("check_in",""), form.get("check_out",""), float(form.get("deposit") or 0))
        deps.add_flash(request, f"Reservation #{rid} created", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r=deps.redirect("/reservations"); deps.save_session(r, deps.get_session(request)); return r

@router.get("/reservations/{rid}")
def detail(rid: int, request: Request):
    res = services.get_reservation(rid)
    if not res: deps.add_flash(request,"Not found","error"); return deps.redirect("/reservations")
    folio = services.get_balance_breakdown(rid) or {}
    pos = connect().execute("SELECT * FROM pos_tabs WHERE reservation_id=? ORDER BY id DESC", (rid,)).fetchall()
    pays = connect().execute("SELECT * FROM payments WHERE reservation_id=? ORDER BY id DESC", (rid,)).fetchall()
    return render(request, "reservations/detail.html", {"title":f"Reservation #{rid}","r":res,"folio":folio,"pos":pos,"pays":pays,"rooms":_rooms(),"today":datetime.now().strftime("%Y-%m-%d")})

async def _act(request, rid, fn, ok):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        fn(form); deps.add_flash(request, ok, "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r=deps.redirect(f"/reservations/{rid}"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/reservations/{rid}/checkin")
async def checkin(rid: int, request: Request):
    return await _act(request,rid, lambda f: services.update_reservation_status(rid,"Checked In"), "Checked in")

@router.post("/reservations/{rid}/checkout")
async def checkout(rid: int, request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        needs, bal, _ = services.checkout_requires_override(rid)
        if needs:
            deps.add_flash(request, f"Unpaid balance {services.fmt(bal)}. Use override checkout with password.", "error")
        else:
            services.checkout_reservation(rid); deps.add_flash(request, "Checked out", "success")
    except Exception as e: deps.add_flash(request,str(e),"error")
    r=deps.redirect(f"/reservations/{rid}"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/reservations/{rid}/checkout-override")
async def checkout_override(rid: int, request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        services.checkout_reservation(rid, override=True, password=form.get("password",""))
        deps.add_flash(request, "Checked out (override)", "success")
    except Exception as e: deps.add_flash(request,str(e),"error")
    r=deps.redirect(f"/reservations/{rid}"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/reservations/{rid}/cancel")
async def cancel(rid: int, request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try: services.cancel_reservation(rid, form.get("password","")); deps.add_flash(request,"Cancelled","success")
    except Exception as e: deps.add_flash(request,str(e),"error")
    r=deps.redirect(f"/reservations/{rid}"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/reservations/{rid}/noshow")
async def noshow(rid: int, request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try: services.mark_no_show(rid, form.get("password","")); deps.add_flash(request,"Marked no-show","success")
    except Exception as e: deps.add_flash(request,str(e),"error")
    r=deps.redirect(f"/reservations/{rid}"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/reservations/{rid}/delete")
async def delete(rid: int, request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try: services.delete_reservation(rid, form.get("password","")); deps.add_flash(request,"Deleted","success"); dest="/reservations"
    except Exception as e: deps.add_flash(request,str(e),"error"); dest=f"/reservations/{rid}"
    r=deps.redirect(dest); deps.save_session(r, deps.get_session(request)); return r

@router.post("/reservations/{rid}/modify")
async def modify(rid: int, request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        services.modify_reservation(rid, float(form.get("rate") or 0), form.get("check_in",""), form.get("check_out",""), int(form.get("room_id")), form.get("password",""))
        deps.add_flash(request,"Reservation modified","success")
    except Exception as e: deps.add_flash(request,str(e),"error")
    r=deps.redirect(f"/reservations/{rid}"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/reservations/{rid}/pay")
async def pay(rid: int, request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        services.record_payment(rid, float(form.get("amount") or 0), form.get("method","Cash"), form.get("notes",""), form.get("payment_type","Payment"))
        deps.add_flash(request,"Payment recorded","success")
    except Exception as e: deps.add_flash(request,str(e),"error")
    r=deps.redirect(f"/reservations/{rid}"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/reservations/{rid}/refund")
async def refund(rid: int, request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        services.record_refund(rid, float(form.get("amount") or 0), form.get("method","Cash"), form.get("notes",""), form.get("password",""))
        deps.add_flash(request,"Refund recorded","success")
    except Exception as e: deps.add_flash(request,str(e),"error")
    r=deps.redirect(f"/reservations/{rid}"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/reservations/{rid}/invoice")
async def invoice(rid: int, request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        text = services.generate_invoice_text(rid, form.get("currency","EGP"))
        path = services.save_invoice_file(text, rid, form.get("currency","EGP"))
        deps.add_flash(request, f"Invoice saved: {Path(path).name}", "success")
        r=deps.redirect(f"/files/invoice/{Path(path).name}")
    except Exception as e:
        deps.add_flash(request,str(e),"error"); r=deps.redirect(f"/reservations/{rid}")
    deps.save_session(r, deps.get_session(request)); return r
