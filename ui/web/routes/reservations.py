from __future__ import annotations
from pathlib import Path
from datetime import datetime, timedelta
from fastapi import APIRouter, Form, Request
from app_core import barcodes, services
from app_core.db import connect
from ui.web import deps
from ui.web.rendering import render
router = APIRouter()


def _enrich_pos_rows(rows):
    """Attach batch trace fields (received/expiry/state) to POS rows when missing."""
    out = []
    warn = 7
    try:
        warn = int(services.get_setting("expiry_warn_days", "7") or 7)
    except Exception:
        pass
    batch_ids = {r["batch_id"] for r in rows if r["batch_id"]}
    batch_map = {}
    if batch_ids:
        ph = ",".join("?" * len(batch_ids))
        b_rows = connect().execute(
            f"SELECT id, lot_code, received_at, expiry_date, quantity_received, quantity_remaining, supplier FROM inventory_batches WHERE id IN ({ph})",
            tuple(batch_ids),
        ).fetchall()
        for b in b_rows:
            bd = dict(b)
            st, days = barcodes.expiry_status(bd.get("expiry_date"), warn)
            bd["expiry_state"] = st
            bd["days_to_expiry"] = days
            batch_map[bd["id"]] = bd
    for r in rows:
        d = dict(r)
        b = batch_map.get(d.get("batch_id"))
        if b:
            d.setdefault("lot_code", b.get("lot_code"))
            d["batch_lot_code"] = b.get("lot_code")
            d["batch_received_at"] = b.get("received_at")
            d["batch_expiry_date"] = b.get("expiry_date")
            d["batch_expiry_state"] = b.get("expiry_state")
            d["batch_days_to_expiry"] = b.get("days_to_expiry")
            d["batch_supplier"] = b.get("supplier")
        out.append(d)
    return out

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
    form = await deps.get_form(request)
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
    pos = connect().execute(
        """SELECT p.*, i.item_name AS inventory_item_name, i.track_batches, a.name AS activity_name
           FROM pos_tabs p
           LEFT JOIN inventory i ON i.id = p.inventory_item_id
           LEFT JOIN activities a ON a.id = p.activity_id
           WHERE p.reservation_id=? ORDER BY p.id DESC""", (rid,)
    ).fetchall()
    pos = _enrich_pos_rows(pos)
    pays = connect().execute("SELECT * FROM payments WHERE reservation_id=? ORDER BY id DESC", (rid,)).fetchall()
    try:
        activities = services.list_activities(active_only=True)
    except Exception:
        activities = []
    try:
        inventory = services.get_inventory_items("")
    except Exception:
        inventory = []
    try:
        active_batches = services.list_inventory_batches(include_depleted=False)
    except Exception:
        active_batches = []
    return render(request, "reservations/detail.html", {
        "title":f"Reservation #{rid}","r":res,"folio":folio,"pos":pos,"pays":pays,
        "rooms":_rooms(),"activities":activities,"inventory":inventory,"active_batches":active_batches,
        "today":datetime.now().strftime("%Y-%m-%d")
    })

async def _act(request, rid, fn, ok):
    form = await deps.get_form(request)
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
    form = await deps.get_form(request)
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
    form = await deps.require_unlocked_form(request)
    try:
        services.checkout_reservation(rid, override=True, password=deps.service_password(request, form))
        deps.add_flash(request, "Checked out (override)", "success")
    except Exception as e: deps.add_flash(request,str(e),"error")
    r=deps.redirect(f"/reservations/{rid}"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/reservations/{rid}/cancel")
async def cancel(rid: int, request: Request):
    form = await deps.require_unlocked_form(request)
    try: services.cancel_reservation(rid, deps.service_password(request, form)); deps.add_flash(request,"Cancelled","success")
    except Exception as e: deps.add_flash(request,str(e),"error")
    r=deps.redirect(f"/reservations/{rid}"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/reservations/{rid}/noshow")
async def noshow(rid: int, request: Request):
    form = await deps.require_unlocked_form(request)
    try: services.mark_no_show(rid, deps.service_password(request, form)); deps.add_flash(request,"Marked no-show","success")
    except Exception as e: deps.add_flash(request,str(e),"error")
    r=deps.redirect(f"/reservations/{rid}"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/reservations/{rid}/delete")
async def delete(rid: int, request: Request):
    form = await deps.require_unlocked_form(request)
    try: services.delete_reservation(rid, deps.service_password(request, form)); deps.add_flash(request,"Deleted","success"); dest="/reservations"
    except Exception as e: deps.add_flash(request,str(e),"error"); dest=f"/reservations/{rid}"
    r=deps.redirect(dest); deps.save_session(r, deps.get_session(request)); return r

@router.post("/reservations/{rid}/modify")
async def modify(rid: int, request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        services.modify_reservation(rid, float(form.get("rate") or 0), form.get("check_in",""), form.get("check_out",""), int(form.get("room_id")), deps.service_password(request, form))
        deps.add_flash(request,"Reservation modified","success")
    except Exception as e: deps.add_flash(request,str(e),"error")
    r=deps.redirect(f"/reservations/{rid}"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/reservations/{rid}/pay")
async def pay(rid: int, request: Request):
    form = await deps.get_form(request)
    try:
        services.record_payment(rid, float(form.get("amount") or 0), form.get("method","Cash"), form.get("notes",""), form.get("payment_type","Payment"))
        deps.add_flash(request,"Payment recorded","success")
    except Exception as e: deps.add_flash(request,str(e),"error")
    r=deps.redirect(f"/reservations/{rid}"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/reservations/{rid}/refund")
async def refund(rid: int, request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        services.record_refund(rid, float(form.get("amount") or 0), form.get("method","Cash"), form.get("notes",""), deps.service_password(request, form))
        deps.add_flash(request,"Refund recorded","success")
    except Exception as e: deps.add_flash(request,str(e),"error")
    r=deps.redirect(f"/reservations/{rid}"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/reservations/{rid}/invoice")
async def invoice(rid: int, request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        # Explicit proof accepted if provided; service currently does not require password.
        _ = deps.service_password(request, form)
        text = services.generate_invoice_text(rid, form.get("currency","EGP"))
        path = services.save_invoice_file(text, rid, form.get("currency","EGP"))
        deps.add_flash(request, f"Invoice saved: {Path(path).name}", "success")
        r=deps.redirect(f"/files/invoice/{Path(path).name}")
    except Exception as e:
        deps.add_flash(request,str(e),"error"); r=deps.redirect(f"/reservations/{rid}")
    deps.save_session(r, deps.get_session(request)); return r


@router.post("/reservations/{rid}/book-activity")
async def book_activity(rid: int, request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        # Explicit proof accepted if provided; backend enforces any required checks.
        _ = deps.service_password(request, form)
        act_id = int(form.get("activity_id"))
        qty = int(form.get("qty") or 1)
        scheduled_at = (form.get("scheduled_at") or "").strip() or None
        notes = (form.get("notes") or "").strip()
        res = services.book_activity(rid, act_id, qty, scheduled_at=scheduled_at, notes=notes)
        deps.add_flash(request, f"Activity/trip booked (POS #{res.get('pos_id')}) — added to reservation folio & invoice", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect(f"/reservations/{rid}"); deps.save_session(r, deps.get_session(request)); return r
