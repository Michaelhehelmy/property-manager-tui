from __future__ import annotations
import os
from datetime import datetime
from textual.widgets import Static, Button, Input, RichLog, DataTable, Select, Checkbox, Collapsible
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from rich.text import Text
from app_core import services
from app_core.services import t, fmt, selected_id, SETTINGS_CATALOG
from . import common as _common
from .base import ErrorModal, ConfirmModal, ProtectedModal

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
            try:
                text = services.generate_invoice_text(self.reservation_id, currency) or ""
            except Exception as exc:
                text = f"Invoice unavailable: {exc}"
            # Augment with batch/lot/expiry/received where available (defensive; service already includes traceability when present)
            try:
                from app_core.db import connect
                conn = connect()
                pos_items = conn.execute("""
                    SELECT p.item_description, p.quantity, p.lot_code, p.batch_id
                    FROM pos_tabs p WHERE p.reservation_id=? AND p.voided_at IS NULL
                """, (self.reservation_id,)).fetchall()
                trace = []
                for p in pos_items:
                    try:
                        lot = p["lot_code"] or ""
                        bid = p["batch_id"]
                        if not lot and not bid:
                            continue
                        lot_disp = f"{lot}#{bid}" if lot and bid else (lot or f"#{bid}")
                        exp = recv = None
                        if bid:
                            try:
                                b = conn.execute("SELECT expiry_date, received_at FROM inventory_batches WHERE id=?", (bid,)).fetchone()
                                if b:
                                    exp = b["expiry_date"]
                                    recv = b["received_at"]
                            except Exception:
                                pass
                        desc = p["item_description"] or "Item"
                        qty = p["quantity"] if "quantity" in p.keys() else "?"
                        trace.append(f"  - {desc} x{qty}: lot {lot_disp} | exp {exp or '-'} | recv {recv or '-'}")
                    except Exception:
                        continue
                if trace and "batch traceability" not in (text or "").lower():
                    text = (text or "").rstrip() + "\n\nBATCH TRACEABILITY\n" + "\n".join(trace) + "\n" + "=" * 60
            except Exception:
                pass
            self.texts[currency] = text
            if self.save:
                try:
                    self.paths[currency] = services.save_invoice_file(self.texts[currency], self.reservation_id, currency)
                except Exception as exc:
                    self.paths[currency] = f"(not saved: {exc})"
        self.currency = currency
        try:
            log = self.query_one("#invoice_body", RichLog)
            log.clear()
            for ln in (self.texts.get(currency) or "(no invoice text)").split("\n"):
                log.write(ln)
        except Exception:
            pass
        path = self.paths.get(currency, "(not saved)")
        try:
            self.query_one("#path_info", Static).update(f"Saved: {path}")
        except Exception:
            pass
        try:
            btn = self.query_one("#btn_cur", Button)
            btn.label = t("Show in EGP") if currency == "USD" else t("Show in USD")
        except Exception:
            pass
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
        self.items = items or []
        self.total = total
        self.method = method
        self._provided_text = receipt_text
        self._provided_path = receipt_path
        self.payment_id = payment_id
    def compose(self):
        with Vertical(id="invoice_dialog"):
            yield Static(t("Receipt"), classes="title")
            yield Static("Traceability (lot/batch, expiry, received) shown per line when available.", classes="help")
            yield RichLog(id="body", highlight=False, markup=False)
            with Horizontal(classes="modal_buttons"):
                yield Button("🖨 " + t("Print"), id="btn_print", variant="success")
                yield Button(t("Close"), id="btn_close", variant="error")
    def on_mount(self):
        text = self._provided_text or ""
        self._path = self._provided_path or ""
        if not text:
            try:
                text = services.generate_walkin_receipt_text(self.items, self.total, self.method, payment_id=self.payment_id)
                self._path = services.save_walkin_receipt_file(text, self.payment_id or 0)
            except Exception:
                try:
                    W = 48
                    display_name = (services.get_app_display_name() or "Property Manager").upper()
                except Exception:
                    W, display_name = 48, "WALK-IN"
                lines = ["=" * W, f"{display_name} — WALK-IN".center(W), "=" * W,
                         datetime.now().strftime("%Y-%m-%d %H:%M"), "-" * W]
                for it in self.items:
                    try:
                        desc = it.get("desc") or it.get("item_description") or "Item"
                        qty = it.get("qty", it.get("quantity", 0))
                        price = float(it.get("price", it.get("unit_price", 0)) or 0)
                        gross = float(it.get("gross") or (float(qty or 0) * price))
                        lines.append(f"  {desc}  x{qty}  @ {price:.2f}  = {gross:.2f}")
                    except Exception:
                        lines.append("  (item unavailable)")
                try:
                    total = float(self.total or 0)
                except Exception:
                    total = 0.0
                lines += ["-" * W, f"  TOTAL: {total:.2f} EGP", f"  Paid by: {self.method or '-'}", "=" * W, "", "Thank you!", "=" * W]
                text = "\n".join(lines)
                try:
                    self._path = services.save_walkin_receipt_file(text, self.payment_id or 0)
                except Exception:
                    try:
                        self._path = services.save_invoice_file(text, 0, "EGP")
                    except Exception:
                        self._path = ""
        # UI-only traceability augmentation for display (services text may omit batch details)
        try:
            trace_lines = []
            from app_core.db import connect
            for it in (self.items or []):
                bid = (it or {}).get("batch_id")
                lot = (it or {}).get("lot_code") or ""
                exp = (it or {}).get("expiry_date") or (it or {}).get("batch_expiry_date")
                recv = (it or {}).get("received_at") or (it or {}).get("batch_received_at")
                if not (bid or lot or exp or recv):
                    continue
                lot_disp = f"{lot}#{bid}" if lot and bid else (lot or (f"#{bid}" if bid else "-"))
                if bid and (not exp or not recv):
                    try:
                        b = connect().execute("SELECT expiry_date, received_at FROM inventory_batches WHERE id=?", (bid,)).fetchone()
                        if b:
                            exp = exp or b["expiry_date"]
                            recv = recv or b["received_at"]
                    except Exception:
                        pass
                desc = (it or {}).get("desc") or (it or {}).get("item_description") or "Item"
                trace_lines.append(f"  - {desc}: lot {lot_disp} | exp {exp or '-'} | recv {recv or '-'}")
            if trace_lines and "batch traceability:" not in (text or "").lower():
                text = (text or "").rstrip() + "\n\nBatch traceability:\n" + "\n".join(trace_lines)
        except Exception:
            pass
        try:
            log = self.query_one("#body", RichLog)
            log.clear()
            for ln in (text or "(no receipt text)").split("\n"):
                log.write(ln)
        except Exception as exc:
            self.notify(str(exc), severity="error")
    def on_button_pressed(self, e):
        if e.button.id == "btn_print":
            try:
                if not getattr(self, "_path", ""):
                    self.notify("No printable receipt file available.", severity="warning")
                    return
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
        if not _common.PLOTEXT_OK:
            log.write("Install plotext: pip install plotext")
            return
        try:
            plt = _common._plt
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
            yield Static("Traceability: lot/batch and expiry/received shown when available (FEFO allocations appear as specific batch lines).", classes="help")
            yield DataTable(id="table")
            with Horizontal(classes="modal_buttons"):
                yield Button(t("Void"), id="btn_void", variant="error")
                yield Button(t("Close"), id="btn_close", variant="primary")
    def on_mount(self):
        self._load()
    def _load(self):
        table = self.query_one("#table", DataTable)
        table.clear(columns=True)
        table.cursor_type = "row"
        table.add_columns("ID", t("Item"), "Lot/Batch", "Exp/Recv", t("Qty"), t("Price"), "Tax", "Gross")
        try:
            from app_core.db import connect
        except Exception:
            connect = None
        try:
            items = services.get_pos_items(self.rid) or []
        except Exception as exc:
            self.notify(str(exc), severity="error")
            items = []
        total = 0.0
        for p in items:
            try:
                qty = float(p["quantity"] or 0)
                price = float(p["unit_price"] or 0)
                tax = float(p["tax_amount"] or 0)
                gross = qty * price + tax
            except Exception:
                qty, price, tax, gross = 0.0, 0.0, 0.0, 0.0
            try:
                lot = (p["lot_code"] or "").strip() if "lot_code" in p.keys() else ""
            except Exception:
                lot = ""
            try:
                bid = p["batch_id"] if "batch_id" in p.keys() else None
            except Exception:
                bid = None
            lot_disp = f"{lot}#{bid}" if lot and bid else (lot or (f"#{bid}" if bid else "-"))
            extra = "-"
            if bid and connect is not None:
                try:
                    b = connect().execute("SELECT expiry_date, received_at FROM inventory_batches WHERE id=?", (bid,)).fetchone()
                    if b:
                        exp = b["expiry_date"] or "-"
                        recv = b["received_at"] or "-"
                        extra = f"exp:{exp}/recv:{recv}"
                except Exception:
                    extra = "-"
            try:
                desc = str(p["item_description"] or "-")
            except Exception:
                desc = "-"
            table.add_row(str(p["id"]), desc, lot_disp or "-", extra, f"{qty:g}", f"{price:.2f}", f"{tax:.2f}", f"{gross:.2f}")
            total += gross
        if items:
            table.add_row("", "TOTAL", "-", "-", "", "", "", f"{total:.2f}")
    def on_button_pressed(self, e):
        if e.button.id == "btn_void":
            try:
                pos_id = selected_id(self.query_one("#table", DataTable))
            except Exception:
                pos_id = None
            if pos_id:
                def cb(ok):
                    try:
                        if ok:
                            self._load()
                            self.app.refresh_all_tables()
                    except Exception as exc:
                        self.notify(str(exc), severity="error")
                self.app.push_screen(VoidItemScreen(pos_id), callback=cb)
        elif e.button.id == "btn_close":
            self.dismiss()
    def action_cancel(self):
        self.dismiss()


# ═══════════════════════════════════════════════════════════════════════════
# SETTINGS MODALS
# ═══════════════════════════════════════════════════════════════════════════

