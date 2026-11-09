from __future__ import annotations
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Request
from app_core import paths, services
from ui.web import deps
from ui.web.rendering import render
router = APIRouter()

@router.get("/reports")
def reports(request: Request):
    today = datetime.now().strftime("%Y-%m-%d")
    month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")
    exports = []
    paths.ensure_data_dirs()
    if paths.EXPORT_DIR.exists():
        for p in sorted(paths.EXPORT_DIR.glob("**/*"), reverse=True):
            if p.is_file(): exports.append({"name": p.relative_to(paths.EXPORT_DIR).as_posix(), "kind":"export"})
    invoices = []
    if paths.INVOICE_DIR.exists():
        for p in sorted(paths.INVOICE_DIR.glob("*.txt"), reverse=True)[:100]:
            invoices.append({"name": p.name, "kind":"invoice"})
    return render(request, "reports/index.html", {"title":"Reports & Analytics","start":month_start,"end":today,
                  "presets": services.list_analytics_presets(),"exports":exports[:100],"invoices":invoices})

@router.post("/reports/run")
async def run_report(request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    kind = form.get("kind","pl"); start = form.get("start"); end = form.get("end")
    try:
        text = {
            "pl": services.report_pl_statement, "cash": services.report_cash_flow, "vat": services.report_vat,
            "payments": services.report_payment_methods, "occupancy": services.report_occupancy,
        }.get(kind, services.report_pl_statement)(start, end)
        deps.add_flash(request, "Report generated", "success")
        return render(request, "reports/view.html", {"title":f"Report: {kind}", "text":text, "kind":kind})
    except Exception as e:
        deps.add_flash(request, str(e), "error"); r=deps.redirect("/reports"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/reports/analytics-export")
async def analytics_export(request: Request):
    form = await request.form(); deps.verify_csrf(request, form.get("csrf"))
    try:
        res = services.generate_unified_report(form.get("preset","management_pack"), form.get("start"), form.get("end"))
        deps.add_flash(request, f"Analytics export created: {', '.join(res.get('outputs',{}).keys())}", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r=deps.redirect("/reports"); deps.save_session(r, deps.get_session(request)); return r
