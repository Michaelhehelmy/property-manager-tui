"""All modals. UI-only — all logic in services.py."""
import os
from datetime import datetime
from textual.widgets import Static, Button, Input, RichLog, DataTable, Select, Checkbox, Collapsible
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from rich.text import Text
from app_core import services
from app_core.services import t, fmt, selected_id, SETTINGS_CATALOG

try:
    import plotext as _plt
    PLOTEXT_OK = True
except ImportError:  # pragma: no cover - plotext is installed in this environment
    _plt = None  # pragma: no cover
    PLOTEXT_OK = False  # pragma: no cover


# ═══════════════════════════════════════════════════════════════════════════
# BASE
# ═══════════════════════════════════════════════════════════════════════════

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
                yield Button(t("Save"), id="btn_set", variant="success")
                yield Button(t("Quit App"), id="btn_quit", variant="error")
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
                yield Button("Change", id="btn_do", variant="warning")
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

class DeleteReservationScreen(ProtectedModal):
    CONFIRM_LABEL = "Delete Permanently"
    CONFIRM_VARIANT = "error"
    def __init__(self, rid, guest, details):
        super().__init__()
        self.rid = rid
        self.guest = guest
        self.details = details
    def compose(self):
        with VerticalScroll(id="protected_modal"):
            yield Static(f"🔒 {t('Delete')} #{self.rid}", classes="title")
            yield Static(f"{self.guest}\n{self.details}\n\n"
                         "Only 'Reserved' with no payments/POS can be deleted. Others must be cancelled.",
                         classes="warn")
            yield Input(placeholder=t("Super Password"), id="pw_field", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button(t(self.CONFIRM_LABEL), id="btn_do", variant="error")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")
    def perform(self, password):
        services.delete_reservation(self.rid, password)


class ModifyReservationScreen(ProtectedModal):
    CONFIRM_LABEL = "Save"
    def __init__(self, rid, rate, ci, co, room_id):
        super().__init__()
        self.rid = rid
        self.rate = rate
        self.ci = ci
        self.co = co
        self.room_id = room_id
    def compose(self):
        rooms = services.get_all_rooms()
        opts = [(f"{r['room_number']} - {r['room_type']}", r["id"]) for r in rooms]
        with VerticalScroll(id="protected_modal"):
            yield Static(f"🔒 {t('Modify')} #{self.rid}", classes="title")
            yield Input(placeholder=t("Agreed Nightly Rate"), id="rate", value=str(self.rate))
            yield Input(placeholder=t("Check-In Date"), id="ci", value=self.ci)
            yield Input(placeholder=t("Check-Out Date"), id="co", value=self.co)
            yield Select(opts, id="room", prompt=t("Room"))
            yield Input(placeholder=t("Super Password"), id="pw_field", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button(t("Save"), id="btn_do", variant="success")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")
    def on_mount(self):
        try:
            self.query_one("#room", Select).value = self.room_id
        except Exception:
            pass
    def perform(self, password):
        rid_room = self.query_one("#room", Select).value
        if not isinstance(rid_room, int):
            raise ValueError("Select a room.")
        services.modify_reservation(self.rid, float(self.query_one("#rate", Input).value),
                                    self.query_one("#ci", Input).value,
                                    self.query_one("#co", Input).value,
                                    rid_room, password)


class VoidItemScreen(ProtectedModal):
    CONFIRM_LABEL = "Void"
    CONFIRM_VARIANT = "error"
    def __init__(self, pos_id):
        super().__init__()
        self.pos_id = pos_id
    def compose(self):
        with VerticalScroll(id="protected_modal"):
            yield Static(f"🔒 {t('Void')} POS #{self.pos_id}", classes="title")
            yield Static("Stock will be restored and COGS reversed.", classes="warn")
            yield Input(placeholder=t("Super Password"), id="pw_field", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button("Void", id="btn_do", variant="error")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")
    def perform(self, password):
        services.void_pos_item(self.pos_id, password)


class CancelConfirmScreen(ProtectedModal):
    CONFIRM_VARIANT = "error"

    def __init__(self, rid, mode):
        super().__init__()
        self.rid = rid
        self.mode = mode

    def compose(self):
        label = t("Cancel Reservation") if self.mode == "cancel" else t("No-Show")
        est = services.estimate_cancellation_outcome(self.rid, self.mode) or {}
        policy = est.get("policy", "full_refund")
        paid_amt = est.get("total_paid", 0) or 0
        refund = est.get("estimated_refund", 0) or 0
        kept = est.get("kept_amount", 0) or 0
        with VerticalScroll(id="protected_modal"):
            yield Static(f"🔒 {label} #{self.rid}", classes="title")
            yield Static(
                f"Policy: {policy}. Paid: {fmt(paid_amt)}. Kept: {fmt(kept)}. "
                f"Estimated refund: {fmt(refund)}.\n"
                "Charges reversed. POS voided. Stock restored.",
                classes="warn",
            )
            yield Input(placeholder=t("Super Password"), id="pw_field", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button(t("Confirm"), id="btn_do", variant="error")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")

    def perform(self, password):
        if self.mode == "cancel":
            services.cancel_reservation(self.rid, password)
        else:
            services.mark_no_show(self.rid, password)


class RefundScreen(ProtectedModal):
    TITLE = "Refund"
    def __init__(self, rid=None):
        super().__init__()
        self.rid = rid
    def compose(self):
        balance = services.get_balance_breakdown(self.rid) if self.rid else None
        max_refund = (balance or {}).get("total_paid", 0) if balance else 0
        with VerticalScroll(id="protected_modal"):
            yield Static("🔒 " + t("Refund"), classes="title")
            yield Input(placeholder=t("Reservation ID for Billing"),
                        id="rid", value=str(self.rid) if self.rid else "")
            yield Static(f"Max refund: {fmt(max_refund)}", classes="help")
            yield Input(placeholder=t("Amount"), id="amount")
            yield Select([(m, m) for m in ("Cash", "Card", "OTA", "Bank Transfer")],
                         id="method", prompt=t("Method"))
            yield Input(placeholder=t("Notes"), id="notes")
            yield Input(placeholder=t("Super Password"), id="pw_field", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button(t("Confirm"), id="btn_do", variant="success")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")
    def perform(self, password):
        rid = int(self.query_one("#rid", Input).value)
        method = self.query_one("#method", Select).value
        if not isinstance(method, str):
            raise ValueError("Select a method.")
        services.record_refund(rid, float(self.query_one("#amount", Input).value),
                               method, self.query_one("#notes", Input).value, password)


class OverrideCheckoutModal(ProtectedModal):
    TITLE = "Unpaid Balance"
    CONFIRM_LABEL = "Override & Checkout"
    CONFIRM_VARIANT = "warning"
    def __init__(self, rid, balance):
        super().__init__()
        self.rid = rid
        self.balance = balance
    def compose(self):
        with VerticalScroll(id="protected_modal"):
            yield Static("🔒 " + t("Unpaid Balance"), classes="title")
            yield Static(f"Guest owes {fmt(self.balance)}. Override will be logged to audit trail.",
                         classes="warn")
            yield Input(placeholder=t("Super Password"), id="pw_field", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button(t("Override & Checkout"), id="btn_do", variant="warning")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")
    def perform(self, password):
        services.checkout_reservation(self.rid, override=True, password=password)


# ═══════════════════════════════════════════════════════════════════════════
# INVENTORY
# ═══════════════════════════════════════════════════════════════════════════

class InventoryEditScreen(ModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]
    def __init__(self, mode, item=None):
        super().__init__()
        self.mode = mode
        self.item = item or {}
    def compose(self):
        it = self.item
        low_default = it.get("low_stock_threshold") if it.get("low_stock_threshold") is not None else services.get_setting("low_stock_threshold", "5")
        over_default = it.get("overstock_threshold") if it.get("overstock_threshold") is not None else services.get_setting("overstock_threshold", "100")
        with VerticalScroll(id="inv_edit_dialog"):
            yield Static(t("Add Item") if self.mode == "add" else t("Edit"), classes="title")
            yield Input(placeholder="Item Name", id="name", value=it.get("item_name", ""))
            yield Input(placeholder="Category", id="cat", value=it.get("category", ""))
            yield Input(placeholder="Unit", id="unit", value=it.get("unit", ""))
            if self.mode == "add":
                yield Input(placeholder="Stock Qty", id="stock", value="0")
            yield Input(placeholder="Unit Cost", id="cost", value=str(it.get("unit_cost", 0)))
            yield Input(placeholder="Selling Price", id="sell", value=str(it.get("selling_price", 0)))
            yield Input(placeholder="Low Stock Threshold", id="low", value=str(low_default))
            yield Input(placeholder="Overstock Threshold", id="over", value=str(over_default))
            yield Input(placeholder=t("Super Password"), id="pw", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button(t("Save"), id="btn_save", variant="success")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")
    def on_button_pressed(self, e):
        if e.button.id == "btn_save":
            try:
                pw = self.query_one("#pw", Input).value
                sku = self.query_one("#sku", Input).value
                barcode = self.query_one("#barcode", Input).value
                barcode_type = self.query_one("#barcode_type", Input).value or "code128"
                track_batches = self.query_one("#track_batches", Input).value or "0"
                sld = self.query_one("#shelf_life_days", Input).value.strip()
                shelf_life_days = int(sld) if sld not in ("", "None", "null") else None
                default_supplier = self.query_one("#default_supplier", Input).value
                if self.mode == "add":
                    services.add_inventory_item(
                        self.query_one("#name", Input).value,
                        self.query_one("#cat", Input).value,
                        self.query_one("#unit", Input).value,
                        float(self.query_one("#stock", Input).value or 0),
                        float(self.query_one("#cost", Input).value or 0),
                        float(self.query_one("#sell", Input).value or 0),
                        float(self.query_one("#low", Input).value or 5),
                        float(self.query_one("#over", Input).value or 100), pw,
                        sku=sku, barcode=barcode, barcode_type=barcode_type,
                        track_batches=track_batches, shelf_life_days=shelf_life_days, default_supplier=default_supplier)
                else:
                    services.update_inventory_item(
                        self.item["id"],
                        self.query_one("#name", Input).value,
                        self.query_one("#cat", Input).value,
                        self.query_one("#unit", Input).value,
                        float(self.query_one("#sell", Input).value or 0),
                        float(self.query_one("#low", Input).value or 5),
                        float(self.query_one("#over", Input).value or 100), pw,
                        sku=sku, barcode=barcode, barcode_type=barcode_type,
                        track_batches=track_batches, shelf_life_days=shelf_life_days, default_supplier=default_supplier)
                self.dismiss(True)
            except PermissionError:
                self.notify(t("Wrong super password."), severity="error")
            except Exception as exc:
                self.notify(str(exc), severity="error")
        elif e.button.id == "btn_cancel":
            self.dismiss(False)
    def action_cancel(self):
        self.dismiss(False)


class InventoryDeleteScreen(ProtectedModal):
    CONFIRM_LABEL = "Delete"
    CONFIRM_VARIANT = "error"
    def __init__(self, item_id, item_name):
        super().__init__()
        self.item_id = item_id
        self.item_name = item_name
    def compose(self):
        with VerticalScroll(id="protected_modal"):
            yield Static(f"🔒 {t('Delete')}: {self.item_name}", classes="title")
            yield Static("Only zero-stock items can be deleted. Moves to soft-delete (hidden).",
                         classes="warn")
            yield Input(placeholder=t("Super Password"), id="pw_field", password=True)
            with Horizontal(classes="modal_buttons"):
                yield Button("Delete", id="btn_do", variant="error")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")
    def perform(self, password):
        services.delete_inventory_item(self.item_id, password)


class QtyInputScreen(ModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]
    def __init__(self, item_name, default_qty=1):
        super().__init__()
        self.item_name = item_name
        self.default_qty = default_qty
    def compose(self):
        with Vertical(id="protected_modal"):
            yield Static(f"{t('Quantity')}: {self.item_name}", classes="title")
            yield Input(placeholder=t("Quantity"), id="qty", value=str(self.default_qty))
            with Horizontal(classes="modal_buttons"):
                yield Button(t("Add"), id="btn_ok", variant="success")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")
    def on_button_pressed(self, e):
        if e.button.id == "btn_ok":
            try:
                qty = int(self.query_one("#qty", Input).value)
                if qty <= 0:
                    raise ValueError("Must be a positive integer.")
                self.dismiss(qty)
            except ValueError:
                self.notify("Enter a positive integer.", severity="error")
        elif e.button.id == "btn_cancel":
            self.dismiss(None)
    def action_cancel(self):
        self.dismiss(None)


# ═══════════════════════════════════════════════════════════════════════════
# EXPENSES
# ═══════════════════════════════════════════════════════════════════════════

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
        pt.add_columns("When", "Res", "Room", "Item", "Qty", "Price", "Tax")
        for i in (p.get("all_pos") or [])[:250]:
            pt.add_row(
                str(i.get("created_at") or ""), str(i.get("reservation_id")), str(i.get("room_number") or ""),
                str(i.get("item_description") or ""), str(i.get("quantity") or 0),
                f"{float(i.get('unit_price') or 0):.2f}", f"{float(i.get('tax_amount') or 0):.2f}",
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
        for i in pos_items[:40]:
            tag = " [VOID]" if i.get("voided_at") else ""
            lines.append(f"  - {i.get('item_description')} x{i.get('quantity')} @ {fmt(i.get('unit_price',0))} (tax {fmt(i.get('tax_amount',0))}){tag}")
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

class InvoiceScreen(ModalScreen):
    BINDINGS = [("escape", "close_modal", "Close")]
    def __init__(self, reservation_id, save=False):
        super().__init__()
        self.reservation_id = reservation_id
        self.save = save
        self.currency = "EGP"
        self.texts = {}
        self.paths = {}
    def compose(self):
        with Vertical(id="invoice_dialog"):
            yield Static(f"{t('Guest Invoice')} — #{self.reservation_id}", classes="title")
            yield RichLog(id="invoice_body", highlight=False, markup=False)
            yield Static("", id="path_info")
            with Horizontal(classes="modal_buttons"):
                yield Button(t("Show in USD"), id="btn_cur", variant="primary")
                yield Button("🖨 " + t("Print Invoice"), id="btn_print", variant="success")
                yield Button(t("Close"), id="btn_close", variant="error")
    def on_mount(self):
        self._show("EGP")
    def _show(self, currency):
        if currency not in self.texts:
            self.texts[currency] = services.generate_invoice_text(self.reservation_id, currency)
            if self.save:
                self.paths[currency] = services.save_invoice_file(
                    self.texts[currency], self.reservation_id, currency)
        self.currency = currency
        log = self.query_one("#invoice_body", RichLog)
        log.clear()
        for ln in self.texts[currency].split("\n"):
            log.write(ln)
        path = self.paths.get(currency, "(not saved)")
        self.query_one("#path_info", Static).update(f"Saved: {path}")
        btn = self.query_one("#btn_cur", Button)
        btn.label = t("Show in EGP") if currency == "USD" else t("Show in USD")
    def on_button_pressed(self, e):
        if e.button.id == "btn_cur":
            self._show("USD" if self.currency == "EGP" else "EGP")
        elif e.button.id == "btn_print":
            try:
                if self.currency not in self.paths:
                    self.paths[self.currency] = services.save_invoice_file(
                        self.texts[self.currency], self.reservation_id, self.currency)
                ok, msg = services.print_to_printer(self.paths[self.currency])
                self.notify(msg, severity="information" if ok else "error")
            except Exception as exc:
                self.notify(str(exc), severity="error")
        elif e.button.id == "btn_close":
            self.dismiss()
    def action_close_modal(self):
        self.dismiss()


class WalkInReceiptScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Close")]
    def __init__(self, items, total, method, receipt_text=None, receipt_path=None, payment_id=None):
        super().__init__()
        self.items = items
        self.total = total
        self.method = method
        self._provided_text = receipt_text
        self._provided_path = receipt_path
        self.payment_id = payment_id
    def compose(self):
        with Vertical(id="invoice_dialog"):
            yield Static(t("Receipt"), classes="title")
            yield RichLog(id="body", highlight=False, markup=False)
            with Horizontal(classes="modal_buttons"):
                yield Button("🖨 " + t("Print"), id="btn_print", variant="success")
                yield Button(t("Close"), id="btn_close", variant="error")
    def on_mount(self):
        if self._provided_text:
            text = self._provided_text
            self._path = self._provided_path or ""
        else:
            try:
                text = services.generate_walkin_receipt_text(self.items, self.total, self.method, payment_id=self.payment_id)
                self._path = services.save_walkin_receipt_file(text, self.payment_id or 0)
            except Exception:
                W = 48
                display_name = services.get_app_display_name().upper()
                lines = ["=" * W, f"{display_name} — WALK-IN".center(W), "=" * W,
                         datetime.now().strftime("%Y-%m-%d %H:%M"),
                         "-" * W]
                for it in self.items:
                    lines.append(f"  {it['desc']}  x{it['qty']}  @ {it['price']:.2f}  = {it['gross']:.2f}")
                lines += ["-" * W, f"  TOTAL: {self.total:.2f} EGP",
                          f"  Paid by: {self.method}", "=" * W,
                          "", "Thank you!", "=" * W]
                text = "\n".join(lines)
                try:
                    self._path = services.save_walkin_receipt_file(text, 0)
                except Exception:
                    self._path = services.save_invoice_file(text, 0, "EGP")
        log = self.query_one("#body", RichLog)
        log.clear()
        for ln in text.split("\n"):
            log.write(ln)
    def on_button_pressed(self, e):
        if e.button.id == "btn_print":
            try:
                ok, msg = services.print_to_printer(self._path)
                self.notify(msg, severity="information" if ok else "error")
            except Exception as exc:
                self.notify(str(exc), severity="error")
        elif e.button.id == "btn_close":
            self.dismiss()
    def action_close(self):
        self.dismiss()


# ═══════════════════════════════════════════════════════════════════════════
# REPORTS / CHART / FOLIO
# ═══════════════════════════════════════════════════════════════════════════

class ReportScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Close")]
    def __init__(self, title, text):
        super().__init__()
        self.report_title = title
        self.report_text = text
    def compose(self):
        with Vertical(id="report_dialog"):
            yield Static(self.report_title, classes="title")
            yield RichLog(id="report_body", highlight=False, markup=False)
            with Horizontal(classes="modal_buttons"):
                yield Button(t("Close"), id="btn_close", variant="primary")
    def on_mount(self):
        log = self.query_one("#report_body", RichLog)
        log.clear()
        for ln in self.report_text.split("\n"):
            log.write(ln)
    def on_button_pressed(self, e):
        if e.button.id == "btn_close":
            self.dismiss()
    def action_close(self):
        self.dismiss()


class ChartScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Close")]
    def __init__(self, chart_type="revenue", start=None, end=None):
        super().__init__()
        self.chart_type = chart_type
        now = datetime.now()
        self.start = start or now.replace(day=1).strftime("%Y-%m-%d")
        self.end = end or now.strftime("%Y-%m-%d")
    def compose(self):
        with Vertical(id="chart_dialog"):
            yield Static("📊 " + t("Chart"), classes="title")
            yield Select(
                options=[("Revenue vs Expense", "revenue"), ("Profit", "profit"),
                         ("Payment Methods", "payments"), ("Occupancy", "occupancy"),
                         ("Occupancy Heatmap", "heatmap")],
                id="type", prompt=t("Chart"), value=self.chart_type)
            yield RichLog(id="chart_body", highlight=False, markup=False)
            with Horizontal(classes="modal_buttons"):
                yield Button(t("Close"), id="btn_close", variant="primary")
    def on_mount(self):
        self._draw_chart()
    def on_select_changed(self, e):
        if e.select.id == "type" and isinstance(e.value, str):
            self.chart_type = e.value
            self._draw_chart()
    def _draw_chart(self):
        log = self.query_one("#chart_body", RichLog)
        log.clear()
        if not PLOTEXT_OK:
            log.write("Install plotext: pip install plotext")
            return
        try:
            plt = _plt
            plt.clf()
            try:
                plt.theme("dark")
            except Exception:
                pass
            if self.chart_type == "revenue":
                s = services.get_daily_revenue_expense_series(self.start, self.end)
                if not s or not any(r or e for _, r, e in s):
                    plt.title("No data")
                else:
                    dates = [d[5:] for d, _, _ in s]
                    plt.plot(dates, [r for _, r, _ in s], color="green", label="Revenue")
                    plt.plot(dates, [e for _, _, e in s], color="red", label="Expense")  # pragma: no cover - plotext rendering may raise in headless env
                    plt.title("Revenue vs Expense")  # pragma: no cover - follows plotext rendering path in headless env
            elif self.chart_type == "profit":
                s = services.get_daily_profit_series(self.start, self.end)
                if not s or not any(v for _, v in s):
                    plt.title("No data")
                else:
                    plt.bar([d[5:] for d, _ in s], [v for _, v in s], color="cyan")
                    plt.title("Daily Profit")
            elif self.chart_type == "payments":
                d = services.get_payment_method_dict(self.start, self.end)
                if not d:
                    plt.title("No data")
                else:
                    plt.bar(list(d.keys()), list(d.values()), color="blue")
                    plt.title("Payment Methods")
            elif self.chart_type == "occupancy":
                s = services.get_daily_occupancy_series(self.start, self.end)
                if not s or not any(v for _, v in s):
                    plt.title("No data")
                else:
                    plt.plot([d[5:] for d, _ in s], [v for _, v in s], color="yellow")
                    plt.title("Occupancy %")  # pragma: no cover - plotext rendering may raise in headless env
            elif self.chart_type == "heatmap":
                data = services.get_occupancy_heatmap_data(self.start, self.end)
                m = data.get("matrix") or []
                if not m or not any(any(row) for row in m):
                    plt.title("No data")
                else:
                    plt.matrix_plot(m)
                    plt.title("Occupancy heatmap")
            text = plt.build()
            for line in text.split("\n"):
                try:
                    log.write(Text.from_ansi(line))
                except Exception:  # pragma: no cover - Text.from_ansi failure path destabilizes headless harness
                    log.write(line)  # pragma: no cover
        except Exception as exc:
            log.write(f"Chart error: {exc}")
    def on_button_pressed(self, e):
        if e.button.id == "btn_close":
            self.dismiss()
    def action_close(self):
        self.dismiss()


class ViewFolioScreen(ModalScreen):
    BINDINGS = [("escape", "cancel", "Close")]
    def __init__(self, rid):
        super().__init__()
        self.rid = rid
    def compose(self):
        with Vertical(id="folio_dialog"):
            yield Static(f"{t('View Folio')} — #{self.rid}", classes="title")
            yield DataTable(id="table")
            with Horizontal(classes="modal_buttons"):
                yield Button(t("Void"), id="btn_void", variant="error")
                yield Button(t("Close"), id="btn_close", variant="primary")
    def on_mount(self):
        self._load()
    def _load(self):
        table = self.query_one("#table", DataTable)
        table.clear(columns=True)
        table.add_columns("ID", t("Item"), t("Qty"), t("Price"), "Tax", "Gross")
        for p in services.get_pos_items(self.rid):
            gross = p["quantity"] * p["unit_price"] + p["tax_amount"]
            table.add_row(str(p["id"]), p["item_description"], str(p["quantity"]),
                          f"{p['unit_price']:.2f}", f"{p['tax_amount']:.2f}", f"{gross:.2f}")
    def on_button_pressed(self, e):
        if e.button.id == "btn_void":
            pos_id = selected_id(self.query_one("#table", DataTable))
            if pos_id:
                def cb(ok):
                    if ok:
                        self._load()
                        self.app.refresh_all_tables()
                self.app.push_screen(VoidItemScreen(pos_id), callback=cb)
        elif e.button.id == "btn_close":
            self.dismiss()
    def action_cancel(self):
        self.dismiss()


# ═══════════════════════════════════════════════════════════════════════════
# SETTINGS MODALS
# ═══════════════════════════════════════════════════════════════════════════

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
                yield Button("Save Setup", id="btn_save", variant="success")
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

