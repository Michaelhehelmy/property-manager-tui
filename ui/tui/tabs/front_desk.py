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

class FrontDeskTab(VerticalScroll):
    def compose(self):
        yield Static("", id="summary", classes="title")
        yield Static(t("Filters"), classes="title")
        yield RadioSet(
            RadioButton(t("All Rooms"), id="m_all", value=True),
            RadioButton(t("Available in Period"), id="m_avail"),
            RadioButton(t("Room View"), id="m_room"),
            id="mode")
        with Horizontal(classes="button_row"):
            yield Input(placeholder=t("From"), id="from")
            yield Input(placeholder=t("To"), id="to")
            yield Select([], id="room_sel", prompt=t("Room"))
            yield Button(t("Apply"), id="btn_apply", variant="primary", tooltip="Apply the selected filters/view to the front desk display")
        yield RadioSet(
            RadioButton(t("Day"), id="w_day"),
            RadioButton(t("Week"), id="w_week"),
            RadioButton(t("Month"), id="w_month", value=True),
            RadioButton(t("Custom"), id="w_custom"),
            id="window")
        with Horizontal(classes="button_row"):
            yield Button("📈 " + t("Occupancy Chart"), id="btn_chart", variant="primary", tooltip="Show occupancy chart for the selected period")
        yield DataTable(id="room_table")
        with Horizontal(classes="button_row"):
            yield Button(t("Mark Clean"), id="btn_clean", variant="success", tooltip="Mark selected room as Clean (ready for guests)")
            yield Button(t("Mark Dirty"), id="btn_dirty", variant="warning", tooltip="Mark selected room as Dirty (needs housekeeping)")
            yield Button(t("Mark Maintenance"), id="btn_maint", variant="error", tooltip="Mark selected room as Maintenance (out of service)")
            yield Button(t("Edit"), id="btn_edit", variant="primary", tooltip="Edit selected item")
        yield Static("", id="context", classes="warn")
        yield DataTable(id="upcoming")

    def on_mount(self):
        now = datetime.now()
        self.query_one("#from", Input).value = now.strftime("%Y-%m-%d")
        self.query_one("#to", Input).value = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            self.query_one("#room_sel", Select).set_options(_room_options())
        except Exception:  # pragma: no cover - defensive/widget-state branch
            pass  # pragma: no cover - defensive/widget-state branch
        self.refresh_data()

    def _mode(self):
        try:
            idx = self.query_one("#mode", RadioSet).pressed_index
            return ["all", "avail", "room"][idx] if idx is not None else "all"
        except Exception:  # pragma: no cover - defensive/widget-state branch
            return "all"  # pragma: no cover - defensive/widget-state branch

    def _window_range(self):
        from ..helpers import compute_window_range
        now = datetime.now()
        try:
            idx = self.query_one("#window", RadioSet).pressed_index
        except Exception:  # pragma: no cover - defensive/widget-state branch
            idx = 2
        try:
            wsd = services.get_setting("week_start_day", "monday")
        except Exception:
            wsd = "monday"
        return compute_window_range(
            idx, now, wsd,
            self.query_one("#from", Input).value,
            self.query_one("#to", Input).value,
        )

    def _refresh_summary(self):
        s = services.get_front_desk_summary()
        start, end = self._window_range()
        try:
            occ = services.get_occupancy_for_window(start, end)
        except Exception:
            occ = {"pct": 0, "adr": 0, "revpar": 0}
        txt = (f"Occupancy: {s['occupancy_pct']}% ({s['occupied']}/{s['total']}) · "
               f"Clean {s['clean']} · Dirty {s['dirty']} · Maint {s['maint']}\n"
               f"Arrivals today: {s['arrivals_today']} · Departures today: {s['departures_today']} · "
               f"In-house: {s['in_house']}\n"
               f"Window ({start} → {end}): {occ['pct']}% · ADR {occ['adr']:.2f} · RevPAR {occ['revpar']:.2f}")
        self.query_one("#summary", Static).update(txt)

    def refresh_data(self):
        try:
            self._refresh_summary()
        except Exception:  # pragma: no cover - defensive/widget-state branch
            pass  # pragma: no cover - defensive/widget-state branch
        mode = self._mode()
        table = self.query_one("#room_table", DataTable)
        old = table.cursor_row if table.row_count > 0 else 0
        table.clear(columns=True)

        if mode == "avail":
            ci = self.query_one("#from", Input).value.strip()
            co = self.query_one("#to", Input).value.strip()
            try:
                datetime.strptime(ci, "%Y-%m-%d"); datetime.strptime(co, "%Y-%m-%d")
            except Exception:  # pragma: no cover - defensive/widget-state branch
                show_error(self.app, "From/To required for availability mode.")  # pragma: no cover - defensive/widget-state branch
                mode = "all"
            if mode == "avail":
                table.add_columns("ID", t("Room"), t("Room Type"), t("Base Price"), t("Status"))
                for r in services.get_rooms_available_between(ci, co):
                    table.add_row(str(r["id"]), r["room_number"], r["room_type"],
                                  fmt(r["base_price"]), r["housekeeping_status"])
        if mode in ("all", "room"):
            table.add_columns("ID", t("Room"), t("Room Type"), t("Base Price"), t("Status"),
                              "Next Guest", "Next In")
            for r in services.get_all_rooms():
                from app_core.db import connect
                nxt = connect().execute("""SELECT guest_name, check_in_date FROM reservations
                    WHERE room_id=? AND status IN ('Reserved','Checked In')
                    AND check_in_date >= date('now','localtime') ORDER BY check_in_date LIMIT 1""",
                    (r["id"],)).fetchone()
                ng = nxt["guest_name"] if nxt else "-"
                nd = nxt["check_in_date"] if nxt else "-"
                table.add_row(str(r["id"]), r["room_number"], r["room_type"],
                              fmt(r["base_price"]), r["housekeeping_status"], ng, nd)

        if table.row_count > 0:
            try:
                table.move_cursor(row=min(old, table.row_count - 1), animate=False)
            except Exception:  # pragma: no cover - defensive/widget-state branch
                pass  # pragma: no cover - defensive/widget-state branch

        if mode == "room":
            self.query_one("#context", Static).update(t("Upcoming Reservations"))
            rid = self.query_one("#room_sel", Select).value
            if not isinstance(rid, int):
                rid = selected_id(table)
            upcoming = self.query_one("#upcoming", DataTable)
            upcoming.clear(columns=True)
            upcoming.add_columns("ID", t("Guest"), "In", "Out", t("Status"), "Rate")
            if rid:
                for r in services.get_upcoming_reservations_for_room(rid):
                    upcoming.add_row(str(r["id"]), r["guest_name"], r["check_in_date"],
                                     r["check_out_date"], r["status"], fmt(r["agreed_nightly_rate"]))
        else:
            self.query_one("#context", Static).update("")
            self.query_one("#upcoming", DataTable).clear(columns=True)
        self._update_edit_btn()

    def _update_edit_btn(self):
        try:
            btn = self.query_one("#btn_edit", Button)
            table = self.query_one("#room_table", DataTable)
            col = table.cursor_column
            if col == 2:
                btn.label = "🔒 " + t("Edit Type"); btn.display = True
            elif col == 3:
                btn.label = "🔒 " + t("Edit Price"); btn.display = True
            else:
                btn.display = False
        except Exception:  # pragma: no cover - defensive/widget-state branch
            pass  # pragma: no cover - defensive/widget-state branch

    def on_data_table_cell_highlighted(self, e):
        if e.data_table.id == "room_table":
            self._update_edit_btn()

    def on_button_pressed(self, e):
        bid = e.button.id
        try:
            if bid in ("btn_clean", "btn_dirty", "btn_maint"):
                rid = selected_id(self.query_one("#room_table", DataTable))
                if rid:
                    st = {"btn_clean": "Clean", "btn_dirty": "Dirty", "btn_maint": "Maintenance"}[bid]
                    services.update_housekeeping_status(rid, st)
                    self.app.refresh_all_tables()
                    self.app.notify(f"Room {rid} -> {st}")
            elif bid == "btn_edit":
                table = self.query_one("#room_table", DataTable)
                rid = selected_id(table)
                if rid:
                    col = table.cursor_column
                    if col == 2:
                        push_modal(self.app, EditRoomFieldScreen(rid, "type"), on_success=self.refresh_data)
                    elif col == 3:
                        push_modal(self.app, EditRoomFieldScreen(rid, "price"), on_success=self.refresh_data)
            elif bid == "btn_apply":
                self.refresh_data()
            elif bid == "btn_chart":
                self.app.push_screen(ChartScreen("occupancy"))
        except Exception as exc:  # pragma: no cover - defensive/widget-state branch
            self.app.notify(str(exc), severity="error")  # pragma: no cover - defensive/widget-state branch


# ═══════════════════════════════════════════════════════════════════════════
# RESERVATIONS
# ═══════════════════════════════════════════════════════════════════════════

