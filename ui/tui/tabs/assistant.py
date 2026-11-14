from __future__ import annotations
import os
from datetime import datetime, timedelta
from textual.widgets import (Static, DataTable, Button, Input, RichLog, Select,
                              RadioSet, RadioButton, Checkbox, Collapsible, TabbedContent, TabPane)
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual import events
from app_core import services, updater
from app_core.services import t, fmt, selected_id, SETTINGS_CATALOG
from app_core import assistant as assistant_backend
from ..modals import (
    ErrorModal, ConfirmModal, ProtectedModal, UnlockSettingsModal, RecoveryResetModal, SetSuperPasswordScreen,
    RecoveryCodesScreen, ChangePasswordModal, ResetDefaultsModal, ImportSeedConfigModal, AddRoomModal, DeleteReservationScreen, ModifyReservationScreen,
    VoidItemScreen, CancelConfirmScreen, RefundScreen, OverrideCheckoutModal,
    InventoryEditScreen, InventoryDeleteScreen, QtyInputScreen, VoidExpenseScreen,
    EditRoomFieldScreen, RatePlanEditScreen, RatePlanDeleteScreen, GuestEditScreen,
    GuestMergeModal, GuestCRMModal, GuestHistoryModal, InvoiceScreen, WalkInReceiptScreen,
    ReportScreen, ChartScreen, ViewFolioScreen, ReviewChangesModal, ImportSettingsModal,
    AuditLogScreen, LanguageScreen, ApplyDemoDataModal, BrandingWizardScreen, AssistantCorrectionModal,
    BatchSelectScreen,
)
from .common import PAYMENT_METHODS, PAYMENT_TYPES, SOURCES, push_modal, show_error, _room_options, _category_options

class AssistantTab(Vertical):
    """Chat tab with side panel: core backend facts + optional talking-model polish."""

    QUICK_PROMPTS = [
        ("Help & examples", "What can you do?"),
        ("Biz briefing", "Business partner briefing for this month"),
        ("Today ops", "Today's ops"),
        ("Who owes", "Who owes money?"),
        ("Low stock", "Any low stock items?"),
        ("P&L last month", "P&L for last month"),
        ("Compare months", "Compare this month vs last month"),
        ("Guest #1 portfolio", "Show guest #1 full portfolio"),
        ("Guest #1 last order", "What did guest #1 order most on their last visit?"),
        ("Activities", "List registered activities"),
        ("Activity stats", "Activity booking statistics"),
    ]

    def compose(self):
        yield Static("🤖 " + t("Manager Assistant"), classes="title")
        yield Static("Grounded in real app data (read-only). Local replies (no external LLM). Tip: ask with IDs/names/date ranges, e.g. 'guest #3 full portfolio', 'P&L last month', 'who owes money?'.", classes="help")
        with Horizontal(id="assistant_split"):
            with Vertical(id="assistant_side"):
                yield Static("Core / Talking (local)", classes="warn")
                yield Static("", id="assistant_mode_info", classes="help")
                yield Static("", id="assistant_status", classes="warn")
                yield Static("Quick prompts (real backend):", classes="help")
                for i, (label, _) in enumerate(self.QUICK_PROMPTS):
                    yield Button(label, id=f"assistant_q_{i}", variant="primary", tooltip=self.QUICK_PROMPTS[i][1])
                yield Button("Help & examples", id="assistant_caps", variant="primary", tooltip="Show what I can do and how to ask with examples")
                yield Static("Evaluate last answer:", classes="help")
                yield Button("👍 Helpful", id="assistant_fb_up", variant="success", tooltip="Mark last answer helpful (helps learning)")
                yield Button("👎 Not helpful", id="assistant_fb_down", variant="warning", tooltip="Mark last answer not helpful")
                yield Button("✏ Correct intent", id="assistant_fb_correct", variant="primary", tooltip="Correct the detected intent so the assistant learns")
                yield Button("Unlock", id="assistant_unlock", variant="warning", tooltip="Unlock to access assistant if unlock is required")
            with Vertical(id="assistant_main"):
                yield RichLog(id="assistant_log", highlight=False, markup=False, wrap=True)
                yield Static("", id="assistant_typing", classes="help")
                yield Input(placeholder="Ask in plain English (e.g. 'guest #3 full portfolio', 'P&L last month', 'who owes money?', 'today's ops')", id="assistant_input")
                with Horizontal(classes="button_row"):
                    yield Button("Send", id="assistant_send", variant="success", tooltip="Send question (answered from real app data)")
                    yield Button("Clear", id="assistant_clear", variant="error", tooltip="Clear conversation history")
        self._history = []
        self._last_result = None
        self._typing_timer = None
        self._typing_state = None

    def on_mount(self):
        self._refresh_gate()
        self._write_system("Ready (grounded, read-only, local). Ask in plain English — I’ll answer from real app data.")
        self._write_system("Examples: 'what can you do?' | 'business partner briefing for this month' | 'guest #3 full portfolio' | 'who owes money?' | 'list registered activities'")

    def _log(self):
        return self.query_one("#assistant_log", RichLog)

    def _write_system(self, text):
        self._log().write("[system] " + str(text))

    def _stop_typing(self, flush: bool = False):
        timer = getattr(self, "_typing_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        self._typing_timer = None
        state = getattr(self, "_typing_state", None)
        if state and flush:
            try:
                self._log().write(state.get("final_line", ""))
            except Exception:
                pass
        try:
            self.query_one("#assistant_typing", Static).update("")
        except Exception:
            pass
        self._typing_state = None

    def _assistant_unlocked(self):
        try:
            req = services.get_setting("assistant_require_unlock", "true") == "true"
        except Exception:
            req = True
        return (not req) or services.is_session_unlocked()

    def _refresh_gate(self):
        ok = self._assistant_unlocked()
        msg = "" if ok else "🔒 Unlock required (sensitive financial/guest data)."
        try:
            self.query_one("#assistant_status", Static).update(msg)
        except Exception:  # pragma: no cover - defensive/widget-state branch
            pass  # pragma: no cover - defensive/widget-state branch
        try:
            cfg = assistant_backend._llm_config()
            mode = cfg.get('response_mode', 'talking')
            if cfg.get('local_available') or cfg.get('enabled'):
                mp = cfg.get('model_path') or cfg.get('model') or 'local-gguf'
                info = f"Mode: {mode} | Talking: local model ({mp})"
            else:
                info = f"Mode: {mode} | Talking: local deterministic (no external LLM)"
            self.query_one("#assistant_mode_info", Static).update(info)
        except Exception:  # pragma: no cover - defensive/widget-state branch
            pass  # pragma: no cover - defensive/widget-state branch
        for bid in ["assistant_send", "assistant_clear", "assistant_caps", "assistant_input"] + [f"assistant_q_{i}" for i in range(len(self.QUICK_PROMPTS))]:
            try:
                w = self.query_one(f"#{bid}")
                if hasattr(w, "disabled"):
                    w.disabled = not ok
            except Exception:  # pragma: no cover - defensive/widget-state branch
                pass  # pragma: no cover - defensive/widget-state branch
        try:
            self.query_one("#assistant_unlock", Button).display = not ok
        except Exception:  # pragma: no cover - defensive/widget-state branch
            pass  # pragma: no cover - defensive/widget-state branch

    def on_button_pressed(self, e):
        bid = e.button.id or ""
        try:
            if bid == "assistant_unlock":
                self.app.push_screen(UnlockSettingsModal(), callback=lambda ok: self._after_unlock(ok))
            elif bid == "assistant_clear":
                self._stop_typing(flush=False)
                self._history = []
                self._last_result = None
                self._log().clear()
                self._write_system("Chat cleared.")
            elif bid == "assistant_fb_up":
                self._record_feedback(rating=1)
            elif bid == "assistant_fb_down":
                self._record_feedback(rating=-1)
            elif bid == "assistant_fb_correct":
                self._open_correction()
            elif bid == "assistant_caps":
                self._ask("what can you do?")
            elif bid == "assistant_send":
                msg = self.query_one("#assistant_input", Input).value.strip()
                if not msg:
                    return
                self.query_one("#assistant_input", Input).value = ""
                self._ask(msg)
            elif bid.startswith("assistant_q_"):
                idx = int(bid.rsplit("_", 1)[-1])
                if 0 <= idx < len(self.QUICK_PROMPTS):
                    self._ask(self.QUICK_PROMPTS[idx][1])
        except Exception as exc:  # pragma: no cover - defensive/widget-state branch
            self.app.notify(str(exc), severity="error")  # pragma: no cover - defensive/widget-state branch

    def on_input_submitted(self, e):
        if getattr(e.input, "id", "") == "assistant_input":
            msg = e.value.strip()
            if msg:
                e.input.value = ""
                self._ask(msg)


    def _reload_rooms(self):
        try:
            table = self.query_one("#unlocked_rooms_table", DataTable)
            table.clear(columns=True)
            table.add_columns("ID", "Number", "Type", "Base", "HK")
            for r in services.get_all_rooms():
                table.add_row(str(r["id"]), r["room_number"], r["room_type"],
                              f"{float(r['base_price'] or 0):.2f}", r["housekeeping_status"])
        except Exception:  # pragma: no cover - defensive/widget-state branch
            pass  # pragma: no cover - defensive/widget-state branch



    def _record_feedback(self, rating=0, corrected_intent="", corrected_slots=None, note=""):
        res = getattr(self, "_last_result", None)
        if not res:
            self.app.notify("No assistant answer to rate yet.", severity="warning")
            return
        msg = res.get("user_message") or getattr(self, "_last_user_message", "")
        if not msg:
            for m in reversed(self._history):
                if m.get("role") == "user":
                    msg = m.get("content", ""); break
        try:
            assistant_backend.record_feedback(
                message=msg,
                predicted_intent=res.get("intent") or "",
                corrected_intent=corrected_intent or "",
                rating=int(rating or 0),
                confidence=res.get("confidence"),
                slots=res.get("slots") or {},
                corrected_slots=corrected_slots or {},
                tools_used=res.get("tools_used") or [],
                answer=res.get("answer") or "",
                user_note=note or "",
                source=res.get("source") or "",
            )
            self.app.notify("Feedback saved (instant exemplar learning + retraining data).")
        except Exception as exc:
            self.app.notify(f"Could not save feedback: {exc}", severity="error")

    def _open_correction(self):
        res = getattr(self, "_last_result", None)
        if not res:
            self.app.notify("No assistant answer to correct yet.", severity="warning")
            return
        pred = res.get("intent") or ""
        def _after(data):
            if not data:
                return
            self._record_feedback(rating=-1, corrected_intent=data.get("corrected_intent",""), corrected_slots=data.get("slots") or {}, note=data.get("note",""))
        self.app.push_screen(AssistantCorrectionModal(pred), callback=_after)


    def _after_unlock(self, ok):
        if ok == "recovery":
            self.app.push_screen(RecoveryResetModal(), callback=self._after_unlock)
            return
        self._refresh_gate()

    def _ask(self, message):
        if not self._assistant_unlocked():
            self._refresh_gate()
            self.app.notify("Unlock required for Manager Assistant.", severity="warning")
            return
        self._stop_typing(flush=True)
        self._log().write("\nYou: " + message)
        self._history.append({"role": "user", "content": message})
        self._last_user_message = message
        self.app.notify("Core backend working...", severity="information", timeout=2)
        history_snapshot = list(self._history[:-1])

        def _work():
            try:
                result = assistant_backend.chat(message, history=history_snapshot)
                self.app.call_from_thread(lambda: self._render_result(result, message))  # pragma: no cover - call_from_thread lambda (worker scheduling)
            except Exception as exc:
                self.app.call_from_thread(lambda: self._render_result({"ok": False, "source": "error", "tools_used": [], "answer": f"Assistant error: {exc}"}, message))

        self.run_worker(_work, thread=True, exclusive=False)

    def _render_result(self, result, user_message=""):
        result = result or {}
        answer = result.get("answer") or "(no answer)"
        source = result.get("source", "?")
        intent = result.get("intent", "?")
        conf = result.get("confidence")
        tools = ",".join(result.get("tools_used") or []) or "-"
        self._history.append({"role": "assistant", "content": answer, "intent": intent, "confidence": conf, "tools_used": result.get("tools_used") or [], "slots": result.get("slots") or {}, "user_message": user_message or getattr(self, "_last_user_message", "")})
        self._last_result = result
        from ..helpers import format_assistant_prefix, typing_step_for_length
        prefix = format_assistant_prefix(intent, conf, source, result.get("tools_used") or [])
        final_line = f"{prefix}{answer}\n"
        self._refresh_gate()
        # Typewriter reveal for a more "real app" feel
        if not answer or len(answer) <= 8:
            self._log().write(final_line)
            return
        self._stop_typing(flush=False)
        state = {"prefix": prefix, "answer": answer, "idx": 0, "final_line": final_line}
        self._typing_state = state
        try:
            typing = self.query_one("#assistant_typing", Static)
        except Exception:
            self._log().write(final_line)
            self._typing_state = None
            return
        typing.update(prefix + "▋")

        step, interval = typing_step_for_length(len(answer))

        def _tick():
            st = getattr(self, "_typing_state", None)
            if not st or st is not state:
                return
            st["idx"] = min(len(st["answer"]), st["idx"] + step)
            partial = st["answer"][: st["idx"]]
            cursor = "▋" if st["idx"] < len(st["answer"]) else ""
            try:
                typing.update(st["prefix"] + partial + cursor)
            except Exception:
                return
            if st["idx"] >= len(st["answer"]):
                try:
                    self._log().write(st["final_line"])
                except Exception:
                    pass
                self._stop_typing(flush=False)

        try:
            self._typing_timer = self.set_interval(interval, _tick)
        except Exception:
            self._log().write(final_line)
            self._typing_state = None
