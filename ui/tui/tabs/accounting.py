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

class AccountingTab(VerticalScroll):
    def compose(self):
        yield Static("Accounting: record/void operational expenses and review entries. Protected actions may require unlock/super password. Hover buttons for tips.", classes="help")
        now = datetime.now()
        first = now.replace(day=1).strftime("%Y-%m-%d")
        today = now.strftime("%Y-%m-%d")

        with Horizontal(classes="form_row"):
            with Vertical(classes="box"):
                yield Static(t("Record Operational Expense"), classes="title")
                yield Select(_category_options(), id="exp_cat", prompt=t("Category"))
                yield Input(placeholder=t("Parameter Name"), id="exp_name")
                yield Input(placeholder=t("Amount"), id="exp_amt")
                yield Input(placeholder=t("VAT Paid"), id="exp_vat", value="0")
                yield Select([(m, m) for m in PAYMENT_METHODS], id="exp_method", prompt=t("Method"), value="Cash")
                yield Input(placeholder=t("Notes"), id="exp_notes")
                yield Button(t("Record Expense"), id="btn_exp", variant="error")
            with Vertical(classes="box"):
                yield Static(t("General Ledger"), classes="title")
                yield RichLog(id="ledger_log", highlight=True, markup=True)
                yield Button(t("Refresh"), id="btn_refresh", variant="primary")

        with Horizontal(classes="button_row"):
            yield Input(placeholder=t("From"), id="from", value=first)
            yield Input(placeholder=t("To"), id="to", value=today)
            yield Button(t("Apply"), id="btn_apply", variant="primary", tooltip="Apply the selected filters/view to the front desk display")

        with Horizontal(classes="button_row"):
            yield Button("📊 " + t("Chart"), id="btn_chart", variant="primary", tooltip="Show chart/analytics for the selected context")
            yield Button(t("P&L Statement"), id="btn_pl", variant="primary")
            yield Button(t("Cash Flow"), id="btn_cash", variant="primary")
            yield Button(t("Payment Methods"), id="btn_methods", variant="primary")
        with Horizontal(classes="button_row"):
            yield Button(t("VAT Report"), id="btn_vat", variant="primary")
            yield Button(t("Daily Ops"), id="btn_daily", variant="primary")
            yield Button(t("Refund"), id="btn_refund", variant="error")
        with Horizontal(classes="button_row"):
            yield Select(
                [("Unified Management Pack", "management_pack"),
                 ("Finance Monthly", "finance_monthly"),
                 ("Daily Ops Pack", "ops_daily"),
                 ("ML Features v1", "ml_features_v1")],
                id="analytics_preset", prompt="Analytics Preset", value="management_pack")
            yield Button("📦 Export Analytics", id="btn_analytics", variant="primary")

        yield Static(t("Recent Expenses"), classes="title")
        yield DataTable(id="exp_table")
        with Horizontal(classes="button_row"):
            yield Button("🔒 " + t("Void Expense"), id="btn_void_exp", variant="error")
        yield Static(t("Recent Ledger"), classes="title")
        yield DataTable(id="ledger_table")

    def on_mount(self):
        self.refresh_data()

    def _range(self):
        return self.query_one("#from", Input).value.strip(), self.query_one("#to", Input).value.strip()

    def _validate_dates(self):
        s, e = self._range()
        try:
            datetime.strptime(s, "%Y-%m-%d"); datetime.strptime(e, "%Y-%m-%d")
        except ValueError:
            show_error(self.app, "Dates must be YYYY-MM-DD.")
            return None
        return s, e

    def refresh_data(self):
        log = self.query_one("#ledger_log", RichLog)
        log.clear()
        s = services.get_general_ledger_summary() or {}
        log.write(f"[green]{t('Total Revenue')}:[/] {fmt(s.get('billed_revenue', s.get('revenue', 0)))}")
        log.write(f"[red]{t('Total Expenses')}:[/] {fmt(s.get('expenses', 0))}")
        net = s.get('net_profit', 0)
        color = "green" if net >= 0 else "red"
        log.write(f"[{color}]{t('Net Profit')}:[/] {fmt(net)}")
        log.write(f"[blue]Cash In/Out:[/] {fmt(s.get('cash_in', 0))} / {fmt(s.get('cash_out', 0))}")
        log.write(f"[yellow]A/R:[/] {fmt(s.get('ar_balance', s.get('accounts_receivable', 0)))}")

        exp = self.query_one("#exp_table", DataTable)
        exp.clear(columns=True)
        exp.add_columns("ID", "Date", t("Category"), t("Parameter Name"), "Net", "VAT", t("Method"), t("Notes"))
        for r in services.get_recent_expenses(50):
            exp.add_row(str(r["id"]), r["at"], r["category_name"], r["parameter_name"],
                        f"{r['net_amount']:.2f}", f"{r['vat_amount']:.2f}", r["method"], r["notes"])

        lgr = self.query_one("#ledger_table", DataTable)
        lgr.clear(columns=True)
        lgr.add_columns("ID", "Date", "Type", t("Category"), "Net", "VAT", "Gross", "Description")
        for r in services.get_recent_ledger(100):
            lgr.add_row(str(r["id"]), r["at"], r["entry_type"], r["category"] or "-",
                        f"{r.get('net', 0):.2f}" if hasattr(r, "get") else "0",
                        f"{r['vat']:.2f}", f"{r['gross']:.2f}", r["description"])

    def on_button_pressed(self, e):
        bid = e.button.id
        try:
            if bid == "btn_exp":
                self._add_expense()
            elif bid == "btn_refresh":
                self.refresh_data()
            elif bid == "btn_apply":
                self.refresh_data()
            elif bid == "btn_chart":
                rng = self._validate_dates()
                if rng:
                    self.app.push_screen(ChartScreen("revenue", *rng))
            elif bid == "btn_pl":
                rng = self._validate_dates()
                if rng:
                    self.app.push_screen(ReportScreen(f"P&L — {rng[0]} → {rng[1]}",
                                                     services.report_pl_statement(*rng)))
            elif bid == "btn_cash":
                rng = self._validate_dates()
                if rng:
                    self.app.push_screen(ReportScreen("Cash Flow", services.report_cash_flow(*rng)))
            elif bid == "btn_methods":
                rng = self._validate_dates()
                if rng:
                    self.app.push_screen(ReportScreen("Payment Methods",
                                                     services.report_payment_methods(*rng)))
            elif bid == "btn_vat":
                rng = self._validate_dates()
                if rng:
                    self.app.push_screen(ReportScreen("VAT Report", services.report_vat(*rng)))
            elif bid == "btn_daily":
                self.app.push_screen(ReportScreen("Daily Ops",
                                                 services.report_daily_ops(datetime.now().strftime("%Y-%m-%d"))))
            elif bid == "btn_refund":
                push_modal(self.app, RefundScreen(), on_success=self.refresh_data)
            elif bid == "btn_void_exp":
                eid = selected_id(self.query_one("#exp_table", DataTable))
                if eid:
                    push_modal(self.app, VoidExpenseScreen(eid), on_success=self.refresh_data)
            elif bid == "btn_analytics":
                rng = self._validate_dates()
                if rng:
                    preset = self.query_one("#analytics_preset", Select).value
                    if not isinstance(preset, str):
                        preset = "management_pack"
                    self.app.notify("Generating analytics export...", severity="information", timeout=3)

                    def _work():
                        try:
                            res = services.generate_unified_report(preset, rng[0], rng[1])
                            lines = [f"Analytics preset exported: {preset}",
                                     f"Range: {rng[0]} → {rng[1]}",
                                     f"Datasets: {', '.join(res.get('datasets', []))}",
                                     "", "Outputs:"]
                            for k, v in (res.get("outputs") or {}).items():
                                if isinstance(v, list):
                                    for p in v:  # pragma: no cover - analytics output list iteration (worker)
                                        lines.append(f"- {k}: {p}")  # pragma: no cover - analytics output list iteration (worker)
                                else:
                                    lines.append(f"- {k}: {v}")
                            self.app.call_from_thread(
                                lambda: self.app.push_screen(ReportScreen(f"Analytics Export — {preset}", "\n".join(lines)))
                            )
                            self.app.call_from_thread(
                                lambda: self.app.notify("Analytics export generated.", severity="information", timeout=6)
                            )
                        except Exception as exc:
                            self.app.call_from_thread(lambda: show_error(self.app, str(exc)))

                    self.run_worker(_work, thread=True, exclusive=False)
        except Exception as exc:  # pragma: no cover - defensive/widget-state branch
            show_error(self.app, str(exc))  # pragma: no cover - defensive/widget-state branch

    def _add_expense(self):
        cat = self.query_one("#exp_cat", Select).value
        name = self.query_one("#exp_name", Input).value.strip()
        try:
            amt = float(self.query_one("#exp_amt", Input).value)
            vat = float(self.query_one("#exp_vat", Input).value or 0)
        except ValueError:
            return show_error(self.app, "Amount must be a number.")
        method = self.query_one("#exp_method", Select).value
        if not isinstance(cat, int):
            return show_error(self.app, "Select a category.")
        if not isinstance(method, str):
            method = "Cash"
        try:
            services.record_operational_expense(cat, name, amt, vat,
                                                self.query_one("#exp_notes", Input).value, method)
            self.app.refresh_all_tables()
            self.app.notify("Expense recorded.")
            self.query_one("#exp_name", Input).value = ""
            self.query_one("#exp_amt", Input).value = ""
            self.query_one("#exp_vat", Input).value = "0"
        except Exception as exc:
            show_error(self.app, str(exc))


# ═══════════════════════════════════════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════════════════════════════════════

