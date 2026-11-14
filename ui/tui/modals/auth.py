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

class UnlockSettingsModal(ModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]
    def compose(self):
        with Vertical(id="pw_dialog"):
            yield Static("🔒 " + t("Unlock Settings"), classes="title")
            yield Input(placeholder=t("Super Password"), id="pw", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button("Unlock", id="btn_do", variant="success")
                yield Button("Recovery Code", id="btn_recovery", variant="primary")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")
    def on_button_pressed(self, e):
        if e.button.id == "btn_do":
            ok = services.unlock_session(self.query_one("#pw", Input).value)
            if ok:
                self.dismiss(True)
            else:
                self.notify(t("Wrong super password."), severity="error")
        elif e.button.id == "btn_recovery":
            self.dismiss("recovery")
        elif e.button.id == "btn_cancel":
            self.dismiss(False)
    def action_cancel(self):
        self.dismiss(False)


class RecoveryResetModal(ModalScreen):
    """Use a recovery code to set a new super password and unlock."""
    BINDINGS = [("escape", "cancel", "Cancel")]
    def compose(self):
        with VerticalScroll(id="pw_dialog"):
            yield Static("🔑 " + t("Recover with Recovery Code"), classes="title")
            yield Static("Enter one unused recovery code and choose a new super password.", classes="warn")
            yield Input(placeholder="Recovery code", id="code")
            yield Input(placeholder="New super password", id="pw_new", password=True)
            yield Input(placeholder=t("Confirm Password"), id="pw_cf", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button("Reset & Unlock", id="btn_do", variant="warning")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")
    def on_button_pressed(self, e):
        if e.button.id == "btn_do":
            code = self.query_one("#code", Input).value
            pw = self.query_one("#pw_new", Input).value
            cf = self.query_one("#pw_cf", Input).value
            try:
                services.use_recovery_code(code, pw, cf)
                if not services.unlock_session(pw):
                    # password was set; unlock should succeed, but be defensive
                    services._session["unlocked"] = True
                    services._session["last_activity"] = datetime.now()
                self.notify("Password reset. Settings unlocked.")
                self.dismiss(True)
            except Exception as exc:
                self.notify(str(exc), severity="error")
        elif e.button.id == "btn_cancel":
            self.dismiss(False)
    def action_cancel(self):
        self.dismiss(False)


class SetSuperPasswordScreen(ModalScreen):
    BINDINGS = [("escape", "noop", "Locked")]
    def compose(self):
        with VerticalScroll(id="pw_dialog"):
            yield Static("🔐 " + t("Set Super Password"), classes="title")
            yield Static("Minimum 6 characters. Cannot be changed without the current password. "
                         "A recovery code will be shown next.", classes="warn")
            yield Input(placeholder=t("Super Password"), id="pw_new", password=True)
            yield Input(placeholder=t("Confirm Password"), id="pw_confirm", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button("Next → Save Password & Continue", id="btn_set", variant="success")
                yield Button(t("Quit App"), id="btn_quit", variant="error")

    def on_mount(self):
        try:
            self.query_one("#pw_new", Input).focus()
        except Exception:
            pass

    def on_button_pressed(self, e):
        if e.button.id == "btn_set":
            pw = self.query_one("#pw_new", Input).value
            cf = self.query_one("#pw_confirm", Input).value
            if pw != cf:
                self.notify("Passwords do not match.", severity="error")
                return
            try:
                services.set_super_password(pw)
                codes = services.generate_recovery_codes(pw)
                def _after_codes(_result):
                    self.dismiss(True)
                self.app.push_screen(RecoveryCodesScreen(codes), callback=_after_codes)
            except Exception as exc:
                self.notify(str(exc), severity="error")
        elif e.button.id == "btn_quit":
            self.dismiss(False)
    def action_noop(self):
        pass


class RecoveryCodesScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Close")]
    def __init__(self, codes):
        super().__init__()
        self.codes = codes
    def compose(self):
        with Vertical(id="report_dialog"):
            yield Static("🔑 " + t("Recovery Code"), classes="title")
            yield Static("Save these codes. Each can be used ONCE to reset the super password (use 'Recovery Code' on unlock screens).", classes="warn")
            yield RichLog(id="report_body", highlight=False, markup=False)
            with Horizontal(classes="modal_buttons"):
                yield Button("Copy All", id="btn_copy", variant="primary")
                yield Button(t("Close"), id="btn_close", variant="success")
    def on_mount(self):
        log = self.query_one("#report_body", RichLog)
        log.clear()
        log.write("Recovery codes:\n")
        for c in self.codes:
            log.write(f"  {c}")
        log.write("\nStore them somewhere safe. They are not recoverable.")
    def on_button_pressed(self, e):
        if e.button.id == "btn_copy":
            try:
                self.app.copy_to_clipboard("\n".join(self.codes))
                self.notify("Copied.")  # pragma: no cover - clipboard notify in headless TUI flow
            except Exception as exc:
                self.notify(str(exc), severity="error")
        elif e.button.id == "btn_close":
            self.dismiss()
    def action_close(self):
        self.dismiss()


class ChangePasswordModal(ModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]
    def compose(self):
        with VerticalScroll(id="pw_dialog"):
            yield Static("🔒 " + t("Change Password"), classes="title")
            yield Input(placeholder="Current Password", id="cur", password=True)
            yield Input(placeholder="New Password", id="new", password=True)
            yield Input(placeholder=t("Confirm Password"), id="cf", password=True)
            yield Static("⚠ If you forget the new password, you can use an unused recovery code on the unlock screen. "
                         "Without a recovery code, the only recovery is deleting the database.", classes="warn")
            with Horizontal(classes="modal_buttons"):
                yield Button("Save New Password", id="btn_do", variant="warning")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")
    def on_button_pressed(self, e):
        if e.button.id == "btn_do":
            try:
                services.change_password(self.query_one("#cur", Input).value,
                                         self.query_one("#new", Input).value,
                                         self.query_one("#cf", Input).value)
                self.notify("Password changed.")
                self.dismiss(True)
            except Exception as exc:
                self.notify(str(exc), severity="error")
        elif e.button.id == "btn_cancel":
            self.dismiss(False)
    def action_cancel(self):
        self.dismiss(False)


class ResetDefaultsModal(ProtectedModal):
    TITLE = "Reset Settings to Defaults"
    CONFIRM_LABEL = "Reset Defaults"
    CONFIRM_VARIANT = "warning"

    def body(self):
        yield Static("Resets most settings to defaults (keeps schema/language).", classes="warn")
        yield Static("This cannot be undone. Enter super password to confirm.", classes="help")

    def perform(self, password):
        services.reset_to_defaults(password)


# ═══════════════════════════════════════════════════════════════════════════
# RESERVATION ACTIONS
# ═══════════════════════════════════════════════════════════════════════════

