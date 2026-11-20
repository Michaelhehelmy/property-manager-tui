from __future__ import annotations
from datetime import datetime
from fastapi import APIRouter, Request
from app_core import paths, services
from ui.web import deps
from ui.web.rendering import render
from ui.web.route_utils import flash_redirect

router = APIRouter()


@router.get("/reports")
def reports(request: Request, tab: str = "generate"):
    today = datetime.now().strftime("%Y-%m-%d")
    month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")
    exports = []
    paths.ensure_data_dirs()
    if paths.EXPORT_DIR.exists():
        for p in sorted(paths.EXPORT_DIR.glob("**/*"), reverse=True):
            if p.is_file():
                exports.append({"name": p.relative_to(paths.EXPORT_DIR).as_posix(), "kind": "export"})
    invoices = []
    if paths.INVOICE_DIR.exists():
        for p in sorted(paths.INVOICE_DIR.glob("*.txt"), reverse=True)[:100]:
            invoices.append({"name": p.name, "kind": "invoice"})
    tab = (tab or "generate").strip().lower()
    if tab not in {"generate", "downloads"}:
        tab = "generate"
    return render(
        request,
        "reports/index.html",
        {
            "title": "Reports & Analytics",
            "start": month_start,
            "end": today,
            "presets": services.list_analytics_presets(),
            "exports": exports[:100],
            "invoices": invoices,
            "active_tab": tab,
        },
    )


@router.post("/reports/run")
async def run_report(request: Request):
    form = await deps.get_form(request)
    deps.require_unlock(request)
    kind = form.get("kind", "pl")
    start = form.get("start")
    end = form.get("end")
    day = form.get("day") or end or start or datetime.now().strftime("%Y-%m-%d")
    try:
        if kind == "daily_ops":
            text = services.report_daily_ops(day)
            label = day
        elif kind == "front_desk":
            text = services.report_front_desk_summary(day)
            label = day
        elif kind == "housekeeping_ops":
            text = services.report_housekeeping_ops()
            label = "current"
        elif kind == "groups":
            text = services.report_reservation_groups()
            label = "current"
        elif kind == "packages":
            text = services.report_packages_summary()
            label = "current"
        elif kind == "shifts":
            text = services.report_cash_shifts_summary()
            label = "recent"
        elif kind == "night_audit":
            text = services.report_night_audit_summary()
            label = "recent"
        else:
            text = {
                "pl": services.report_pl_statement,
                "cash": services.report_cash_flow,
                "vat": services.report_vat,
                "payments": services.report_payment_methods,
                "occupancy": services.report_occupancy,
            }.get(kind, services.report_pl_statement)(start, end)
            label = f"{start} to {end}" if start and end else kind
        deps.add_flash(request, "Report generated", "success")
        return render(
            request, "reports/view.html", {"title": f"Report: {kind}", "text": text, "kind": kind, "period": label}
        )
    except Exception as e:
        return flash_redirect(request, "/reports?tab=generate", str(e), "error")


@router.post("/reports/analytics-export")
async def analytics_export(request: Request):
    form = await deps.get_form(request)
    deps.require_unlock(request)
    try:
        res = services.generate_unified_report(
            form.get("preset", "management_pack"), form.get("start"), form.get("end")
        )
        return flash_redirect(
            request,
            "/reports?tab=downloads",
            f"Analytics export created: {', '.join(res.get('outputs', {}).keys())}",
            "success",
        )
    except Exception as e:
        return flash_redirect(request, "/reports?tab=downloads", str(e), "error")
