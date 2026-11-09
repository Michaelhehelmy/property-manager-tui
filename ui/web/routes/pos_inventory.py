from __future__ import annotations
from pathlib import Path
from fastapi import APIRouter, Request
from app_core import services
from app_core.db import connect
from ui.web import deps
from ui.web.rendering import render
router = APIRouter()

@router.get("/pos")
def pos(request: Request, q: str = ""):
    inv = services.search_inventory(q, "", "All") if hasattr(services, 'search_inventory') else []
    try: inv = services.search_inventory(q)
    except Exception: inv = connect().execute("SELECT * FROM inventory WHERE active=1 AND item_name LIKE ? ORDER BY item_name LIMIT 200", (f"%{q}%",)).fetchall()
    res = connect().execute("SELECT id, guest_name, status FROM reservations WHERE status IN ('Reserved','Checked In') ORDER BY id DESC LIMIT 100").fetchall()
    return render(request, "pos/index.html", {"title":"POS / Inventory","inv":inv,"reservations":res,"q":q})

@router.post("/pos/add")
async def pos_add(request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        rid = form.get("reservation_id"); rid = int(rid) if rid else None
        inv_id = form.get("inv_id"); inv_id = int(inv_id) if inv_id else None
        services.add_pos_item(rid, form.get("desc","Item"), int(form.get("qty") or 1), float(form.get("price") or 0), inv_id)
        deps.add_flash(request, "POS item added", "success")
    except Exception as e: deps.add_flash(request, str(e), "error")
    r=deps.redirect("/pos"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/pos/walkin")
async def walkin(request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        # simple single-item walk-in from form; full cart can be extended
        items=[{"desc":form.get("desc","Walk-in"),"qty":int(form.get("qty") or 1),"price":float(form.get("price") or 0),"inv_id":int(form["inv_id"]) if form.get("inv_id") else None}]
        res = services.complete_walkin_sale(items, form.get("method","Cash"), form.get("notes",""), "Payment")
        deps.add_flash(request, f"Walk-in sale completed: {services.fmt(res['total_gross'])}", "success")
    except Exception as e: deps.add_flash(request, str(e), "error")
    r=deps.redirect("/pos"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/pos/void/{pos_id}")
async def pos_void(pos_id: int, request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try: services.void_pos_item(pos_id, form.get("password","")); deps.add_flash(request,"POS voided","success")
    except Exception as e: deps.add_flash(request,str(e),"error")
    ref = form.get("next") or "/pos"; r=deps.redirect(ref); deps.save_session(r, deps.get_session(request)); return r

@router.post("/inventory/restock")
async def restock(request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        services.restock_inventory(int(form.get("item_id")), float(form.get("qty") or 0), float(form.get("unit_cost") or 0), form.get("password",""))
        deps.add_flash(request, "Inventory restocked", "success")
    except Exception as e: deps.add_flash(request, str(e), "error")
    r=deps.redirect("/pos"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/inventory/add")
async def inv_add(request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        services.add_inventory_item(form.get("name",""), form.get("category",""), form.get("unit",""), float(form.get("stock") or 0),
            float(form.get("unit_cost") or 0), float(form.get("selling_price") or 0), float(form.get("low") or 0), float(form.get("over") or 0), form.get("password",""))
        deps.add_flash(request, "Inventory item added", "success")
    except Exception as e: deps.add_flash(request, str(e), "error")
    r=deps.redirect("/pos"); deps.save_session(r, deps.get_session(request)); return r
