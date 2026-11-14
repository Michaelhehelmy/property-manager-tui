"""Billing/document helpers extracted from services monolith (invoice/receipt text & files)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .. import paths

INVOICE_DIR = paths.INVOICE_DIR


def _money_fmt(value, currency, usd_to_egp=1.0):
    factor = 1.0 if currency == "EGP" else (1.0 / float(usd_to_egp or 1.0))
    try:
        v = round(float(value or 0) + 0.0, 2) * factor
    except Exception:
        v = 0.0
    return f"{v:,.2f} {currency}"


def build_invoice_text(reservation, nights, accom, pos_items, payments, *, currency="EGP", usd_to_egp=1.0,
                       display_name="Property Manager", address="", phone="", footer_text="", traceability_text_fn=None):
    if traceability_text_fn is None:
        def traceability_text_fn(**_kw):
            return ""
    W = 72
    m = lambda v: _money_fmt(v, currency, usd_to_egp)
    accom_net = round(float(accom.get("net") or 0), 2)
    accom_service = round(float(accom.get("service") or 0), 2)
    accom_vat = round(float(accom.get("vat") or 0), 2)
    accom_city = round(float(accom.get("city_tax") or 0), 2)
    accom_gross = round(float(accom.get("gross") or 0), 2)
    pos_gross = round(sum(float(p.get("gross") or 0) for p in pos_items), 2)
    total_paid = round(sum(float(p.get("amount") or 0) for p in payments), 2)
    balance = round(accom_gross + pos_gross - total_paid, 2)

    lines = ["=" * W, str(display_name).center(W), "INVOICE / RECEIPT".center(W), "=" * W]
    if address:
        for ln in str(address).split("\n"):
            if ln.strip():
                lines.append(ln.strip().center(W))
    if phone:
        lines.append(str(phone).strip().center(W))
    if currency != "EGP":
        lines.append(f"(USD view at 1 USD = {float(usd_to_egp or 1.0):.2f} EGP)")

    lines += [
        f"Invoice #:    INV-{int(reservation['id']):05d}",
        f"Generated:    {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Status:       {reservation['status']}",
        "-" * W,
        f"Guest:        {reservation['guest_name']}",
        f"Source:       {reservation['source']}",
        f"Room:         {reservation['room_number']} ({reservation['room_type']})",
        f"Check-In:     {reservation['check_in_date']}",
        f"Check-Out:    {reservation['check_out_date']}",
        f"Nights:       {nights}",
        f"Nightly Rate: {m(reservation['agreed_nightly_rate'])}",
        "-" * W,
        "ACCOMMODATION",
        f"  Net: {m(accom_net)}",
        f"  Service: {m(accom_service)}",
        f"  VAT: {m(accom_vat)}",
        f"  City tax: {m(accom_city)}",
        f"  Accommodation total: {m(accom_gross)}",
        "-" * W,
        "POS / EXTRA (traceable)",
    ]

    if pos_items:
        for p in pos_items:
            trace = traceability_text_fn(
                lot_code=p.get("lot_code"),
                batch_id=p.get("batch_id"),
                expiry_date=p.get("batch_expiry_date"),
                received_at=p.get("batch_received_at"),
            )
            trace_s = f" [{trace}]" if trace else ""
            desc = p.get("item_description") or "Item"
            lot_code = p.get("lot_code")
            if lot_code and f"[lot {lot_code}]" in desc and trace:
                desc_trace = trace.replace(f"lot {lot_code}", "").strip(", ").strip()
                trace_s = f" [{desc_trace}]" if desc_trace else ""
            lines.append(f"  {desc}{trace_s} x{p.get('quantity')} @ {m(p.get('unit_price'))} = {m(p.get('gross') or 0)}")
    else:
        lines.append("  (none)")

    lines += ["-" * W, "PAYMENTS"]
    if payments:
        for p in payments:
            amount = float(p.get("amount") or 0)
            sign = "-" if amount < 0 else ""
            at = p.get("at") or ""
            at_s = f" @ {str(at)[:16]}" if at else ""
            ptype = p.get("payment_type") or ""
            method = p.get("method") or ""
            lines.append(f"  [{ptype}] {method}: {sign}{m(abs(amount))}{at_s}")
    else:
        lines.append("  (none)")

    lines += [
        f"  Total Paid: {m(total_paid)}",
        "=" * W,
        f"  Total Charges: {m(accom_gross + pos_gross)}",
        f"  Balance Due:   {m(balance)}",
        "=" * W, "",
        footer_text or "Thank you for staying with us!",
        "=" * W,
    ]
    return "\n".join(lines)


def save_invoice_file(text, reservation_id, currency):
    INVOICE_DIR.mkdir(exist_ok=True)
    suffix = "" if currency == "EGP" else f"_{str(currency).lower()}"
    rid = int(reservation_id)
    path = (INVOICE_DIR / f"invoice_Res{rid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suffix}.txt").resolve()
    base = INVOICE_DIR.resolve()
    if base != path and base not in path.parents:
        raise ValueError("Refusing to write invoice outside invoice directory.")
    path.write_text(str(text or ""), encoding="utf-8")
    return str(path)


def build_walkin_receipt_text(receipt_items, total_gross, method, payment_id=None, when=None,
                              display_name="PROPERTY MANAGER", traceability_text_fn=None):
    if traceability_text_fn is None:
        def traceability_text_fn(**_kw):
            return ""
    W = 60
    when = when or datetime.now()
    ref = f"Payment #{payment_id}" if payment_id else "Walk-in"
    lines = ["=" * W, f"{str(display_name).upper()} — WALK-IN RECEIPT".center(W), "=" * W,
             when.strftime("%Y-%m-%d %H:%M"), f"Ref: {ref}", "-" * W, "ITEMS (traceable when batched)"]
    for it in receipt_items or []:
        desc = it.get("desc") or it.get("item_description") or "Item"
        qty = it.get("qty", it.get("quantity", 1))
        try:
            price = round(float(it.get("price", it.get("unit_price", 0)) or 0) + 0.0, 2)
        except Exception:
            price = 0.0
        try:
            gross = round(float(it.get("gross", 0) or 0) + 0.0, 2)
        except Exception:
            gross = 0.0
        if gross == 0 and price:
            try:
                gross = round(float(qty) * price, 2)
            except Exception:
                gross = 0.0
        trace = traceability_text_fn(
            lot_code=it.get("lot_code"), batch_id=it.get("batch_id"),
            expiry_date=it.get("expiry_date") or it.get("batch_expiry_date"),
            received_at=it.get("received_at") or it.get("batch_received_at"),
        )
        trace_s = f" [{trace}]" if trace else ""
        lot_code = it.get("lot_code")
        if lot_code and f"[lot {lot_code}]" in str(desc) and trace:
            trace2 = trace.replace(f"lot {lot_code}", "").strip(", ").strip()
            trace_s = f" [{trace2}]" if trace2 else ""
        lines.append(f"  {desc}{trace_s}")
        lines.append(f"    x{qty} @ {price:.2f} = {gross:.2f} EGP")
    try:
        total = round(float(total_gross or 0) + 0.0, 2)
    except Exception:
        total = 0.0
    lines += ["-" * W, f"  TOTAL: {total:.2f} EGP", f"  Paid by: {method}", "=" * W, "", "Thank you!", "=" * W]
    return "\n".join(lines)


def save_walkin_receipt_file(text, payment_id):
    INVOICE_DIR.mkdir(exist_ok=True)
    safe_id = int(payment_id) if payment_id else 0
    path = (INVOICE_DIR / f"walkin_receipt_Pay{safe_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt").resolve()
    base = INVOICE_DIR.resolve()
    if base != path and base not in path.parents:
        raise ValueError("Refusing to write receipt outside invoice directory.")
    path.write_text(str(text or ""), encoding="utf-8")
    return str(path)
