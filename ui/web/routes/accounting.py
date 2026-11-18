from __future__ import annotations
from fastapi import APIRouter, Request
from app_core import services
from app_core.db import connect
from ui.web import deps
from ui.web.rendering import render
from ui.web.route_utils import flash_redirect
router = APIRouter()

@router.get("/accounting")
def accounting(request: Request, tab: str = "overview", shift_id: int | None = None):
    cats = connect().execute("SELECT id, category_name FROM expense_categories ORDER BY category_name").fetchall()
    expenses = services.get_recent_expenses(50)
    ledger = services.get_recent_ledger(100)
    summary = services.get_general_ledger_summary()
    res = connect().execute("SELECT id, guest_name FROM reservations WHERE status IN ('Reserved','Checked In','Checked Out') ORDER BY id DESC LIMIT 100").fetchall()
    try:
        cash_shifts = services.list_cash_shifts(50)
        open_shift = services.get_open_cash_shift()
        shift_movements = services.list_cash_movements(open_shift["id"]) if open_shift else []
        open_shift_recon = services.get_cash_shift_reconciliation(open_shift["id"]) if open_shift and hasattr(services, "get_cash_shift_reconciliation") else None
    except Exception:
        cash_shifts, open_shift, shift_movements, open_shift_recon = [], None, [], None
    selected_shift = None
    selected_shift_recon = None
    selected_shift_movements = []
    if shift_id is not None:
        try:
            sid = int(shift_id)
            selected_shift_recon = services.get_cash_shift_reconciliation(sid) if hasattr(services, "get_cash_shift_reconciliation") else None
            if selected_shift_recon:
                selected_shift = dict(selected_shift_recon)
            selected_shift_movements = services.list_cash_movements(sid) if hasattr(services, "list_cash_movements") else []
        except Exception:
            selected_shift = None
            selected_shift_recon = None
            selected_shift_movements = []
    try:
        night_audits = services.list_night_audit_runs(30)
    except Exception:
        night_audits = []
    allowed = {"overview","expenses","payments","ledger","shifts","night_audit"}
    tab = (tab or "overview").strip().lower()
    if tab not in allowed: tab = "overview"
    return render(request, "accounting/index.html", {
        "title":"Accounting","cats":cats,"expenses":expenses,"ledger":ledger,"summary":summary,"reservations":res,
        "cash_shifts":cash_shifts,"open_shift":open_shift,"open_shift_recon":open_shift_recon,"shift_movements":shift_movements,
        "selected_shift":selected_shift,"selected_shift_recon":selected_shift_recon,"selected_shift_movements":selected_shift_movements,
        "night_audits":night_audits,"active_tab":tab
    })

@router.post("/accounting/expense")
async def expense(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        services.record_operational_expense(
            int(form.get("cat_id")),
            form.get("param", "Expense"),
            float(form.get("amount") or 0),
            float(form.get("vat") or 0),
            form.get("notes", ""),
            form.get("method", "Cash"),
        )
        return flash_redirect(request, "/accounting?tab=expenses", "Expense recorded", "success")
    except Exception as e:
        return flash_redirect(request, "/accounting?tab=expenses", str(e), "error")

@router.post("/accounting/expense/{eid}/void")
async def void_expense(eid: int, request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        services.void_expense(eid, deps.service_password(request, form))
        return flash_redirect(request, "/accounting?tab=expenses", "Expense voided", "success")
    except Exception as e:
        return flash_redirect(request, "/accounting?tab=expenses", str(e), "error")

@router.post("/accounting/payment")
async def payment(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        rid = form.get("reservation_id")
        services.record_payment(
            int(rid) if rid else None,
            float(form.get("amount") or 0),
            form.get("method", "Cash"),
            form.get("notes", ""),
            form.get("payment_type", "Payment"),
            reference=(form.get("reference") or "").strip(),
        )
        return flash_redirect(request, "/accounting?tab=payments", "Payment recorded", "success")
    except Exception as e:
        return flash_redirect(request, "/accounting?tab=payments", str(e), "error")

@router.post("/accounting/refund")
async def refund(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        services.record_refund(
            int(form.get("reservation_id")),
            float(form.get("amount") or 0),
            form.get("method", "Cash"),
            form.get("notes", ""),
            deps.service_password(request, form),
            reference=(form.get("reference") or "").strip(),
        )
        return flash_redirect(request, "/accounting?tab=payments", "Refund recorded", "success")
    except Exception as e:
        return flash_redirect(request, "/accounting?tab=payments", str(e), "error")


@router.post("/accounting/shifts/open")
async def shift_open(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        services.open_cash_shift(
            name=form.get("name", ""),
            opening_float=float(form.get("opening_float") or 0),
            notes=form.get("notes", ""),
        )
        return flash_redirect(request, "/accounting?tab=shifts", "Cash shift opened", "success")
    except Exception as e:
        return flash_redirect(request, "/accounting?tab=shifts", str(e), "error")


@router.post("/accounting/shifts/{shift_id}/movement")
async def shift_movement(shift_id: int, request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        services.add_cash_movement(
            shift_id,
            form.get("movement_type", "adjustment"),
            float(form.get("amount") or 0),
            method=form.get("method", "Cash"),
            reference=form.get("reference", ""),
            notes=form.get("notes", ""),
        )
        return flash_redirect(request, f"/accounting?tab=shifts&shift_id={shift_id}", "Cash movement recorded", "success")
    except Exception as e:
        return flash_redirect(request, "/accounting?tab=shifts", str(e), "error")


@router.post("/accounting/shifts/{shift_id}/close")
async def shift_close(shift_id: int, request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        counted = form.get("counted_cash")
        services.close_cash_shift(
            shift_id,
            counted_cash=float(counted) if counted not in (None, "") else None,
            notes=form.get("notes", ""),
        )
        return flash_redirect(request, f"/accounting?tab=shifts&shift_id={shift_id}", "Cash shift closed", "success")
    except Exception as e:
        return flash_redirect(request, "/accounting?tab=shifts", str(e), "error")


@router.post("/accounting/night-audit/run")
async def night_audit_run(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        services.run_night_audit(business_date=form.get("business_date") or None, notes=form.get("notes", ""))
        return flash_redirect(request, "/accounting?tab=night_audit", "Night audit snapshot recorded (advisory)", "success")
    except Exception as e:
        return flash_redirect(request, "/accounting?tab=night_audit", str(e), "error")
