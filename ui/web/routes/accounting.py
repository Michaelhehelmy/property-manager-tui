from __future__ import annotations
from datetime import datetime
from fastapi import APIRouter, Request
from app_core import services
from app_core.db import connect
from ui.web import deps
from ui.web.rendering import render
router = APIRouter()

@router.get("/accounting")
def accounting(request: Request):
    cats = connect().execute("SELECT id, category_name FROM expense_categories ORDER BY category_name").fetchall()
    expenses = services.get_recent_expenses(50)
    ledger = services.get_recent_ledger(100)
    summary = services.get_general_ledger_summary()
    res = connect().execute("SELECT id, guest_name FROM reservations WHERE status IN ('Reserved','Checked In','Checked Out') ORDER BY id DESC LIMIT 100").fetchall()
    return render(request, "accounting/index.html", {"title":"Accounting","cats":cats,"expenses":expenses,"ledger":ledger,"summary":summary,"reservations":res})

@router.post("/accounting/expense")
async def expense(request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    deps.require_unlock(request)
    try:
        services.record_operational_expense(int(form.get("cat_id")), form.get("param","Expense"), float(form.get("amount") or 0), float(form.get("vat") or 0), form.get("notes",""), form.get("method","Cash"))
        deps.add_flash(request, "Expense recorded", "success")
    except Exception as e: deps.add_flash(request, str(e), "error")
    r=deps.redirect("/accounting"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/accounting/expense/{eid}/void")
async def void_expense(eid: int, request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    deps.require_unlock(request)
    try: services.void_expense(eid, form.get("password","")); deps.add_flash(request,"Expense voided","success")
    except Exception as e: deps.add_flash(request,str(e),"error")
    r=deps.redirect("/accounting"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/accounting/payment")
async def payment(request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    deps.require_unlock(request)
    try:
        rid = form.get("reservation_id"); rid = int(rid) if rid else None
        services.record_payment(rid, float(form.get("amount") or 0), form.get("method","Cash"), form.get("notes",""), form.get("payment_type","Payment"))
        deps.add_flash(request,"Payment recorded","success")
    except Exception as e: deps.add_flash(request,str(e),"error")
    r=deps.redirect("/accounting"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/accounting/refund")
async def refund(request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    deps.require_unlock(request)
    try:
        services.record_refund(int(form.get("reservation_id")), float(form.get("amount") or 0), form.get("method","Cash"), form.get("notes",""), form.get("password",""))
        deps.add_flash(request,"Refund recorded","success")
    except Exception as e: deps.add_flash(request,str(e),"error")
    r=deps.redirect("/accounting"); deps.save_session(r, deps.get_session(request)); return r
