"""Entry point and Textual TUI app shell. Rename-safe: `python <this-file>.py`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app_core import services
from app_core.services import get_app_display_name, get_app_tagline, get_loading_animation, get_gift_attribution, is_branding_configured, set_language, t
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

from .modals import BrandingWizardScreen, LanguageScreen, SetSuperPasswordScreen
from .tabs import AccountingTab, FrontDeskTab, PosInventoryTab, ReservationsTab, SettingsTab

if TYPE_CHECKING:
    from textual.timer import Timer


class LoadingScreen(ModalScreen[None]):
    """Startup animation using configured display name and animation style."""

    BINDINGS = [("escape", "skip", "Skip")]

    def compose(self) -> ComposeResult:
        with Vertical(id="loading_dialog"):
            yield Static("", id="loading_text")

    def on_mount(self) -> None:
        self._target = get_app_display_name() or "Property Manager"
        self._style = get_loading_animation() or "reveal"
        self._tagline = get_app_tagline()
        self._phase = 0
        self._hold = 0
        self._done = False
        self._tick_timer: Timer = self.set_interval(0.09, self._tick)
        self._render()

    def _render(self) -> None:
        try:
            widget = self.query_one("#loading_text", Static)
        except Exception:
            return
        name = self._target
        style = self._style
        phase = self._phase
        if style == "none":
            body = f"[bold cyan]{name}[/]"
            self._finish(widget, body)
            return
        if style == "reveal":
            i = min(len(name), phase)
            body = f"[bold cyan]{name[:i]}[/]"
            widget.update(body)
            if i >= len(name):
                self._hold += 1
                if self._hold > 4:
                    self._finish(widget, body)
        elif style == "typewriter":
            i = min(len(name), phase)
            cursor = "[dim]▋[/]" if i < len(name) or phase % 2 == 0 else ""
            widget.update(f"[bold cyan]{name[:i]}[/]{cursor}")
            if i >= len(name):
                self._hold += 1
                if self._hold > 5:
                    self._finish(widget, f"[bold cyan]{name}[/]")
        elif style == "pulse":
            body = f"[bold cyan]{name}[/]" if phase % 2 == 0 else f"[bold white]{name}[/]"
            widget.update(body)
            if phase > 8:
                self._finish(widget, f"[bold cyan]{name}[/]")
        elif style == "wave":
            colors = ["cyan", "blue", "magenta", "cyan", "white"]
            out = []
            for idx, ch in enumerate(name):
                c = colors[(idx + phase) % len(colors)]
                out.append(f"[bold {c}]{ch}[/]")
            widget.update("".join(out))
            if phase > max(10, len(name) + 6):
                self._finish(widget, f"[bold cyan]{name}[/]")
        else:
            self._finish(widget, f"[bold cyan]{name}[/]")

    def _finish(self, widget: Static, body: str) -> None:
        if self._done:
            return
        self._done = True
        self._stop_tick()
        tagline = f"\n\n[dim]{self._tagline}[/]" if self._tagline else ""
        widget.update(body + tagline)
        self.set_timer(0.35, self._finalize_loading)

    def _tick(self) -> None:
        if self._done:
            return
        self._phase += 1
        self._render()

    def _stop_tick(self) -> None:
        timer = getattr(self, "_tick_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass

    def _finalize_loading(self) -> None:
        if self.is_attached:
            self.dismiss(None)

    def action_skip(self) -> None:
        self._done = True
        self._stop_tick()
        self.dismiss(None)


class PropertyManagerApp(App[None]):
    CSS = """
    Screen { background: #1e1e2e; color: #cdd6f4; }
    Header { background: #313244; color: #89b4fa; }
    Footer { background: #313244; }
    TabbedContent { height: 100%; padding: 1; }
    TabPane { padding: 1 2; background: #1e1e2e; overflow-y: auto; overflow-x: hidden; }
    DataTable { height: auto; max-height: 14; border: solid #45475a; margin: 0 0 1 0; background: #1e1e2e; }
    Button { margin: 0 1 1 0; padding: 0 2; min-width: 12; background: #89b4fa; color: #1e1e2e; text-style: bold; border: none; }
    Button:hover { background: #b4befe; }
    Input { margin: 0 0 1 0; border: solid #45475a; padding: 0 1; }
    Select { margin: 0 0 1 0; }
    RadioSet { height: auto; margin: 0 0 1 0; }
    Checkbox { margin: 0 0 1 0; }
    .box { border: solid #585b70; padding: 1 2; margin: 0 1 1 0; background: #24273a; width: 1fr; }
    .title { text-style: bold; color: #f9e2af; margin: 0 0 1 0; padding: 0 1; }
    .warn { color: #f9e2af; margin: 1 0 1 0; padding: 0 1; }
    .help { color: #a6adc8; padding: 0 1; }
    .button_row { height: auto; margin: 0 0 1 0; }
    .form_row { height: auto; margin: 0 0 1 0; }
    .modal_buttons { height: auto; margin-top: 1; }

    LoadingScreen { align: center middle; background: #1e1e2e; }
    #loading_dialog { width: auto; min-width: 30; max-width: 90%; height: auto; align: center middle; background: #1e1e2e; border: none; padding: 2; }
    #loading_text { text-align: center; text-style: bold; }

    ErrorModal, ConfirmModal, ProtectedModal, UnlockSettingsModal, SetSuperPasswordScreen,
    RecoveryCodesScreen, ChangePasswordModal, DeleteReservationScreen, ModifyReservationScreen,
    VoidItemScreen, CancelConfirmScreen, RefundScreen, OverrideCheckoutModal,
    InventoryEditScreen, InventoryDeleteScreen, QtyInputScreen, VoidExpenseScreen,
    EditRoomFieldScreen, RatePlanEditScreen, RatePlanDeleteScreen, GuestEditScreen,
    GuestMergeModal, GuestCRMModal, GuestHistoryModal, InvoiceScreen, WalkInReceiptScreen,
    ReportScreen, ChartScreen, ViewFolioScreen, ReviewChangesModal, ImportSettingsModal,
    AuditLogScreen, LanguageScreen, ApplyDemoDataModal, BrandingWizardScreen
    { align: center middle; }

    #protected_modal, #pw_dialog, #folio_dialog, #lang_dialog, #inv_edit_dialog,
    #report_dialog, #chart_dialog, #crm_dialog, #invoice_dialog {
        width: 88; height: auto; max-height: 92%;
        border: thick #89b4fa; background: #24273a; padding: 1 2;
    }
    #invoice_body, #report_body, #review_body, #chart_body, #body {
        height: 1fr; min-height: 12;
        background: #1e1e2e; border: solid #45475a; padding: 0 1;
    }
    #path_info { color: #a6adc8; margin: 1 0; }
    #crm_dialog #table, #folio_dialog #table { height: auto; max-height: 12; }
    #wiz_preview { min-height: 3; content-align: center middle; }
    """

    BINDINGS = [("ctrl+l", "language", "Language")]

    _REFRESHABLE_TABS = (FrontDeskTab, ReservationsTab, PosInventoryTab, AccountingTab)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent():
            with TabPane(t("Front Desk"), id="tab_fd"):
                yield FrontDeskTab()
            with TabPane(t("Reservations"), id="tab_res"):
                yield ReservationsTab()
            with TabPane(t("POS & Inventory"), id="tab_pos"):
                yield PosInventoryTab()
            with TabPane(t("Accounting"), id="tab_acct"):
                yield AccountingTab()
            with TabPane(f"⚙ {t('Settings')}", id="tab_set"):
                yield SettingsTab()
        yield Footer()

    def on_mount(self) -> None:
        self._apply_branding()
        self.set_interval(30, self._check_session_timeout)
        self._start_flow()

    def _apply_branding(self) -> None:
        name = get_app_display_name()
        tagline = get_app_tagline()
        self.title = f"{name} / Property Manager"
        valid, msg = services.verify_app_attribution()
        gift = get_gift_attribution() if valid else (msg or get_gift_attribution())
        self.sub_title = f"★ {gift}" if valid else f"⚠ {gift}"

    def _start_flow(self) -> None:
        if not is_branding_configured():
            self.push_screen(BrandingWizardScreen(allow_cancel=False), callback=self._after_branding)
            return
        self._after_branding(True)

    def _after_branding(self, ok: bool | None) -> None:
        if not ok and not is_branding_configured():
            self.push_screen(BrandingWizardScreen(allow_cancel=False), callback=self._after_branding)
            return
        self._apply_branding()
        self.push_screen(LoadingScreen(), callback=self._after_loading)

    def _check_session_timeout(self) -> None:
        if services.is_session_unlocked():
            return
        try:
            settings_tab = self.query_one(SettingsTab)
        except Exception:
            return
        render_locked = getattr(settings_tab, "_render_locked", None)
        if callable(render_locked):
            render_locked()

    def _after_loading(self, _result: object = None) -> None:
        if not services.has_super_password():
            self.push_screen(SetSuperPasswordScreen(), callback=self._on_pw_set)

    def _on_pw_set(self, ok: bool | None) -> None:
        if not ok:
            self.exit()

    def action_language(self) -> None:
        self.push_screen(LanguageScreen())

    def refresh_all_tables(self) -> None:
        for cls in self._REFRESHABLE_TABS:
            try:
                tab = self.query_one(cls)
            except Exception:
                continue
            refresh = getattr(tab, "refresh_data", None)
            if not callable(refresh):
                continue
            try:
                refresh()
            except Exception as exc:
                self.notify(f"{cls.__name__} refresh failed: {exc}", severity="warning", timeout=6)


def main() -> None:
    """Initialize services and run the TUI (rename-safe entry point)."""
    services.init_db()
    set_language(services.get_setting("language", "en"))
    PropertyManagerApp().run()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Fatal startup error: {exc}")
        raise SystemExit(1) from exc
