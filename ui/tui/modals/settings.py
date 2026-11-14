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

class ReviewChangesModal(ModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]
    def __init__(self, changes):
        super().__init__()
        self.changes = changes
    def compose(self):
        with Vertical(id="report_dialog"):
            yield Static("Review Changes", classes="title")
            yield RichLog(id="body", highlight=False, markup=False)
            yield Input(placeholder=t("Super Password"), id="pw", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button(t("Save Changes"), id="btn_do", variant="success")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")
    def on_mount(self):
        log = self.query_one("#body", RichLog)
        log.clear()
        log.write("You are about to change:")
        for k, (old, new) in self.changes.items():
            spec = SETTINGS_CATALOG.get(k, (k,))
            log.write(f"  • {spec[0]}: {old} → {new}")
    def on_button_pressed(self, e):
        if e.button.id == "btn_do":
            try:
                staged = {k: v[1] for k, v in self.changes.items()}
                count, errs = services.save_settings(staged, self.query_one("#pw", Input).value)
                if errs:
                    for err in errs:
                        self.notify(err, severity="error")
                else:
                    self.notify(f"{count} saved.")
                    self.dismiss(True)
            except Exception as exc:
                self.notify(str(exc), severity="error")
        elif e.button.id == "btn_cancel":
            self.dismiss(False)
    def action_cancel(self):
        self.dismiss(False)


class ImportSettingsModal(ModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]
    def compose(self):
        with Vertical(id="protected_modal"):
            yield Static(t("Import Settings"), classes="title")
            yield Input(placeholder="Full path to JSON file", id="path")
            yield Input(placeholder=t("Super Password"), id="pw", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button("Import", id="btn_do", variant="success")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")
    def on_button_pressed(self, e):
        if e.button.id == "btn_do":
            try:
                result = services.import_settings_from_json(
                    self.query_one("#path", Input).value.strip(),
                    self.query_one("#pw", Input).value)
                if result["ok"]:
                    self.notify(f"Imported {len(result['changes'])}.")
                    self.dismiss(True)
                else:
                    for err in result["errors"]:
                        self.notify(err, severity="error")
            except Exception as exc:
                self.notify(str(exc), severity="error")
        elif e.button.id == "btn_cancel":
            self.dismiss(False)
    def action_cancel(self):
        self.dismiss(False)


class AuditLogScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Close")]
    def compose(self):
        with Vertical(id="report_dialog"):
            yield Static("Audit Log", classes="title")
            yield RichLog(id="body", highlight=False, markup=False)
            with Horizontal(classes="modal_buttons"):
                yield Button(t("Close"), id="btn_close", variant="primary")
    def on_mount(self):
        log = self.query_one("#body", RichLog)
        log.clear()
        for r in services.get_audit_log(200):
            log.write(f"{r['at']}  {r['action']:<20} {r['target_type']}#{r['target_id']}  {r['details']}")
    def on_button_pressed(self, e):
        if e.button.id == "btn_close":
            self.dismiss()
    def action_close(self):
        self.dismiss()


class BrandingWizardScreen(ModalScreen):
    """First-run/setup wizard for display name and animated branding."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, allow_cancel=False):
        super().__init__()
        self.allow_cancel = allow_cancel
        self._preview_phase = 0
        self._preview_timer = None

    def compose(self):
        name = services.get_setting("app_display_name", "") or services.get_setting("property_name", "")
        if name in ("Sukoon Camp", "My Property"):
            name = ""
        tagline = services.get_setting("app_tagline", "") or ""
        anim = services.get_loading_animation() or "reveal"
        with VerticalScroll(id="protected_modal"):
            yield Static("Setup Wizard", classes="title")
            yield Static(
                "Choose the display name used in the app, loading animation, invoices, and attribution.",
                classes="help",
            )
            yield Input(placeholder="Display name (required)", id="wiz_name", value=name)
            yield Input(placeholder="Tagline (optional)", id="wiz_tagline", value=tagline)
            yield Select(
                [("Reveal letters", "reveal"), ("Typewriter", "typewriter"),
                 ("Wave", "wave"), ("Pulse", "pulse"), ("No animation", "none")],
                id="wiz_anim", prompt="Loading animation", value=anim,
            )
            yield Static("", id="wiz_preview", classes="box")
            yield Static("Attribution: Gifted with love from Michael Helmy to <name>", id="wiz_attr", classes="help")
            with Horizontal(classes="modal_buttons"):
                yield Button("Next → Save & Continue", id="btn_save", variant="success")
                if self.allow_cancel:
                    yield Button(t("Cancel"), id="btn_cancel", variant="error")

    def on_mount(self):
        self._refresh_preview()
        self._preview_timer = self.set_interval(0.12, self._tick_preview)

    def on_unmount(self):
        try:
            if self._preview_timer:
                self._preview_timer.stop()
        except Exception:
            pass

    def on_input_changed(self, e):
        if e.input.id in ("wiz_name", "wiz_tagline"):
            self._preview_phase = 0
            self._refresh_preview()

    def on_select_changed(self, e):
        if e.select.id == "wiz_anim":
            self._preview_phase = 0
            self._refresh_preview()

    def _current_name(self):
        return (self.query_one("#wiz_name", Input).value or "").strip() or "My Property"

    def _current_anim(self):
        val = self.query_one("#wiz_anim", Select).value
        return val if isinstance(val, str) else "reveal"

    def _render_frame(self, name, style, phase):
        if style == "none":
            return f"[bold cyan]{name}[/]"
        if style == "reveal":
            i = min(len(name), phase)
            return f"[bold cyan]{name[:i]}[/]"
        if style == "typewriter":
            i = min(len(name), phase)
            cursor = "[dim]▋[/]" if i < len(name) or phase % 2 == 0 else ""
            return f"[bold cyan]{name[:i]}[/]{cursor}"
        if style == "pulse":
            return f"[bold cyan]{name}[/]" if phase % 2 == 0 else f"[bold white]{name}[/]"
        if style == "wave":
            colors = ["cyan", "blue", "magenta", "cyan"]
            out = []
            for idx, ch in enumerate(name):
                c = colors[(idx + phase) % len(colors)]
                out.append(f"[bold {c}]{ch}[/]")
            return "".join(out)
        return f"[bold cyan]{name}[/]"

    def _refresh_preview(self):
        try:
            name = self._current_name()
            self.query_one("#wiz_attr", Static).update(
                f"Attribution: {services.gift_attribution_for(name)}"
            )
            frame = self._render_frame(name, self._current_anim(), self._preview_phase)
            self.query_one("#wiz_preview", Static).update(frame)
        except Exception:
            pass

    def _tick_preview(self):
        try:
            self._preview_phase += 1
            name = self._current_name()
            style = self._current_anim()
            if style in ("reveal", "typewriter") and self._preview_phase > len(name) + 6:
                self._preview_phase = max(0, len(name))
            self._refresh_preview()
        except Exception:
            pass

    def on_button_pressed(self, e):
        if e.button.id == "btn_save":
            name = (self.query_one("#wiz_name", Input).value or "").strip()
            if not name:
                self.notify("Display name is required.", severity="error")
                return
            tagline = (self.query_one("#wiz_tagline", Input).value or "").strip()
            anim = self._current_anim()
            try:
                services.apply_branding_settings(name, tagline, anim, sync_property_name=True)
                self.dismiss(True)
            except Exception as exc:
                self.notify(str(exc), severity="error")
        elif e.button.id == "btn_cancel" and self.allow_cancel:
            self.dismiss(False)

    def action_cancel(self):
        if self.allow_cancel:
            self.dismiss(False)


class ApplyDemoDataModal(ProtectedModal):
    TITLE = "Load Demo Data (Guarded)"
    CONFIRM_LABEL = "Apply Demo Data"
    CONFIRM_VARIANT = "warning"

    def compose(self):
        presets = services.list_demo_data_presets()
        options = [(p["label"], p["key"]) for p in presets] or [("Analysis Small", "analysis_small")]
        with VerticalScroll(id="protected_modal"):
            yield Static("🔒 " + t("Load Demo Data (Guarded)"), classes="title")
            yield Static(
                "Creates synthetic data for analysis/model prototyping. "
                "Refused when operational data exists. Type APPLY DEMO DATA to confirm.",
                classes="warn",
            )
            yield Select(options, id="demo_preset", prompt="Demo Preset", value=options[0][1])
            yield Input(placeholder='Type: APPLY DEMO DATA', id="demo_confirm")
            yield Input(placeholder=t("Super Password"), id="pw_field", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button(t("Apply Demo Data"), id="btn_do", variant="warning")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")

    def perform(self, password):
        preset = self.query_one("#demo_preset", Select).value
        if not isinstance(preset, str):
            preset = "analysis_small"
        confirm = self.query_one("#demo_confirm", Input).value
        services.apply_demo_data_preset(preset, password, confirm, allow_existing=False)


class LanguageScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Close")]
    def compose(self):
        with Vertical(id="lang_dialog"):
            yield Static(t("Language"), classes="title")
            with Horizontal(classes="modal_buttons"):
                yield Button("English", id="btn_en")
                yield Button("العربية", id="btn_ar")
    def on_button_pressed(self, e):
        if e.button.id == "btn_en":
            self._set("en")
        elif e.button.id == "btn_ar":
            self._set("ar")
    def _set(self, lang):
        services.set_setting("language", lang)
        services.set_language(lang)
        self.dismiss()
        # Fire-and-forget recompose (do not return the awaitable)
        try:
            self.app.refresh(recompose=True)
        except Exception:
            pass
    def action_close(self):
        self.dismiss()


class ImportSeedConfigModal(ModalScreen):
    """Upload/apply editable seed/master config JSON (first-time setup)."""
    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self):
        with VerticalScroll(id="protected_modal"):
            yield Static("📥 Import Seed Config (first-time setup)", classes="title")
            yield Static(
                "Applies rooms/rate plans/inventory/categories/meals from JSON. "
                "Allowed only when there is no operational data. Type APPLY SEED CONFIG to confirm.",
                classes="warn",
            )
            yield Input(placeholder="Full path to seed config JSON file", id="path")
            yield Input(placeholder="Type: APPLY SEED CONFIG", id="confirm")
            yield Input(placeholder=t("Super Password"), id="pw", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button("Import & Apply", id="btn_do", variant="warning")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")

    def on_button_pressed(self, e):
        if e.button.id == "btn_do":
            try:
                res = services.import_seed_config(
                    self.query_one("#path", Input).value.strip(),
                    self.query_one("#pw", Input).value,
                    self.query_one("#confirm", Input).value,
                )
                summary = res.get("summary", {}) if isinstance(res, dict) else res
                self.notify(f"Seed config applied: {summary}")
                self.dismiss(True)
            except Exception as exc:
                self.notify(str(exc), severity="error")
        elif e.button.id == "btn_cancel":
            self.dismiss(False)

    def action_cancel(self):
        self.dismiss(False)


class AddRoomModal(ModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self):
        with VerticalScroll(id="protected_modal"):
            yield Static("➕ " + t("Add Room"), classes="title")
            yield Input(placeholder="Room number (e.g. 101)", id="room_number")
            yield Input(placeholder="Room type (e.g. Standard Room)", id="room_type", value="Standard Room")
            yield Input(placeholder="Base price", id="base_price", value="0")
            yield Select([("Clean", "Clean"), ("Dirty", "Dirty"), ("Inspected", "Inspected"), ("Out of Order", "Out of Order")],
                         id="hk", prompt="Housekeeping", value="Clean")
            with Horizontal(classes="modal_buttons"):
                yield Button(t("Save"), id="btn_save", variant="success")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")

    def on_button_pressed(self, e):
        if e.button.id == "btn_save":
            try:
                hk = self.query_one("#hk", Select).value
                if not isinstance(hk, str):
                    hk = "Clean"
                rid = services.add_room(
                    self.query_one("#room_number", Input).value,
                    self.query_one("#room_type", Input).value,
                    self.query_one("#base_price", Input).value or 0,
                    hk,
                )
                self.notify(f"Room added (#{rid}).")
                self.dismiss(True)
            except Exception as exc:
                self.notify(str(exc), severity="error")
        elif e.button.id == "btn_cancel":
            self.dismiss(False)

    def action_cancel(self):
        self.dismiss(False)




