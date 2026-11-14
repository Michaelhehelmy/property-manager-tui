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

class VoidExpenseScreen(ProtectedModal):
    CONFIRM_LABEL = "Void"
    CONFIRM_VARIANT = "error"
    def __init__(self, expense_id):
        super().__init__()
        self.expense_id = expense_id
    def compose(self):
        with VerticalScroll(id="protected_modal"):
            yield Static(f"🔒 {t('Void Expense')} #{self.expense_id}", classes="title")
            yield Input(placeholder=t("Super Password"), id="pw_field", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button("Void", id="btn_do", variant="error")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")
    def perform(self, password):
        services.void_expense(self.expense_id, password)


# ═══════════════════════════════════════════════════════════════════════════
# ROOMS / RATE PLANS / GUESTS
# ═══════════════════════════════════════════════════════════════════════════

