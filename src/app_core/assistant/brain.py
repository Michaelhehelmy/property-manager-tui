"""Single-file agent brain/manifest for SPMS assistant.

`agent_brain.json` is the control plane: agent identity, boundaries, skills,
tools, workflows, and curated knowledge. Runtime reads this file; JSON never
executes code. Native tools execute only through allowlisted registered handlers.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

from .common import DATA_DIR

logger = logging.getLogger(__name__)

BRAIN_DIR = DATA_DIR / "assistant_knowledge"
BRAIN_PATH = BRAIN_DIR / "agent_brain.json"
BRAIN_VERSION = 1


def _default_agent() -> dict[str, Any]:
    return {
        "name": "Manager Assistant",
        "character": "Warm, clear, professional property-management business partner for SPMS.",
        "tone": ["friendly", "concise", "answer-first", "grounded"],
        "boundaries": [
            "Use only grounded app data/tools and curated knowledge in this file.",
            "Never invent IDs, amounts, dates, guests, bookings, or actions.",
            "Read-only by default; mutating actions require explicit validated confirmation/proof.",
            "No external AI calls/endpoints/keys; local-only runtime.",
        ],
    }


def _default_workflows() -> dict[str, Any]:
    return {
        "answer_flow": [
            "Understand request and extract slots/intent.",
            "Check approved interaction knowledge for strong sanitized match.",
            "Retrieve relevant curated knowledge when app how-to/policy/setup is needed.",
            "Call read-only app tools when live data is needed.",
            "Compose grounded answer with citations/sources; no invented facts.",
            "Propose validated action only for supported mutating flows; require confirmation/proof.",
            "Record interaction and apply thumb up/down/correction learning.",
        ],
        "feedback_flow": {
            "thumb_up": "Mark correct; sanitize and promote to approved internal knowledge for fast reuse.",
            "thumb_down": "Mark wrong; do not promote to approved knowledge; optionally collect correction/expected answer.",
            "full_evaluation": "Store corrected intent/slots/expected answer as correction/training data.",
        },
    }


def _default_skills() -> list[dict[str, Any]]:
    return [
        {
            "id": "app_knowledge_lookup",
            "name": "App Knowledge Lookup",
            "enabled": True,
            "description": "Answer SPMS how-to/policy/setup/troubleshooting questions from curated knowledge.",
            "uses_tools": ["search_app_knowledge"],
            "workflow": [
                "Search curated knowledge entries by query/tags.",
                "Answer using only retrieved grounded entries.",
                "Cite source_path and section.",
            ],
            "tags": ["docs", "policy", "how-to", "knowledge"],
        },
        {
            "id": "tool_builder",
            "name": "Tool Builder",
            "enabled": False,
            "type": "workflow_skill",
            "description": "Guarded workflow for creating/extending assistant tools via clear validated steps.",
            "entry_state": "spec_collect",
            "states": {
                "spec_collect": {
                    "required": ["id", "purpose", "args_schema", "read_only"],
                    "next": "reuse_check",
                    "gate": "required_fields_present",
                },
                "reuse_check": {"next": "type_decide", "gate": "no_duplicate_or_confirm_replace"},
                "type_decide": {"next": "plan", "gate": "type_allowed"},
                "plan": {"next": "generate", "gate": "plan_has_files_tests_risks"},
                "generate": {"next": "register", "gate": "allowed_paths_and_safe_code"},
                "register": {"next": "validate", "gate": "handler_resolves"},
                "validate": {"next_on_pass": "approve", "next_on_fail": "generate", "gate": "checks_pass"},
                "approve": {"next_on_approved": "enable", "next_on_rejected": "spec_collect", "gate": "owner_approved"},
                "enable": {"next": "learn", "gate": "manifest_enabled_and_loaded"},
                "learn": {"terminal": True},
            },
            "tags": ["builder", "workflow", "dev"],
        },
    ]


def tool_def_from_tools_dict(tool_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    spec = spec or {}
    return {
        "id": tool_id,
        "type": "native",
        "handler": tool_id,
        "enabled": True,
        "read_only": bool(spec.get("read_only", True)),
        "description": spec.get("description", "") or "",
        "args_schema": spec.get("parameters", {"type": "object", "properties": {}, "additionalProperties": False}),
        "tags": list(spec.get("tags") or []),
    }


def build_default_brain(tools: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    tools = tools or {}
    tool_defs = [tool_def_from_tools_dict(tid, spec) for tid, spec in sorted(tools.items())]
    # Builtin knowledge tool is declarative; executor handles type=knowledge.
    if not any(t.get("id") == "search_app_knowledge" for t in tool_defs):
        tool_defs.insert(
            0,
            {
                "id": "search_app_knowledge",
                "type": "knowledge",
                "handler": "builtin.search_app_knowledge",
                "enabled": True,
                "read_only": True,
                "description": "Search curated SPMS app knowledge entries in agent_brain.json.",
                "args_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                "tags": ["knowledge", "docs", "policy", "how-to"],
            },
        )
    return {
        "version": BRAIN_VERSION,
        "generated_at": None,
        "agent": _default_agent(),
        "boundaries": list(_default_agent()["boundaries"]),
        "workflows": _default_workflows(),
        "skills": _default_skills(),
        "tools": tool_defs,
        "knowledge": [],
    }


def _is_str_list(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def validate_brain(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["manifest must be a JSON object"]
    if data.get("version") != BRAIN_VERSION:
        errors.append(f"version must be {BRAIN_VERSION}")
    agent = data.get("agent")
    if not isinstance(agent, dict):
        errors.append("agent must be an object")
    else:
        if not str(agent.get("name") or "").strip():
            errors.append("agent.name must be a non-empty string")
    for key in ("skills", "tools", "knowledge"):
        if not isinstance(data.get(key), list):
            errors.append(f"{key} must be a list")
    if not isinstance(data.get("workflows"), dict):
        errors.append("workflows must be an object")
    if not isinstance(data.get("boundaries", []), list):
        errors.append("boundaries must be a list")

    seen_tool_ids: set[str] = set()
    for i, t in enumerate(data.get("tools") or []):
        if not isinstance(t, dict):
            errors.append(f"tools[{i}] must be an object")
            continue
        tid = str(t.get("id") or "").strip()
        if not tid:
            errors.append(f"tools[{i}].id is required")
            continue
        if tid in seen_tool_ids:
            errors.append(f"duplicate tool id: {tid}")
        seen_tool_ids.add(tid)
        ttype = str(t.get("type") or "native")
        if ttype not in {"native", "knowledge", "composite", "service"}:
            errors.append(f"tools[{i}].type invalid: {ttype}")
        if ttype == "native" and not str(t.get("handler") or "").strip():
            errors.append(f"tools[{i}].handler required for native tool")
        if "enabled" in t and not isinstance(t.get("enabled"), bool):
            errors.append(f"tools[{i}].enabled must be boolean")
        if "read_only" in t and not isinstance(t.get("read_only"), bool):
            errors.append(f"tools[{i}].read_only must be boolean")
        if "tags" in t and not _is_str_list(t.get("tags")):
            errors.append(f"tools[{i}].tags must be list[str]")
    seen_skill_ids: set[str] = set()
    for i, s in enumerate(data.get("skills") or []):
        if not isinstance(s, dict):
            errors.append(f"skills[{i}] must be an object")
            continue
        sid = str(s.get("id") or "").strip()
        if not sid:
            errors.append(f"skills[{i}].id is required")
            continue
        if sid in seen_skill_ids:
            errors.append(f"duplicate skill id: {sid}")
        seen_skill_ids.add(sid)
        if "enabled" in s and not isinstance(s.get("enabled"), bool):
            errors.append(f"skills[{i}].enabled must be boolean")
        if "uses_tools" in s and not _is_str_list(s.get("uses_tools")):
            errors.append(f"skills[{i}].uses_tools must be list[str]")
    return errors


def normalize_brain(data: dict[str, Any], tools: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    base = build_default_brain(tools)
    if not isinstance(data, dict):
        return base
    out = deepcopy(base)
    for key in ("version", "generated_at"):
        if key in data:
            out[key] = data[key]
    if isinstance(data.get("agent"), dict):
        out["agent"].update({k: v for k, v in data["agent"].items() if v is not None})
    if isinstance(data.get("boundaries"), list):
        out["boundaries"] = [str(x) for x in data["boundaries"] if str(x).strip()]
    if isinstance(data.get("workflows"), dict):
        out["workflows"].update(data["workflows"])
    if isinstance(data.get("skills"), list):
        out["skills"] = [s for s in data["skills"] if isinstance(s, dict) and str(s.get("id") or "").strip()]
    if isinstance(data.get("tools"), list):
        # Keep manifest as control plane: only declared tools. Merge metadata defaults for known tools.
        by_id = {t["id"]: t for t in out["tools"]}
        merged: list[dict[str, Any]] = []
        for raw in data["tools"]:
            if not isinstance(raw, dict):
                continue
            tid = str(raw.get("id") or "").strip()
            if not tid:
                continue
            d = deepcopy(by_id.get(tid, {}))
            d.update(raw)
            d["id"] = tid
            d.setdefault("type", "native")
            d.setdefault("enabled", True)
            d.setdefault("read_only", True)
            d.setdefault("tags", [])
            merged.append(d)
        out["tools"] = merged
    if isinstance(data.get("knowledge"), list):
        out["knowledge"] = [k for k in data["knowledge"] if isinstance(k, dict)]
    # Ensure name fallback
    if not str(out.get("agent", {}).get("name") or "").strip():
        out["agent"]["name"] = "Manager Assistant"
    return out


class Brain:
    def __init__(self, data: dict[str, Any], path: Path | None = None, mtime: float | None = None):
        self.data = data
        self.path = path
        self.mtime = mtime

    @property
    def agent(self) -> dict[str, Any]:
        return self.data.get("agent") or {}

    @property
    def name(self) -> str:
        return str(self.agent.get("name") or "Manager Assistant")

    @property
    def boundaries(self) -> list[str]:
        b = self.data.get("boundaries")
        return [str(x) for x in b] if isinstance(b, list) else []

    @property
    def workflows(self) -> dict[str, Any]:
        w = self.data.get("workflows")
        return w if isinstance(w, dict) else {}

    def tools(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        items = [t for t in (self.data.get("tools") or []) if isinstance(t, dict)]
        if enabled_only:
            items = [t for t in items if t.get("enabled", True) is True]
        return items

    def skills(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        items = [s for s in (self.data.get("skills") or []) if isinstance(s, dict)]
        if enabled_only:
            items = [s for s in items if s.get("enabled", True) is True]
        return items

    def knowledge(self) -> list[dict[str, Any]]:
        return [k for k in (self.data.get("knowledge") or []) if isinstance(k, dict)]

    def get_tool(self, tool_id: str) -> dict[str, Any] | None:
        for t in self.tools(enabled_only=False):
            if t.get("id") == tool_id:
                return t
        return None

    def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        for s in self.skills(enabled_only=False):
            if s.get("id") == skill_id:
                return s
        return None


_BRAIN_CACHE: Brain | None = None
_BRAIN_MTIME: float | None = None


def _default_tools() -> dict[str, dict[str, Any]]:
    try:
        from .tools import TOOLS

        return TOOLS
    except Exception:
        return {}


def load_brain(path: Path | None = None, tools: dict[str, dict[str, Any]] | None = None, *, force: bool = False) -> Brain:
    global _BRAIN_CACHE, _BRAIN_MTIME
    p = path or BRAIN_PATH
    tools = tools if tools is not None else _default_tools()
    try:
        mtime = p.stat().st_mtime if p.exists() else None
    except OSError:  # pragma: no cover - defensive filesystem edge case
        mtime = None
    if not force and _BRAIN_CACHE is not None and p == (_BRAIN_CACHE.path or BRAIN_PATH) and mtime == _BRAIN_MTIME:
        return _BRAIN_CACHE

    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            data = normalize_brain(raw, tools)
            errs = validate_brain(data)
            if errs:
                logger.warning("agent_brain.json validation issues: %s", errs)
        except Exception:
            logger.warning("Failed to load agent_brain.json; using default brain", exc_info=True)
            data = build_default_brain(tools)
    else:
        data = build_default_brain(tools)

    _BRAIN_CACHE = Brain(data=data, path=p, mtime=mtime)
    _BRAIN_MTIME = mtime
    return _BRAIN_CACHE


def save_brain(data: dict[str, Any], path: Path | None = None) -> Path:
    global _BRAIN_CACHE, _BRAIN_MTIME
    p = path or BRAIN_PATH
    errs = validate_brain(data)
    if errs:
        raise ValueError("Invalid agent brain: " + "; ".join(errs))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        _BRAIN_MTIME = p.stat().st_mtime
    except OSError:  # pragma: no cover - defensive filesystem edge case
        _BRAIN_MTIME = None
    _BRAIN_CACHE = Brain(data=deepcopy(data), path=p, mtime=_BRAIN_MTIME)
    return p


def get_brain(*, force: bool = False) -> Brain:
    return load_brain(force=force)


def sync_tools_into_brain(tools: dict[str, dict[str, Any]] | None = None, *, path: Path | None = None, enable_new: bool = False) -> dict[str, Any]:
    """Add missing tool definitions from TOOLS/registry into manifest (control-plane opt-in)."""
    b = load_brain(path=path, tools=tools, force=True)
    data = deepcopy(b.data)
    tools = tools or _default_tools()
    existing = {str(t.get("id")) for t in data.get("tools") or [] if isinstance(t, dict)}
    for tid, spec in sorted((tools or {}).items()):
        if tid in existing:
            continue
        entry = tool_def_from_tools_dict(tid, spec)
        entry["enabled"] = bool(enable_new)
        data.setdefault("tools", []).append(entry)
    save_brain(data, path=path)
    return data
