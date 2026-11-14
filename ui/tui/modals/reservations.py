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
            yield Static("Stock will be restored (to batch/lot if traceable) and COGS reversed.", classes="warn")
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

