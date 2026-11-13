from __future__ import annotations

from pathlib import Path
from fastapi import APIRouter, Request
from app_core import barcodes, services
from app_core.db import connect
from ui.web import deps
from ui.web.rendering import render

router = APIRouter()


def _warn_days() -> int:
    try:
        return int(services.get_setting("expiry_warn_days", "7") or 7)
    except Exception:
        return 7


def _enrich_pos_rows(rows):
    out = []
    warn = _warn_days()
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


def _get_cart(request: Request):
    cart = deps.get_session(request).get("walkin_cart")
    if not isinstance(cart, list):
        cart = []
        deps.get_session(request)["walkin_cart"] = cart
    return cart


def _batch_trace(batch_id=None, lot_code=None):
    if not batch_id and not lot_code:
        return {}
    row = None
    try:
        if batch_id:
            row = connect().execute(
                "SELECT id, lot_code, received_at, expiry_date, supplier, quantity_remaining FROM inventory_batches WHERE id=?",
                (int(batch_id),),
            ).fetchone()
        elif lot_code:
            row = connect().execute(
                "SELECT id, lot_code, received_at, expiry_date, supplier, quantity_remaining FROM inventory_batches WHERE lot_code=? ORDER BY id DESC LIMIT 1",
                (str(lot_code).strip(),),
            ).fetchone()
    except Exception:
        row = None
    if not row:
        return {"lot_code": (lot_code or "").strip()}
    d = dict(row)
    st, days = barcodes.expiry_status(d.get("expiry_date"), _warn_days())
    return {
        "batch_id": d.get("id"),
        "lot_code": d.get("lot_code"),
        "batch_received_at": d.get("received_at"),
        "batch_expiry_date": d.get("expiry_date"),
        "batch_expiry_state": st,
        "batch_days_to_expiry": days,
        "batch_supplier": d.get("supplier"),
    }


def _cart_view(cart):
    lines = []
    total = 0.0
    for i, it in enumerate(cart):
        qty = int(it.get("qty") or 0)
        price = float(it.get("price") or 0)
        tax = services.calculate_tax(price * qty, 0)
        gross = float(tax.get("gross", 0) or 0)
        total += gross
        line = {
            "idx": i,
            "desc": it.get("desc") or "",
            "qty": qty,
            "price": price,
            "inv_id": it.get("inv_id"),
            "batch_id": it.get("batch_id"),
            "lot_code": (it.get("lot_code") or "").strip(),
            "tax": round(float(tax.get("service", 0) or 0) + float(tax.get("vat", 0) or 0) + float(tax.get("city_tax", 0) or 0), 2),
            "gross": round(gross, 2),
        }
        # Prefer persisted trace on cart item; enrich from DB if missing (graceful if gone).
        for k in ("batch_received_at", "batch_expiry_date", "batch_expiry_state", "batch_days_to_expiry", "batch_supplier"):
            if it.get(k):
                line[k] = it.get(k)
        if not line.get("batch_received_at") and (line.get("batch_id") or line.get("lot_code")):
            line.update({k: v for k, v in _batch_trace(line.get("batch_id"), line.get("lot_code")).items() if v not in (None, "")})
        lines.append(line)
    return lines, round(total, 2)


@router.get("/pos")
def pos(request: Request, q: str = "", scan: str = "", batch_item: str = "", tab: str = "sell"):
    cart = _get_cart(request)
    reservations = connect().execute("""
        SELECT r.id, r.guest_name, r.status, rm.room_number
        FROM reservations r LEFT JOIN rooms rm ON r.room_id=rm.id
        WHERE r.status NOT IN ('Cancelled','No-Show','Deleted')
        ORDER BY r.check_in_date DESC, r.id DESC
    """).fetchall()
    inv = services.get_inventory_items(q)
    try:
        activities = services.list_activities(active_only=False)
        activity_stats = services.get_activity_stats()
    except Exception:
        activities, activity_stats = [], []
    try:
        meals = services.list_meals(active_only=False) if hasattr(services, 'list_meals') else []
    except Exception:
        meals = []
    walkin_pos = connect().execute(
        """SELECT p.*, i.item_name AS inventory_item_name, a.name AS activity_name
           FROM pos_tabs p
           LEFT JOIN inventory i ON i.id = p.inventory_item_id
           LEFT JOIN activities a ON a.id = p.activity_id
           WHERE p.reservation_id IS NULL ORDER BY p.id DESC LIMIT 20"""
    ).fetchall()
    walkin_pos = _enrich_pos_rows(walkin_pos)
    walkin_payments = connect().execute("SELECT * FROM payments WHERE reservation_id IS NULL ORDER BY id DESC LIMIT 20").fetchall()
    scan_result = None
    if scan:
        try:
            scan_result = services.resolve_barcode(scan)
        except Exception as e:
            scan_result = {"type": "error", "code": scan, "error": str(e)}
    try:
        expiring = services.get_expiring_batches(None, include_expired=True)
    except Exception:
        expiring = []
    batches = []
    try:
        bi = int(batch_item) if batch_item else None
    except Exception:
        bi = None
    try:
        batches = services.list_inventory_batches(item_id=bi, include_depleted=False)
    except Exception:
        batches = []
    cart_lines, cart_total = _cart_view(cart)
    allowed_tabs = {"sell", "inventory", "batches", "meals", "activities", "history"}
    tab = (tab or "sell").strip().lower()
    if tab not in allowed_tabs:
        tab = "sell"
    # keep user in relevant context
    if scan or scan_result:
        tab = "sell"
    if batch_item and tab == "sell":
        tab = "batches"
    return render(request, "pos/index.html", {
        "title":"POS / Inventory", "active_tab": tab,
        "cart": cart_lines, "cart_lines": cart_lines, "cart_total": cart_total,
        "reservations": reservations, "inv": inv, "activities": activities, "activity_stats": activity_stats,
        "q": q, "meals": meals, "walkin_pos": walkin_pos, "walkin_payments": walkin_payments,
        "scan": scan, "scan_result": scan_result, "expiring_batches": expiring, "batches": batches, "batch_item": batch_item,
    })


@router.post("/pos/add")
async def pos_add(request: Request):
    """Add a single POS item directly to an existing reservation (real backend)."""
    form = await deps.require_unlocked_form(request)
    next_url = (form.get("next") or "/pos?tab=sell").strip() or "/pos?tab=sell"
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/pos?tab=sell"
    try:
        rid = form.get("reservation_id"); rid = int(rid) if rid else None
        inv_id = form.get("inv_id"); inv_id = int(inv_id) if inv_id else None
        batch_id = form.get("batch_id"); batch_id = int(batch_id) if batch_id not in (None, "", "None") else None
        lot_code = (form.get("lot_code") or "").strip() or None
        services.add_pos_item(rid, form.get("desc", "Item"), int(form.get("qty") or 1), float(form.get("price") or 0), inv_id, batch_id=batch_id, lot_code=lot_code)
        deps.add_flash(request, "POS item added to reservation", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect(next_url); deps.save_session(r, deps.get_session(request)); return r


@router.post("/pos/cart/add")
async def cart_add(request: Request):
    form = await deps.get_form(request)
    try:
        desc = (form.get("desc") or "").strip() or "Item"
        qty = int(form.get("qty") or 1)
        price = float(form.get("price") or 0)
        inv_id = form.get("inv_id")
        inv_id = int(inv_id) if inv_id not in (None, "", "None") else None
        batch_id = form.get("batch_id")
        batch_id = int(batch_id) if batch_id not in (None, "", "None") else None
        lot_code = (form.get("lot_code") or "").strip() or None
        if qty <= 0:
            raise ValueError("Quantity must be positive.")
        if price < 0:
            raise ValueError("Price cannot be negative.")
        cart = _get_cart(request)
        item = {"desc": desc, "qty": qty, "price": price, "inv_id": inv_id, "batch_id": batch_id, "lot_code": lot_code}
        # Persist trace metadata for UI/receipt context (graceful if missing).
        item.update({k: v for k, v in _batch_trace(batch_id, lot_code).items() if v not in (None, "")})
        cart.append(item)
        deps.get_session(request)["walkin_cart"] = cart
        deps.add_flash(request, f"Added to walk-in cart: {desc} x{qty}", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/pos?tab=sell"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/pos/cart/remove")
async def cart_remove(request: Request):
    form = await deps.get_form(request)
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
    r = deps.redirect("/pos?tab=sell"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/pos/cart/clear")
async def cart_clear(request: Request):
    form = await deps.get_form(request)
    deps.get_session(request)["walkin_cart"] = []
    deps.add_flash(request, "Walk-in cart cleared.", "info")
    r = deps.redirect("/pos?tab=sell"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/pos/walkin")
async def walkin(request: Request):
    """Atomic walk-in checkout. Uses multi-item session cart when present; supports legacy single-item form."""
    form = await deps.get_form(request)
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
            r = deps.redirect("/pos?tab=sell")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
        r = deps.redirect("/pos?tab=sell")
    deps.save_session(r, deps.get_session(request)); return r


@router.post("/pos/void/{pos_id}")
async def pos_void(pos_id: int, request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        services.void_pos_item(pos_id, deps.service_password(request, form))
        deps.add_flash(request, "POS voided", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    ref = (form.get("next") or "/pos?tab=history").strip() or "/pos?tab=history"
    if not ref.startswith("/") or ref.startswith("//"):
        ref = "/pos?tab=history"
    r = deps.redirect(ref); deps.save_session(r, deps.get_session(request)); return r


@router.post("/inventory/restock")
async def restock(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        services.restock_inventory(
            int(form.get("item_id")),
            float(form.get("qty") or 0),
            float(form.get("unit_cost") or 0),
            deps.service_password(request, form, allow_session_sentinel=True),
        )
        deps.add_flash(request, "Inventory restocked (batch recorded with received time)", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/pos?tab=inventory"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/inventory/add")
async def inv_add(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        services.add_inventory_item(
            form.get("name", ""), form.get("category", ""), form.get("unit", ""), float(form.get("stock") or 0),
            float(form.get("unit_cost") or 0), float(form.get("selling_price") or 0), float(form.get("low") or 0), float(form.get("over") or 0), deps.service_password(request, form),
            sku=form.get("sku",""), barcode=form.get("barcode",""), barcode_type=form.get("barcode_type","code128"),
            track_batches=form.get("track_batches","0"), shelf_life_days=form.get("shelf_life_days") or None, default_supplier=form.get("default_supplier","")
        )
        deps.add_flash(request, "Inventory item added", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/pos?tab=inventory"); deps.save_session(r, deps.get_session(request)); return r


def _parse_recipe_components(text: str):
    comps = []
    for line in (text or "").replace(";", "\n").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.replace(":", ",")
        parts = [p.strip() for p in line.split(",") if p.strip()]
        if len(parts) >= 2:
            comps.append({"ingredient_item_id": parts[0], "qty_per_sale": parts[1]})
    return comps


@router.post("/recipes/set")
async def recipe_set(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        menu_id = int(form.get("menu_item_id") or form.get("item_id") or 0)
        comps = _parse_recipe_components(form.get("components") or "")
        services.set_recipe_components(menu_id, comps, deps.service_password(request, form))
        deps.add_flash(request, f"Recipe saved ({len(comps)} ingredients). Selling this meal auto-deducts ingredients.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/pos?tab=meals"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/recipes/clear")
async def recipe_clear(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        menu_id = int(form.get("menu_item_id") or form.get("item_id") or 0)
        services.clear_recipe_components(menu_id, deps.service_password(request, form))
        deps.add_flash(request, "Recipe cleared. Item uses normal stock deduction when sold.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/pos?tab=meals"); deps.save_session(r, deps.get_session(request)); return r


def _parse_meal_ingredients(text: str):
    comps = []
    for line in (text or "").replace(";", "\n").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.replace(":", ",")
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[0] and parts[1]:
            comps.append({"inventory_item_id": parts[0], "qty_per_sale": parts[1], "note": ",".join(parts[2:]).strip() if len(parts) > 2 else ""})
    return comps


@router.post("/meals/save")
async def meal_save(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        meal_id = (form.get("meal_id") or "").strip()
        name = form.get("name", "")
        desc = form.get("description", "")
        prep = form.get("preparation", "")
        price = form.get("selling_price") or 0
        category = form.get("category") or "Meals"
        pw = deps.service_password(request, form)
        ings = _parse_meal_ingredients(form.get("ingredients") or "")
        if meal_id:
            services.update_meal(int(meal_id), name, desc, prep, price, ings, pw, category=category, active=1)
            deps.add_flash(request, "Meal updated. Registered ingredients will auto-deduct when ordered.", "success")
        else:
            res = services.create_meal(name, desc, prep, price, ings, pw, category=category)
            deps.add_flash(request, f"Meal registered (POS inventory #{res.get('linked_inventory_id')}). Ingredients auto-deduct on order.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/pos?tab=meals"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/meals/delete")
async def meal_delete(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        services.delete_meal(int(form.get("meal_id") or 0), deps.service_password(request, form))
        deps.add_flash(request, "Meal deleted.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/pos?tab=meals"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/activities/create")
async def activity_create(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        _ = deps.service_password(request, form)
        services.create_activity(form.get("name",""), form.get("description",""), form.get("category","Trip"), float(form.get("unit_price") or 0), 1)
        deps.add_flash(request, "Activity/trip registered", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/pos?tab=activities"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/activities/{aid}/toggle")
async def activity_toggle(aid: int, request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        act = services.get_activity(aid)
        if not act: raise ValueError("Activity not found")
        services.set_activity_active(aid, 0 if act.get("active") else 1)
        deps.add_flash(request, "Activity updated", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/pos?tab=activities"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/inventory/batch/receive")
async def batch_receive(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        batch = services.receive_inventory_batch(
            int(form.get("item_id")), float(form.get("qty") or 0), float(form.get("unit_cost") or 0),
            received_at=(form.get("received_at") or "").strip() or None,
            expiry_date=(form.get("expiry_date") or "").strip() or None,
            supplier=form.get("supplier", ""), reference=form.get("reference", ""), notes=form.get("notes", ""),
            password=deps.service_password(request, form, allow_session_sentinel=True),
            print_label_after=bool(form.get("print_label")),
        )
        deps.add_flash(request, f"Batch received: lot {batch.get('lot_code')} (received {batch.get('received_at')}, expiry {batch.get('expiry_date') or 'n/a'})", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/pos?tab=batches"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/inventory/label/item/{item_id}")
async def label_item(item_id: int, request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        _ = deps.service_password(request, form)
        ok, msg, p = services.print_item_label(item_id)
        deps.add_flash(request, f"Item label: {msg} ({p})", "success" if ok else "warning")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    nxt = (form.get("next") or "/pos?tab=batches").strip()
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/pos?tab=batches"
    r = deps.redirect(nxt); deps.save_session(r, deps.get_session(request)); return r


@router.post("/inventory/label/batch/{batch_id}")
async def label_batch(batch_id: int, request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        _ = deps.service_password(request, form)
        ok, msg, p = services.print_batch_label(batch_id)
        deps.add_flash(request, f"Batch label: {msg} ({p})", "success" if ok else "warning")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    nxt = (form.get("next") or "/pos?tab=batches").strip()
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/pos?tab=batches"
    r = deps.redirect(nxt); deps.save_session(r, deps.get_session(request)); return r


@router.get("/inventory/scan")
def inventory_scan(request: Request, code: str = ""):
    # redirect to pos with scan param so UI shows resolved result
    from urllib.parse import quote
    r = deps.redirect(f"/pos?scan={quote(code or '')}")
    deps.save_session(r, deps.get_session(request)); return r

