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
        provider=(services.get_setting("assistant_provider","auto") or "auto").strip().lower()
        response_mode=(services.get_setting("assistant_response_mode","talking") or "talking").strip().lower()
        model_path=(services.get_setting("assistant_local_model_path","") or os.environ.get("ASSISTANT_LOCAL_MODEL_PATH","") or "").strip()
        n_ctx=int(services.get_setting("assistant_local_n_ctx","2048") or 2048)
    except Exception:
        provider,response_mode,model_path,n_ctx="auto","talking","",2048
    # normalize: anything "openai"/external -> treated as local/auto (no external)
    if provider in ("openai","openai_compatible","external"):
        provider = "auto"
    local_available = False
    try:
        import llama_cpp  # type: ignore
        if model_path and Path(model_path).expanduser().exists():
            local_available = True
    except Exception:
        local_available = False
    # 'enabled' means local model runtime available; deterministic talking is always available separately
    enabled = provider != "deterministic_only" and local_available
    return {"provider":provider,"response_mode":response_mode,"model_path":model_path,"n_ctx":n_ctx,
            "local_available": local_available, "enabled": enabled, "external": False}


def get_llm_config() -> dict:
    """Public assistant LLM/runtime configuration/status (no external endpoints)."""
    return _llm_config()

def _get_local_llm():
    global _LOCAL_LLM_CACHE
    cfg = _llm_config()
    if not cfg.get("local_available"): return None
    if _LOCAL_LLM_CACHE is not None: return _LOCAL_LLM_CACHE or None
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
    if llm is None: return None
    try:
        out = llm(prompt, max_tokens=max_tokens, temperature=temperature, stop=["</s>","User:","Manager question:"])
        if isinstance(out, dict):
            txt = ((out.get("choices") or [{}])[0].get("text") or "").strip()
            return txt or None
        return str(out).strip() or None
    except Exception:
        return None

def talking_rephrase(message: str, core_result: dict, config: dict | None = None) -> str | None:
    """Local-only friendly rephrase. Uses local GGUF model if available, else deterministic formatter."""
    config = config or _pkg()._llm_config()
    core_answer=(core_result or {}).get("answer") or ""
    data=(core_result or {}).get("data")
    # Try local model first (if enabled)
    if config.get("enabled"):
        try:
            data_str=json.dumps(data,ensure_ascii=False,default=str)[:3500]
        except Exception:
            data_str=str(data)[:3500]
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
    text=(message or "").strip()
    if not text: return None
    slots=extract_slots(text)
    cls=rule_classify(text,slots)
    intent=cls.get("intent","capabilities_help"); conf=cls.get("confidence",0.0)
    if cls.get("low_confidence") and conf < 0.4 and not (slots.get("reservation_id") or slots.get("guest_id")):
        return None
    if intent=="compound_multi_intent": res=_execute_compound(text,slots)
    else: res=_execute_single(intent,text,slots)
    res.setdefault("source","core_deterministic"); res.setdefault("ok",True); res.setdefault("tools_used",[])
    res["intent"]=res.get("intent") or intent; res["confidence"]=conf; res["slots"]=slots; res["router"]=cls.get("router","rules")
    return res

def llm_route(message: str, history: list[dict] | None = None, config: dict | None = None) -> dict | None:
    """Local-only 'full' route: uses local GGUF if available for phrasing, else deterministic tool routing. No external calls."""
    config=config or _pkg()._llm_config()
    # Always produce a grounded deterministic/tool result
    slots=extract_slots(message, history)
    cls=classify_intent(message, history)
    intent=cls.get("intent") or "capabilities_help"
    slots={**slots, **(cls.get("slots") or {})}
    if intent=="compound_multi_intent": core=_execute_compound(message, slots)
    else: core=_execute_single(intent, message, slots)
    core.setdefault("source","local_grounded"); core["intent"]=core.get("intent") or intent
    core["confidence"]=cls.get("confidence",0.0); core["slots"]=slots; core["router"]=cls.get("router","rules")
    # optionally polish with local model
    if config.get("enabled") and core.get("ok") and not core.get("needs_clarification"):
        polished = _pkg().talking_rephrase(message, {**core, "core_answer": core.get("answer")}, config)
        if polished: core["answer"]=polished; core["source"]="local_llm+"+core.get("source","core"); core["talking_model"]="local_gguf"
    else:
        core["source"]="local_grounded_core"
    return core

def chat(message: str, history: list[dict] | None = None) -> dict:
    message=(message or "").strip()
    if not message:
        return {"ok":False,"source":"none","tools_used":[],"data":None,"answer":"Hi! How can I help? Ask me about reports, guests, reservations, operations, or a business briefing — I’ll answer from real app data."}
    try:
        services.touch_session()
    except Exception:
        logger.debug("assistant.chat: failed to touch session", exc_info=True)
    config=_pkg()._llm_config(); mode=config.get("response_mode") or "talking"; provider=config.get("provider") or "auto"

    if mode=="full_llm":
        res=llm_route(message, history=history, config=config)
        if res: res["response_mode"]="full_llm_local"; return res

    slots=extract_slots(message, history)
    cls=classify_intent(message, history)
    intent=cls.get("intent") or "capabilities_help"; confidence=float(cls.get("confidence") or 0.0)
    slots={**slots, **(cls.get("slots") or {})}
    strong = bool(slots.get("reservation_id") or slots.get("guest_id") or confidence>=0.85)

    if intent=="compound_multi_intent":
        core=_execute_compound(message, slots)
    elif confidence < 0.45 and not strong and intent not in ("capabilities_help","unsupported_or_unsafe","greeting_smalltalk"):
        # stay local: clarify rather than calling external llm
        core=_execute_single("ambiguous_needs_clarification", message, slots); core["intent"]=intent
    else:
        if intent=="capabilities_help" and confidence < 0.5 and cls.get("low_confidence"):
            core=_execute_single("ambiguous_needs_clarification", message, slots); core["intent"]="ambiguous_needs_clarification"
        else:
            core=_execute_single(intent, message, slots)

    core.setdefault("source","core_deterministic"); core["intent"]=core.get("intent") or intent
    core["confidence"]=confidence; core["slots"]=slots; core["router"]=cls.get("router","rules")
    core["response_mode"]=mode; core["core_answer"]=core.get("answer"); core["core_source"]=core.get("source")

    # Local "talking" polish (deterministic always, optional local GGUF) — no external calls
    if mode in ("talking","auto","local") and core.get("ok") and not core.get("needs_clarification") and intent not in ("greeting_smalltalk",):
        polished = _pkg().talking_rephrase(message, core, config)
        if polished and polished.strip():
            # ensure grounded: if model somehow drops key, prefer core; here we trust but keep core data
            core["answer"]=polished
            core["source"]=f"talking_local+{core.get('core_source','core')}"
            if config.get("enabled"): core["talking_model"]="local_gguf"
            else: core["talking_model"]="deterministic"
        else:
            core["source"]=core.get("core_source") or core.get("source")
    elif mode=="core":
        core["source"]=core.get("core_source") or core.get("source")

    # propagate resolved guest for context
    if core.get("resolved_guest_id"):
        try:
            core.setdefault("slots", {})["guest_id"] = core["resolved_guest_id"]
        except Exception:
            logger.debug("assistant.chat: failed to propagate resolved_guest_id", exc_info=True)
    return core
