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

class EditRoomFieldScreen(ProtectedModal):
    CONFIRM_LABEL = "Save"
    def __init__(self, room_id, field_key):
        super().__init__()
        self.room_id = room_id
        self.field_key = field_key
    def compose(self):
        room = services.get_room(self.room_id)
        label = t("Room Type") if self.field_key == "type" else t("Base Price")
        current = room["room_type"] if self.field_key == "type" else str(room["base_price"])
        with VerticalScroll(id="protected_modal"):
            yield Static(f"🔒 {t('Edit')} {label} — Room {room['room_number']}", classes="title")
            yield Static(f"{t('Current')}: {current}", classes="help")
            yield Input(placeholder=label, id="val", value=current)
            yield Input(placeholder=t("Super Password"), id="pw_field", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button(t("Save"), id="btn_do", variant="success")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")
    def perform(self, password):
        val = self.query_one("#val", Input).value.strip()
        if self.field_key == "type":
            services.update_room_type(self.room_id, val, password)
        else:
            services.update_room_price(self.room_id, float(val), password)


class RatePlanEditScreen(ProtectedModal):
    CONFIRM_LABEL = "Save"
    def __init__(self, plan_id=None):
        super().__init__()
        self.plan_id = plan_id
    def compose(self):
        name, modifier = "", 0.0
        if self.plan_id is not None:
            for p in services.get_all_rate_plans():
                if p["id"] == self.plan_id:
                    name, modifier = p["name"], p["price_modifier"]
                    break
        with VerticalScroll(id="protected_modal"):
            yield Static(f"🔒 {t('Rate Plan')}", classes="title")
            yield Input(placeholder="Name", id="name", value=name)
            yield Input(placeholder="Modifier (EGP)", id="mod", value=str(modifier))
            yield Input(placeholder=t("Super Password"), id="pw_field", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button(t("Save"), id="btn_do", variant="success")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")
    def perform(self, password):
        name = self.query_one("#name", Input).value.strip()
        mod = float(self.query_one("#mod", Input).value or 0)
        if self.plan_id is None:
            services.add_rate_plan(name, mod, password)
        else:
            services.update_rate_plan(self.plan_id, name, mod, password)


class RatePlanDeleteScreen(ProtectedModal):
    CONFIRM_LABEL = "Delete"
    CONFIRM_VARIANT = "error"
    def __init__(self, plan_id, plan_name):
        super().__init__()
        self.plan_id = plan_id
        self.plan_name = plan_name
        try:
            self._usage_count = services.count_reservations_using_plan(plan_id)
        except Exception:
            self._usage_count = None
    def compose(self):
        with VerticalScroll(id="protected_modal"):
            yield Static(f"🔒 {t('Delete')}: {self.plan_name}", classes="title")
            if self._usage_count is None:
                yield Static("Could not check reservation usage.", classes="help")
            elif self._usage_count > 0:
                yield Static(f"⚠ Used by {self._usage_count} reservation(s). Deletion will be blocked.", classes="warn")
            else:
                yield Static("No reservations use this rate plan.", classes="help")
            yield Input(placeholder=t("Super Password"), id="pw_field", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button("Delete", id="btn_do", variant="error")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")
    def perform(self, password):
        services.delete_rate_plan(self.plan_id, password)


class GuestEditScreen(ProtectedModal):
    CONFIRM_LABEL = "Save"
    def __init__(self, guest):
        super().__init__()
        self.guest = guest
    def compose(self):
        g = self.guest
        with VerticalScroll(id="protected_modal"):
            yield Static(f"🔒 {t('Edit')}: {g['name']}", classes="title")
            yield Input(placeholder="Name", id="name", value=g["name"])
            yield Input(placeholder="Phone", id="phone", value=g["phone"])
            yield Input(placeholder="Email", id="email", value=g["email"])
            yield Input(placeholder="Notes", id="notes", value=g["notes"])
            yield Input(placeholder=t("Super Password"), id="pw_field", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button(t("Save"), id="btn_do", variant="success")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")
    def perform(self, password):
        services.update_guest(self.guest["id"],
                              self.query_one("#name", Input).value,
                              self.query_one("#phone", Input).value,
                              self.query_one("#email", Input).value,
                              self.query_one("#notes", Input).value, password)


class GuestMergeModal(ProtectedModal):
    TITLE = "Merge Selected"
    CONFIRM_LABEL = "Merge"
    CONFIRM_VARIANT = "warning"
    def body(self):
        guests = services.get_all_guests()
        opts = [(g["canonical_name"], g["id"]) for g in guests]
        yield Select(opts, id="winner", prompt="Winner (keeps)")
        yield Select(opts, id="loser", prompt="Loser (removed)")
    def perform(self, password):
        a = self.query_one("#winner", Select).value
        b = self.query_one("#loser", Select).value
        if not isinstance(a, int) or not isinstance(b, int):
            raise ValueError("Select both guests.")
        moved = services.merge_guests(a, b, password)
        self.notify(f"Merged. {moved} reservations re-linked.")


class GuestCRMModal(ModalScreen):
    BINDINGS = [("escape", "close", "Close")]
    def compose(self):
        with VerticalScroll(id="crm_dialog"):
            yield Static("👥 " + t("Guests") + " — Portfolio", classes="title")
            yield Input(placeholder=t("Search"), id="search")
            yield DataTable(id="table")
            yield Static("", id="dupes", classes="warn")
            with Horizontal(classes="modal_buttons"):
                yield Button("📁 Portfolio", id="btn_hist", variant="primary")
                yield Button(t("Merge Selected"), id="btn_merge", variant="warning")
                yield Button(t("Edit"), id="btn_edit", variant="primary")
                yield Button("Refresh", id="btn_refresh", variant="primary")
                yield Button(t("Close"), id="btn_close", variant="error")
    def on_mount(self):
        self._load()
    def _load(self):
        table = self.query_one("#table", DataTable)
        table.clear(columns=True)
        table.cursor_type = "row"
        table.add_columns("ID", "Name", "Phone", "Stays", "Last Stay", "Total", "Outstanding")
        search = self.query_one("#search", Input).value or ""
        for g in services.get_all_guests(search):
            table.add_row(
                str(g["id"]), str(g.get("canonical_name") or ""), str(g.get("phone") or "-"),
                str(g.get("stays") or 0), str(g.get("last_stay") or "-"),
                f"{float(g.get('total') or 0):.2f}", f"{float(g.get('outstanding') or 0):.2f}",
            )
    def on_input_changed(self, e):
        if e.input.id == "search":
            self._load()
    def on_button_pressed(self, e):
        bid = e.button.id
        if bid == "btn_close":
            self.dismiss()
        elif bid == "btn_refresh":
            try:
                dupes = services.get_duplicate_suggestions()
                if dupes:
                    txt = "⚠ " + t("Duplicate Suggestions") + ":\n"
                    for d in dupes[:5]:
                        txt += f"  {d['name_a']} ≈ {d['name_b']} ({d['score']})\n"
                    self.query_one("#dupes", Static).update(txt)
                else:
                    self.query_one("#dupes", Static).update("")
            except Exception as exc:
                self.notify(str(exc), severity="error")
        elif bid == "btn_hist":
            gid = selected_id(self.query_one("#table", DataTable))
            if gid:
                self.app.push_screen(GuestHistoryModal(gid))
        elif bid == "btn_edit":
            gid = selected_id(self.query_one("#table", DataTable))
            if gid:
                g = services.get_guest_by_id(gid)
                if g:
                    self.app.push_screen(GuestEditScreen(g),
                                         callback=lambda ok: self._load() if ok else None)
        elif bid == "btn_merge":
            self.app.push_screen(GuestMergeModal(),
                                 callback=lambda ok: self._load() if ok else None)
    def action_close(self):
        self.dismiss()


class GuestHistoryModal(ModalScreen):
    """Comprehensive guest portfolio: profile, stats, all trips/reservations, POS activity, payments, invoices."""
    BINDINGS = [("escape", "close", "Close")]
    def __init__(self, guest_id):
        super().__init__()
        self.guest_id = guest_id
        self._portfolio = None

    def compose(self):
        with VerticalScroll(id="folio_dialog"):
            yield Static("Loading guest portfolio...", id="gh_title", classes="title")
            yield Static("", id="gh_summary")
            yield Collapsible(Static("", id="gh_profile"), title="Profile & Top Items", collapsed=False)
            yield Collapsible(DataTable(id="gh_trips"), title="Trips / Reservations (select row for details)", collapsed=False)
            yield Collapsible(DataTable(id="gh_pos"), title="All POS / Activity", collapsed=True)
            yield Collapsible(DataTable(id="gh_pay"), title="All Payments", collapsed=True)
            yield Collapsible(Static("", id="gh_invoices"), title="Invoices (saved files)", collapsed=True)
            yield Collapsible(Static("", id="gh_trip_details"), title="Selected Trip Details", collapsed=True, id="gh_col_detail")
            with Horizontal(classes="modal_buttons"):
                yield Button(t("Close"), id="btn_close", variant="primary")

    def on_mount(self):
        self._load()

    def _load(self):
        try:
            p = services.get_guest_portfolio(self.guest_id)
        except Exception as exc:
            self.query_one("#gh_title", Static).update(f"Guest portfolio error: {exc}")
            return
        self._portfolio = p
        if not p:
            self.query_one("#gh_title", Static).update("Guest not found")
            return
        g = p.get("guest") or {}
        s = p.get("stats") or {}
        name = g.get("name") or g.get("canonical_name") or "?"
        self.query_one("#gh_title", Static).update(f"Guest Portfolio — {name} (#{g.get('id', self.guest_id)})")
        summary = (
            f"Stays: {s.get('stays_total',0)} ({s.get('completed_stays',0)} completed, {s.get('total_nights',0)} nights) | "
            f"Charges: {fmt(s.get('total_charges',0))} | Paid: {fmt(s.get('total_paid',0))} | "
            f"Outstanding: {fmt(s.get('total_outstanding',0))} | POS: {fmt(s.get('pos_gross_total',0))}\n"
            f"First visit: {s.get('first_visit') or '-'} | Last visit: {s.get('last_visit') or '-'} | Favorite room: {s.get('favorite_room') or '-'}"
        )
        self.query_one("#gh_summary", Static).update(summary)
        prof = [f"Phone: {g.get('phone') or '-'}", f"Email: {g.get('email') or '-'}", f"Notes: {g.get('notes') or '-'}"]
        tops = p.get("top_items_overall") or []
        if tops:
            prof.append("Top items overall: " + ", ".join([f"{x['item']} ({fmt(x['gross'])})" for x in tops[:8]]))
        self.query_one("#gh_profile", Static).update("\n".join(prof))

        tt = self.query_one("#gh_trips", DataTable)
        tt.clear(columns=True)
        tt.cursor_type = "row"
        tt.add_columns("ID", "Room", "In", "Out", "N", "Status", "Charges", "Paid", "Due", "POS#", "Inv#")
        for trip in p.get("trips") or []:
            bd = trip.get("balance") or {}
            tt.add_row(
                str(trip.get("id")), str(trip.get("room_number") or "-"),
                str(trip.get("check_in_date") or ""), str(trip.get("check_out_date") or ""),
                str(trip.get("nights") or 0), str(trip.get("status") or ""),
                f"{float(bd.get('total_charges') or 0):.2f}", f"{float(bd.get('total_paid') or 0):.2f}",
                f"{float(bd.get('balance_due') or 0):.2f}",
                str(len(trip.get("pos_active") or trip.get("pos_items") or [])), str(len(trip.get("invoices") or [])),
            )

        pt = self.query_one("#gh_pos", DataTable)
        pt.clear(columns=True)
        pt.add_columns("When", "Res", "Room", "Item", "Lot/Batch", "Exp/Recv", "Qty", "Price", "Tax")
        from app_core.db import connect
        for i in (p.get("all_pos") or [])[:250]:
            try:
                lot = i.get("lot_code") or ""
                bid = i.get("batch_id")
                lot_disp = f"{lot}#{bid}" if lot and bid else (lot or (f"#{bid}" if bid else ""))
            except Exception:
                lot_disp = ""
                bid = None
            extra = "-"
            try:
                exp = i.get("expiry_date")
                recv = i.get("received_at")
                if (not exp or not recv) and bid:
                    b = connect().execute("SELECT expiry_date, received_at FROM inventory_batches WHERE id=?", (bid,)).fetchone()
                    if b:
                        exp = exp or b["expiry_date"]
                        recv = recv or b["received_at"]
                if exp or recv:
                    extra = f"exp:{exp or '-'}/recv:{recv or '-'}"
            except Exception:
                extra = "-"
            try:
                qty = i.get("quantity") or 0
                up = float(i.get("unit_price") or 0)
                tax = float(i.get("tax_amount") or 0)
            except Exception:
                qty, up, tax = 0, 0.0, 0.0
            pt.add_row(
                str(i.get("created_at") or "-"), str(i.get("reservation_id") or "-"), str(i.get("room_number") or "-"),
                str(i.get("item_description") or "-"), lot_disp or "-", extra, str(qty),
                f"{up:.2f}", f"{tax:.2f}",
            )

        payt = self.query_one("#gh_pay", DataTable)
        payt.clear(columns=True)
        payt.add_columns("When", "Res", "Room", "Method", "Type", "Amount")
        for x in (p.get("all_payments") or [])[:250]:
            payt.add_row(
                str(x.get("at") or ""), str(x.get("reservation_id")), str(x.get("room_number") or ""),
                str(x.get("method") or ""), str(x.get("payment_type") or ""), f"{float(x.get('amount') or 0):.2f}",
            )

        inv_lines = []
        for trip in p.get("trips") or []:
            for inv in (trip.get("invoices") or []):
                inv_lines.append(f"Res #{trip.get('id')}: {inv.get('name')}  ({inv.get('modified','')})  [{inv.get('path','')}]")
        self.query_one("#gh_invoices", Static).update("\n".join(inv_lines) if inv_lines else "No saved invoice files for this guest's reservations.")

    def on_data_table_row_selected(self, e):
        if getattr(e.data_table, "id", "") != "gh_trips":
            return
        rid = selected_id(e.data_table)
        if not rid or not self._portfolio:
            return
        trip = next((x for x in (self._portfolio.get("trips") or []) if x.get("id") == rid), None)
        if not trip:
            return
        bd = trip.get("balance") or {}
        lines = [
            f"Reservation #{trip['id']}  Room {trip.get('room_number') or '-'}  {trip.get('check_in_date')} → {trip.get('check_out_date')}  ({trip.get('status')})",
            f"Rate: {fmt(trip.get('agreed_nightly_rate',0))}  Plan: {trip.get('rate_plan') or '-'}  Source: {trip.get('source') or '-'}  Nights: {trip.get('nights',0)}",
            f"Charges {fmt(bd.get('total_charges',0))} (accommodation {fmt(bd.get('accommodation_gross',0))}, POS {fmt(bd.get('pos_gross',0))})  Paid {fmt(bd.get('total_paid',0))}  Due {fmt(bd.get('balance_due',0))}",
            f"Notes: {trip.get('notes') or '-'}",
            "",
            "POS / Activity:",
        ]
        pos_items = trip.get("pos_items") or []
        from app_core.db import connect
        for i in pos_items[:40]:
            tag = " [VOID]" if i.get("voided_at") else ""
            try:
                lot = i.get("lot_code") or ""
                bid = i.get("batch_id")
                lot_disp = f"{lot}#{bid}" if lot and bid else (lot or (f"#{bid}" if bid else ""))
            except Exception:
                lot_disp = ""
                bid = None
            exp_recv = ""
            try:
                exp = i.get("expiry_date")
                recv = i.get("received_at")
                if (not exp or not recv) and bid:
                    b = connect().execute("SELECT expiry_date, received_at FROM inventory_batches WHERE id=?", (bid,)).fetchone()
                    if b:
                        exp = exp or b["expiry_date"]
                        recv = recv or b["received_at"]
                if exp or recv or lot_disp:
                    exp_recv = f" [lot:{lot_disp or '-'} exp:{exp or '-'} recv:{recv or '-'}]"
            except Exception:
                exp_recv = f" [lot:{lot_disp or '-'}]" if lot_disp else ""
            try:
                qty = i.get("quantity") or 0
                up = float(i.get("unit_price") or 0)
                tax = float(i.get("tax_amount") or 0)
                desc = i.get("item_description") or "?"
            except Exception:
                qty, up, tax, desc = 0, 0.0, 0.0, "?"
            lines.append(f"  - {desc} x{qty} @ {fmt(up)} (tax {fmt(tax)}){exp_recv}{tag}")
        if not pos_items:
            lines.append("  (none)")
        lines.append("")
        lines.append("Payments:")
        pays = trip.get("payments") or []
        for pay in pays[:40]:
            tag = " [VOID]" if pay.get("voided_at") else ""
            lines.append(f"  - {pay.get('at')} {pay.get('method')} {pay.get('payment_type') or ''} {fmt(pay.get('amount',0))}{tag}")
        if not pays:
            lines.append("  (none)")
        invs = trip.get("invoices") or []
        lines.append("")
        lines.append("Invoices: " + (", ".join(x.get('name', '') for x in invs) if invs else "(none saved)"))
        self.query_one("#gh_trip_details", Static).update("\n".join(lines))
        try:
            self.query_one("#gh_col_detail", Collapsible).collapsed = False
        except Exception:
            pass

    def on_button_pressed(self, e):
        if e.button.id == "btn_close":
            self.dismiss()

    def action_close(self):
        self.dismiss()


# ═══════════════════════════════════════════════════════════════════════════
# INVOICE / RECEIPT
# ═══════════════════════════════════════════════════════════════════════════

