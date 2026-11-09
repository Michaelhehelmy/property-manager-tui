"""All tabs — UI wiring only, logic in services.py."""
import os
from datetime import datetime, timedelta
from textual.widgets import (Static, DataTable, Button, Input, RichLog, Select,
                              RadioSet, RadioButton, Checkbox, Collapsible, TabbedContent, TabPane)
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual import events
import services
from services import t, fmt, selected_id
from modals import (
    ErrorModal, ConfirmModal, ProtectedModal, UnlockSettingsModal, SetSuperPasswordScreen,
    RecoveryCodesScreen, ChangePasswordModal, DeleteReservationScreen, ModifyReservationScreen,
    VoidItemScreen, CancelConfirmScreen, RefundScreen, OverrideCheckoutModal,
    InventoryEditScreen, InventoryDeleteScreen, QtyInputScreen, VoidExpenseScreen,
    EditRoomFieldScreen, RatePlanEditScreen, RatePlanDeleteScreen, GuestEditScreen,
    GuestMergeModal, GuestCRMModal, GuestHistoryModal, InvoiceScreen, WalkInReceiptScreen,
    ReportScreen, ChartScreen, ViewFolioScreen, ReviewChangesModal, ImportSettingsModal,
    AuditLogScreen, LanguageScreen, ApplyDemoDataModal, BrandingWizardScreen,
)

PAYMENT_METHODS = ["Cash", "Card", "OTA", "Bank Transfer"]
PAYMENT_TYPES = ["Payment", "Deposit"]
SOURCES = ["Direct", "Booking.com", "Airbnb", "Walk-in", "Phone", "Other"]


def push_modal(app, modal, on_success=None):
    """Push modal; callback is guarded so exceptions can't crash the app."""
    def cb(ok):
        try:
            if ok and on_success is not None:
                on_success()
            elif ok:
                app.refresh_all_tables()
        except Exception as exc:
            try:
                app.notify(str(exc), severity="error")
            except Exception:
                pass
    try:
        app.push_screen(modal, callback=cb)
    except Exception as exc:
        try:
            app.notify(str(exc), severity="error")
        except Exception:
            pass


def show_error(app, msg):
    app.push_screen(ErrorModal(msg))


def _room_options():
    return [(f"{r['room_number']} - {r['room_type']}", r["id"]) for r in services.get_all_rooms()]


def _category_options():
    return [(c["category_name"], c["id"]) for c in services.get_expense_categories()]


# ═══════════════════════════════════════════════════════════════════════════
# FRONT DESK
# ═══════════════════════════════════════════════════════════════════════════

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
            yield Button(t("Apply"), id="btn_apply", variant="primary")
        yield RadioSet(
            RadioButton(t("Day"), id="w_day"),
            RadioButton(t("Week"), id="w_week"),
            RadioButton(t("Month"), id="w_month", value=True),
            RadioButton(t("Custom"), id="w_custom"),
            id="window")
        with Horizontal(classes="button_row"):
            yield Button("📈 " + t("Occupancy Chart"), id="btn_chart", variant="primary")
        yield DataTable(id="room_table")
        with Horizontal(classes="button_row"):
            yield Button(t("Mark Clean"), id="btn_clean", variant="success")
            yield Button(t("Mark Dirty"), id="btn_dirty", variant="warning")
            yield Button(t("Mark Maintenance"), id="btn_maint", variant="error")
            yield Button(t("Edit"), id="btn_edit", variant="primary")
        yield Static("", id="context", classes="warn")
        yield DataTable(id="upcoming")

    def on_mount(self):
        now = datetime.now()
        self.query_one("#from", Input).value = now.strftime("%Y-%m-%d")
        self.query_one("#to", Input).value = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            self.query_one("#room_sel", Select).set_options(_room_options())
        except Exception:
            pass
        self.refresh_data()

    def _mode(self):
        try:
            idx = self.query_one("#mode", RadioSet).pressed_index
            return ["all", "avail", "room"][idx] if idx is not None else "all"
        except Exception:
            return "all"

    def _window_range(self):
        now = datetime.now()
        try:
            idx = self.query_one("#window", RadioSet).pressed_index
        except Exception:
            idx = 2
        if idx == 0:
            return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")
        if idx == 1:
            wsd = services.get_setting("week_start_day", "monday")
            start_idx = {"monday": 0, "sunday": 6, "saturday": 5}.get(wsd, 0)
            start = now - timedelta(days=(now.weekday() - start_idx) % 7)
            return start.strftime("%Y-%m-%d"), (start + timedelta(days=6)).strftime("%Y-%m-%d")
        if idx == 2:
            start = now.replace(day=1)
            nm = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            return start.strftime("%Y-%m-%d"), (nm - timedelta(days=1)).strftime("%Y-%m-%d")
        return self.query_one("#from", Input).value, self.query_one("#to", Input).value

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
        except Exception:
            pass
        mode = self._mode()
        table = self.query_one("#room_table", DataTable)
        old = table.cursor_row if table.row_count > 0 else 0
        table.clear(columns=True)

        if mode == "avail":
            ci = self.query_one("#from", Input).value.strip()
            co = self.query_one("#to", Input).value.strip()
            try:
                datetime.strptime(ci, "%Y-%m-%d"); datetime.strptime(co, "%Y-%m-%d")
            except Exception:
                show_error(self.app, "From/To required for availability mode.")
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
                from db import connect
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
            except Exception:
                pass

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
        except Exception:
            pass

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
        except Exception as exc:
            self.app.notify(str(exc), severity="error")


# ═══════════════════════════════════════════════════════════════════════════
# RESERVATIONS
# ═══════════════════════════════════════════════════════════════════════════

class ReservationsTab(VerticalScroll):
    def __init__(self):
        super().__init__()
        self._guest_id = None
        self._debounce = None

    def compose(self):
        yield Static(t("Active Bookings & Guest Management"), classes="title")
        yield Input(placeholder=t("Search"), id="search")
        yield DataTable(id="table")
        with Horizontal(classes="button_row"):
            yield Button(t("Check-In Guest"), id="btn_in", variant="success")
            yield Button(t("Check-Out & Invoice"), id="btn_out", variant="warning")
            yield Button(t("Preview Invoice"), id="btn_preview", variant="primary")
            yield Button(t("View Folio"), id="btn_folio", variant="primary")
        with Horizontal(classes="button_row"):
            yield Button("🔒 " + t("Modify"), id="btn_modify", variant="primary")
            yield Button(t("Cancel Reservation"), id="btn_cancel", variant="error")
            yield Button(t("No-Show"), id="btn_noshow", variant="error")
            yield Button(t("Delete"), id="btn_delete", variant="error")
        with Horizontal(classes="button_row"):
            yield Button("👥 " + t("Guests"), id="btn_guests", variant="primary")
            yield Button("📊 " + t("Chart"), id="btn_chart", variant="primary")

        with Collapsible(title="➕ " + t("Create New Reservation"), collapsed=True):
            yield Input(placeholder=t("Guest Name"), id="guest")
            yield Static("", id="hint", classes="warn")
            yield Select(_room_options(), id="room", prompt=t("Room"))
            yield Select([("—", None)] + [(n, n) for n in SOURCES], id="source", prompt=t("Source"), value="Direct")
            yield Select([(p["name"], p["id"]) for p in services.get_all_rate_plans()],
                         id="plan", prompt=t("Rate Plan"))
            yield Input(placeholder=t("Agreed Nightly Rate"), id="rate")
            yield Input(placeholder=t("Deposit Amount"), id="deposit", value="")
            yield Input(placeholder=t("Check-In Date"), id="ci")
            yield Input(placeholder=t("Check-Out Date"), id="co")
            yield Button(t("Save Reservation"), id="btn_save", variant="primary")

        with Collapsible(title="💰 " + t("Guest Folio & Settlement"), collapsed=True):
            yield Input(placeholder=t("Reservation ID for Billing"), id="pay_rid")
            yield Button(t("Check Guest Balance Due"), id="btn_balance", variant="primary")
            yield Input(placeholder=t("Payment Amount"), id="pay_amt")
            yield Select([(m, m) for m in PAYMENT_METHODS], id="pay_method", prompt=t("Method"), value="Cash")
            yield Select([(pt, pt) for pt in PAYMENT_TYPES], id="pay_type", prompt=t("Payment Type"), value="Payment")
            yield Input(placeholder=t("Notes"), id="pay_notes", value="")
            yield Button(t("Record Payment"), id="btn_pay", variant="success")

    def on_mount(self):
        now = datetime.now()
        self.query_one("#ci", Input).value = now.strftime("%Y-%m-%d")
        self.query_one("#co", Input).value = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        self.refresh_data()

    def set_room(self, room_id):
        try:
            self.query_one("#room", Select).value = room_id
        except Exception:
            pass

    def on_input_changed(self, e):
        if e.input.id == "guest":
            if self._debounce:
                try:
                    self._debounce.stop()
                except Exception:
                    pass
            self._debounce = self.set_timer(0.3, self._check_guest)
        elif e.input.id == "search":
            self.refresh_data(e.value)

    def _check_guest(self):
        name = self.query_one("#guest", Input).value.strip()
        self._guest_id = None
        if len(name) < 3:
            self.query_one("#hint", Static).update("")
            return
        try:
            matches = services.find_guest_matches(name)
            if matches:
                top = matches[0]
                if top["name"].lower() == name.lower() or top["score"] >= 0.95:
                    self._guest_id = top["id"]
                    self.query_one("#hint", Static).update(
                        f"✓ Linked to existing guest: {top['name']} (#{top['id']}, {top['stays']} stays)")
                else:
                    txt = "💡 Similar guests found:\n"
                    for m in matches[:3]:
                        txt += f"  • {m['name']} (#{m['id']}, {m['stays']} stays, score {m['score']})\n"
                    txt += "  Type full exact name to auto-link, or click Save to create new."
                    self.query_one("#hint", Static).update(txt)
            else:
                self.query_one("#hint", Static).update("")
        except Exception:
            self.query_one("#hint", Static).update("")

    def on_select_changed(self, e):
        if e.select.id == "plan":
            plan_id = e.value
            room_id = self.query_one("#room", Select).value
            if isinstance(plan_id, int) and isinstance(room_id, int):
                try:
                    room = services.get_room(room_id)
                    plans = {p["id"]: p for p in services.get_all_rate_plans()}
                    if room and plan_id in plans:
                        self.query_one("#rate", Input).value = str(room["base_price"] + plans[plan_id]["price_modifier"])
                except Exception:
                    pass

    def refresh_data(self, search=""):
        table = self.query_one("#table", DataTable)
        old = table.cursor_row if table.row_count > 0 else 0
        table.clear(columns=True)
        table.add_columns("ID", t("Guest"), t("Source"), t("Room"), t("Agreed Nightly Rate"),
                          "In", "Out", t("Status"), "")
        from db import connect
        sql = """SELECT r.id, r.guest_name, r.source, rm.room_number, r.agreed_nightly_rate,
                        r.check_in_date, r.check_out_date, r.status, r.guest_id,
                        (SELECT COUNT(*) FROM reservations WHERE guest_id=r.guest_id) AS stays
                 FROM reservations r JOIN rooms rm ON r.room_id = rm.id WHERE r.status != 'Deleted'"""
        params = []
        if search:
            sql += " AND (r.guest_name LIKE ? OR r.source LIKE ?)"
            params += [f"%{search}%", f"%{search}%"]
        sql += " ORDER BY r.id DESC"
        for r in connect().execute(sql, params).fetchall():
            badge = "🔁" if (r["stays"] or 0) > 1 else ""
            table.add_row(str(r["id"]), r["guest_name"], r["source"], r["room_number"],
                          fmt(r["agreed_nightly_rate"]), r["check_in_date"], r["check_out_date"],
                          r["status"], badge)
        if table.row_count > 0:
            try:
                table.move_cursor(row=min(old, table.row_count - 1), animate=False)
            except Exception:
                pass

    def on_button_pressed(self, e):
        bid = e.button.id
        try:
            if bid == "btn_save":
                self._save_reservation()
            elif bid == "btn_in":
                rid = selected_id(self.query_one("#table", DataTable))
                if rid:
                    services.update_reservation_status(rid, "Checked In")
                    self.app.refresh_all_tables()
                    self.app.notify(f"Res #{rid} checked in.")
            elif bid == "btn_out":
                self._checkout()
            elif bid == "btn_preview":
                rid = selected_id(self.query_one("#table", DataTable))
                if rid:
                    self.app.push_screen(InvoiceScreen(rid, save=False))
            elif bid == "btn_cancel":
                rid = selected_id(self.query_one("#table", DataTable))
                if rid:
                    push_modal(self.app, CancelConfirmScreen(rid, "cancel"), on_success=self.refresh_data)
            elif bid == "btn_noshow":
                rid = selected_id(self.query_one("#table", DataTable))
                if rid:
                    push_modal(self.app, CancelConfirmScreen(rid, "no_show"), on_success=self.refresh_data)
            elif bid == "btn_modify":
                table = self.query_one("#table", DataTable)
                rid = selected_id(table)
                if rid:
                    row = table.get_row_at(table.cursor_row)
                    room = row[3]
                    room_id = None
                    for r in services.get_all_rooms():
                        if r["room_number"] == room:
                            room_id = r["id"]
                            break
                    if room_id:
                        push_modal(self.app,
                                   ModifyReservationScreen(rid, str(row[4]).replace(" EGP", ""),
                                                           row[5], row[6], room_id),
                                   on_success=self.refresh_data)
            elif bid == "btn_folio":
                rid = selected_id(self.query_one("#table", DataTable))
                if rid:
                    self.app.push_screen(ViewFolioScreen(rid))
            elif bid == "btn_delete":
                table = self.query_one("#table", DataTable)
                rid = selected_id(table)
                if rid:
                    row = table.get_row_at(table.cursor_row)
                    push_modal(self.app,
                               DeleteReservationScreen(rid, row[1], f"Room {row[3]}"),
                               on_success=self.refresh_data)
            elif bid == "btn_balance":
                self._check_balance()
            elif bid == "btn_pay":
                self._record_payment()
            elif bid == "btn_guests":
                self.app.push_screen(GuestCRMModal())
            elif bid == "btn_chart":
                self.app.push_screen(ChartScreen("revenue"))
        except Exception as exc:
            self.app.notify(str(exc), severity="error")

    def _save_reservation(self):
        guest = self.query_one("#guest", Input).value.strip()
        room_id = self.query_one("#room", Select).value
        source = self.query_one("#source", Select).value or "Direct"
        plan_id = self.query_one("#plan", Select).value
        rate_s = self.query_one("#rate", Input).value.strip()
        ci = self.query_one("#ci", Input).value.strip()
        co = self.query_one("#co", Input).value.strip()
        dep_s = self.query_one("#deposit", Input).value.strip()

        if not guest:
            return show_error(self.app, "Guest name required.")
        if not isinstance(room_id, int):
            return show_error(self.app, "Select a room.")
        try:
            rate = float(rate_s)
        except ValueError:
            return show_error(self.app, "Rate must be a number.")
        try:
            datetime.strptime(ci, "%Y-%m-%d"); datetime.strptime(co, "%Y-%m-%d")
        except ValueError:
            return show_error(self.app, "Dates must be YYYY-MM-DD.")
        dep = 0.0
        if dep_s:
            try:
                dep = float(dep_s)
            except ValueError:
                return show_error(self.app, "Deposit must be a number.")
        plan = plan_id if isinstance(plan_id, int) else None
        src = source if isinstance(source, str) else "Direct"

        try:
            new_id = services.create_reservation(guest, src, room_id, plan, rate, ci, co,
                                                 dep, guest_id=self._guest_id)
            self.app.refresh_all_tables()
            self.app.notify(f"Reservation #{new_id} created.")
            for iid in ("guest", "rate", "deposit"):
                self.query_one(f"#{iid}", Input).value = ""
            self.query_one("#hint", Static).update("")
            table = self.query_one("#table", DataTable)
            for i in range(table.row_count):
                if table.get_row_at(i)[0] == str(new_id):
                    table.move_cursor(row=i, animate=False)
                    break
        except Exception as exc:
            show_error(self.app, str(exc))

    def _checkout(self):
        rid = selected_id(self.query_one("#table", DataTable))
        if not rid:
            return
        needs_override, bal, _thresh = services.checkout_requires_override(rid)
        if needs_override:
            push_modal(self.app, OverrideCheckoutModal(rid, bal),
                       on_success=lambda: self._do_checkout(rid, override=True))
            return
        self._do_checkout(rid)

    def _do_checkout(self, rid, override=False):
        try:
            if not override:
                services.checkout_reservation(rid)
            # override path already executed by modal; just finalize UI
        except Exception as exc:
            return show_error(self.app, str(exc))
        self.app.refresh_all_tables()
        self.app.push_screen(InvoiceScreen(rid, save=True))

    def _check_balance(self):
        try:
            rid = int(self.query_one("#pay_rid", Input).value)
        except ValueError:
            return show_error(self.app, "Invalid reservation ID.")
        bd = services.get_balance_breakdown(rid)
        if not bd:
            return show_error(self.app, f"Res #{rid} not found.")
        self.app.notify(
            f"Res #{rid}: total {fmt(bd['total_charges'])} · paid {fmt(bd['total_paid'])} · "
            f"balance {fmt(bd['balance_due'])}", timeout=10)

    def _record_payment(self):
        try:
            rid = int(self.query_one("#pay_rid", Input).value)
            amt = float(self.query_one("#pay_amt", Input).value)
        except ValueError:
            return show_error(self.app, "Invalid reservation ID or amount.")
        method = self.query_one("#pay_method", Select).value
        ptype = self.query_one("#pay_type", Select).value
        notes = self.query_one("#pay_notes", Input).value
        if not isinstance(method, str):
            return show_error(self.app, "Select a payment method.")
        if not isinstance(ptype, str):
            ptype = "Payment"
        try:
            services.record_payment(rid, amt, method, notes, ptype)
            self.app.refresh_all_tables()
            self.app.notify(f"{ptype} {fmt(amt)} recorded.")
            self.query_one("#pay_amt", Input).value = ""
            self.query_one("#pay_notes", Input).value = ""
        except Exception as exc:
            show_error(self.app, str(exc))


# ═══════════════════════════════════════════════════════════════════════════
# POS & INVENTORY
# ═══════════════════════════════════════════════════════════════════════════

class PosInventoryTab(Vertical):
    def compose(self):
        with TabbedContent():
            with TabPane(t("New Order"), id="tab_order"):
                yield NewOrderTab()
            with TabPane(t("Inventory"), id="tab_inv"):
                yield InventorySubTab()

    def refresh_data(self):
        try:
            self.query_one(NewOrderTab).refresh_data()
        except Exception:
            pass
        try:
            self.query_one(InventorySubTab).refresh_data()
        except Exception:
            pass


class NewOrderTab(VerticalScroll):
    def __init__(self):
        super().__init__()
        self._res_id = None
        self._walkin_cart = []  # [{desc, qty, price, inv_id}]

    def compose(self):
        yield Static(t("Target"), classes="title")
        yield RadioSet(
            RadioButton(t("Existing Reservation"), id="t_res", value=True),
            RadioButton(t("Walk-in"), id="t_walk"),
            id="target")
        with Horizontal(classes="button_row"):
            yield Input(placeholder=t("Reservation ID for Billing"), id="rid")
            yield Button(t("Load"), id="btn_load", variant="primary")
        yield Static("", id="info", classes="warn")
        yield Static(t("Item Grid"), classes="title")
        with Horizontal(classes="button_row"):
            yield Input(placeholder=t("Search"), id="search")
            yield Select([("All", "All")], id="cat", prompt=t("Category"))
        yield DataTable(id="grid")
        yield Static(t("Custom Item"), classes="title")
        with Horizontal(classes="button_row"):
            yield Input(placeholder=t("Description"), id="custom_desc")
            yield Input(placeholder=t("Qty"), id="custom_qty", value="1")
            yield Input(placeholder=t("Unit Price"), id="custom_price")
            yield Button(t("Add"), id="btn_custom", variant="primary")
        yield Static(t("Cart"), classes="title")
        yield DataTable(id="cart")
        with Horizontal(classes="button_row"):
            yield Button(t("Void"), id="btn_void", variant="error")
            yield Select([(m, m) for m in PAYMENT_METHODS], id="pay_method", prompt=t("Method"), value="Cash")
            yield Button(t("Pay"), id="btn_pay", variant="success")

    def on_mount(self):
        cats = services.get_inventory_categories()
        try:
            self.query_one("#cat", Select).set_options([("All", "All")] + [(c, c) for c in cats])
        except Exception:
            pass
        self.refresh_data()

    def _is_walkin(self):
        try:
            return self.query_one("#target", RadioSet).pressed_index == 1
        except Exception:
            return False

    def refresh_data(self):
        self._refresh_info()
        grid = self.query_one("#grid", DataTable)
        grid.clear(columns=True)
        grid.add_columns("+", t("Item"), t("Stock"), t("Price"), t("Category"))
        sw = self.query_one("#search", Input)
        q = sw.value if isinstance(sw.value, str) else ""
        cw = self.query_one("#cat", Select)
        cv = cw.value
        cat = cv if isinstance(cv, str) and cv != "All" else ""
        for r in services.search_inventory(q, cat, ""):
            grid.add_row("+", r["item_name"], f"{r['stock_qty']:.2f}",
                         f"{r['selling_price']:.2f}", r["category"])
        self._refresh_cart()

    def _refresh_info(self):
        if self._is_walkin():
            self.query_one("#info", Static).update("Walk-in sale — items collected in cart, paid on Pay.")
            return
        if self._res_id:
            bd = services.get_balance_breakdown(self._res_id)
            if bd:
                from db import connect
                r = connect().execute("SELECT guest_name, room_id FROM reservations WHERE id=?",
                                      (self._res_id,)).fetchone()
                if r:
                    room = services.get_room(r["room_id"])
                    rn = room["room_number"] if room else "?"
                    self.query_one("#info", Static).update(
                        f"{r['guest_name']} · Room {rn} · Balance: {fmt(bd['balance_due'])}")
                else:
                    self.query_one("#info", Static).update("Reservation not found.")
            else:
                self.query_one("#info", Static).update("Reservation not found.")

    def _refresh_cart(self):
        cart = self.query_one("#cart", DataTable)
        cart.clear(columns=True)
        cart.add_columns("ID", t("Item"), t("Qty"), t("Price"), "Tax", "Gross")
        if self._is_walkin():
            total = 0.0
            for i, it in enumerate(self._walkin_cart, start=1):
                tax = services.calculate_tax(it["price"] * it["qty"], 0)
                cart.add_row(f"W{i}", it["desc"], str(it["qty"]),
                             f"{it['price']:.2f}", f"{tax['service']+tax['vat']+tax['city_tax']:.2f}",
                             f"{tax['gross']:.2f}")
                total += tax["gross"]
            if self._walkin_cart:
                cart.add_row("", "TOTAL", "", "", "", f"{total:.2f}")
        elif self._res_id:
            total = 0.0
            for p in services.get_pos_items(self._res_id):
                gross = p["quantity"] * p["unit_price"] + p["tax_amount"]
                cart.add_row(str(p["id"]), p["item_description"], str(p["quantity"]),
                             f"{p['unit_price']:.2f}", f"{p['tax_amount']:.2f}", f"{gross:.2f}")
                total += gross
            if total > 0:
                cart.add_row("", "TOTAL", "", "", "", f"{total:.2f}")

    def on_input_changed(self, e):
        if e.input.id == "search":
            self.refresh_data()

    def on_select_changed(self, e):
        if e.select.id == "cat":
            self.refresh_data()

    def on_data_table_row_selected(self, e):
        if e.data_table.id == "grid":
            try:
                row = e.data_table.get_row_at(e.cursor_row)
                name = row[1]
                self.app.push_screen(QtyInputScreen(name),
                                     callback=lambda qty: self._add_from_grid(name, qty) if qty else None)
            except Exception as exc:
                self.app.notify(str(exc), severity="error")

    def _add_from_grid(self, item_name, qty):
        try:
            from db import connect
            row = connect().execute("SELECT id, selling_price FROM inventory WHERE item_name=? AND active=1",
                                    (item_name,)).fetchone()
            if not row:
                return self.app.notify("Item not found.", severity="error")
            self._add_to_cart(item_name, qty, row["selling_price"], row["id"])
        except Exception as exc:
            self.app.notify(str(exc), severity="error")

    def _add_to_cart(self, desc, qty, price, inv_id):
        if self._is_walkin():
            self._walkin_cart.append({"desc": desc, "qty": qty, "price": price, "inv_id": inv_id})
            self._refresh_cart()
            return
        if not self._res_id:
            return show_error(self.app, "Load a reservation first, or switch to Walk-in.")
        services.add_pos_item(self._res_id, desc, qty, price, inv_id)
        self.app.refresh_all_tables()
        self._refresh_cart()

    def on_button_pressed(self, e):
        bid = e.button.id
        try:
            if bid == "btn_load":
                try:
                    self._res_id = int(self.query_one("#rid", Input).value)
                except ValueError:
                    self._res_id = None
                    return show_error(self.app, "Invalid reservation ID.")
                self.refresh_data()
            elif bid == "btn_custom":
                desc = self.query_one("#custom_desc", Input).value.strip()
                try:
                    qty = int(self.query_one("#custom_qty", Input).value)
                    price = float(self.query_one("#custom_price", Input).value)
                except ValueError:
                    return show_error(self.app, "Invalid custom item values.")
                if not desc:
                    return show_error(self.app, "Description required.")
                if qty <= 0 or price < 0:
                    return show_error(self.app, "Qty must be positive; price >= 0.")
                self._add_to_cart(desc, qty, price, None)
                self.query_one("#custom_desc", Input).value = ""
                self.query_one("#custom_price", Input).value = ""
            elif bid == "btn_void":
                cart = self.query_one("#cart", DataTable)
                if cart.row_count == 0:
                    return
                try:
                    row = cart.get_row_at(cart.cursor_row)
                except Exception:
                    return
                if row[1] == "TOTAL":
                    return
                if self._is_walkin():
                    if row[0].startswith("W"):
                        idx = int(row[0][1:]) - 1
                        if 0 <= idx < len(self._walkin_cart):
                            self._walkin_cart.pop(idx)
                            self._refresh_cart()
                else:
                    pos_id = int(row[0])
                    push_modal(self.app, VoidItemScreen(pos_id),
                               on_success=lambda: (self.refresh_data(), self.app.refresh_all_tables()))
            elif bid == "btn_pay":
                self._pay()
        except Exception as exc:
            show_error(self.app, str(exc))

    def _pay(self):
        if not self._is_walkin():
            return show_error(self.app, "Pay Now is for walk-in only.")
        if not self._walkin_cart:
            return show_error(self.app, "Cart is empty.")
        method = self.query_one("#pay_method", Select).value
        if not isinstance(method, str):
            return show_error(self.app, "Select a payment method.")
        items_copy = list(self._walkin_cart)
        try:
            result = services.complete_walkin_sale(items_copy, method, "", "Payment")
            self._walkin_cart = []
            self._refresh_cart()
            self.app.refresh_all_tables()
            self.app.push_screen(
                WalkInReceiptScreen(result["receipt_items"], result["total_gross"], result["method"])
            )
        except Exception as exc:
            show_error(self.app, str(exc))


class InventorySubTab(VerticalScroll):
    def compose(self):
        yield Static("", id="warnings", classes="warn")
        with Horizontal(classes="button_row"):
            yield Input(placeholder=t("Search"), id="search")
            yield Select([("All", "All")], id="cat", prompt=t("Category"))
            yield Select([("All", "All"), (t("Low Stock"), "Low"),
                          (t("Overstock"), "Over"), (t("Negative Stock"), "Negative")],
                         id="stock", prompt=t("Stock"))
        yield DataTable(id="table")
        with Horizontal(classes="button_row"):
            yield Button("➕ " + t("New Item"), id="btn_new", variant="primary")
            yield Button("🔒 " + t("Edit"), id="btn_edit", variant="warning")
            yield Button("🔒 " + t("Delete"), id="btn_del", variant="error")
            yield Button(t("Restock Selected"), id="btn_restock", variant="success")
        with Horizontal(classes="button_row"):
            yield Input(placeholder=t("Quantity"), id="rs_qty")
            yield Input(placeholder="Unit Cost", id="rs_cost", value="0")
            yield Button(t("Restock"), id="btn_rs_do", variant="success")
        yield Static(t("Recent Movements"), classes="title")
        yield DataTable(id="logs")

    def on_mount(self):
        cats = services.get_inventory_categories()
        try:
            self.query_one("#cat", Select).set_options([("All", "All")] + [(c, c) for c in cats])
        except Exception:
            pass
        self.refresh_data()

    def _refresh_warnings(self):
        try:
            w = services.get_inventory_warnings()
            lines = []
            for iid, name, qty in w["negative"]:
                lines.append(f"🔴 NEGATIVE: {name} ({qty})")
            for iid, name, qty, th in w["low"]:
                lines.append(f"🔴 LOW: {name} ({qty}/{th})")
            for iid, name, qty, th in w["over"]:
                lines.append(f"🟡 OVER: {name} ({qty}/{th})")
            if not lines:
                lines.append("✅ " + t("All Healthy"))
            self.query_one("#warnings", Static).update("\n".join(lines))
        except Exception:
            pass

    def refresh_data(self):
        self._refresh_warnings()
        table = self.query_one("#table", DataTable)
        table.clear(columns=True)
        table.add_columns("ID", t("Item"), t("Category"), t("Stock"), t("Unit"),
                          t("Unit Cost"), t("Sell"), "Warn")
        sw = self.query_one("#search", Input)
        q = sw.value if isinstance(sw.value, str) else ""
        cw = self.query_one("#cat", Select)
        cv = cw.value
        cat = cv if isinstance(cv, str) and cv != "All" else ""
        kw = self.query_one("#stock", Select)
        kv = kw.value
        stk = kv if isinstance(kv, str) and kv != "All" else ""
        low = float(services.get_setting("low_stock_threshold", "5"))
        over = float(services.get_setting("overstock_threshold", "100"))
        for r in services.search_inventory(q, cat, stk):
            warn = ""
            if r["stock_qty"] < 0:
                warn = "🔴 NEG"
            elif r["stock_qty"] < low:
                warn = "🔴 LOW"
            elif r["stock_qty"] > over:
                warn = "🟡 OVER"
            table.add_row(str(r["id"]), r["item_name"], r["category"],
                          f"{r['stock_qty']:.2f}", r["unit"],
                          f"{r['unit_cost']:.2f}", f"{r['selling_price']:.2f}", warn)
        logs = self.query_one("#logs", DataTable)
        logs.clear(columns=True)
        logs.add_columns("At", t("Item"), "Type", t("Qty"), "Ref")
        for r in services.get_recent_inventory_logs(20):
            logs.add_row(r["at"], r["item_name"], r["change_type"],
                         f"{r['quantity']:.2f}", r["reference"] or "")

    def on_input_changed(self, e):
        if e.input.id == "search":
            self.refresh_data()

    def on_select_changed(self, e):
        if e.select.id in ("cat", "stock"):
            self.refresh_data()

    def _sel_item(self):
        table = self.query_one("#table", DataTable)
        iid = selected_id(table)
        if iid is None:
            return None
        row = table.get_row_at(table.cursor_row)
        return {"id": iid, "item_name": row[1], "category": row[2],
                "stock_qty": float(row[3]), "unit": row[4],
                "unit_cost": float(row[5]), "selling_price": float(row[6])}

    def on_button_pressed(self, e):
        bid = e.button.id
        try:
            if bid == "btn_new":
                push_modal(self.app, InventoryEditScreen("add"), on_success=self.refresh_data)
            elif bid == "btn_edit":
                item = self._sel_item()
                if item:
                    # Fetch full record with thresholds from DB
                    from db import connect
                    full = connect().execute("""SELECT id, item_name, category, unit, stock_qty,
                                                       unit_cost, selling_price, low_stock_threshold,
                                                       overstock_threshold FROM inventory WHERE id=?""",
                                             (item["id"],)).fetchone()
                    payload = dict(full) if full else item
                    push_modal(self.app, InventoryEditScreen("edit", payload), on_success=self.refresh_data)
            elif bid == "btn_del":
                item = self._sel_item()
                if item:
                    push_modal(self.app, InventoryDeleteScreen(item["id"], item["item_name"]),
                               on_success=self.refresh_data)
            elif bid == "btn_restock":
                item = self._sel_item()
                if item:
                    self.query_one("#rs_qty", Input).value = ""
                    self.query_one("#rs_cost", Input).value = str(item["unit_cost"])
                    self.app.notify(f"Restocking: {item['item_name']}")
            elif bid == "btn_rs_do":
                self._do_restock()
        except Exception as exc:
            show_error(self.app, str(exc))

    def _do_restock(self):
        item = self._sel_item()
        if not item:
            return show_error(self.app, "Select an item.")
        try:
            qty = float(self.query_one("#rs_qty", Input).value)
            cost = float(self.query_one("#rs_cost", Input).value or 0)
        except ValueError:
            return show_error(self.app, "Invalid qty/cost.")
        pw = self.app.query_one(SettingsTab)._pw_for_restock() if hasattr(self.app.query_one(SettingsTab), "_pw_for_restock") else ""
        # Restock uses session: prompt with protected modal if not session unlocked
        if not services.is_session_unlocked():
            self.app.push_screen(UnlockSettingsModal(),
                                 callback=lambda ok: self._finish_restock(item, qty, cost, ok))
        else:
            self._finish_restock(item, qty, cost, True)

    def _finish_restock(self, item, qty, cost, ok):
        if not ok:
            return
        try:
            services.restock_inventory(item["id"], qty, cost, password="__session__")
            self.app.refresh_all_tables()
            self.app.notify(f"Restocked {item['item_name']} +{qty}")
        except PermissionError:
            show_error(self.app, "Unlock Settings first to restock.")
        except Exception as exc:
            show_error(self.app, str(exc))


# ═══════════════════════════════════════════════════════════════════════════
# ACCOUNTING
# ═══════════════════════════════════════════════════════════════════════════

class AccountingTab(VerticalScroll):
    def compose(self):
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
            yield Button(t("Apply"), id="btn_apply", variant="primary")

        with Horizontal(classes="button_row"):
            yield Button("📊 " + t("Chart"), id="btn_chart", variant="primary")
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
        s = services.get_general_ledger_summary()
        log.write(f"[green]{t('Total Revenue')}:[/] {fmt(s['billed_revenue'])}")
        log.write(f"[red]{t('Total Expenses')}:[/] {fmt(s['expenses'])}")
        color = "green" if s["net_profit"] >= 0 else "red"
        log.write(f"[{color}]{t('Net Profit')}:[/] {fmt(s['net_profit'])}")
        log.write(f"[blue]Cash In/Out:[/] {fmt(s['cash_in'])} / {fmt(s['cash_out'])}")
        log.write(f"[yellow]A/R:[/] {fmt(s['ar_balance'])}")

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
                            res = services.export_analytics_preset(preset, rng[0], rng[1])
                            lines = [f"Analytics preset exported: {preset}",
                                     f"Range: {rng[0]} → {rng[1]}",
                                     f"Datasets: {', '.join(res.get('datasets', []))}",
                                     "", "Outputs:"]
                            for k, v in (res.get("outputs") or {}).items():
                                if isinstance(v, list):
                                    for p in v:
                                        lines.append(f"- {k}: {p}")
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
        except Exception as exc:
            show_error(self.app, str(exc))

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

class SettingsTab(VerticalScroll):
    def compose(self):
        yield Static("⚙ " + t("Settings"), classes="title")
        yield Static("", id="lock_msg", classes="warn")
        yield Vertical(id="body")

    def on_mount(self):
        self._render_locked()
        self.set_interval(30, self._check_timeout)

    def _check_timeout(self):
        if not services.is_session_unlocked():
            if self.query_one("#body", Vertical).children:
                try:
                    body = self.query_one("#body", Vertical)
                    for child in list(body.children):
                        if child.id and child.id.startswith("unlocked_"):
                            self._render_locked()
                            return
                except Exception:
                    pass

    def _render_locked(self):
        body = self.query_one("#body", Vertical)
        for w in list(body.children):
            try:
                w.remove()
            except Exception:
                pass
        self.query_one("#lock_msg", Static).update("🔒 Settings locked.")
        body.mount(Button("🔓 " + t("Unlock Settings"), id="unlocked_btn_unlock", variant="primary"))

    async def _render_unlocked(self):
        body = self.query_one("#body", Vertical)
        for w in list(body.children):
            try:
                w.remove()
            except Exception:
                pass
        self.query_one("#lock_msg", Static).update("")

        await body.mount(Button("🔒 " + t("Lock Now"), id="unlocked_btn_lock", variant="warning"))
        await body.mount(Button(t("Save Changes"), id="unlocked_btn_save", variant="success"))

        sections = {}
        for key, spec in SETTINGS_CATALOG.items():
            if spec[1] == "internal":
                continue
            sections.setdefault(spec[3], []).append((key, spec))
        titles = {"property": t("Property Information"), "financial": t("Financial & Tax"),
                  "reservations": t("Reservations"), "inventory": t("Inventory"), "system": t("System")}
        for section_id in ["property", "financial", "reservations", "inventory", "system"]:
            if section_id not in sections:
                continue
            coll = Collapsible(title=titles[section_id],
                               collapsed=(section_id not in ("property", "financial")))
            await body.mount(coll)
            for key, spec in sections[section_id]:
                label, typ, default = spec[:3]
                current = services.get_setting(key, default)
                await coll.mount(Static(label, classes="warn"))
                if len(spec) > 4 and spec[4]:
                    await coll.mount(Static(spec[4], classes="help"))
                if typ == "enum":
                    opts = spec[5] if len(spec) > 5 else []
                    sel = Select([(o, o) for o in opts], id=f"unlocked_set_{key}")
                    await coll.mount(sel)
                    try:
                        sel.value = current
                    except Exception:
                        pass
                elif typ == "bool":
                    cb = Checkbox("Enabled", id=f"unlocked_set_{key}", value=(current == "true"))
                    await coll.mount(cb)
                else:
                    await coll.mount(Input(id=f"unlocked_set_{key}", value=current))

        coll_rp = Collapsible(title=t("Rate Plans"), collapsed=True)
        await body.mount(coll_rp)
        await coll_rp.mount(DataTable(id="unlocked_rp_table"))
        rp_row = Horizontal(classes="button_row")
        await coll_rp.mount(rp_row)
        await rp_row.mount(Button("➕ " + t("Add Item"), id="unlocked_btn_rp_add", variant="primary"))
        await rp_row.mount(Button(t("Edit"), id="unlocked_btn_rp_edit", variant="primary"))
        await rp_row.mount(Button(t("Delete"), id="unlocked_btn_rp_del", variant="error"))

        coll_pr = Collapsible(title=t("Printers"), collapsed=True)
        await body.mount(coll_pr)
        await coll_pr.mount(Input(id="unlocked_set_default_printer",
                                   value=services.get_setting("default_printer", "")))
        pr_row = Horizontal(classes="button_row")
        await coll_pr.mount(pr_row)
        await pr_row.mount(Button(t("Detect Printers"), id="unlocked_btn_pr_detect", variant="primary"))
        await pr_row.mount(Button(t("Test Print"), id="unlocked_btn_pr_test", variant="primary"))
        await coll_pr.mount(DataTable(id="unlocked_printer_table"))

        coll_sec = Collapsible(title=t("Security"), collapsed=True)
        await body.mount(coll_sec)
        await coll_sec.mount(Button("🔒 " + t("Change Password"), id="unlocked_btn_change_pw", variant="warning"))
        await coll_sec.mount(Button("Audit Log", id="unlocked_btn_audit", variant="primary"))

        coll_m = Collapsible(title=t("Maintenance"), collapsed=True)
        await body.mount(coll_m)
        await coll_m.mount(Static(f"DB: {services.db.DB_PATH}", classes="help"))
        try:
            size = os.path.getsize(services.db.DB_PATH)
            await coll_m.mount(Static(f"Size: {size // 1024} KB", classes="help"))
        except Exception:
            pass
        m_row = Horizontal(classes="button_row")
        await coll_m.mount(m_row)
        await m_row.mount(Button(t("Export Settings"), id="unlocked_btn_export", variant="primary"))
        await m_row.mount(Button(t("Import Settings"), id="unlocked_btn_import", variant="primary"))
        await m_row.mount(Button(t("Backup Now"), id="unlocked_btn_backup", variant="success"))
        await m_row.mount(Button("Branding / Animation", id="unlocked_btn_branding", variant="primary"))
        await m_row.mount(Button("Load Demo Data (Guarded)", id="unlocked_btn_demo_data", variant="warning"))

        self._reload_rp()

    def _reload_rp(self):
        try:
            table = self.query_one("#unlocked_rp_table", DataTable)
            table.clear(columns=True)
            table.add_columns("ID", "Name", "Modifier")
            for r in services.get_all_rate_plans():
                table.add_row(str(r["id"]), r["name"], f"{r['price_modifier']:+.2f} EGP")
        except Exception:
            pass

    def _after_unlock(self, ok):
        if ok:
            try:
                self.app.call_later(self._async_unlock)
            except Exception:
                pass

    async def _async_unlock(self):
        try:
            await self._render_unlocked()
        except Exception as exc:
            self.app.notify(str(exc), severity="error")

    async def on_button_pressed(self, e):
        bid = e.button.id
        try:
            if bid == "unlocked_btn_unlock":
                self.app.push_screen(UnlockSettingsModal(), callback=self._after_unlock)
            elif bid == "unlocked_btn_lock":
                services.lock_session()
                self._render_locked()
            elif bid == "unlocked_btn_save":
                self._save_settings()
            elif bid == "unlocked_btn_change_pw":
                self.app.push_screen(ChangePasswordModal())
            elif bid == "unlocked_btn_audit":
                self.app.push_screen(AuditLogScreen())
            elif bid == "unlocked_btn_export":
                path = services.export_settings_to_json()
                self.app.notify(f"Exported: {path}")
            elif bid == "unlocked_btn_import":
                self.app.push_screen(ImportSettingsModal(),
                                     callback=lambda ok: self.app.refresh(recompose=True) if ok else None)
            elif bid == "unlocked_btn_backup":
                path = services.backup_now()
                self.app.notify(f"Backup: {path}")
            elif bid == "unlocked_btn_branding":
                def _after(ok):
                    if ok:
                        try:
                            self.app._apply_branding()
                        except Exception:
                            pass
                        self.app.refresh_all_tables()
                        self.app.notify("Branding updated.")
                self.app.push_screen(BrandingWizardScreen(allow_cancel=True), callback=_after)
            elif bid == "unlocked_btn_demo_data":
                push_modal(self.app, ApplyDemoDataModal(),
                           on_success=lambda: (self.app.refresh_all_tables(), self.app.notify("Demo data preset applied.")))
            elif bid == "unlocked_btn_rp_add":
                push_modal(self.app, RatePlanEditScreen(), on_success=self._reload_rp)
            elif bid == "unlocked_btn_rp_edit":
                pid = selected_id(self.query_one("#unlocked_rp_table", DataTable))
                if pid:
                    push_modal(self.app, RatePlanEditScreen(pid), on_success=self._reload_rp)
            elif bid == "unlocked_btn_rp_del":
                pid = selected_id(self.query_one("#unlocked_rp_table", DataTable))
                if pid:
                    row = self.query_one("#unlocked_rp_table", DataTable).get_row_at(
                        self.query_one("#unlocked_rp_table", DataTable).cursor_row)
                    push_modal(self.app, RatePlanDeleteScreen(pid, row[1]), on_success=self._reload_rp)
            elif bid == "unlocked_btn_pr_detect":
                detected = services.list_printers()
                table = self.query_one("#unlocked_printer_table", DataTable)
                table.clear(columns=True)
                table.add_columns("Queue", "Default")
                for q in detected["queues"]:
                    table.add_row(q, "✓" if q == detected["default"] else "")
                self.app.notify(f"Detected {len(detected['queues'])} printer(s) [{detected['source']}]")
            elif bid == "unlocked_btn_pr_test":
                ok, msg = services.test_print()
                self.app.notify(msg, severity="information" if ok else "error")
        except Exception as exc:
            self.app.notify(str(exc), severity="error")

    def _collect_changes(self):
        changes = {}
        for key, spec in SETTINGS_CATALOG.items():
            if spec[1] == "internal":
                continue
            widget_id = f"#unlocked_set_{key}"
            try:
                if spec[1] == "enum":
                    w = self.query_one(widget_id, Select)
                    new = w.value if isinstance(w.value, str) else spec[2]
                elif spec[1] == "bool":
                    w = self.query_one(widget_id, Checkbox)
                    new = "true" if w.value else "false"
                else:
                    w = self.query_one(widget_id, Input)
                    new = w.value.strip()
                current = services.get_setting(key, spec[2])
                if new != current:
                    changes[key] = (current, new)
            except Exception:
                continue
        return changes

    def _save_settings(self):
        changes = self._collect_changes()
        if not changes:
            return self.app.notify("No changes.", severity="information")
        self.app.push_screen(ReviewChangesModal(changes), callback=self._after_save)

    def _after_save(self, ok):
        if ok:
            try:
                services.set_language(services.get_setting("language", "en"))
                self.app.refresh_all_tables()
            except Exception as exc:
                self.app.notify(str(exc), severity="error")