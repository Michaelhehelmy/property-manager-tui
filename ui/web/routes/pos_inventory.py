from __future__ import annotations

from pathlib import Path
from fastapi import APIRouter, Request
from app_core import services
from app_core.db import connect
from ui.web import deps
from ui.web.rendering import render

router = APIRouter()


def _get_cart(request: Request):
    cart = deps.get_session(request).get("walkin_cart")
    if not isinstance(cart, list):
        cart = []
        deps.get_session(request)["walkin_cart"] = cart
    return cart


def _cart_view(cart):
    lines = []
    total = 0.0
    for i, it in enumerate(cart):
        qty = int(it.get("qty") or 0)
        price = float(it.get("price") or 0)
        tax = services.calculate_tax(price * qty, 0)
        gross = float(tax.get("gross", 0) or 0)
        total += gross
        lines.append({
            "idx": i,
            "desc": it.get("desc") or "",
            "qty": qty,
            "price": price,
            "inv_id": it.get("inv_id"),
            "tax": round(float(tax.get("service", 0) or 0) + float(tax.get("vat", 0) or 0) + float(tax.get("city_tax", 0) or 0), 2),
            "gross": round(gross, 2),
        })
    return lines, round(total, 2)


@router.get("/pos")
def pos(request: Request, q: str = ""):
    try:
        inv = services.search_inventory(q)
    except Exception:
        inv = connect().execute(
            "SELECT * FROM inventory WHERE active=1 AND item_name LIKE ? ORDER BY item_name LIMIT 200",
            (f"%{q}%",),
        ).fetchall()
    res = connect().execute(
        "SELECT id, guest_name, status FROM reservations WHERE status IN ('Reserved','Checked In') ORDER BY id DESC LIMIT 100"
    ).fetchall()
    cart_raw = _get_cart(request)
    cart, cart_total = _cart_view(cart_raw)
    walkin_pos = services.list_walkin_pos(limit=30)
    walkin_payments = services.list_walkin_payments(limit=20)
    return render(request, "pos/index.html", {
        "title": "POS / Inventory",
        "inv": inv,
        "reservations": res,
        "q": q,
        "cart": cart,
        "cart_total": cart_total,
        "walkin_pos": walkin_pos,
        "walkin_payments": walkin_payments,
    })


@router.post("/pos/add")
async def pos_add(request: Request):
    """Add a single POS item directly to an existing reservation (real backend)."""
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        rid = form.get("reservation_id"); rid = int(rid) if rid else None
        inv_id = form.get("inv_id"); inv_id = int(inv_id) if inv_id else None
        services.add_pos_item(rid, form.get("desc", "Item"), int(form.get("qty") or 1), float(form.get("price") or 0), inv_id)
        deps.add_flash(request, "POS item added to reservation", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/pos"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/pos/cart/add")
async def cart_add(request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        desc = (form.get("desc") or "").strip() or "Item"
        qty = int(form.get("qty") or 1)
        price = float(form.get("price") or 0)
        inv_id = form.get("inv_id")
        inv_id = int(inv_id) if inv_id not in (None, "", "None") else None
        if qty <= 0:
            raise ValueError("Quantity must be positive.")
        if price < 0:
            raise ValueError("Price cannot be negative.")
        cart = _get_cart(request)
        cart.append({"desc": desc, "qty": qty, "price": price, "inv_id": inv_id})
        deps.get_session(request)["walkin_cart"] = cart
        deps.add_flash(request, f"Added to walk-in cart: {desc} x{qty}", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/pos"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/pos/cart/remove")
async def cart_remove(request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        idx = int(form.get("idx"))
        cart = _get_cart(request)
        if 0 <= idx < len(cart):
            removed = cart.pop(idx)
            deps.get_session(request)["walkin_cart"] = cart
            deps.add_flash(request, f"Removed: {removed.get('desc','item')}", "info")
        else:
            deps.add_flash(request, "Cart item not found.", "error")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/pos"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/pos/cart/clear")
async def cart_clear(request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    deps.get_session(request)["walkin_cart"] = []
    deps.add_flash(request, "Walk-in cart cleared.", "info")
    r = deps.redirect("/pos"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/pos/walkin")
async def walkin(request: Request):
    """Atomic walk-in checkout. Uses multi-item session cart when present; supports legacy single-item form."""
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        method = form.get("method", "Cash")
        notes = form.get("notes", "")
        cart = list(_get_cart(request))
        # Legacy single-item fallback only when cart is empty and form has explicit price
        if not cart and form.get("price") not in (None, "") and form.get("legacy_single"):
            inv_id = form.get("inv_id"); inv_id = int(inv_id) if inv_id not in (None, "", "None") else None
            cart = [{"desc": form.get("desc", "Walk-in"), "qty": int(form.get("qty") or 1), "price": float(form.get("price") or 0), "inv_id": inv_id}]
        if not cart:
            raise ValueError("Walk-in cart is empty. Add items to cart before checkout.")
        res = services.complete_walkin_sale(cart, method, notes, "Payment")
        deps.get_session(request)["walkin_cart"] = []
        name = res.get("receipt_name") or ""
        if name:
            deps.add_flash(request, f"Walk-in sale completed ({len(res.get('pos_ids',[]))} items, {services.fmt(res['total_gross'])}). Receipt: {name}", "success")
            r = deps.redirect(f"/files/invoice/{name}")
        else:
            deps.add_flash(request, f"Walk-in sale completed: {services.fmt(res['total_gross'])}", "success")
            r = deps.redirect("/pos")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
        r = deps.redirect("/pos")
    deps.save_session(r, deps.get_session(request)); return r


@router.post("/pos/void/{pos_id}")
async def pos_void(pos_id: int, request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        services.void_pos_item(pos_id, form.get("password", ""))
        deps.add_flash(request, "POS voided", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    ref = form.get("next") or "/pos"
    r = deps.redirect(ref); deps.save_session(r, deps.get_session(request)); return r


@router.post("/inventory/restock")
async def restock(request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        services.restock_inventory(int(form.get("item_id")), float(form.get("qty") or 0), float(form.get("unit_cost") or 0), form.get("password", ""))
        deps.add_flash(request, "Inventory restocked", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/pos"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/inventory/add")
async def inv_add(request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        services.add_inventory_item(form.get("name", ""), form.get("category", ""), form.get("unit", ""), float(form.get("stock") or 0),
            float(form.get("unit_cost") or 0), float(form.get("selling_price") or 0), float(form.get("low") or 0), float(form.get("over") or 0), form.get("password", ""))
        deps.add_flash(request, "Inventory item added", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/pos"); deps.save_session(r, deps.get_session(request)); return r
