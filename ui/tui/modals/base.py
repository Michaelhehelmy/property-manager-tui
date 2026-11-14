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

class ErrorModal(ModalScreen):
    BINDINGS = [("escape", "close", "Close")]
    def __init__(self, msg, title="Error"):
        super().__init__()
        self.msg = msg
        self.err_title = title
    def compose(self):
        with Vertical(id="protected_modal"):
            yield Static(self.err_title, classes="title")
            yield Static(self.msg, classes="warn")
            with Horizontal(classes="modal_buttons"):
                yield Button("OK", id="btn_ok", variant="primary")
    def on_button_pressed(self, e):
        if e.button.id == "btn_ok":
            self.dismiss()
    def action_close(self):
        self.dismiss()


class ConfirmModal(ModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]
    def __init__(self, message, confirm_label="Confirm", variant="warning"):
        super().__init__()
        self.message = message
        self.label = confirm_label
        self.variant = variant
    def compose(self):
        with Vertical(id="protected_modal"):
            yield Static(self.message, classes="warn")
            with Horizontal(classes="modal_buttons"):
                yield Button(self.label, id="btn_do", variant=self.variant)
                yield Button(t("Cancel"), id="btn_cancel", variant="error")
    def on_button_pressed(self, e):
        if e.button.id == "btn_do":
            self.dismiss(True)
        elif e.button.id == "btn_cancel":
            self.dismiss(False)
    def action_cancel(self):
        self.dismiss(False)


class ProtectedModal(ModalScreen):
    TITLE = "Confirm"
    CONFIRM_LABEL = "Confirm"
    CONFIRM_VARIANT = "success"
    BINDINGS = [("escape", "cancel_modal", "Cancel")]

    def compose(self):
        with VerticalScroll(id="protected_modal"):
            yield Static("🔒 " + t(self.TITLE), classes="title")
            yield from self.body()
            yield Input(placeholder=t("Super Password"), id="pw_field", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button(t(self.CONFIRM_LABEL), id="btn_do", variant=self.CONFIRM_VARIANT)
                yield Button(t("Cancel"), id="btn_cancel", variant="error")

    def body(self):
        return ()

    def perform(self, password):
        raise NotImplementedError

    def on_button_pressed(self, e):
        if e.button.id == "btn_do":
            try:
                self.perform(self.query_one("#pw_field", Input).value)
                try:
                    services.touch_session()
                except Exception:
                    pass
                self.dismiss(True)
            except PermissionError:
                self.notify(t("Wrong super password."), severity="error")
            except Exception as exc:
                self.notify(str(exc), severity="error")
        elif e.button.id == "btn_cancel":
            self.dismiss(False)

    def action_cancel_modal(self):
        self.dismiss(False)


# ═══════════════════════════════════════════════════════════════════════════
# PASSWORD GATE
# ═══════════════════════════════════════════════════════════════════════════

