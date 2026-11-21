"""Dynamic tool executor for SPMS assistant.

Dispatches manifest-declared tools by type:
- knowledge: search curated knowledge entries in agent_brain.json
- composite: run declared steps across available tools
- native: resolve allowlisted registered handler
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .brain import load_brain
from .common import _norm_text, _token_set
from .tool_registry import REGISTRY, register_tools_dict

logger = logging.getLogger(__name__)


def _ensure_registry() -> None:
    # Lazy auto-registration of existing tools for backward compatibility.
    if REGISTRY.handler_ids():
        return
    try:
        from .tools import TOOLS

        register_tools_dict(TOOLS, source="tools.TOOLS")
    except Exception:
        logger.debug("executor: failed to auto-register TOOLS", exc_info=True)


def _score_text(query: str, *fields: str) -> float:
    qn = _norm_text(query)
    if not qn:
        return 0.0
    qset = _token_set(qn)
    best = 0.0
    for field in fields:
        tn = _norm_text(field or "")
        if not tn:
            continue
        score = 0.0
        if qn == tn:
            score = 1.0
        elif qn in tn:
            score = 0.85
        else:
            tset = _token_set(tn)
            inter = len(qset & tset)
            union = len(qset | tset) or 1
            score = inter / union
            if inter:
                score = max(score, 0.35 + 0.65 * (inter / max(len(qset), 1)))
        best = max(best, score)
    return best


def search_knowledge(query: str, *, top_k: int = 5, brain=None) -> dict[str, Any]:
    b = brain or load_brain()
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "query is required", "results": []}
    k = max(1, min(int(top_k or 5), 20))
    scored: list[dict[str, Any]] = []
    for entry in b.knowledge():
        title = str(entry.get("title") or entry.get("section") or entry.get("id") or "")
        content = str(entry.get("content") or "")
        tags = " ".join([str(x) for x in (entry.get("tags") or []) if str(x).strip()])
        keywords = " ".join([str(x) for x in (entry.get("keywords") or []) if str(x).strip()])
        score = _score_text(q, title, content, tags, keywords, str(entry.get("source_path") or ""))
        if score <= 0.05:
            continue
        scored.append({"score": round(score, 4), "entry": entry})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return {"ok": True, "query": q, "results": scored[:k], "count": len(scored[:k])}


def _validate_args_basic(args_schema: dict[str, Any] | None, args: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    schema = args_schema if isinstance(args_schema, dict) else {}
    required = schema.get("required") or []
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    for key in required:
        if key not in args or args.get(key) in (None, ""):
            errors.append(f"missing required arg: {key}")
    type_map = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list}
    for key, val in args.items():
        p = props.get(key)
        if not isinstance(p, dict):
            continue
        expected = p.get("type")
        if not isinstance(expected, str):
            continue
        py_t = type_map.get(expected)
        if py_t and val is not None:
            if expected in {"integer", "number"} and isinstance(val, bool):
                errors.append(f"arg {key} must be {expected}")
            elif not isinstance(val, py_t):
                errors.append(f"arg {key} must be {expected}")
            elif expected == "integer" and isinstance(val, bool):
                errors.append(f"arg {key} must be integer")
    return errors


def _resolve_tool_def(tool_id: str, brain=None) -> dict[str, Any] | None:
    b = brain or load_brain()
    t = b.get_tool(tool_id)
    if t is not None:
        return t
    _ensure_registry()
    # Backward compat: allow registered/legacy tools not yet declared in manifest.
    # Manifest remains control plane for declared tools; undeclared registered tools are allowed with flag.
    reg = REGISTRY.get(tool_id)
    if reg is None:
        try:
            from .tools import TOOLS

            spec = TOOLS.get(tool_id) or {}
            fn = spec.get("fn")
            if callable(fn):
                REGISTRY.register(tool_id, fn, meta={"description": spec.get("description", ""), "parameters": spec.get("parameters", {})}, source="tools.TOOLS.dynamic")
                reg = REGISTRY.get(tool_id)
        except Exception:
            reg = None
    if reg is None:
        return None
    return {
        "id": tool_id,
        "type": "native",
        "handler": tool_id,
        "enabled": True,
        "read_only": bool(reg.meta.get("read_only", True)),
        "description": reg.meta.get("description", ""),
        "args_schema": reg.meta.get("parameters", {"type": "object", "properties": {}}),
        "manifest_declared": False,
    }


def execute(tool_id: str, args: dict[str, Any] | None = None, *, ctx: dict[str, Any] | None = None, brain=None) -> dict[str, Any]:
    _ensure_registry()
    b = brain or load_brain()
    args = dict(args or {})
    ctx = dict(ctx or {})

    defn = _resolve_tool_def(tool_id, brain=b)
    if defn is None:
        available = [t.get("id") for t in b.tools(enabled_only=True) if t.get("id")]
        return {
            "ok": False,
            "error": f"Unknown tool '{tool_id}' (not declared/enabled in agent_brain.json)",
            "available_tools": available,
        }
    if defn.get("enabled", True) is False:
        return {"ok": False, "error": f"Tool '{tool_id}' is disabled in agent_brain.json", "tool": tool_id}

    arg_errors = _validate_args_basic(defn.get("args_schema"), args)
    if arg_errors:
        return {"ok": False, "error": "invalid_args", "details": arg_errors, "tool": tool_id}

    ttype = str(defn.get("type") or "native")
    try:
        if ttype == "knowledge":
            return _execute_knowledge(defn, args, b)
        if ttype == "composite":
            return _execute_composite(defn, args, ctx, b)
        if ttype == "native":
            return _execute_native(defn, args, ctx)
        return {"ok": False, "error": f"Unsupported tool type '{ttype}'", "tool": tool_id}
    except Exception as e:
        logger.warning("executor.execute failed for %s", tool_id, exc_info=True)
        return {"ok": False, "error": f"Tool '{tool_id}' failed: {e}", "tool": tool_id}


def _execute_knowledge(defn: dict[str, Any], args: dict[str, Any], brain) -> dict[str, Any]:
    query = str(args.get("query") or args.get("q") or "").strip()
    top_k = int(args.get("top_k") or 5)
    res = search_knowledge(query, top_k=top_k, brain=brain)
    res["tool"] = defn.get("id")
    return res


def _execute_native(defn: dict[str, Any], args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    handler_id = str(defn.get("handler") or defn.get("id") or "").strip()
    reg = REGISTRY.get(handler_id)
    if reg is None:
        return {"ok": False, "error": "handler_unavailable", "tool": defn.get("id"), "handler": handler_id}
    fn = reg.fn
    # Backward compat: most existing handlers accept (args).
    try:
        result = fn(args, ctx)
    except TypeError:
        result = fn(args)
    if isinstance(result, dict):
        return result
    return {"ok": True, "result": result}


def _template_value(value: Any, *, args: dict[str, Any], steps: dict[str, Any], ctx: dict[str, Any]) -> Any:
    if isinstance(value, str):
        m = re.fullmatch(r"\$\{([a-zA-Z0-9_.-]+)\}", value)
        if m:
            path = m.group(1).split(".")
            root = {"args": args, "steps": steps, "ctx": ctx}
            cur: Any = root
            for p in path:
                if isinstance(cur, dict) and p in cur:
                    cur = cur[p]
                else:
                    return None
            return cur
        return value.format_map(_SafeMap({"args": args, "steps": steps, "ctx": ctx}))
    if isinstance(value, list):
        return [_template_value(v, args=args, steps=steps, ctx=ctx) for v in value]
    if isinstance(value, dict):
        return {k: _template_value(v, args=args, steps=steps, ctx=ctx) for k, v in value.items()}
    return value


class _SafeMap(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _execute_composite(defn: dict[str, Any], args: dict[str, Any], ctx: dict[str, Any], brain) -> dict[str, Any]:
    steps_def = defn.get("steps") or []
    if not isinstance(steps_def, list) or not steps_def:
        return {"ok": False, "error": "composite tool has no steps", "tool": defn.get("id")}
    outputs: dict[str, Any] = {}
    trace: list[dict[str, Any]] = []
    for i, step in enumerate(steps_def):
        if not isinstance(step, dict):
            continue
        tool_id = str(step.get("tool") or "").strip()
        if not tool_id:
            trace.append({"step": i, "ok": False, "error": "missing tool"})
            continue
        step_args = _template_value(step.get("args") or {}, args=args, steps=outputs, ctx=ctx)
        if not isinstance(step_args, dict):
            step_args = {}
        res = execute(tool_id, step_args, ctx=ctx, brain=brain)
        alias = str(step.get("as") or f"step_{i}")
        outputs[alias] = res
        trace.append({"step": i, "as": alias, "tool": tool_id, "ok": not (isinstance(res, dict) and res.get("error"))})
        if step.get("stop_on_error") and isinstance(res, dict) and res.get("error"):
            break
    return {"ok": True, "tool": defn.get("id"), "steps": trace, "outputs": outputs}
