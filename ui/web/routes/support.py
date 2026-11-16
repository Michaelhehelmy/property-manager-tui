from __future__ import annotations

import json

from fastapi import APIRouter, Request

from app_core.support import DEFAULT_REPO, collect_support_diagnostics
from ui.web import deps
from ui.web.rendering import render

router = APIRouter()


@router.get("/support/report")
def support_report(request: Request):
    """In-app issue reporting: sanitized diagnostics + user-authored report builder."""
    # Client context is best provided by browser/JS; include safe server-known hints.
    qp = request.query_params
    context = {
        "path": str(qp.get("path") or request.url.path),
        "source": "web",
    }
    prefill = {"title": "", "actual": "", "notes": ""}
    # If coming from an in-app error page, include sanitized last error context for prefill.
    if str(qp.get("from_error") or "") in ("1", "true", "yes"):
        try:
            sess = deps.get_session(request)
            last = sess.get("last_error_report") or {}
            ref = str(qp.get("ref") or "")
            if isinstance(last, dict) and (not ref or str(last.get("ref") or "") == ref):
                em = last.get("error_message") or ""
                et = last.get("error_type") or "Error"
                ep = last.get("path") or ""
                er = last.get("ref") or ""
                prefill["title"] = f"App error: {et}"[:120]
                prefill["actual"] = f"Unexpected error in the web app.\nRef: {er}\nPath: {ep}\nError: {et}: {em}".strip()
                prefill["notes"] = "Reported from in-app error page. Please add steps to reproduce."
                context.update({
                    "error_code": er,
                    "error_message": f"{et}: {em}" if em else et,
                    "path": ep or context.get("path"),
                })
        except Exception:
            pass

    diagnostics = collect_support_diagnostics(context=context, include_update_check=False)
    # Safe JSON for embedding in <script type="application/json">
    diagnostics_json = json.dumps(diagnostics, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return render(request, "support/report.html", {
        "title": "Report issue",
        "diagnostics": diagnostics,
        "diagnostics_json": diagnostics_json,
        "github_repo": DEFAULT_REPO,
        "prefill": prefill,
    })
