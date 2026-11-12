from __future__ import annotations
from fastapi import APIRouter, Request
from app_core import barcodes, services
from app_core.db import connect
from ui.web import deps
from ui.web.rendering import render

router = APIRouter()


def _batch_map(batch_ids):
    ids = {int(b) for b in batch_ids if b}
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    rows = connect().execute(
        f"SELECT id, lot_code, received_at, expiry_date, supplier FROM inventory_batches WHERE id IN ({ph})",
        tuple(ids),
    ).fetchall()
    warn = 7
    try:
        warn = int(services.get_setting("expiry_warn_days", "7") or 7)
    except Exception:
        pass
    out = {}
    for r in rows:
        d = dict(r)
        st, days = barcodes.expiry_status(d.get("expiry_date"), warn)
        d["expiry_state"] = st
        d["days_to_expiry"] = days
        out[d["id"]] = d
    return out


def _enrich_pos_items(items, bmap):
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        b = bmap.get(it.get("batch_id"))
        if not b:
            continue
        it.setdefault("lot_code", b.get("lot_code"))
        it["batch_lot_code"] = b.get("lot_code")
        it["batch_received_at"] = b.get("received_at")
        it["batch_expiry_date"] = b.get("expiry_date")
        it["batch_expiry_state"] = b.get("expiry_state")
        it["batch_days_to_expiry"] = b.get("days_to_expiry")
        it["batch_supplier"] = b.get("supplier")
    return items


def _enrich_portfolio(portfolio):
    if not portfolio:
        return portfolio
    batch_ids = set()
    for trip in (portfolio.get("trips") or []):
        for it in (trip.get("pos_items") or []):
            if isinstance(it, dict) and it.get("batch_id"):
                batch_ids.add(it.get("batch_id"))
        for it in (trip.get("pos_active") or []):
            if isinstance(it, dict) and it.get("batch_id"):
                batch_ids.add(it.get("batch_id"))
    for it in (portfolio.get("all_pos") or []):
        if isinstance(it, dict) and it.get("batch_id"):
            batch_ids.add(it.get("batch_id"))
    bmap = _batch_map(batch_ids)
    for trip in (portfolio.get("trips") or []):
        _enrich_pos_items(trip.get("pos_items") or [], bmap)
        _enrich_pos_items(trip.get("pos_active") or [], bmap)
    _enrich_pos_items(portfolio.get("all_pos") or [], bmap)
    return portfolio

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
    portfolio = _enrich_portfolio(portfolio)
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
