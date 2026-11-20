from __future__ import annotations
import logging
import json
from pathlib import Path
from datetime import datetime, timedelta
from fastapi import APIRouter, Form, Request
from app_core import barcodes, services
from app_core.db import connect
from ui.web import deps
from ui.web.rendering import render
from ui.web.route_utils import flash_redirect

logger = logging.getLogger(__name__)
router = APIRouter()


def _enrich_pos_rows(rows):
    """Attach batch trace fields (received/expiry/state) to POS rows when missing."""
    out = []
    warn = 7
    try:
        warn = int(services.get_setting("expiry_warn_days", "7") or 7)
    except Exception:
        logger.debug("web.reservations: failed to read expiry_warn_days, using default", exc_info=True)
    batch_ids = {r["batch_id"] for r in rows if r["batch_id"]}
    batch_map = {}
    if batch_ids:
        ph = ",".join("?" * len(batch_ids))
        b_rows = (
            connect()
            .execute(
                f"SELECT id, lot_code, received_at, expiry_date, quantity_received, quantity_remaining, supplier FROM inventory_batches WHERE id IN ({ph})",
                tuple(batch_ids),
            )
            .fetchall()
        )
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


def _rooms():
    return connect().execute("SELECT id, room_number, room_type, base_price FROM rooms ORDER BY room_number").fetchall()


def _plans():
    return connect().execute("SELECT id, name, price_modifier FROM rate_plans ORDER BY name").fetchall()


@router.get("/reservations")
def list_res(request: Request, q: str = "", source: str = "", segment: str = "", tab: str = "list"):
    ql = f"%{q}%" if q else "%"
    sl = f"%{source}%" if source else "%"
    ml = f"%{segment}%" if segment else "%"
    rows = (
        connect()
        .execute(
            """
        SELECT r.*, rm.room_number, rp.name as rate_plan_name
        FROM reservations r LEFT JOIN rooms rm ON r.room_id=rm.id
        LEFT JOIN rate_plans rp ON r.rate_plan_id=rp.id
        WHERE r.status NOT IN ('Deleted')
          AND (? = '' OR r.guest_name LIKE ? OR CAST(r.id AS TEXT) LIKE ? OR rm.room_number LIKE ? OR COALESCE(r.source,'') LIKE ? OR COALESCE(r.source_detail,'') LIKE ? OR COALESCE(r.market_segment,'') LIKE ?)
          AND (? = '' OR COALESCE(r.source,'') LIKE ?)
          AND (? = '' OR COALESCE(r.market_segment,'') LIKE ?)
        ORDER BY r.id DESC LIMIT 200""",
            (q, ql, ql, ql, ql, ql, ql, source, sl, segment, ml),
        )
        .fetchall()
    )
    now = datetime.now()
    allowed = {"list", "create"}
    tab = (tab or "list").strip().lower()
    if tab not in allowed:
        tab = "list"
    if q or source or segment:
        tab = "list"
    # pricing quote context (create tab)
    quote = None
    quote_error = None
    if request.query_params.get("quote_error"):
        quote_error = request.query_params.get("quote_error")
    if request.query_params.get("quote") == "1":
        try:
            snap = json.loads(request.query_params.get("quote_json") or "{}")
            if snap:
                quote = snap
        except Exception:
            quote = None
    prefill = {
        k: request.query_params.get(k, "")
        for k in [
            "guest_name",
            "source",
            "source_detail",
            "market_segment",
            "room_id",
            "rate_plan_id",
            "check_in",
            "check_out",
            "rate",
            "deposit",
            "booking_notes",
        ]
    }
    return render(
        request,
        "reservations/list.html",
        {
            "title": "Reservations",
            "rows": rows,
            "q": q,
            "source": source,
            "segment": segment,
            "rooms": _rooms(),
            "plans": _plans(),
            "today": now.strftime("%Y-%m-%d"),
            "tomorrow": (now + timedelta(days=1)).strftime("%Y-%m-%d"),
            "active_tab": tab,
            "quote": quote,
            "quote_error": quote_error,
            "prefill": prefill,
        },
    )


@router.post("/reservations/quote")
async def quote(request: Request):
    form = await deps.get_form(request)
    try:
        room_id = int(form.get("room_id") or 0)
        rate_plan_id = int(form["rate_plan_id"]) if form.get("rate_plan_id") else None
        check_in = form.get("check_in", "")
        check_out = form.get("check_out", "")
        rate_in = form.get("rate")
        rate = float(rate_in) if rate_in not in (None, "") else None
        snap = services.quote_reservation_pricing(
            room_id,
            rate_plan_id,
            check_in,
            check_out,
            rate=rate,
            source=form.get("source", ""),
            market_segment=form.get("market_segment", ""),
        )
        params = {
            "tab": "create",
            "quote": "1",
            "quote_json": json.dumps(snap, ensure_ascii=False, default=str),
            "guest_name": form.get("guest_name", ""),
            "source": form.get("source", "Direct"),
            "source_detail": form.get("source_detail", ""),
            "market_segment": form.get("market_segment", ""),
            "room_id": str(room_id),
            "rate_plan_id": str(rate_plan_id or ""),
            "check_in": check_in,
            "check_out": check_out,
            "rate": str(
                (snap.get("pricing") or {}).get("quoted_nightly_rate")
                or (snap.get("pricing") or {}).get("rule_resolved_nightly")
                or rate
                or ""
            ),
            "deposit": form.get("deposit", "0"),
            "booking_notes": form.get("booking_notes", ""),
        }
        from urllib.parse import urlencode

        return deps.redirect("/reservations?" + urlencode(params))
    except Exception as e:
        params = {
            "tab": "create",
            "quote_error": str(e),
            "guest_name": form.get("guest_name", ""),
            "source": form.get("source", "Direct"),
            "source_detail": form.get("source_detail", ""),
            "market_segment": form.get("market_segment", ""),
            "room_id": form.get("room_id", ""),
            "rate_plan_id": form.get("rate_plan_id", ""),
            "check_in": form.get("check_in", ""),
            "check_out": form.get("check_out", ""),
            "rate": form.get("rate", ""),
            "deposit": form.get("deposit", "0"),
            "booking_notes": form.get("booking_notes", ""),
        }
        from urllib.parse import urlencode

        return deps.redirect("/reservations?" + urlencode(params))


@router.post("/reservations/create")
async def create(request: Request):
    form = await deps.get_form(request)
    try:
        rid = services.create_reservation(
            form.get("guest_name", ""),
            form.get("source", "Direct"),
            int(form.get("room_id")),
            int(form["rate_plan_id"]) if form.get("rate_plan_id") else None,
            float(form.get("rate") or 0),
            form.get("check_in", ""),
            form.get("check_out", ""),
            float(form.get("deposit") or 0),
            source_detail=form.get("source_detail", ""),
            market_segment=form.get("market_segment", ""),
            booking_notes=form.get("booking_notes", ""),
        )
        return flash_redirect(request, "/reservations?tab=list", f"Reservation #{rid} created", "success")
    except Exception as e:
        return flash_redirect(request, "/reservations?tab=create", str(e), "error")


# --- Reservation groups (P5 foundation) ---
@router.get("/reservations/groups")
def groups(request: Request):
    try:
        groups = services.list_reservation_groups(status="")
    except Exception:
        groups = []
    recent = (
        connect()
        .execute(
            "SELECT id, guest_name, status FROM reservations WHERE status NOT IN ('Deleted') ORDER BY id DESC LIMIT 200"
        )
        .fetchall()
    )
    return render(
        request,
        "reservations/groups.html",
        {"title": "Reservation Groups", "groups": groups, "recent_reservations": recent},
    )


@router.post("/reservations/groups/create")
async def groups_create(request: Request):
    form = await deps.require_unlocked_form(request, permission="reservations.manage")
    try:
        gid = services.create_reservation_group(
            form.get("name", ""),
            group_type=form.get("group_type", "group"),
            contact_name=form.get("contact_name", ""),
            contact_phone=form.get("contact_phone", ""),
            contact_email=form.get("contact_email", ""),
            notes=form.get("notes", ""),
        )
        return flash_redirect(request, "/reservations/groups", f"Group #{gid} created", "success")
    except Exception as e:
        return flash_redirect(request, "/reservations/groups", str(e), "error")


@router.post("/reservations/groups/{gid}/add")
async def groups_add(gid: int, request: Request):
    form = await deps.require_unlocked_form(request, permission="reservations.manage")
    try:
        services.add_reservation_to_group(
            gid, int(form.get("reservation_id")), role=form.get("role", "member"), notes=form.get("notes", "")
        )
        return flash_redirect(request, "/reservations/groups", "Reservation added to group", "success")
    except Exception as e:
        return flash_redirect(request, "/reservations/groups", str(e), "error")


@router.post("/reservations/groups/{gid}/remove/{rid}")
async def groups_remove(gid: int, rid: int, request: Request):
    await deps.require_unlocked_form(request, permission="reservations.manage")
    try:
        services.remove_reservation_from_group(gid, rid)
        return flash_redirect(request, "/reservations/groups", "Reservation removed from group", "success")
    except Exception as e:
        return flash_redirect(request, "/reservations/groups", str(e), "error")


@router.post("/reservations/groups/{gid}/update")
async def groups_update(gid: int, request: Request):
    form = await deps.require_unlocked_form(request, permission="reservations.manage")
    try:
        services.update_reservation_group(
            gid,
            name=form.get("name"),
            group_type=form.get("group_type"),
            contact_name=form.get("contact_name"),
            contact_phone=form.get("contact_phone"),
            contact_email=form.get("contact_email"),
            notes=form.get("notes"),
            status=form.get("status"),
        )
        return flash_redirect(request, "/reservations/groups", "Group updated", "success")
    except Exception as e:
        return flash_redirect(request, "/reservations/groups", str(e), "error")


@router.post("/reservations/groups/{gid}/payment")
async def group_record_payment(gid: int, request: Request):
    form = await deps.require_unlocked_form(request, permission="payments.record")
    try:
        rid = int(form.get("reservation_id") or 0)
        if not rid:
            raise ValueError("Select a group member reservation.")
        amount = float(form.get("amount") or 0)
        method = form.get("method", "Cash")
        payment_type = form.get("payment_type", "Payment")
        notes = form.get("notes", "")
        reference = (form.get("reference") or "").strip()
        # ensure member belongs to group (coordinated action safety)
        g = services.get_reservation_group(gid)
        if not g:
            raise ValueError("Reservation group not found.")
        member_ids = {
            int(m.get("reservation_id")) for m in (g.get("members") or []) if m.get("reservation_id") is not None
        }
        if rid not in member_ids:
            raise ValueError("Selected reservation is not a member of this group.")
        pid = services.record_payment(
            rid,
            amount,
            method,
            notes,
            payment_type=payment_type,
            idempotency_key=(form.get("idempotency_key") or "").strip(),
            reference=reference,
            allocation_json=(form.get("allocation_json") or "{}"),
        )
        deps.add_flash(request, f"Payment recorded for reservation #{rid} (payment #{pid}).", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/reservations/groups")
    deps.save_session(r, deps.get_session(request))
    return r


@router.get("/reservations/groups/{gid}/folio")
def group_folio(gid: int, request: Request):
    try:
        g = services.get_reservation_group(gid)
        if not g:
            deps.add_flash(request, "Group not found", "error")
            return deps.redirect("/reservations/groups")
        text = services.report_reservation_group_folio(gid)
        return render(
            request,
            "reports/view.html",
            {
                "title": f"Group Folio #{gid} - {g.get('name')}",
                "text": text,
                "kind": "group_folio",
                "period": "current",
            },
        )
    except Exception as e:
        deps.add_flash(request, str(e), "error")
        return deps.redirect("/reservations/groups")


@router.get("/reservations/{rid}")
def detail(rid: int, request: Request):
    res = services.get_reservation(rid)
    if not res:
        deps.add_flash(request, "Not found", "error")
        return deps.redirect("/reservations?tab=list")
    folio = services.get_balance_breakdown(rid) or {}
    pos = (
        connect()
        .execute(
            """SELECT p.*, i.item_name AS inventory_item_name, i.track_batches, a.name AS activity_name
           FROM pos_tabs p
           LEFT JOIN inventory i ON i.id = p.inventory_item_id
           LEFT JOIN activities a ON a.id = p.activity_id
           WHERE p.reservation_id=? ORDER BY p.id DESC""",
            (rid,),
        )
        .fetchall()
    )
    pos = _enrich_pos_rows(pos)
    pays = connect().execute("SELECT * FROM payments WHERE reservation_id=? ORDER BY id DESC", (rid,)).fetchall()
    try:
        activities = services.list_activities(active_only=True)
    except Exception:
        logger.exception("web.reservations: failed to load active activities")
        activities = []
    try:
        inventory = services.get_inventory_items("")
    except Exception:
        logger.exception("web.reservations: failed to load inventory items")
        inventory = []
    try:
        active_batches = services.list_inventory_batches(include_depleted=False)
    except Exception:
        logger.exception("web.reservations: failed to load active batches")
        active_batches = []
    try:
        reservation_services = services.list_reservation_services(rid)
    except Exception:
        logger.exception("web.reservations: failed to load reservation services")
        reservation_services = []
    try:
        service_catalog = services.list_service_catalog(active_only=True)
    except Exception:
        logger.exception("web.reservations: failed to load service catalog")
        service_catalog = []
    try:
        reservation_discounts = services.list_reservation_discounts(rid)
    except Exception:
        logger.exception("web.reservations: failed to load reservation discounts")
        reservation_discounts = []
    try:
        packages = services.list_packages(active_only=True)
    except Exception:
        packages = []
    try:
        reservation_packages = services.list_reservation_packages(rid)
    except Exception:
        reservation_packages = []
    try:
        reservation_group = services.get_reservation_group_membership(rid)
    except Exception:
        reservation_group = None
    modify_quote = None
    modify_quote_error = request.query_params.get("modify_quote_error")
    if request.query_params.get("modify_quote") == "1":
        try:
            modify_quote = json.loads(request.query_params.get("modify_quote_json") or "{}") or None
        except Exception:
            modify_quote = None
    modify_prefill = {
        k: request.query_params.get("m_" + k, "")
        for k in [
            "room_id",
            "check_in",
            "check_out",
            "rate",
            "source",
            "source_detail",
            "market_segment",
            "booking_notes",
        ]
    }
    return render(
        request,
        "reservations/detail.html",
        {
            "title": f"Reservation #{rid}",
            "r": res,
            "folio": folio,
            "pos": pos,
            "pays": pays,
            "rooms": _rooms(),
            "activities": activities,
            "inventory": inventory,
            "active_batches": active_batches,
            "reservation_services": reservation_services,
            "service_catalog": service_catalog,
            "reservation_discounts": reservation_discounts,
            "packages": packages,
            "reservation_packages": reservation_packages,
            "reservation_group": reservation_group,
            "modify_quote": modify_quote,
            "modify_quote_error": modify_quote_error,
            "modify_prefill": modify_prefill,
            "today": datetime.now().strftime("%Y-%m-%d"),
        },
    )


async def _act(request, rid, fn, ok):
    form = await deps.get_form(request)
    try:
        fn(form)
        return flash_redirect(request, f"/reservations/{rid}", ok, "success")
    except Exception as e:
        return flash_redirect(request, f"/reservations/{rid}", str(e), "error")


@router.post("/reservations/{rid}/checkin")
async def checkin(rid: int, request: Request):
    return await _act(request, rid, lambda f: services.update_reservation_status(rid, "Checked In"), "Checked in")


@router.post("/reservations/{rid}/checkout")
async def checkout(rid: int, request: Request):
    form = await deps.get_form(request)
    try:
        needs, bal, _ = services.checkout_requires_override(rid)
        if needs:
            deps.add_flash(
                request, f"Unpaid balance {services.fmt(bal)}. Use override checkout with password.", "error"
            )
        else:
            services.checkout_reservation(rid)
            deps.add_flash(request, "Checked out", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect(f"/reservations/{rid}")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/reservations/{rid}/checkout-override")
async def checkout_override(rid: int, request: Request):
    form = await deps.require_unlocked_form(request, permission="reservations.manage")
    try:
        services.checkout_reservation(rid, override=True, password=deps.service_password(request, form))
        deps.add_flash(request, "Checked out (override)", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect(f"/reservations/{rid}")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/reservations/{rid}/cancel")
async def cancel(rid: int, request: Request):
    form = await deps.require_unlocked_form(request, permission="reservations.manage")
    try:
        services.cancel_reservation(rid, deps.service_password(request, form))
        deps.add_flash(request, "Cancelled", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect(f"/reservations/{rid}")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/reservations/{rid}/noshow")
async def noshow(rid: int, request: Request):
    form = await deps.require_unlocked_form(request, permission="reservations.manage")
    try:
        services.mark_no_show(rid, deps.service_password(request, form))
        deps.add_flash(request, "Marked no-show", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect(f"/reservations/{rid}")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/reservations/{rid}/delete")
async def delete(rid: int, request: Request):
    form = await deps.require_unlocked_form(request, permission="reservations.manage")
    try:
        services.delete_reservation(rid, deps.service_password(request, form))
        deps.add_flash(request, "Deleted", "success")
        dest = "/reservations?tab=list"
    except Exception as e:
        deps.add_flash(request, str(e), "error")
        dest = f"/reservations/{rid}"
    r = deps.redirect(dest)
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/reservations/{rid}/quote-modify")
async def quote_modify(rid: int, request: Request):
    form = await deps.get_form(request)
    try:
        room_id = int(form.get("room_id") or 0)
        check_in = form.get("check_in", "")
        check_out = form.get("check_out", "")
        rate_in = form.get("rate")
        rate = float(rate_in) if rate_in not in (None, "") else None
        # use existing reservation rate_plan if available
        try:
            res = services.get_reservation(rid)
        except Exception:
            res = None
        rate_plan_id = None
        try:
            if res is not None:
                rate_plan_id = res["rate_plan_id"]
        except Exception:
            rate_plan_id = None
        snap = services.quote_reservation_pricing(
            room_id,
            rate_plan_id,
            check_in,
            check_out,
            rate=rate,
            source=form.get("source", ""),
            market_segment=form.get("market_segment", ""),
        )
        from urllib.parse import urlencode

        params = {
            "modify_quote": "1",
            "modify_quote_json": json.dumps(snap, ensure_ascii=False, default=str),
            "m_room_id": str(room_id),
            "m_check_in": check_in,
            "m_check_out": check_out,
            "m_rate": str(
                (snap.get("pricing") or {}).get("quoted_nightly_rate")
                or (snap.get("pricing") or {}).get("rule_resolved_nightly")
                or rate
                or ""
            ),
            "m_source": form.get("source", ""),
            "m_source_detail": form.get("source_detail", ""),
            "m_market_segment": form.get("market_segment", ""),
            "m_booking_notes": form.get("booking_notes", ""),
        }
        return deps.redirect(f"/reservations/{rid}?" + urlencode(params))
    except Exception as e:
        from urllib.parse import urlencode

        params = {
            "modify_quote_error": str(e),
            "m_room_id": form.get("room_id", ""),
            "m_check_in": form.get("check_in", ""),
            "m_check_out": form.get("check_out", ""),
            "m_rate": form.get("rate", ""),
            "m_source": form.get("source", ""),
            "m_source_detail": form.get("source_detail", ""),
            "m_market_segment": form.get("market_segment", ""),
            "m_booking_notes": form.get("booking_notes", ""),
        }
        return deps.redirect(f"/reservations/{rid}?" + urlencode(params))


@router.post("/reservations/{rid}/modify")
async def modify(rid: int, request: Request):
    form = await deps.require_unlocked_form(request, permission="reservations.manage")
    try:
        services.modify_reservation(
            rid,
            float(form.get("rate") or 0),
            form.get("check_in", ""),
            form.get("check_out", ""),
            int(form.get("room_id")),
            deps.service_password(request, form),
            source=form.get("source"),
            source_detail=form.get("source_detail"),
            market_segment=form.get("market_segment"),
            booking_notes=form.get("booking_notes"),
        )
        deps.add_flash(request, "Reservation modified", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect(f"/reservations/{rid}")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/reservations/{rid}/pay")
async def pay(rid: int, request: Request):
    form = await deps.get_form(request)
    try:
        services.record_payment(
            rid,
            float(form.get("amount") or 0),
            form.get("method", "Cash"),
            form.get("notes", ""),
            form.get("payment_type", "Payment"),
            idempotency_key=(form.get("idempotency_key") or "").strip(),
            reference=(form.get("reference") or "").strip(),
            allocation_json=(form.get("allocation_json") or "{}"),
        )
        deps.add_flash(request, "Payment recorded", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect(f"/reservations/{rid}")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/reservations/{rid}/payments/{payment_id}/void")
async def void_payment_route(rid: int, payment_id: int, request: Request):
    form = await deps.require_unlocked_form(request, permission="payments.manage")
    try:
        # ensure payment belongs to reservation
        row = connect().execute("SELECT reservation_id, voided_at FROM payments WHERE id=?", (payment_id,)).fetchone()
        if not row:
            raise ValueError("Payment not found.")
        if int(row["reservation_id"] or 0) != int(rid):
            raise ValueError("Payment does not belong to this reservation.")
        already = bool(row["voided_at"])
        services.void_payment(payment_id, deps.service_password(request, form))
        deps.add_flash(request, "Payment/refund already voided" if already else "Payment/refund voided", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect(f"/reservations/{rid}")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/reservations/{rid}/refund")
async def refund(rid: int, request: Request):
    form = await deps.require_unlocked_form(request, permission="payments.manage")
    try:
        services.record_refund(
            rid,
            float(form.get("amount") or 0),
            form.get("method", "Cash"),
            form.get("notes", ""),
            deps.service_password(request, form),
            idempotency_key=(form.get("idempotency_key") or "").strip(),
            reference=(form.get("reference") or "").strip(),
            allocation_json=(form.get("allocation_json") or "{}"),
        )
        deps.add_flash(request, "Refund recorded", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect(f"/reservations/{rid}")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/reservations/{rid}/invoice")
async def invoice(rid: int, request: Request):
    form = await deps.require_unlocked_form(request, permission="reservations.manage")
    try:
        text = services.generate_invoice_text(rid, form.get("currency", "EGP"))
        path = services.save_invoice_file(text, rid, form.get("currency", "EGP"))
        deps.add_flash(request, f"Invoice saved: {Path(path).name}", "success")
        r = deps.redirect(f"/files/invoice/{Path(path).name}")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
        r = deps.redirect(f"/reservations/{rid}")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/reservations/{rid}/book-activity")
async def book_activity(rid: int, request: Request):
    form = await deps.require_unlocked_form(request, permission="reservations.manage")
    try:
        act_id = int(form.get("activity_id"))
        qty = int(form.get("qty") or 1)
        scheduled_at = (form.get("scheduled_at") or "").strip() or None
        notes = (form.get("notes") or "").strip()
        res = services.book_activity(rid, act_id, qty, scheduled_at=scheduled_at, notes=notes)
        deps.add_flash(
            request,
            f"Activity/trip booked (POS #{res.get('pos_id')}) — added to reservation folio & invoice",
            "success",
        )
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect(f"/reservations/{rid}")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/reservations/{rid}/services/add")
async def add_service(rid: int, request: Request):
    form = await deps.get_form(request)
    try:
        sid = form.get("service_id") or None
        services.add_reservation_service(
            rid,
            service_id=int(sid) if sid not in (None, "", "0") else None,
            description=(form.get("description") or "").strip() or None,
            quantity=float(form.get("quantity") or 1),
            unit_price=float(form.get("unit_price")) if form.get("unit_price") not in (None, "") else None,
            notes=(form.get("notes") or "").strip(),
        )
        deps.add_flash(request, "Extra service added to folio", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect(f"/reservations/{rid}")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/reservations/{rid}/services/{service_line_id}/void")
async def void_service(rid: int, service_line_id: int, request: Request):
    form = await deps.require_unlocked_form(request, permission="reservations.manage")
    try:
        services.void_reservation_service(service_line_id, deps.service_password(request, form))
        deps.add_flash(request, "Extra service voided", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect(f"/reservations/{rid}")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/reservations/{rid}/discounts/apply")
async def apply_discount(rid: int, request: Request):
    form = await deps.get_form(request)
    try:
        services.apply_reservation_discount(
            rid,
            discount_type=(form.get("discount_type") or "amount"),
            value=float(form.get("value")) if form.get("value") not in (None, "") else None,
            voucher_code=(form.get("voucher_code") or "").strip() or None,
            description=(form.get("description") or "").strip(),
            notes=(form.get("notes") or "").strip(),
        )
        deps.add_flash(request, "Discount applied", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect(f"/reservations/{rid}")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/reservations/{rid}/discounts/{discount_id}/void")
async def void_discount(rid: int, discount_id: int, request: Request):
    form = await deps.require_unlocked_form(request, permission="reservations.manage")
    try:
        services.void_reservation_discount(discount_id, deps.service_password(request, form))
        deps.add_flash(request, "Discount voided", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect(f"/reservations/{rid}")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/reservations/{rid}/packages/apply")
async def apply_package(rid: int, request: Request):
    form = await deps.get_form(request)
    try:
        services.apply_package_to_reservation(rid, int(form.get("package_id")), notes=(form.get("notes") or "").strip())
        deps.add_flash(request, "Package applied to reservation", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect(f"/reservations/{rid}")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/reservations/{rid}/packages/{reservation_package_id}/void")
async def void_package(rid: int, reservation_package_id: int, request: Request):
    form = await deps.require_unlocked_form(request, permission="reservations.manage")
    try:
        services.void_reservation_package(reservation_package_id, deps.service_password(request, form))
        deps.add_flash(request, "Applied package voided", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect(f"/reservations/{rid}")
    deps.save_session(r, deps.get_session(request))
    return r
