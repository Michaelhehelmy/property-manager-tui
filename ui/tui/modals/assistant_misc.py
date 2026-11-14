from __future__ import annotations
import os
from datetime import datetime
from textual.widgets import Static, Button, Input, RichLog, DataTable, Select, Checkbox, Collapsible
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from rich.text import Text
from app_core import services
from app_core.services import t, fmt, selected_id, SETTINGS_CATALOG
from .common import PLOTEXT_OK, _plt
from .base import ErrorModal, ConfirmModal, ProtectedModal

class AssistantCorrectionModal(ModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, predicted_intent=""):
        super().__init__()
        self.predicted_intent = predicted_intent or ""

    def compose(self):
        intents = ["", "capabilities_help", "report_pl", "report_cash_flow", "report_vat", "report_payment_methods",
                   "report_occupancy", "report_daily_ops", "decision_support", "guest_search", "guest_profile",
                   "guest_last_visit", "guest_top_items_last_visit", "reservation_summary", "reservation_balance",
                   "rate_plans", "occupancy_summary", "ledger_integrity", "ambiguous_needs_clarification",
                   "unsupported_or_unsafe", "compound_multi_intent"]
        opts = [("(keep predicted)", "")] + [(i, i) for i in intents if i]
        with VerticalScroll(id="protected_modal"):
            yield Static("Correct assistant routing (learn over time)", classes="title")
            yield Static(f"Predicted intent: {self.predicted_intent}", classes="help")
            yield Select(opts, id="intent", prompt="Correct intent", value="")
            yield Input(placeholder="Note (what was wrong / correct details)", id="note")
            yield Input(placeholder='Corrected slots JSON (optional), e.g. {"guest_id":3}', id="slots_json")
            with Horizontal(classes="modal_buttons"):
                yield Button("Save correction", id="btn_save", variant="warning")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")

    def on_button_pressed(self, e):
        if e.button.id == "btn_save":
            intent = self.query_one("#intent", Select).value
            if not isinstance(intent, str):
                intent = ""
            note = self.query_one("#note", Input).value.strip()
            raw = self.query_one("#slots_json", Input).value.strip()
            slots = {}
            if raw:
                try:
                    import json as _json
                    v = _json.loads(raw)
                    if isinstance(v, dict):
                        slots = v
                except Exception as exc:
                    self.notify(f"Invalid slots JSON: {exc}", severity="error")
                    return
            self.dismiss({"corrected_intent": intent, "note": note, "slots": slots})
        elif e.button.id == "btn_cancel":
            self.dismiss(None)

    def action_cancel(self):
        self.dismiss(None)

