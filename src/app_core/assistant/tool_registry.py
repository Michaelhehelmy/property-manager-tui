"""Dynamic tool registry for SPMS assistant.

Handlers register here (decorator or explicit). The agent_brain.json manifest
controls which registered tools are exposed/enabled. JSON never executes code;
it only references allowlisted registered handlers/builtins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


ToolFn = Callable[..., Any]


@dataclass
class RegisteredTool:
    handler_id: str
    fn: ToolFn
    meta: dict[str, Any] = field(default_factory=dict)
    source: str = ""


class ToolRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, RegisteredTool] = {}

    def register(self, handler_id: str, fn: ToolFn | None = None, *, meta: dict[str, Any] | None = None, source: str = ""):
        def wrap(f: ToolFn) -> ToolFn:
            self._handlers[handler_id] = RegisteredTool(
                handler_id=handler_id, fn=f, meta=dict(meta or {}), source=source
            )
            return f

        if fn is not None:
            return wrap(fn)
        return wrap

    def get(self, handler_id: str) -> RegisteredTool | None:
        return self._handlers.get(handler_id)

    def has(self, handler_id: str) -> bool:
        return handler_id in self._handlers

    def handler_ids(self) -> list[str]:
        return sorted(self._handlers.keys())

    def items(self) -> list[RegisteredTool]:
        return [self._handlers[k] for k in sorted(self._handlers.keys())]

    def register_many(self, handlers: dict[str, ToolFn], *, source: str = "") -> None:
        for handler_id, fn in handlers.items():
            if callable(fn):
                self.register(handler_id, fn, source=source)

    def clear(self) -> None:
        self._handlers.clear()


REGISTRY = ToolRegistry()
tool_handler = REGISTRY.register


def register_tools_dict(tools: dict[str, dict[str, Any]], *, source: str = "tools.TOOLS") -> int:
    """Auto-register existing TOOLS dict entries (id -> {fn, description, parameters})."""
    count = 0
    for tool_id, spec in (tools or {}).items():
        fn = (spec or {}).get("fn")
        if not callable(fn):
            continue  # pragma: no cover
        REGISTRY.register(
            tool_id,
            fn,
            meta={
                "description": spec.get("description", ""),
                "parameters": spec.get("parameters", {}),
                "read_only": bool(spec.get("read_only", True)),
            },
            source=source,
        )
        count += 1
    return count
