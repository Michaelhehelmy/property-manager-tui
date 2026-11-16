from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from ui.web import deps

router = APIRouter()


@router.get("/files/{kind}/{name}")
def download(kind: str, name: str, request: Request):
    deps.require_unlock(request)
    p = deps.safe_download_path(kind, name)
    # Confined path validated by deps.safe_download_path; serve with safe headers.
    fname = p.name
    quoted = quote(fname, safe="")
    disposition = f"attachment; filename=\"{fname}\"; filename*=UTF-8''{quoted}"
    return FileResponse(
        str(p),
        filename=fname,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": disposition,
        },
    )
