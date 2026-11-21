"""Deterministic workflow/state-machine engine for SPMS assistant skills.

The small agent gets smarter by following explicit steps/gates from
agent_brain.json, not by increasing model size.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


GateFn = Callable[[dict[str, Any], dict[str, Any]], tuple[bool, str]]


@dataclass
class WorkflowState:
    skill_id: str
    current_state: str
    status: str = "active"  # active|completed|failed|waiting_approval
    data: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "current_state": self.current_state,
            "status": self.status,
            "data": self.data,
            "history": self.history,
            "errors": self.errors,
        }


class WorkflowEngine:
    def __init__(self, gates: dict[str, GateFn] | None = None):
        self.gates: dict[str, GateFn] = dict(gates or {})
        self._install_default_gates()

    def _install_default_gates(self) -> None:
        self.gates.setdefault("always_pass", lambda state, ctx: (True, "ok"))
        self.gates.setdefault("required_fields_present", self._gate_required_fields_present)
        self.gates.setdefault("no_duplicate_or_confirm_replace", lambda state, ctx: (True, "ok"))
        self.gates.setdefault("type_allowed", self._gate_type_allowed)
        self.gates.setdefault("plan_has_files_tests_risks", self._gate_plan_has_files_tests_risks)
        self.gates.setdefault("allowed_paths_and_safe_code", lambda state, ctx: (True, "ok"))
        self.gates.setdefault("handler_resolves", lambda state, ctx: (True, "ok"))
        self.gates.setdefault("checks_pass", self._gate_checks_pass)
        self.gates.setdefault("owner_approved", self._gate_owner_approved)
        self.gates.setdefault("manifest_enabled_and_loaded", lambda state, ctx: (True, "ok"))

    @staticmethod
    def _gate_required_fields_present(state: dict[str, Any], ctx: dict[str, Any]) -> tuple[bool, str]:
        required = ctx.get("required") or []
        spec = (state.get("data") or {}).get("spec") or {}
        missing = [k for k in required if spec.get(k) in (None, "", [], {})]
        if missing:
            return False, f"missing required fields: {', '.join(missing)}"
        return True, "ok"

    @staticmethod
    def _gate_type_allowed(state: dict[str, Any], ctx: dict[str, Any]) -> tuple[bool, str]:
        spec = (state.get("data") or {}).get("spec") or {}
        t = str(spec.get("type") or "native")
        allowed = {"knowledge", "composite", "native", "service"}
        if t not in allowed:
            return False, f"unsupported type: {t}"
        return True, "ok"

    @staticmethod
    def _gate_plan_has_files_tests_risks(state: dict[str, Any], ctx: dict[str, Any]) -> tuple[bool, str]:
        plan = (state.get("data") or {}).get("plan") or {}
        missing = [k for k in ("files", "tests", "risks") if not plan.get(k)]
        if missing:
            return False, f"plan missing: {', '.join(missing)}"
        return True, "ok"

    @staticmethod
    def _gate_checks_pass(state: dict[str, Any], ctx: dict[str, Any]) -> tuple[bool, str]:
        report = (state.get("data") or {}).get("validation") or {}
        if report.get("ok") is True:
            return True, "ok"
        return False, "validation checks not passing"

    @staticmethod
    def _gate_owner_approved(state: dict[str, Any], ctx: dict[str, Any]) -> tuple[bool, str]:
        approval = (state.get("data") or {}).get("approval") or {}
        if approval.get("approved") is True:
            return True, "ok"
        if approval.get("rejected") is True:
            return False, "owner rejected"
        return False, "waiting for owner approval"

    def get_skill_workflow(self, brain: dict[str, Any] | Any, skill_id: str) -> dict[str, Any] | None:
        skills = brain.get("skills") if isinstance(brain, dict) else brain.skills(enabled_only=False)
        for s in skills or []:
            if isinstance(s, dict) and s.get("id") == skill_id:
                states = s.get("states") or s.get("workflow_states")
                if isinstance(states, dict):
                    return {"skill": s, "states": states, "entry_state": s.get("entry_state") or next(iter(states.keys()), None)}
        return None

    def start(self, brain: dict[str, Any] | Any, skill_id: str, *, data: dict[str, Any] | None = None) -> WorkflowState:
        wf = self.get_skill_workflow(brain, skill_id)
        if not wf or not wf.get("entry_state"):
            raise ValueError(f"Workflow skill not found or invalid: {skill_id}")
        st = WorkflowState(skill_id=skill_id, current_state=str(wf["entry_state"]), data=dict(data or {}))
        st.history.append({"event": "start", "state": st.current_state})
        return st

    def current_step(self, brain: dict[str, Any] | Any, state: WorkflowState) -> dict[str, Any] | None:
        wf = self.get_skill_workflow(brain, state.skill_id)
        if not wf:
            return None
        step = (wf.get("states") or {}).get(state.current_state)
        if isinstance(step, dict):
            out = dict(step)
            out["name"] = state.current_state
            return out
        return None

    def _evaluate_gate(self, gate: str | None, state: WorkflowState, ctx: dict[str, Any]) -> tuple[bool, str]:
        if not gate:
            return True, "ok"
        fn = self.gates.get(gate)
        if fn is None:
            # Unknown gates are explicit no-pass to avoid silent unsafe progression.
            return False, f"unknown gate: {gate}"
        ok, msg = fn(state.to_dict(), ctx)
        return bool(ok), str(msg or "")

    def advance(self, brain: dict[str, Any] | Any, state: WorkflowState, *, event: str | None = None) -> WorkflowState:
        if state.status in {"completed", "failed"}:
            return state
        wf = self.get_skill_workflow(brain, state.skill_id)
        if not wf:
            state.status = "failed"
            state.errors.append("workflow not found")
            return state
        step = (wf.get("states") or {}).get(state.current_state)
        if not isinstance(step, dict):
            state.status = "failed"
            state.errors.append(f"invalid state: {state.current_state}")
            return state
        if step.get("terminal"):
            state.status = "completed"
            state.history.append({"event": "complete", "state": state.current_state})
            return state

        gate = step.get("gate")
        ctx = {"required": step.get("required") or [], "event": event, "step": step}
        ok, msg = self._evaluate_gate(gate, state, ctx)
        state.history.append({"event": "gate", "state": state.current_state, "gate": gate, "ok": ok, "message": msg})
        if not ok:
            if "approval" in (gate or "") or "owner" in msg.lower() or "waiting" in msg.lower():
                state.status = "waiting_approval"
            else:
                state.status = "active"
            state.errors.append(msg)
            return state

        # Branching
        next_state = None
        if event and step.get(f"next_on_{event}"):
            next_state = step.get(f"next_on_{event}")
        elif step.get("next"):
            next_state = step.get("next")
        elif step.get("next_on_pass"):
            next_state = step.get("next_on_pass")
        if not next_state:
            state.status = "failed"
            state.errors.append(f"no transition from {state.current_state}")
            return state

        state.current_state = str(next_state)
        state.status = "active"
        state.errors = []
        state.history.append({"event": "transition", "state": state.current_state})
        # Auto-complete terminal
        nxt = (wf.get("states") or {}).get(state.current_state)
        if isinstance(nxt, dict) and nxt.get("terminal"):
            state.status = "completed"
            state.history.append({"event": "complete", "state": state.current_state})
        return state

    def set_data(self, state: WorkflowState, key: str, value: Any) -> WorkflowState:
        state.data[key] = value
        return state
