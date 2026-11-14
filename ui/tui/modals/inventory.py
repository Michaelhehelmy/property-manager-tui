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
            yield Input(placeholder="Item Name", id="name", value=str(it.get("item_name", "") or ""))
            yield Input(placeholder="SKU", id="sku", value=str(it.get("sku", "") or ""))
            yield Input(placeholder="Barcode", id="barcode", value=str(it.get("barcode", "") or ""))
            yield Input(placeholder="Barcode Type (e.g. code128)", id="barcode_type", value=str(it.get("barcode_type", "") or "code128"))
            yield Input(placeholder="Category", id="cat", value=str(it.get("category", "") or ""))
            yield Input(placeholder="Unit", id="unit", value=str(it.get("unit", "") or ""))
            if self.mode == "add":
                yield Input(placeholder="Stock Qty", id="stock", value="0")
            yield Input(placeholder="Unit Cost", id="cost", value=str(it.get("unit_cost", 0) or 0))
            yield Input(placeholder="Selling Price", id="sell", value=str(it.get("selling_price", 0) or 0))
            yield Input(placeholder="Low Stock Threshold", id="low", value=str(low_default))
            yield Input(placeholder="Overstock Threshold", id="over", value=str(over_default))
            yield Input(placeholder="Track Batches/Lots (1=yes, 0=no)", id="track_batches", value=str(it.get("track_batches", 0) or 0))
            sld = it.get("shelf_life_days")
            yield Input(placeholder="Shelf Life Days (blank if none)", id="shelf_life_days", value="" if sld in (None, "") else str(sld))
            yield Input(placeholder="Default Supplier", id="default_supplier", value=str(it.get("default_supplier", "") or ""))
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


class BatchSelectScreen(ModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, item_id, item_name, qty=1):
        super().__init__()
        self.item_id = item_id
        self.item_name = item_name
        self.qty = qty
        self._batches = []

    def compose(self):
        with VerticalScroll(id="protected_modal"):
            yield Static(f"Lot/Batch Selection — {self.item_name}", classes="title")
            yield Static("Select a specific batch/lot or use Auto-FEFO (oldest expiry first).", classes="help")
            yield DataTable(id="batch_sel_table")
            with Horizontal(classes="modal_buttons"):
                yield Button("Auto-FEFO", id="btn_auto", variant="primary")
                yield Button(t("Select Batch"), id="btn_sel", variant="success")
                yield Button(t("Cancel"), id="btn_cancel", variant="error")

    def on_mount(self):
        try:
            self._batches = services.list_inventory_batches(item_id=self.item_id, include_depleted=False) or []
        except Exception:
            self._batches = []
        t = self.query_one("#batch_sel_table", DataTable)
        t.clear(columns=True)
        t.cursor_type = "row"
        t.add_columns("ID", "Lot", "Received", "Expiry", "Rem", "Cost", "State")
        warn = int(services.get_setting("expiry_warn_days", "7") or 7)
        for b in self._batches:
            exp = b.get("expiry_date") or "-"
            st = b.get("expiry_state") or b.get("status") or ""
            try:
                if b.get("expiry_state") == "expired":
                    st = f"EXPIRED"
                elif b.get("expiry_state") == "expiring" or (b.get("expiry_date") and b.get("days_to_expiry") is not None and int(b.get("days_to_expiry")) <= warn):
                    st = f"expiring({b.get('days_to_expiry')})"
            except Exception:
                pass
            try:
                rem = float(b.get("quantity_remaining") or 0)
            except Exception:
                rem = 0
            t.add_row(str(b.get("id")), str(b.get("lot_code", "")), str(b.get("received_at", "")),
                      str(exp), f"{rem:g}", f"{float(b.get('unit_cost') or 0):.2f}", str(st))
        if t.row_count == 0:
            self.query_one("#batch_sel_table", Static) if False else None

    def on_button_pressed(self, e):
        if e.button.id == "btn_auto":
            # Auto-FEFO: service allocates oldest-expiry lots at sale time; show context from available batches.
            if not self._batches:
                self.notify("No active batches; auto-FEFO will use service allocation rules.", severity="warning")
            else:
                self.notify(f"Auto-FEFO selected ({len(self._batches)} batch(es) available). Oldest expiry first.")
            self.dismiss({"mode": "auto", "batch_id": None, "lot_code": None})
            return
        elif e.button.id == "btn_sel":
            try:
                t = self.query_one("#batch_sel_table", DataTable)
                bid = selected_id(t)
                if bid is None and self._batches:
                    bid = self._batches[0].get("id")
                if bid is None:
                    self.notify("No batch selected.", severity="error")
                    return
                b = next((x for x in self._batches if str(x.get("id")) == str(bid)), None)
                lot = (b.get("lot_code") if b else None) or None
                self.dismiss({"mode": "specific", "batch_id": int(bid), "lot_code": lot})
                return
            except Exception as exc:
                self.notify(str(exc), severity="error")
                return
        elif e.button.id == "btn_cancel":
            self.dismiss(None)
            return

    def action_cancel(self):
        self.dismiss(None)


# ═══════════════════════════════════════════════════════════════════════════
# EXPENSES
# ═══════════════════════════════════════════════════════════════════════════

