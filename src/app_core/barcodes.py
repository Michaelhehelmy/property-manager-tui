"""Barcode/label helpers: lot codes, payloads, resolution, and simple label rendering.

Local-first, no external dependencies (Tier 0). Designed to be portable and to
integrate with existing printer/raw-socket output paths.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

DATE_FMT = "%Y-%m-%d"


def _now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def normalize_code(code, max_len: int = 256):
    if code is None:
        return ""
    s = str(code).strip()
    # strip common control chars from scanners
    s = re.sub(r"[\r\n\t\x00]+", "", s)
    # remove other non-printable/control chars defensively
    s = "".join(ch for ch in s if ch.isprintable())
    if max_len and len(s) > max_len:
        s = s[:max_len]
    return s


def sanitize_label_text(value, max_len: int = 120) -> str:
    """Sanitize text embedded into printer command templates (ZPL/ESC-POS/TSPL/EPL-ish)."""
    s = normalize_code(value, max_len=max_len)
    # remove characters commonly significant in label languages
    s = re.sub(r"[\^~\\\[\]`]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def make_lot_code(item_id: int, received_at: str | None = None, seq: int | None = None) -> str:
    dt = None
    if received_at:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", DATE_FMT):
            try:
                dt = datetime.strptime(str(received_at)[:19], fmt)
                break
            except Exception:
                pass
    dt = dt or datetime.now()
    stamp = dt.strftime("%y%m%d")
    suffix = f"{int(seq):04d}" if seq is not None else datetime.now().strftime("%H%M%S")
    return f"L{int(item_id)}-{stamp}-{suffix}"


def make_lot_payload(item, batch) -> dict:
    """Versioned, compact payload intended for QR/DataMatrix labels."""
    def _get(o, k, d=None):
        try:
            return o[k]
        except Exception:
            return getattr(o, k, d)
    return {
        "v": 1,
        "t": "lot",
        "item": _get(item, "id"),
        "sku": _get(item, "sku") or "",
        "name": _get(item, "item_name") or "",
        "lot": _get(batch, "lot_code") or "",
        "recv": _get(batch, "received_at") or "",
        "exp": _get(batch, "expiry_date") or "",
        "qty": float(_get(batch, "quantity_received") or 0),
        "rem": float(_get(batch, "quantity_remaining") or 0),
        "cost": float(_get(batch, "unit_cost") or 0),
        "sup": _get(batch, "supplier") or "",
    }


def encode_payload(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def try_decode_payload(code: str):
    s = normalize_code(code)
    if not s or not (s.startswith("{") and s.endswith("}")):
        return None
    try:
        obj = json.loads(s)
    except Exception:
        return None
    if isinstance(obj, dict) and obj.get("v") and obj.get("t") == "lot":
        return obj
    return None


def suggest_expiry(received_at: str | None, shelf_life_days) -> str | None:
    if shelf_life_days in (None, "", 0, "0"):
        return None
    try:
        days = int(shelf_life_days)
    except Exception:
        return None
    if days <= 0:
        return None
    dt = None
    if received_at:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", DATE_FMT):
            try:
                dt = datetime.strptime(str(received_at)[:19], fmt)
                break
            except Exception:
                pass
    dt = dt or datetime.now()
    from datetime import timedelta
    return (dt + timedelta(days=days)).strftime(DATE_FMT)


def expiry_status(expiry_date: str | None, warn_days: int = 7):
    """Return ('ok'|'expiring'|'expired'|'none', days_left or None)."""
    if not expiry_date:
        return "none", None
    try:
        exp = datetime.strptime(str(expiry_date)[:10], DATE_FMT).date()
    except Exception:
        return "none", None
    today = datetime.now().date()
    days = (exp - today).days
    if days < 0:
        return "expired", days
    if days <= int(warn_days or 0):
        return "expiring", days
    return "ok", days


def _escpos_text_barcode(code: str, human: str = "") -> bytes:
    # Simple ESC/POS: center, text, Code128 barcode if supported, fallback to text.
    # GS k m d... (Code128 = 73) varies; to be broadly safe emit readable label text.
    lines = []
    if human:
        lines.append(human)
    lines.append(code)
    txt = "\n".join(lines) + "\n\n\n"
    # initialize + text
    return b"\x1b@" + txt.encode("utf-8", errors="replace") + b"\x1dV\x00"


def render_item_label_text(item) -> str:
    def _get(o, k, d=""):
        try:
            return o[k] if o[k] is not None else d
        except Exception:
            return getattr(o, k, d)
    name = _get(item, "item_name")
    sku = _get(item, "sku") or ""
    code = _get(item, "barcode") or sku or f"ITEM-{_get(item,'id','')}"
    cat = _get(item, "category") or ""
    price = _get(item, "selling_price") or 0
    return "\n".join([
        "=== ITEM LABEL ===",
        f"Name: {name}",
        f"SKU: {sku}" if sku else None,
        f"Category: {cat}",
        f"Code: {code}",
        f"Type: {_get(item,'barcode_type','code128')}",
        f"Price: {price}",
        "==================",
        "",
    ]).replace("\nNone\n", "\n")


def render_batch_label_text(item, batch, include_payload: bool = True) -> str:
    def _get(o, k, d=""):
        try:
            v = o[k]
            return d if v is None else v
        except Exception:
            v = getattr(o, k, d)
            return d if v is None else v
    payload = make_lot_payload(item, batch) if include_payload else None
    lines = [
        "=== BATCH / LOT LABEL ===",
        f"Item: {_get(item,'item_name')}",
        f"SKU: {_get(item,'sku')}",
        f"LOT: {_get(batch,'lot_code')}",
        f"Received: {_get(batch,'received_at')}",
        f"Expiry: {_get(batch,'expiry_date') or 'N/A'}",
        f"Qty: {_get(batch,'quantity_received')} (rem {_get(batch,'quantity_remaining')})",
        f"Unit cost: {_get(batch,'unit_cost')}",
        f"Supplier: {_get(batch,'supplier')}",
        f"Ref: {_get(batch,'reference')}",
        f"Code: {_get(batch,'barcode_value') or _get(batch,'lot_code')}",
    ]
    if payload:
        lines.append("QR-PAYLOAD:")
        lines.append(encode_payload(payload))
    lines.append("========================")
    lines.append("")
    return "\n".join(lines)


def render_label_for_print(fmt: str, item, batch=None, include_payload: bool = True):
    """Return (content, is_binary). Tier 0: text/command strings safe for file/raw print."""
    fmt = normalize_code(fmt or "code128", max_len=32).lower() or "code128"
    allowed = {"code128", "qr", "zpl", "escpos", "text", "tspl", "epl"}
    if fmt not in allowed:
        fmt = "code128"
    if batch is not None:
        text = render_batch_label_text(item, batch, include_payload=include_payload)
    else:
        text = render_item_label_text(item)
    # ZPL/EPL/TSPL/ESC-POS command templates (simple, readable text fallback embedded)
    try:
        if batch is not None:
            raw_code = (batch.get("lot_code") if isinstance(batch, dict) else getattr(batch, "lot_code", None))
        else:
            if isinstance(item, dict):
                raw_code = item.get("barcode") or item.get("sku") or f"ITEM-{item.get('id','')}"
            else:
                raw_code = getattr(item, "barcode", None) or getattr(item, "sku", None) or f"ITEM-{getattr(item,'id','')}"
    except Exception:
        raw_code = "CODE"
    code = sanitize_label_text(raw_code, max_len=80) or "CODE"
    if fmt == "zpl":
        zpl = (
            "^XA"
            f"^FO30,30^A0N,30,30^FD{code}^FS"
            "^FO30,70^BY2^BCN,80,Y,N,N"
            f"^FD{code}^FS"
            "^XZ\n"
        )
        return zpl, False
    if fmt == "escpos":
        # readable text label; many ESC/POS printers will print it fine
        return text, False
    # default: human-readable + payload text (works for code128/qr/tspl/epl as content to encode by external tools/printers)
    return text, False
