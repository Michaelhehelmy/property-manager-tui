from __future__ import annotations

import json
from fastapi import APIRouter, Request

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
        "llm": assistant.get_llm_config(),
        "intents": assistant.INTENT_LABELS,
    }
    return render(request, "assistant/index.html", ctx)


@router.post("/assistant/ask")
async def assistant_ask(request: Request):
    form = await deps.get_form(request)
    if not _assistant_allowed(request):
        deps.add_flash(request, "Unlock required to use Manager Assistant (sensitive data).", "error")
        r = deps.redirect("/unlock?next=/assistant")
        deps.save_session(r, deps.get_session(request))
        return r
    message = (form.get("message") or "").strip()
    hist = _history(request)
    if message:
        hist.append({"role": "user", "content": message})
        result = assistant.chat(message, history=[h for h in hist[:-1] if h.get("role") in ("user","assistant")])
        answer = result.get("answer") or ""
        meta = f"[source={result.get('source')} intent={result.get('intent')} conf={result.get('confidence')} tools={','.join(result.get('tools_used') or []) or '-'}]"
        hist.append({
            "role": "assistant",
            "content": answer,
            "meta": meta,
            "intent": result.get("intent"),
            "confidence": result.get("confidence"),
            "tools_used": result.get("tools_used") or [],
            "slots": result.get("slots") or {},
            "source": result.get("source"),
            "user_message": message,
        })
        deps.get_session(request)["assistant_history"] = hist[-40:]
    if request.headers.get("HX-Request"):
        templates = request.app.state.templates
        ctx = {"request": request, "history": _history(request)[-20:], "csrf_token": deps.csrf_token(request), "intents": assistant.INTENT_LABELS}
        resp = templates.TemplateResponse(request=request, name="assistant/_messages.html", context=ctx)
        deps.save_session(resp, deps.get_session(request))
        return resp
    r = deps.redirect("/assistant")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/assistant/feedback")
async def assistant_feedback(request: Request):
    form = await deps.get_form(request)
    if not _assistant_allowed(request):
        deps.add_flash(request, "Unlock required.", "error")
        r = deps.redirect("/assistant"); deps.save_session(r, deps.get_session(request)); return r
    idx = form.get("msg_index")
    rating = int(form.get("rating") or 0)
    corrected_intent = (form.get("corrected_intent") or "").strip()
    user_note = (form.get("user_note") or "").strip()
    corrected_slots_raw = (form.get("corrected_slots_json") or "").strip()
    hist = _history(request)
    msg = None
    try:
        i = int(idx)
        if 0 <= i < len(hist):
            msg = hist[i]
    except Exception:
        msg = None
    # fallback: last assistant
    if msg is None or msg.get("role") != "assistant":
        for m in reversed(hist):
            if m.get("role") == "assistant":
                msg = m; break
    if msg and msg.get("role") == "assistant":
        try:
            corrected_slots = json.loads(corrected_slots_raw) if corrected_slots_raw else {}
            if not isinstance(corrected_slots, dict):
                corrected_slots = {}
        except Exception:
            corrected_slots = {}
        assistant.record_feedback(
            message=msg.get("user_message") or "",
            predicted_intent=msg.get("intent") or "",
            corrected_intent=corrected_intent,
            rating=rating,
            confidence=msg.get("confidence"),
            slots=msg.get("slots") or {},
            corrected_slots=corrected_slots,
            tools_used=msg.get("tools_used") or [],
            answer=msg.get("content") or "",
            user_note=user_note,
            source=msg.get("source") or "",
            status="new" if (corrected_intent or rating != 0) else "new",
        )
        msg["feedback"] = {"rating": rating, "corrected_intent": corrected_intent, "note": user_note}
        deps.get_session(request)["assistant_history"] = hist[-40:]
        deps.add_flash(request, "Feedback saved. Corrections are used to improve routing over time (instant exemplar memory + retraining dataset).", "success")
    else:
        deps.add_flash(request, "Could not find assistant message to rate.", "error")

    if request.headers.get("HX-Request"):
        templates = request.app.state.templates
        ctx = {"request": request, "history": _history(request)[-20:], "csrf_token": deps.csrf_token(request), "intents": assistant.INTENT_LABELS}
        resp = templates.TemplateResponse(request=request, name="assistant/_messages.html", context=ctx)
        deps.save_session(resp, deps.get_session(request))
        return resp
    r = deps.redirect("/assistant")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/assistant/clear")
async def assistant_clear(request: Request):
    form = await deps.get_form(request)
    deps.get_session(request)["assistant_history"] = []
    if request.headers.get("HX-Request"):
        templates = request.app.state.templates
        ctx = {"request": request, "history": [], "csrf_token": deps.csrf_token(request), "intents": assistant.INTENT_LABELS}
        resp = templates.TemplateResponse(request=request, name="assistant/_messages.html", context=ctx)
        deps.save_session(resp, deps.get_session(request))
        return resp
    r = deps.redirect("/assistant")
    deps.save_session(r, deps.get_session(request))
    return r
