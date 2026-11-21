from __future__ import annotations

import logging

import json
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app_core import assistant, services
from app_core.assistant.actions import create_proposal, execute_pending_action
from ui.web import deps
from ui.web.rendering import render
from ui.web.route_utils import flash_redirect, guarded_unlock_redirect, htmx_partial_or_redirect

logger = logging.getLogger(__name__)


def _json_safe_value(value, _depth=0):
    """Return a JSON-safe sanitized copy (drops exception/trace-like objects)."""
    if _depth > 8:
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, BaseException):  # pragma: no cover
        return "internal_error"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            try:
                key = str(k)
            except Exception:  # pragma: no cover
                continue
            # avoid leaking traceback/exception internals by key names
            if key in {"__traceback__", "tb", "traceback", "exception", "exc_info"}:
                continue
            out[key] = _json_safe_value(v, _depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(v, _depth + 1) for v in list(value)[:200]]
    # fallback: string form, but avoid repr of exception objects
    try:
        if value.__class__.__module__ in ("traceback", "types") and value.__class__.__name__ in (
            "TracebackType",
            "FrameType",
            "CodeType",
        ):
            return "internal_error"
    except Exception:  # pragma: no cover
        pass
    return str(value)


router = APIRouter()

PENDING_ACTION_SESSION_KEY = "assistant_pending_action"
PENDING_ACTION_TTL_SECONDS = 600


def _now_ts() -> int:
    return int(time.time())


def _iso_utc(ts: int | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else _now_ts(), tz=timezone.utc).isoformat()


def _parse_body_fields(form, payload: dict[str, Any] | None, *keys: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in keys:
        v = None
        if payload and k in payload:
            v = payload.get(k)
        elif form is not None:
            try:
                v = form.get(k)
            except Exception:  # pragma: no cover
                v = None
        if v is not None:
            out[k] = v
    return out


async def _get_request_data(request: Request):
    form = None
    payload = None
    ct = (request.headers.get("content-type") or "").lower()
    try:
        if "application/json" in ct:
            payload = await request.json()
            if not isinstance(payload, dict):
                payload = {}
        else:
            form = await deps.get_form(request)
    except Exception:  # pragma: no cover
        try:
            form = await deps.get_form(request)
        except Exception:  # pragma: no cover
            form = None
            payload = payload if isinstance(payload, dict) else {}
    return form, (payload or {})


def _normalize_pending_action(proposed: dict[str, Any] | None, ui_context: Any = None) -> dict[str, Any] | None:
    if not isinstance(proposed, dict):
        return None
    action = dict(proposed)
    # Ensure proposal shape/id if caller only provided a raw action hint
    if not action.get("id") or not action.get("type"):
        try:
            action = create_proposal(action)
        except Exception:
            # best-effort fallback normalization
            action = dict(action)
    created = _now_ts()
    action.setdefault("created_at", _iso_utc(created))
    try:
        exp = int(action.get("expires_at_ts") or 0)
    except Exception:  # pragma: no cover
        exp = 0
    if exp <= 0:
        exp = created + PENDING_ACTION_TTL_SECONDS
    action["expires_at_ts"] = exp
    action["expires_at"] = _iso_utc(exp)
    action["ui_context"] = ui_context or action.get("ui_context") or {}
    action.setdefault("args", {})
    action.setdefault("resolved", {})
    action.setdefault("preview", {})
    action.setdefault(
        "summary", action.get("title") or action.get("description") or action.get("type") or "Proposed action"
    )
    return action


def set_pending_action(
    request: Request, proposed: dict[str, Any] | None, ui_context: Any = None
) -> dict[str, Any] | None:
    action = _normalize_pending_action(proposed, ui_context=ui_context)
    sess = deps.get_session(request)
    if action:
        sess[PENDING_ACTION_SESSION_KEY] = action
    else:
        sess.pop(PENDING_ACTION_SESSION_KEY, None)
    return action


def get_pending_action(request: Request, *, clear_if_expired: bool = True) -> dict[str, Any] | None:
    sess = deps.get_session(request)
    action = sess.get(PENDING_ACTION_SESSION_KEY)
    if not isinstance(action, dict):
        return None
    try:
        exp = int(action.get("expires_at_ts") or 0)
    except Exception:  # pragma: no cover
        exp = 0
    if exp and _now_ts() > exp:
        if clear_if_expired:
            sess.pop(PENDING_ACTION_SESSION_KEY, None)
        return None
    return action


def clear_pending_action(request: Request) -> None:
    deps.get_session(request).pop(PENDING_ACTION_SESSION_KEY, None)


def _assistant_history_context(hist: list[dict[str, Any]], *, limit: int = 20) -> list[dict[str, Any]]:
    return [h for h in hist[-limit:] if h.get("role") in ("user", "assistant")]


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
    pending = get_pending_action(request)
    ctx = {
        "title": "Manager Assistant",
        "history": _history(request)[-20:],
        "pending_action": pending,
        "assistant_locked": not _assistant_allowed(request),
        "llm": assistant.get_llm_config(),
        "intents": assistant.INTENT_LABELS,
    }
    return render(request, "assistant/index.html", ctx)


@router.post("/assistant/ask")
async def assistant_ask(request: Request):
    form = await deps.get_form(request)
    want_json = (
        (request.headers.get("accept", "").find("application/json") >= 0)
        or (request.headers.get("x-requested-with") == "assistant-widget")
        or (str(form.get("widget") or "") == "1")
    )

    if not _assistant_allowed(request):
        if want_json:
            return Response(
                content=json.dumps(
                    {
                        "ok": False,
                        "error": "Unlock required to use Manager Assistant (sensitive data).",
                        "ui_actions": [{"type": "navigate", "url": "/unlock?next=/assistant"}],
                    },
                    separators=(",", ":"),
                ),
                media_type="application/json",
            )
        return guarded_unlock_redirect(
            request, "/unlock?next=/assistant", "Unlock required to use Manager Assistant (sensitive data)."
        )

    message = (form.get("message") or "").strip()
    ui_context = None
    raw_ctx = form.get("ui_context")
    if raw_ctx:
        try:
            ui_context = json.loads(raw_ctx) if isinstance(raw_ctx, str) else raw_ctx
        except Exception:  # pragma: no cover
            ui_context = None

    hist = _history(request)
    result = {
        "ok": True,
        "answer": "",
        "intent": "capabilities_help",
        "confidence": 0.0,
        "tools_used": [],
        "source": "none",
        "ui_actions": [],
    }
    pending_action = get_pending_action(request)
    if message:
        hist.append({"role": "user", "content": message, "ui_context": ui_context or {}})
        history_for_chat = _assistant_history_context(hist[:-1])
        chat_ctx = dict(ui_context or {})
        if pending_action and "pending_action" not in chat_ctx:
            chat_ctx["pending_action"] = pending_action
        try:
            result = assistant.chat(message, history=history_for_chat, ui_context=chat_ctx)
        except TypeError:
            # backward compat if chat signature not yet updated
            try:
                result = assistant.chat(message, history=history_for_chat)
            except Exception:
                logger.exception("assistant_ask_failed")
                if want_json:
                    return Response(
                        content=json.dumps(
                            {
                                "ok": False,
                                "answer": "Sorry, the assistant encountered an error while processing your request. Please try again.",
                                "intent": "error",
                                "confidence": 0.0,
                                "tools_used": [],
                                "source": "error",
                                "slots": {},
                                "ui_actions": [],
                            },
                            separators=(",", ":"),
                        ),
                        media_type="application/json",
                    )
                result = {
                    "ok": False,
                    "answer": "Sorry, the assistant encountered an error while processing your request. Please try again.",
                    "intent": "error",
                    "confidence": 0.0,
                    "tools_used": [],
                    "source": "error",
                    "ui_actions": [],
                }
        except Exception:
            logger.exception("assistant_ask_failed")
            if want_json:
                return Response(
                    content=json.dumps(
                        {
                            "ok": False,
                            "answer": "Sorry, the assistant encountered an error while processing your request. Please try again.",
                            "intent": "error",
                            "confidence": 0.0,
                            "tools_used": [],
                            "source": "error",
                            "slots": {},
                            "ui_actions": [],
                        },
                        separators=(",", ":"),
                    ),
                    media_type="application/json",
                )
            result = {
                "ok": False,
                "answer": "Sorry, the assistant encountered an error while processing your request. Please try again.",
                "intent": "error",
                "confidence": 0.0,
                "tools_used": [],
                "source": "error",
                "ui_actions": [],
            }

        if not isinstance(result, dict):
            result = {
                "ok": True,
                "answer": str(result),
                "intent": "unknown",
                "confidence": 0.0,
                "tools_used": [],
                "source": "unknown",
                "ui_actions": [],
            }
        result.setdefault("ui_actions", [])

        proposed = result.get("proposed_action")
        if proposed:
            pending_action = set_pending_action(
                request, proposed if isinstance(proposed, dict) else {"raw": proposed}, ui_context=ui_context
            )
            result["proposed_action"] = pending_action
        else:
            # keep any existing pending action unless explicitly replaced; ask flow doesn't clear by itself
            pending_action = get_pending_action(request)

        answer = result.get("answer") or ""
        meta = f"[source={result.get('source')} intent={result.get('intent')} conf={result.get('confidence')} tools={','.join(result.get('tools_used') or []) or '-'}]"
        hist.append(
            {
                "role": "assistant",
                "content": answer,
                "meta": meta,
                "intent": result.get("intent"),
                "confidence": result.get("confidence"),
                "tools_used": result.get("tools_used") or [],
                "slots": result.get("slots") or {},
                "source": result.get("source"),
                "user_message": message,
                "ui_actions": result.get("ui_actions") or [],
                "proposed_action": result.get("proposed_action") or None,
            }
        )
        deps.get_session(request)["assistant_history"] = hist[-40:]
    else:
        pending_action = get_pending_action(request)

    if want_json:
        # Error responses: return fixed safe payload to avoid any exception/trace exposure.
        if not bool(result.get("ok", True)) and str(result.get("source") or "") == "error":
            return Response(
                content=json.dumps(
                    {
                        "ok": False,
                        "answer": "Sorry, the assistant encountered an error while processing your request. Please try again.",
                        "intent": "error",
                        "confidence": 0.0,
                        "tools_used": [],
                        "source": "error",
                        "slots": {},
                        "ui_actions": [],
                    },
                    separators=(",", ":"),
                ),
                media_type="application/json",
            )

        # Return only sanitized, JSON-safe fields (no exception/trace data).
        safe_slots = _json_safe_value(result.get("slots") if isinstance(result.get("slots"), dict) else {})
        safe_ui_actions = _json_safe_value(
            result.get("ui_actions") if isinstance(result.get("ui_actions"), list) else []
        )
        payload = {
            "ok": bool(result.get("ok", True)),
            "answer": str(result.get("answer") or ""),
            "intent": str(result.get("intent") or ""),
            "confidence": _json_safe_value(result.get("confidence")),
            "tools_used": [str(x) for x in (result.get("tools_used") or []) if isinstance(x, (str, int, float, bool))],
            "source": str(result.get("source") or ""),
            "slots": safe_slots,
            "ui_actions": safe_ui_actions,
        }
        pa = result.get("proposed_action") or pending_action
        if isinstance(pa, dict):
            safe_pa = _json_safe_value(pa)
            if isinstance(safe_pa, dict):
                payload["proposed_action"] = safe_pa
                payload["pending_action"] = safe_pa
        else:
            cur = get_pending_action(request)
            if isinstance(cur, dict):
                safe_cur = _json_safe_value(cur)
                if isinstance(safe_cur, dict):
                    payload["pending_action"] = safe_cur
        # Final JSON serialization to ensure a clean, non-tainted response body.
        try:
            clean_body = json.dumps(payload, default=lambda _o: "internal_error", separators=(",", ":"))
            # Validate serialized form is parseable JSON
            json.loads(clean_body)
        except Exception:  # pragma: no cover
            clean_body = json.dumps(
                {
                    "ok": False,
                    "answer": "Sorry, the assistant encountered an error while processing your request. Please try again.",
                    "intent": "error",
                    "confidence": 0.0,
                    "tools_used": [],
                    "source": "error",
                    "slots": {},
                    "ui_actions": [],
                },
                separators=(",", ":"),
            )
        return Response(
            content=clean_body, media_type="application/json"
        )  # codeql[py/stack-trace-exposure] sanitized JSON body

    shown = _history(request)[-20:]
    animate_index = None
    if message and shown:
        for i in range(len(shown) - 1, -1, -1):
            if shown[i].get("role") == "assistant":
                animate_index = i
                break
    ctx = {
        "request": request,
        "history": shown,
        "pending_action": get_pending_action(request),
        "csrf_token": deps.csrf_token(request),
        "intents": assistant.INTENT_LABELS,
        "animate_index": animate_index,
    }
    return htmx_partial_or_redirect(
        request, partial_template="assistant/_messages.html", partial_context=ctx, redirect_url="/assistant"
    )


@router.post("/assistant/feedback")
async def assistant_feedback(request: Request):
    form = await deps.get_form(request)
    if not _assistant_allowed(request):
        return guarded_unlock_redirect(request, "/assistant", "Unlock required.")
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
                msg = m
                break
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
        deps.add_flash(
            request,
            "Feedback saved. Corrections are used to improve routing over time (instant exemplar memory + retraining dataset).",
            "success",
        )
    else:
        deps.add_flash(request, "Could not find assistant message to rate.", "error")

    ctx = {
        "request": request,
        "history": _history(request)[-20:],
        "csrf_token": deps.csrf_token(request),
        "intents": assistant.INTENT_LABELS,
    }
    return htmx_partial_or_redirect(
        request, partial_template="assistant/_messages.html", partial_context=ctx, redirect_url="/assistant"
    )


@router.post("/assistant/action/confirm")
async def assistant_action_confirm(request: Request):
    form, payload = await _get_request_data(request)
    want_json = (
        (request.headers.get("accept", "").find("application/json") >= 0)
        or (request.headers.get("x-requested-with") == "assistant-widget")
        or (str((payload or {}).get("widget") or (form.get("widget") if form is not None else "") or "") == "1")
    )

    if not _assistant_allowed(request):
        if want_json:
            return {
                "ok": False,
                "error": "Unlock required to confirm assistant actions.",
                "ui_actions": [{"type": "navigate", "url": "/unlock?next=/assistant"}],
            }
        return guarded_unlock_redirect(
            request, "/unlock?next=/assistant", "Unlock required to confirm assistant actions."
        )

    fields = _parse_body_fields(form, payload, "action_id", "password", "proof", "proof_password")
    action_id = str(fields.get("action_id") or "").strip()
    pending = get_pending_action(request)
    if not pending:
        if want_json:
            return {"ok": False, "error": "No pending assistant action (it may have expired).", "pending_action": None}
        deps.add_flash(request, "No pending assistant action (it may have expired).", "error")
        ctx = {
            "request": request,
            "history": _history(request)[-20:],
            "pending_action": None,
            "csrf_token": deps.csrf_token(request),
            "intents": assistant.INTENT_LABELS,
        }
        return htmx_partial_or_redirect(
            request, partial_template="assistant/_messages.html", partial_context=ctx, redirect_url="/assistant"
        )

    if not action_id or str(pending.get("id") or "") != action_id:
        if want_json:
            return {
                "ok": False,
                "error": "Pending action does not match (stale or replaced). Please review and try again.",
                "pending_action": pending,
            }
        deps.add_flash(
            request, "Pending action does not match (stale or replaced). Please review and try again.", "error"
        )
        ctx = {
            "request": request,
            "history": _history(request)[-20:],
            "pending_action": pending,
            "csrf_token": deps.csrf_token(request),
            "intents": assistant.INTENT_LABELS,
        }
        return htmx_partial_or_redirect(
            request, partial_template="assistant/_messages.html", partial_context=ctx, redirect_url="/assistant"
        )

    # Best-effort proof resolution: prefer explicit password, else session sentinel when unlocked.
    proof = fields.get("proof") or fields.get("proof_password") or None
    if proof is None and form is not None:
        try:
            proof = deps.service_password(request, form, allow_session_sentinel=True)
        except Exception:
            proof = fields.get("password") or None
    if proof is None:
        proof = fields.get("password") or None

    hist = _history(request)
    exec_result = None
    try:
        exec_result = execute_pending_action(pending, request=request, password=proof, proof=proof)
        if not isinstance(exec_result, dict):
            exec_result = {"ok": True, "answer": str(exec_result), "ui_actions": []}
    except Exception:
        logger.exception("assistant_action_confirm_failed")
        exec_result = {
            "ok": False,
            "answer": "Failed to execute action due to an internal error. Please try again or check logs.",
            "error": "internal_error",
            "ui_actions": [],
            "terminal": True,
        }

    ok = bool(exec_result.get("ok", False))
    terminal_failure = bool(exec_result.get("terminal")) or not ok
    # Clear on success or terminal failure; keep pending for retryable failures if explicitly marked non-terminal
    if ok or terminal_failure:
        clear_pending_action(request)
        pending_after = get_pending_action(request)
    else:
        pending_after = get_pending_action(request)

    answer = exec_result.get("answer") or ("Action completed." if ok else "Action failed.")
    meta = f"[action={pending.get('type')} status={'ok' if ok else 'failed'}]"
    hist.append(
        {
            "role": "assistant",
            "content": answer,
            "meta": meta,
            "intent": "action_confirm",
            "confidence": 1.0,
            "tools_used": exec_result.get("tools_used") or ["assistant_action_confirm"],
            "slots": {"action_id": action_id, "action_type": pending.get("type")},
            "source": "assistant_action",
            "user_message": f"[confirm action] {pending.get('summary') or pending.get('type') or action_id}",
            "ui_actions": exec_result.get("ui_actions") or [],
            "action_result": exec_result,
            "proposed_action": None,
        }
    )
    deps.get_session(request)["assistant_history"] = hist[-40:]

    if want_json:
        safe_exec = {
            "ok": bool((exec_result or {}).get("ok", False)),
            "answer": str((exec_result or {}).get("answer") or answer),
            "tools_used": [
                str(x) for x in ((exec_result or {}).get("tools_used") or []) if isinstance(x, (str, int, float))
            ],
            "ui_actions": (exec_result or {}).get("ui_actions")
            if isinstance((exec_result or {}).get("ui_actions"), list)
            else [],
        }
        if not ok and (exec_result or {}).get("error"):
            safe_exec["error"] = "internal_error"
        return {
            "ok": ok,
            "answer": str(answer),
            "result": safe_exec,
            "ui_actions": safe_exec["ui_actions"],
            "pending_action": pending_after if isinstance(pending_after, dict) else None,
            "proposed_action": None,
        }

    shown = _history(request)[-20:]
    animate_index = len(shown) - 1 if shown else None
    ctx = {
        "request": request,
        "history": shown,
        "pending_action": pending_after,
        "csrf_token": deps.csrf_token(request),
        "intents": assistant.INTENT_LABELS,
        "animate_index": animate_index,
    }
    if ok:
        deps.add_flash(request, answer if len(answer) < 180 else "Action completed.", "success")
    else:
        deps.add_flash(request, answer if len(answer) < 180 else "Action failed.", "error")
    return htmx_partial_or_redirect(
        request, partial_template="assistant/_messages.html", partial_context=ctx, redirect_url="/assistant"
    )


@router.post("/assistant/action/cancel")
async def assistant_action_cancel(request: Request):
    form, payload = await _get_request_data(request)
    want_json = (
        (request.headers.get("accept", "").find("application/json") >= 0)
        or (request.headers.get("x-requested-with") == "assistant-widget")
        or (str((payload or {}).get("widget") or (form.get("widget") if form is not None else "") or "") == "1")
    )

    if not _assistant_allowed(request):
        if want_json:
            return {
                "ok": False,
                "error": "Unlock required to manage assistant actions.",
                "ui_actions": [{"type": "navigate", "url": "/unlock?next=/assistant"}],
            }
        return guarded_unlock_redirect(
            request, "/unlock?next=/assistant", "Unlock required to manage assistant actions."
        )

    fields = _parse_body_fields(form, payload, "action_id")
    action_id = str(fields.get("action_id") or "").strip()
    pending = get_pending_action(request)
    if pending and action_id and str(pending.get("id") or "") != action_id:
        # stale id: don't clear current pending, report mismatch
        if want_json:
            return {
                "ok": False,
                "error": "Pending action does not match; nothing cancelled.",
                "pending_action": pending,
            }
        deps.add_flash(request, "Pending action does not match; nothing cancelled.", "error")
        ctx = {
            "request": request,
            "history": _history(request)[-20:],
            "pending_action": pending,
            "csrf_token": deps.csrf_token(request),
            "intents": assistant.INTENT_LABELS,
        }
        return htmx_partial_or_redirect(
            request, partial_template="assistant/_messages.html", partial_context=ctx, redirect_url="/assistant"
        )

    if pending:
        clear_pending_action(request)
        hist = _history(request)
        hist.append(
            {
                "role": "assistant",
                "content": f"Cancelled proposed action: {pending.get('summary') or pending.get('type') or 'action'}.",
                "meta": "[action=cancelled]",
                "intent": "action_cancel",
                "confidence": 1.0,
                "tools_used": ["assistant_action_cancel"],
                "slots": {"action_id": str(pending.get("id") or action_id)},
                "source": "assistant_action",
                "user_message": f"[cancel action] {pending.get('summary') or pending.get('type') or ''}",
                "ui_actions": [],
                "proposed_action": None,
            }
        )
        deps.get_session(request)["assistant_history"] = hist[-40:]
        msg = "Proposed action cancelled."
    else:
        msg = "No pending action to cancel."

    if want_json:
        return {"ok": True, "answer": msg, "pending_action": None, "proposed_action": None, "ui_actions": []}

    deps.add_flash(request, msg, "info" if pending else "warning")
    shown = _history(request)[-20:]
    animate_index = len(shown) - 1 if shown and pending else None
    ctx = {
        "request": request,
        "history": shown,
        "pending_action": None,
        "csrf_token": deps.csrf_token(request),
        "intents": assistant.INTENT_LABELS,
        "animate_index": animate_index,
    }
    return htmx_partial_or_redirect(
        request, partial_template="assistant/_messages.html", partial_context=ctx, redirect_url="/assistant"
    )


@router.post("/assistant/clear")
async def assistant_clear(request: Request):
    form = await deps.get_form(request)
    deps.get_session(request)["assistant_history"] = []
    clear_pending_action(request)
    ctx = {
        "request": request,
        "history": [],
        "pending_action": None,
        "csrf_token": deps.csrf_token(request),
        "intents": assistant.INTENT_LABELS,
    }
    return htmx_partial_or_redirect(
        request, partial_template="assistant/_messages.html", partial_context=ctx, redirect_url="/assistant"
    )
