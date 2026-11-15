from __future__ import annotations

import json

from fastapi import APIRouter, Request

from app_core.support import DEFAULT_REPO, collect_support_diagnostics
from ui.web.rendering import render

router = APIRouter()


@router.get("/support/report")
def support_report(request: Request):
    """In-app issue reporting: sanitized diagnostics + user-authored report builder."""
    # Client context is best provided by browser/JS; include safe server-known hints.
    context = {
        "path": str(request.url.path),
        "source": "web",
    }
    diagnostics = collect_support_diagnostics(context=context, include_update_check=False)
    # Safe JSON for embedding in <script type="application/json">
    diagnostics_json = json.dumps(diagnostics, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return render(request, "support/report.html", {
        "title": "Report issue",
        "diagnostics": diagnostics,
        "diagnostics_json": diagnostics_json,
        "github_repo": DEFAULT_REPO,
    })
