from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app_core import assistant, services
from ui.web import deps
from ui.web.rendering import render

router = APIRouter()


def _assistant_allowed(request: Request) -> bool:
    try:
        req = services.get_setting("assistant_require_unlock", "true") == "true"
    except Exception:
        req = True
    return (not req) or deps.is_unlocked(request)


def _history(request: Request):
    sess = deps.get_session(request)
    hist = sess.get("assistant_history")
    if not isinstance(hist, list):
        hist = []
    return hist


@router.get("/assistant")
def assistant_page(request: Request):
    ctx = {
        "title": "Manager Assistant",
        "history": _history(request)[-20:],
        "assistant_locked": not _assistant_allowed(request),
        "llm": assistant._llm_config(),
    }
    return render(request, "assistant/index.html", ctx)


@router.post("/assistant/ask")
async def assistant_ask(request: Request):
    form = await request.form()
    deps.verify_csrf(request, form.get("csrf"))
    if not _assistant_allowed(request):
        deps.add_flash(request, "Unlock required to use Manager Assistant (sensitive data).", "error")
        r = deps.redirect("/unlock?next=/assistant")
        deps.save_session(r, deps.get_session(request))
        return r
    message = (form.get("message") or "").strip()
    hist = _history(request)
    if message:
        hist.append({"role": "user", "content": message})
        result = assistant.chat(message, history=hist[:-1])
        answer = result.get("answer") or ""
        meta = f"[source={result.get('source')} tools={','.join(result.get('tools_used') or []) or '-'}]"
        hist.append({"role": "assistant", "content": answer, "meta": meta})
        deps.get_session(request)["assistant_history"] = hist[-30:]
    # HTMX partial
    if request.headers.get("HX-Request"):
        html = render(request, "assistant/_messages.html", {"history": _history(request)[-20:]}).body.decode("utf-8", "ignore")
        # extract inner fragment roughly by returning full render; simpler: render partial template directly via app templates
        templates = request.app.state.templates
        ctx = {"request": request, "history": _history(request)[-20:], "csrf_token": deps.csrf_token(request)}
        resp = templates.TemplateResponse(request=request, name="assistant/_messages.html", context=ctx)
        deps.save_session(resp, deps.get_session(request))
        return resp
    r = deps.redirect("/assistant")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/assistant/clear")
async def assistant_clear(request: Request):
    form = await request.form()
    deps.verify_csrf(request, form.get("csrf"))
    deps.get_session(request)["assistant_history"] = []
    if request.headers.get("HX-Request"):
        templates = request.app.state.templates
        ctx = {"request": request, "history": [], "csrf_token": deps.csrf_token(request)}
        resp = templates.TemplateResponse(request=request, name="assistant/_messages.html", context=ctx)
        deps.save_session(resp, deps.get_session(request))
        return resp
    r = deps.redirect("/assistant")
    deps.save_session(r, deps.get_session(request))
    return r
