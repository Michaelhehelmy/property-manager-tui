from __future__ import annotations
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from ui.web import deps
router = APIRouter()

@router.get("/files/{kind}/{name}")
def download(kind: str, name: str, request: Request):
    deps.require_unlock(request)
    p = deps.safe_download_path(kind, name)
    # Confined path validated by deps.safe_download_path; serve with safe headers.
    return FileResponse(str(p), filename=p.name, headers={"X-Content-Type-Options": "nosniff", "Content-Disposition": f'attachment; filename="{p.name}"'})
