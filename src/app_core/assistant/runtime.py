"""Local runtime config, rephrasing, and chat entrypoints."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .. import services
from .common import extract_slots
from .engine import _execute_compound, _execute_single
from .formatting import _local_talking_rephrase
from .nlu import classify_intent, rule_classify

logger = logging.getLogger(__name__)

_LOCAL_LLM_CACHE = None


def _pkg():
    from app_core import assistant as _a

    return _a


# ── Local-only LLM config & runtime (no external/OpenAI calls) ──────────────


def _llm_config():
    """Local-only config. No external endpoints or OpenAI API calls."""
    try:
        provider = (services.get_setting("assistant_provider", "auto") or "auto").strip().lower()
        response_mode = (services.get_setting("assistant_response_mode", "talking") or "talking").strip().lower()
        model_path = (
            services.get_setting("assistant_local_model_path", "")
            or os.environ.get("ASSISTANT_LOCAL_MODEL_PATH", "")
            or ""
        ).strip()
        n_ctx = int(services.get_setting("assistant_local_n_ctx", "2048") or 2048)
    except Exception:
        provider, response_mode, model_path, n_ctx = "auto", "talking", "", 2048
    # normalize: anything "openai"/external -> treated as local/auto (no external)
    if provider in ("openai", "openai_compatible", "external"):
        provider = "auto"
    local_available = False
    try:
        import llama_cpp  # type: ignore  # noqa: F401

        if model_path and Path(model_path).expanduser().exists():
            local_available = True
    except Exception:
        local_available = False
    # 'enabled' means local model runtime available; deterministic talking is always available separately
    enabled = provider != "deterministic_only" and local_available
    return {
        "provider": provider,
        "response_mode": response_mode,
        "model_path": model_path,
        "n_ctx": n_ctx,
        "local_available": local_available,
        "enabled": enabled,
        "external": False,
    }


def get_llm_config() -> dict:
    """Public assistant LLM/runtime configuration/status (no external endpoints)."""
    return _llm_config()


def _get_local_llm():
    global _LOCAL_LLM_CACHE
    cfg = _llm_config()
    if not cfg.get("local_available"):
        return None
    if _LOCAL_LLM_CACHE is not None:
        return _LOCAL_LLM_CACHE or None
    try:
        from llama_cpp import Llama  # type: ignore

        mp = str(Path(cfg["model_path"]).expanduser())
        _LOCAL_LLM_CACHE = Llama(model_path=mp, n_ctx=int(cfg.get("n_ctx") or 2048), verbose=False)
        return _LOCAL_LLM_CACHE
    except Exception:
        _LOCAL_LLM_CACHE = False
        return None


def _local_llm_generate(prompt: str, max_tokens: int = 512, temperature: float = 0.2) -> str | None:
    llm = _get_local_llm()
    if llm is None:
        return None
    try:
        out = llm(prompt, max_tokens=max_tokens, temperature=temperature, stop=["</s>", "User:", "Manager question:"])
        if isinstance(out, dict):
            txt = ((out.get("choices") or [{}])[0].get("text") or "").strip()
            return txt or None
        return str(out).strip() or None
    except Exception:
        return None


def talking_rephrase(message: str, core_result: dict, config: dict | None = None) -> str | None:
    """Local-only friendly rephrase. Uses local GGUF model if available, else deterministic formatter."""
    config = config or _pkg()._llm_config()
    core_answer = (core_result or {}).get("answer") or ""
    data = (core_result or {}).get("data")
    # Try local model first (if enabled)
    if config.get("enabled"):
        try:
            data_str = json.dumps(data, ensure_ascii=False, default=str)[:3500]
        except Exception:
            data_str = str(data)[:3500]
        prompt = (
            "You are a friendly, clear property-manager business partner assistant. "
            "Rewrite the grounded answer below for the manager in warm, natural English. "
            "RULES: Use ONLY the provided grounded data/answer. Do NOT invent or guess any facts, numbers, names, dates, IDs, items, or actions. "
            "Keep all IDs/dates/amounts accurate. Be concise, answer-first, use short bullets when helpful, and include 1 helpful follow-up suggestion if relevant.\n\n"
            f"Manager question: {message}\n\nGrounded answer:\n{core_answer}\n\nGrounded data (JSON):\n{data_str}\n\nFriendly reply:"
        )
        gen = getattr(_pkg(), "_local_llm_generate", _local_llm_generate)
        txt = gen(prompt, max_tokens=500, temperature=0.2)
        if txt:
            return txt.strip()
    # Deterministic local fallback (always available)
    return _local_talking_rephrase(message, core_result)


def deterministic_route(message: str) -> dict | None:
    text = (message or "").strip()
    if not text:
        return None
    slots = extract_slots(text)
    cls = rule_classify(text, slots)
    intent = cls.get("intent", "capabilities_help")
    conf = cls.get("confidence", 0.0)
    if cls.get("low_confidence") and conf < 0.4 and not (slots.get("reservation_id") or slots.get("guest_id")):
        return None
    if intent == "compound_multi_intent":
        res = _execute_compound(text, slots, ui_context=None)
    else:
        res = _execute_single(intent, text, slots, ui_context=None)
    res.setdefault("source", "core_deterministic")
    res.setdefault("ok", True)
    res.setdefault("tools_used", [])
    res["intent"] = res.get("intent") or intent
    res["confidence"] = conf
    res["slots"] = slots
    res["router"] = cls.get("router", "rules")
    return res


def llm_route(message: str, history: list[dict] | None = None, config: dict | None = None) -> dict | None:
    """Local-only 'full' route: uses local GGUF if available for phrasing, else deterministic tool routing. No external calls."""
    config = config or _pkg()._llm_config()
    # Always produce a grounded deterministic/tool result
    slots = extract_slots(message, history)
    cls = classify_intent(message, history)
    intent = cls.get("intent") or "capabilities_help"
    slots = {**slots, **(cls.get("slots") or {})}
    if intent == "compound_multi_intent":
        core = _execute_compound(message, slots, ui_context=None)
    else:
        core = _execute_single(intent, message, slots, ui_context=None)
    core.setdefault("source", "local_grounded")
    core["intent"] = core.get("intent") or intent
    core["confidence"] = cls.get("confidence", 0.0)
    core["slots"] = slots
    core["router"] = cls.get("router", "rules")
    # optionally polish with local model
    if config.get("enabled") and core.get("ok") and not core.get("needs_clarification"):
        polished = _pkg().talking_rephrase(message, {**core, "core_answer": core.get("answer")}, config)
        if polished:
            core["answer"] = polished
            core["source"] = "local_llm+" + core.get("source", "core")
            core["talking_model"] = "local_gguf"
    else:
        core["source"] = "local_grounded_core"
    return core


UI_FIELD_GUIDE = {
    "/reservations": {
        "create": [
            ("guest_name", "Guest name", "Full name of the guest/contact for this reservation."),
            ("source", "Source", "Booking source/channel (e.g. Direct, Booking.com, Walk-in)."),
            ("room_id", "Room", "Select the room to assign for this stay."),
            ("rate_plan_id", "Rate plan", "Optional pricing plan/package to apply."),
            ("rate", "Rate (agreed nightly)", "Nightly rate agreed for the reservation."),
            ("check_in", "Check-in date", "Arrival/check-in date."),
            ("check_out", "Check-out date", "Departure/check-out date (must be after check-in)."),
            ("deposit", "Deposit", "Optional deposit amount collected/recorded."),
        ],
    },
    "/pos": {
        "sell": [
            ("scan/barcode", "Scan/lookup", "Scan or enter barcode/SKU/lot to find item/batch quickly."),
            (
                "reservation_id",
                "Reservation (charge to folio)",
                "Select a reservation to post POS charges to its folio.",
            ),
            (
                "desc/qty/price",
                "Item, quantity, price",
                "Item description, quantity, and unit/line price for the charge.",
            ),
            (
                "walk-in cart",
                "Walk-in cart",
                "Add items to cart and complete a walk-in sale without a room reservation.",
            ),
        ],
        "inventory": [
            ("item_name/category/unit", "Item details", "Name, category, and unit of measure for the product."),
            ("stock_qty/unit_cost/selling_price", "Stock & pricing", "Current/opening stock, cost, and selling price."),
            ("sku/barcode", "SKU/Barcode", "Identifiers for lookup, labels, and traceability."),
            (
                "batch/expiry settings",
                "Batch/expiry tracking",
                "Enable lot tracking and shelf-life defaults when needed.",
            ),
        ],
    },
    "/guests": {
        "directory": [
            ("search (q)", "Search guests", "Find guests by name/phone/email or ID fragment."),
        ],
    },
    "/settings": {
        "general": [("business settings", "General settings", "Core app/business configuration options.")],
        "branding": [("display name/tagline/logo", "Branding", "Property display name, tagline, and branding assets.")],
        "rooms": [("room_number/type/price", "Rooms", "Room inventory, type, and base pricing.")],
        "rate_plans": [("plan name/modifier", "Rate plans", "Pricing plans/packages and modifiers.")],
    },
}


def _ui_where(ui_context):
    ctx = ui_context or {}
    path = (ctx.get("path") or "/").rstrip("/") or "/"
    tab = (ctx.get("tab") or "").strip()
    title = ctx.get("title") or ""
    recent = ctx.get("recent_actions") or []
    return path, tab, title, recent


def _get_pending_from_ui(ui_context):
    ctx = ui_context or {}
    p = ctx.get("pending_action")
    if isinstance(p, dict):
        return p
    sess = ctx.get("session")
    if isinstance(sess, dict) and isinstance(sess.get("pending_action"), dict):
        return sess.get("pending_action")
    return None


def _ui_guidance(message: str, ui_context=None):
    """Detect navigation/help requests and return (handled_result_or_None)."""
    t = (message or "").strip()
    tl = t.lower()
    path, tab, title, recent = _ui_where(ui_context)
    actions = []

    def nav(url, label=None):
        actions.append({"type": "navigate", "url": url})

    # confirm/cancel pending action in chat (UI buttons call endpoints; this is text fallback)
    pending = _get_pending_from_ui(ui_context)
    if pending and isinstance(pending, dict):
        pid = str(pending.get("id") or pending.get("action_id") or "")
        if tl in {"confirm", "confirm action", "yes confirm", "proceed", "approve"} or tl.startswith("confirm "):
            return {
                "ok": True,
                "intent": "action_confirm_request",
                "confidence": 0.99,
                "tools_used": [],
                "data": {"pending_action": pending},
                "answer": "Please use the **Confirm** button on the proposed action to execute it safely (with required checks/proofs). I can’t auto-execute writes from a plain text confirmation.",
                "source": "action_guard",
                "ui_actions": actions,
                "slots": {"pending_action_id": pid},
            }
        if tl in {"cancel", "cancel action", "abort", "never mind", "nevermind"} or tl.startswith("cancel "):
            try:
                from .actions import cancel_pending_action

                cancelled = cancel_pending_action(pending, reason="user_cancel_text")
            except Exception:
                cancelled = {"ok": True, "status": "cancelled", "action_id": pid}
            return {
                "ok": True,
                "intent": "action_cancel",
                "confidence": 0.99,
                "tools_used": [],
                "data": {"pending_action": cancelled},
                "answer": "Pending action cancelled (if it was still active). You can ask me to prepare a different action.",
                "source": "action_guard",
                "ui_actions": actions,
                "slots": {"pending_action_id": pid},
                "pending_action": None,
                "clear_pending_action": True,
            }

    # where am I / context
    if any(k in tl for k in ["where am i", "where am i?", "which page", "what page", "current page", "what tab"]):
        loc = f"You are on **{path}**" + (f" (tab: **{tab}**)" if tab else "") + (f" — {title}" if title else "") + "."
        if recent:
            last = recent[-3:]
            ra = "; ".join(
                [
                    f"{a.get('type', 'action')}{(': ' + a.get('text', '')) if a.get('text') else ''}{(' ' + a.get('href', '')) if a.get('href') else ''}"
                    for a in last
                ]
            )
            loc += f"\nRecent actions: {ra}"
        return {
            "ok": True,
            "intent": "ui_context",
            "confidence": 0.99,
            "tools_used": [],
            "data": {"ui_context": ui_context or {}},
            "answer": loc,
            "source": "ui_context",
            "ui_actions": actions,
            "slots": {},
        }

    # navigation/help intents
    wants_how = any(
        k in tl
        for k in ["how to", "how do i", "how can i", "guide me", "show me how", "help me", "steps to", "explain how"]
    )
    wants_open = any(k in tl for k in ["open ", "go to", "take me", "navigate to", "show ", "switch to"])
    wants_explain_fields = any(
        k in tl
        for k in [
            "explain fields",
            "explain all fields",
            "what do the fields mean",
            "field meanings",
            "what does each field",
            "explain this form",
            "explain form",
        ]
    )

    # reservation create
    if ("reservation" in tl or "booking" in tl) and (
        ("add" in tl) or ("create" in tl) or ("new" in tl) or ("make" in tl)
    ):
        nav("/reservations?tab=create")
        guide = UI_FIELD_GUIDE.get("/reservations", {}).get("create", [])
        lines = ["I can take you to **Reservations → Create** to add a new reservation."]
        if wants_how or wants_explain_fields or "how" in tl or "explain" in tl or True:
            lines.append(
                "\n**Steps:** open Reservations → Create tab, fill guest and stay details, choose room/rate, then submit to create the reservation."
            )
            if guide:
                lines.append("\n**Fields explained:**")
                for _, label, desc in guide:
                    lines.append(f"- **{label}:** {desc}")
        lines.append(
            "\nI’ve opened the Create Reservation tab for you. If you want, ask me to explain any specific field while you fill it in."
        )
        return {
            "ok": True,
            "intent": "ui_navigate_help",
            "confidence": 0.98,
            "tools_used": [],
            "data": {"target": "/reservations?tab=create", "ui_context": ui_context or {}},
            "answer": "\n".join(lines),
            "source": "ui_guide",
            "ui_actions": actions,
            "slots": {},
        }

    # explain fields on current screen
    if wants_explain_fields or (
        ("explain" in tl or "what does" in tl) and ("field" in tl or "form" in tl or "input" in tl)
    ):
        guide = (
            UI_FIELD_GUIDE.get(path, {}).get(tab)
            or UI_FIELD_GUIDE.get(path, {}).get("sell")
            or UI_FIELD_GUIDE.get(path, {}).get("directory")
            or []
        )
        # also include visible fields from context as fallback
        visible = (ui_context or {}).get("fields") or []
        lines = [f"Here’s guidance for the current screen (**{path}**{(' / ' + tab) if tab else ''}):"]
        if guide:
            lines.append("\n**Known fields:**")
            for _, label, desc in guide:
                lines.append(f"- **{label}:** {desc}")
        if visible:
            labels = []
            for f in visible:
                if f.get("kind") == "label":
                    labels.append(f.get("text"))
                elif f.get("label"):
                    labels.append(f.get("label"))
            labels = [x for x in labels if x]
            if labels:
                uniq = []
                seen = set()
                for x in labels:
                    k = x.lower()
                    if k in seen:
                        continue
                    seen.add(k)
                    uniq.append(x)
                if uniq:
                    lines.append("\n**Visible labels/fields detected on this page:** " + ", ".join(uniq[:20]))
        if not guide and not visible:
            lines.append(
                "I don’t have a detailed field map for this specific view yet, but tell me the field name and I’ll explain what it’s for."
            )
        return {
            "ok": True,
            "intent": "ui_explain_fields",
            "confidence": 0.95,
            "tools_used": [],
            "data": {"ui_context": ui_context or {}},
            "answer": "\n".join(lines),
            "source": "ui_guide",
            "ui_actions": actions,
            "slots": {},
        }

    # generic navigation shortcuts
    nav_map = [
        ("/", ["dashboard", "home", "overview"]),
        ("/reservations?tab=list", ["reservation list", "reservations list", "bookings", "all reservations"]),
        ("/reservations?tab=create", ["new reservation", "create reservation", "add reservation", "book a room"]),
        ("/guests", ["guests", "guest directory", "guest list"]),
        ("/pos?tab=sell", ["pos", "sell", "point of sale", "walk-in sale", "sell product", "sell meal"]),
        ("/pos?tab=inventory", ["inventory", "products", "stock"]),
        ("/pos?tab=meals", ["meals", "recipes"]),
        ("/pos?tab=activities", ["activities", "trips"]),
        ("/accounting", ["accounting", "expenses", "ledger"]),
        ("/reports", ["reports", "analytics"]),
        ("/settings", ["settings", "branding", "rooms setup", "rate plans"]),
    ]
    if wants_open or ("open" in tl) or ("go to" in tl) or ("take me" in tl):
        for url, keys in nav_map:
            if any(k in tl for k in keys):
                nav(url)
                return {
                    "ok": True,
                    "intent": "ui_navigate",
                    "confidence": 0.97,
                    "tools_used": [],
                    "data": {"target": url, "ui_context": ui_context or {}},
                    "answer": f"Opening **{url}** for you now.",
                    "source": "ui_guide",
                    "ui_actions": actions,
                    "slots": {},
                }

    # how-to for POS/products/meals without reservation
    if wants_how and any(
        k in tl
        for k in ["sell", "pos", "product", "meal", "without reservation", "no reservation", "walk-in", "walk in"]
    ):
        nav("/pos?tab=sell")
        lines = [
            "To sell products/meals **without a room reservation** (walk-in): go to **POS / Inventory → Sell**, add items to the **walk-in cart**, then complete the walk-in sale/payment."
        ]
        lines.append("- You can scan barcodes/SKUs/lots, search products, and sell meals/recipes from inventory.")
        lines.append("- Reservation/folio charging is optional — only use it when you want to post to a guest stay.")
        return {
            "ok": True,
            "intent": "ui_navigate_help",
            "confidence": 0.96,
            "tools_used": [],
            "data": {"target": "/pos?tab=sell", "ui_context": ui_context or {}},
            "answer": "\n".join(lines),
            "source": "ui_guide",
            "ui_actions": actions,
            "slots": {},
        }

    return None


def chat(message: str, history: list[dict] | None = None, ui_context: dict | None = None) -> dict:
    message = (message or "").strip()
    if not message:
        return {
            "ok": False,
            "source": "none",
            "tools_used": [],
            "data": None,
            "ui_actions": [],
            "answer": "Hi! How can I help? Ask me about reports, guests, reservations, operations, a business briefing — or ask me to open a page/tab and explain fields. I’ll answer from real app data.",
        }
    try:
        services.touch_session()
    except Exception:
        logger.debug("assistant.chat: failed to touch session", exc_info=True)

    # UI navigation/help and action confirm/cancel guards
    try:
        ui_res = _ui_guidance(message, ui_context=ui_context)
        if ui_res:
            ui_res.setdefault("ui_actions", [])
            ui_res.setdefault("response_mode", "core")
            ui_res["ui_context"] = ui_context or {}
            # preserve pending action context for clients when present
            if (
                "proposed_action" not in ui_res
                and _get_pending_from_ui(ui_context)
                and not ui_res.get("clear_pending_action")
            ):
                ui_res.setdefault("pending_action", _get_pending_from_ui(ui_context))
            return ui_res
    except Exception:
        logger.debug("assistant.chat: ui guidance failed", exc_info=True)

    config = _pkg()._llm_config()
    mode = config.get("response_mode") or "talking"

    if mode == "full_llm":
        res = llm_route(message, history=history, config=config)
        if res:
            res["response_mode"] = "full_llm_local"
            res.setdefault("ui_actions", [])
            res["ui_context"] = ui_context or {}
            return res

    slots = extract_slots(message, history)
    cls = classify_intent(message, history)
    intent = cls.get("intent") or "capabilities_help"
    confidence = float(cls.get("confidence") or 0.0)
    slots = {**slots, **(cls.get("slots") or {})}
    strong = bool(slots.get("reservation_id") or slots.get("guest_id") or confidence >= 0.85)

    if intent == "compound_multi_intent":
        core = _execute_compound(message, slots, ui_context=ui_context)
    elif (
        confidence < 0.45
        and not strong
        and intent not in ("capabilities_help", "unsupported_or_unsafe", "greeting_smalltalk")
    ):
        # stay local: clarify rather than calling external llm
        core = _execute_single("ambiguous_needs_clarification", message, slots, ui_context=ui_context)
        core["intent"] = intent
    else:
        if intent == "capabilities_help" and confidence < 0.5 and cls.get("low_confidence"):
            core = _execute_single("ambiguous_needs_clarification", message, slots, ui_context=ui_context)
            core["intent"] = "ambiguous_needs_clarification"
        else:
            core = _execute_single(intent, message, slots, ui_context=ui_context)

    core.setdefault("source", "core_deterministic")
    core["intent"] = core.get("intent") or intent
    core["confidence"] = confidence
    core["slots"] = slots
    core["router"] = cls.get("router", "rules")
    core["response_mode"] = mode
    core["core_answer"] = core.get("answer")
    core["core_source"] = core.get("source")
    core.setdefault("ui_actions", [])
    core["ui_context"] = ui_context or {}
    # carry pending action through if not replaced
    if not core.get("proposed_action") and not core.get("clear_pending_action") and _get_pending_from_ui(ui_context):
        core.setdefault("pending_action", _get_pending_from_ui(ui_context))
    core.setdefault("proposed_action", None)

    # Local "talking" polish (deterministic always, optional local GGUF) — no external calls
    if (
        mode in ("talking", "auto", "local")
        and core.get("ok")
        and not core.get("needs_clarification")
        and intent not in ("greeting_smalltalk",)
    ):
        polished = _pkg().talking_rephrase(message, core, config)
        if polished and polished.strip():
            # ensure grounded: if model somehow drops key, prefer core; here we trust but keep core data
            core["answer"] = polished
            core["source"] = f"talking_local+{core.get('core_source', 'core')}"
            if config.get("enabled"):
                core["talking_model"] = "local_gguf"
            else:
                core["talking_model"] = "deterministic"
        else:
            core["source"] = core.get("core_source") or core.get("source")
    elif mode == "core":
        core["source"] = core.get("core_source") or core.get("source")

    # propagate resolved guest for context
    if core.get("resolved_guest_id"):
        try:
            core.setdefault("slots", {})["guest_id"] = core["resolved_guest_id"]
        except Exception:
            logger.debug("assistant.chat: failed to propagate resolved_guest_id", exc_info=True)
    return core
