"""Property manager — business logic, ledger, validation, security."""
import hashlib
import secrets
import os
import platform
import subprocess
import socket
import unicodedata
import re
import difflib
import json
import sqlite3
import csv
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from .. import db, paths
from .. import barcodes

import sys as _sys

def _svc_attr(name, default=None):
    try:
        svc = _sys.modules.get("app_core.services")
        if svc is not None and hasattr(svc, name):
            return getattr(svc, name)
    except Exception:
        pass
    return globals().get(name, default)


BASE_DIR = paths.BASE_DIR
DATA_DIR = paths.DATA_DIR
EXPORT_DIR = paths.EXPORT_DIR
INVOICE_DIR = paths.INVOICE_DIR
CURRENCY = "EGP"
DATE_FMT = "%Y-%m-%d"
DATETIME_FMT = "%Y-%m-%d %H:%M:%S"

GIFT_SALT = "_SUKOON_SECURE_SALT_2026"
LEGACY_GIFT_ATTRIBUTION = "Gifted with love from Michael Helmy to Sukoon Camp"
LEGACY_GIFT_HASH = hashlib.sha256((LEGACY_GIFT_ATTRIBUTION + GIFT_SALT).encode()).hexdigest()
# Backward-compatible aliases
GIFT_ATTRIBUTION = LEGACY_GIFT_ATTRIBUTION
GIFT_HASH = LEGACY_GIFT_HASH

SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1


def gift_attribution_for(name):
    name = (name or "My Property").strip() or "My Property"
    return f"Gifted with love from Michael Helmy to {name}"


def gift_hash_for(attribution_text):
    return hashlib.sha256(((attribution_text or "") + GIFT_SALT).encode()).hexdigest()


def get_app_display_name():
    for key in ("app_display_name", "property_name"):
        val = (get_setting(key, "") or "").strip()
        if val and val != "Sukoon Camp":
            return val
    val = (get_setting("property_name", "") or "").strip()
    return val or "My Property"


def get_app_tagline():
    return (get_setting("app_tagline", "") or "").strip()


def get_loading_animation():
    val = (get_setting("loading_animation", "reveal") or "reveal").strip().lower()
    return val if val in {"reveal", "typewriter", "wave", "pulse", "none"} else "reveal"


def is_branding_configured():
    return (get_setting("branding_configured", "false") or "false").lower() == "true"


def get_gift_attribution():
    return gift_attribution_for(get_app_display_name())


# ═══════════════════════════════════════════════════════════════════════════
# HARDENING HELPERS (rounding / traceability / safe file+print boundaries)
# ═══════════════════════════════════════════════════════════════════════════

_MONEY_Q = 0.01
_QTY_Q = 0.0001


def _round_money(value):
    try:
        return round(float(value or 0) + 0.0, 2)
    except Exception:
        return 0.0


def _round_qty(value):
    try:
        return round(float(value or 0) + 0.0, 4)
    except Exception:
        return 0.0


def _qty_eq(a, b, eps=1e-9):
    return abs(float(a or 0) - float(b or 0)) <= eps


def _traceability_text(lot_code=None, batch_id=None, expiry_date=None, received_at=None):
    parts = []
    if lot_code:
        parts.append(f"lot {lot_code}")
    if batch_id not in (None, "", 0):
        parts.append(f"batch #{batch_id}")
    if expiry_date:
        parts.append(f"exp {expiry_date}")
    if received_at:
        recv = str(received_at)
        parts.append(f"rcv {recv[:19]}")
    return ", ".join(parts)


def _allowed_print_roots():
    roots = [EXPORT_DIR, INVOICE_DIR, DATA_DIR]
    try:
        roots.extend([paths.DATA_DIR, paths.EXPORT_DIR, paths.INVOICE_DIR])
    except Exception:
        pass
    out = []
    for r in roots:
        try:
            rr = Path(r).resolve()
            # never allow filesystem root as an approved print root
            if rr == rr.parent:
                continue
            out.append(rr)
        except Exception:
            pass
    # de-dup while preserving order
    seen = set()
    uniq = []
    for r in out:
        s = str(r)
        if s not in seen:
            uniq.append(r)
            seen.add(s)
    return uniq


def _is_under_allowed_root(path_obj):
    try:
        p = Path(path_obj).resolve()
    except Exception:
        return False
    for root in _allowed_print_roots():
        if p == root or root in p.parents:
            return True
    return False


def _sanitize_command_arg(value, label="value"):
    s = str(value or "")
    if "\x00" in s or "\n" in s or "\r" in s:
        raise ValueError(f"Invalid {label}.")
    return s


# ═══════════════════════════════════════════════════════════════════════════
# SETTINGS CATALOG
# (label, type, default, section, help, options)
# ═══════════════════════════════════════════════════════════════════════════

SETTINGS_CATALOG = {
    "property_name": ("Property Name", "str", "My Property", "property", "Shown in header and invoices"),
    "app_display_name": ("App Display Name", "str", "My Property", "property", "Used in app branding, animation, and attribution"),
    "app_tagline": ("Tagline", "str", "", "property", "Optional subtitle/tagline for branding"),
    "loading_animation": ("Loading Animation", "enum", "reveal", "system", "Startup name animation style",
                          ["reveal", "typewriter", "wave", "pulse", "none"]),
    "branding_configured": ("Branding Configured", "bool", "false", "system", "Internal: setup wizard completed"),
    "property_address": ("Address", "text", "", "property", "Multi-line, shown on invoices"),
    "property_phone": ("Phone", "str", "", "property", ""),
    "property_email": ("Email", "str", "", "property", ""),
    "invoice_footer_text": ("Invoice Footer", "text", "Thank you for staying with us!", "property", ""),
    "vat_rate": ("VAT Rate (decimal)", "pct", "0.14", "financial", "Applies to new charges only"),
    "service_charge_rate": ("Service Charge (decimal)", "pct", "0.10", "financial", "Applies to new charges only"),
    "city_tax_per_night": ("City Tax / Night (EGP)", "float", "0.0", "financial", ""),
    "usd_to_egp": ("USD to EGP Rate", "float", "50.0", "financial", "Invoice display only"),
    "default_selling_markup_pct": ("Default Markup (decimal)", "pct", "0.30", "financial", "Suggested price for new inventory"),
    "cancel_policy": ("Cancel Policy", "enum", "full_refund", "reservations", "Refund behaviour on cancel",
                      ["full_refund", "keep_deposit", "keep_first_night"]),
    "noshow_policy": ("No-Show Policy", "enum", "keep_first_night", "reservations", "",
                      ["full_refund", "keep_deposit", "keep_first_night"]),
    "week_start_day": ("Week Starts On", "enum", "monday", "reservations", "",
                       ["monday", "sunday", "saturday"]),
    "min_nights": ("Min Nights", "int", "1", "reservations", ""),
    "max_nights": ("Max Nights", "int", "365", "reservations", ""),
    "checkout_requires_payment": ("Require Payment at Checkout", "bool", "false", "reservations", ""),
    "checkout_unpaid_threshold_egp": ("Unpaid Threshold (EGP)", "float", "0.01", "reservations", ""),
    "allow_overbooking": ("Allow Overbooking", "bool", "false", "reservations", ""),
    "low_stock_threshold": ("Default Low Stock", "float", "5", "inventory", ""),
    "overstock_threshold": ("Default Overstock", "float", "100", "inventory", ""),
    "allow_negative_stock": ("Allow Negative Stock", "bool", "true", "inventory", ""),
    "language": ("Language", "enum", "en", "system", "", ["en", "ar"]),
    "session_timeout_minutes": ("Session Timeout (min)", "int", "20", "system", "Settings auto-lock"),
    "default_printer": ("Default Printer", "str", "", "system", "cups:Name, win:Name, raw:host:port"),
    "lockout_attempts": ("Lockout After N Failures", "int", "5", "system", ""),
    "lockout_minutes": ("Lockout Duration (min)", "int", "15", "system", ""),
    "assistant_response_mode": ("Assistant Response Mode", "enum", "talking", "system",
                                "core=grounded backend facts; talking=core facts rephrased by talking model when available; full_llm=LLM tool-calling conversation",
                                ["core", "talking", "full_llm"]),
    "assistant_provider": ("Assistant Tool/LLM Provider", "enum", "auto", "system", "Used for full_llm/talking when an OpenAI-compatible endpoint is configured",
                           ["auto", "deterministic_only", "openai_compatible"]),
    "assistant_llm_base_url": ("Assistant LLM Base URL", "str", "", "system", "OpenAI-compatible endpoint for talking/full_llm (e.g. http://127.0.0.1:8080/v1)"),
    "assistant_llm_model": ("Assistant LLM Model", "str", "", "system", "Model name for talking/full_llm"),
    "assistant_llm_api_key": ("Assistant LLM API Key", "str", "", "system", "Optional; leave blank for local endpoints"),
    "assistant_require_unlock": ("Assistant Requires Unlock", "bool", "true", "system", "Gate chat access to sensitive manager data"),
    "barcode_enabled": ("Barcode Enabled", "bool", "true", "system", "Enable barcode/label and batch features"),
    "barcode_scanner_mode": ("Barcode Scanner Mode", "enum", "keyboard_wedge", "system", "Primary scan input mode",
                             ["keyboard_wedge", "serial", "tcp"]),
    "barcode_scanner_port": ("Barcode Scanner Port", "str", "", "system", "Serial port or host:port for non-wedge scanners"),
    "barcode_suffix_enter": ("Scanner Sends Enter", "bool", "true", "system", "Keyboard-wedge scanners usually suffix Enter"),
    "label_printer": ("Label Printer", "str", "", "system", "Like default printer (cups/win/raw:host:port); blank = default printer"),
    "label_format": ("Label Format", "enum", "code128", "system", "Default label/barcode format",
                     ["code128", "qr", "zpl", "escpos"]),
    "label_include_qr_payload": ("Label Includes QR Payload", "bool", "true", "system", "Embed batch data payload on lot labels"),
    "expiry_warn_days": ("Expiry Warning Days", "int", "7", "inventory", "Days before expiry to warn"),
    "block_sell_expired": ("Block Selling Expired Batches", "bool", "false", "inventory", "If true, refuse sale from expired batches"),
    "batch_deduction_policy": ("Batch Deduction Policy", "enum", "fefo", "inventory", "Batch allocation when selling",
                               ["fefo", "manual"]),
    "schema_version": ("Schema Version", "internal", "4", "system", ""),
}

_SEED = {
    "rooms": [
        {"room_number": "101", "room_type": "Standard Room", "base_price": 50.0, "housekeeping_status": "Clean"},
        {"room_number": "102", "room_type": "Standard Room", "base_price": 50.0, "housekeeping_status": "Clean"},
        {"room_number": "103", "room_type": "Standard Room", "base_price": 55.0, "housekeeping_status": "Clean"},
        {"room_number": "104", "room_type": "Twin Room", "base_price": 60.0, "housekeeping_status": "Clean"},
        {"room_number": "201", "room_type": "Deluxe Room", "base_price": 85.0, "housekeeping_status": "Clean"},
        {"room_number": "202", "room_type": "Deluxe Room", "base_price": 85.0, "housekeeping_status": "Dirty"},
        {"room_number": "203", "room_type": "Sea View Suite", "base_price": 120.0, "housekeeping_status": "Clean"},
        {"room_number": "301", "room_type": "Family Suite", "base_price": 140.0, "housekeeping_status": "Clean"},
        {"room_number": "302", "room_type": "Bungalow", "base_price": 90.0, "housekeeping_status": "Clean"},
        {"room_number": "401", "room_type": "Executive Suite", "base_price": 180.0, "housekeeping_status": "Inspected"},
    ],
    "rate_plans": [
        {"name": "Room Only", "price_modifier": 0.0},
        {"name": "Bed & Breakfast", "price_modifier": 20.0},
        {"name": "Half Board", "price_modifier": 45.0},
        {"name": "Full Board", "price_modifier": 70.0},
        {"name": "All Inclusive", "price_modifier": 110.0},
    ],
    "inventory": [
        # F&B (some perishable/batch-tracked examples)
        {"item_name": "Bottled Water (0.5L)", "category": "F&B", "stock_qty": 200.0, "unit": "pcs", "unit_cost": 3.0, "selling_price": 20.0, "sku": "FB-WATER-05", "barcode": "8901001", "track_batches": 1, "shelf_life_days": 365},
        {"item_name": "Sparkling Water (0.75L)", "category": "F&B", "stock_qty": 80.0, "unit": "pcs", "unit_cost": 6.0, "selling_price": 35.0, "sku": "FB-WATER-SP", "barcode": "8901002", "track_batches": 1, "shelf_life_days": 365},
        {"item_name": "Fresh Orange Juice", "category": "F&B", "stock_qty": 30.0, "unit": "bottle", "unit_cost": 12.0, "selling_price": 45.0, "sku": "FB-JUICE-OJ", "barcode": "8901003", "track_batches": 1, "shelf_life_days": 7},
        {"item_name": "Milk (1L)", "category": "F&B", "stock_qty": 24.0, "unit": "L", "unit_cost": 18.0, "selling_price": 35.0, "sku": "FB-MILK-1L", "barcode": "8901004", "track_batches": 1, "shelf_life_days": 10},
        {"item_name": "Espresso Coffee Beans", "category": "F&B", "stock_qty": 8.0, "unit": "kg", "unit_cost": 200.0, "selling_price": 300.0, "sku": "FB-COFFEE-BEAN", "barcode": "8901005", "track_batches": 1, "shelf_life_days": 180},
        {"item_name": "Tea Bags (Box)", "category": "F&B", "stock_qty": 40.0, "unit": "box", "unit_cost": 25.0, "selling_price": 60.0, "sku": "FB-TEA-BOX", "barcode": "8901006"},
        {"item_name": "Croissant", "category": "F&B", "stock_qty": 36.0, "unit": "pcs", "unit_cost": 8.0, "selling_price": 25.0, "sku": "FB-CROISSANT", "barcode": "8901007", "track_batches": 1, "shelf_life_days": 2},
        {"item_name": "Chocolate Bar", "category": "F&B", "stock_qty": 60.0, "unit": "pcs", "unit_cost": 10.0, "selling_price": 30.0, "sku": "FB-CHOCO", "barcode": "8901008", "track_batches": 1, "shelf_life_days": 180},
        # Housekeeping
        {"item_name": "Bed Sheet Set", "category": "Housekeeping", "stock_qty": 40.0, "unit": "sets", "unit_cost": 80.0, "selling_price": 150.0, "sku": "HK-SHEET", "barcode": "8902001"},
        {"item_name": "Bath Towel", "category": "Housekeeping", "stock_qty": 60.0, "unit": "pcs", "unit_cost": 35.0, "selling_price": 70.0, "sku": "HK-TOWEL", "barcode": "8902002"},
        {"item_name": "Shampoo (Mini)", "category": "Housekeeping", "stock_qty": 120.0, "unit": "pcs", "unit_cost": 4.0, "selling_price": 15.0, "sku": "HK-SHAMPOO", "barcode": "8902003", "track_batches": 1, "shelf_life_days": 365},
        {"item_name": "Soap Bar", "category": "Housekeeping", "stock_qty": 150.0, "unit": "pcs", "unit_cost": 3.0, "selling_price": 10.0, "sku": "HK-SOAP", "barcode": "8902004"},
        # Maintenance
        {"item_name": "Light Bulb (LED)", "category": "Maintenance", "stock_qty": 30.0, "unit": "pcs", "unit_cost": 20.0, "selling_price": 45.0, "sku": "MT-BULB", "barcode": "8903001"},
        {"item_name": "Batteries (AA Pack)", "category": "Maintenance", "stock_qty": 25.0, "unit": "pack", "unit_cost": 30.0, "selling_price": 60.0, "sku": "MT-BATT-AA", "barcode": "8903002"},
        {"item_name": "Propane Gas Cylinder", "category": "Maintenance", "stock_qty": 6.0, "unit": "pcs", "unit_cost": 150.0, "selling_price": 250.0, "sku": "MT-PROPANE", "barcode": "8903003"},
        # Retail / Spa
        {"item_name": "Sunscreen Lotion", "category": "Retail", "stock_qty": 20.0, "unit": "pcs", "unit_cost": 60.0, "selling_price": 120.0, "sku": "RT-SUN", "barcode": "8904001", "track_batches": 1, "shelf_life_days": 365},
        {"item_name": "Beach Towel (Retail)", "category": "Retail", "stock_qty": 15.0, "unit": "pcs", "unit_cost": 70.0, "selling_price": 150.0, "sku": "RT-BEACH-TOWEL", "barcode": "8904002"},
        {"item_name": "Massage Oil", "category": "Spa", "stock_qty": 10.0, "unit": "bottle", "unit_cost": 40.0, "selling_price": 90.0, "sku": "SP-OIL", "barcode": "8905001", "track_batches": 1, "shelf_life_days": 365},
    ],
    "expense_categories": [
        "Utilities (Water/Electricity)",
        "Maintenance & Repairs",
        "Supplies & Consumables",
        "Staff & Operational",
        "Food & Beverage Supplies",
        "Housekeeping Supplies",
        "Marketing & Commissions",
        "Transport & Fuel",
        "Licenses & Fees",
        "Miscellaneous",
    ],
    "activities": [
        {"name": "Desert Safari", "description": "Guided desert excursion with sunset views", "category": "Trip", "unit_price": 150.0, "active": 1},
        {"name": "Snorkeling Trip", "description": "Half-day snorkeling experience with equipment", "category": "Trip", "unit_price": 120.0, "active": 1},
        {"name": "City Tour", "description": "Guided local city and culture tour", "category": "Trip", "unit_price": 90.0, "active": 1},
        {"name": "Airport Transfer", "description": "One-way airport pickup or drop-off", "category": "Service", "unit_price": 45.0, "active": 1},
        {"name": "Spa Massage (60 min)", "description": "Relaxing full-body massage session", "category": "Activity", "unit_price": 80.0, "active": 1},
        {"name": "Laundry Service", "description": "Per-bag guest laundry service", "category": "Service", "unit_price": 25.0, "active": 1},
    ],
    "meals": [
        {
            "name": "Continental Breakfast", "description": "Croissant, juice, and coffee/tea",
            "preparation": "Serve fresh croissant with orange juice and hot beverage.",
            "selling_price": 60.0, "category": "Meals", "active": 1,
            "ingredients": [
                {"inventory_item_name": "Croissant", "qty_per_sale": 1, "note": "per serving"},
                {"inventory_item_name": "Fresh Orange Juice", "qty_per_sale": 0.25, "note": "bottle portion"},
                {"inventory_item_name": "Tea Bags (Box)", "qty_per_sale": 0.02, "note": "approx per cup"},
            ],
        },
        {
            "name": "Coffee & Water Combo", "description": "Bottled water and espresso serving",
            "preparation": "Brew espresso from beans and serve with bottled water.",
            "selling_price": 35.0, "category": "Meals", "active": 1,
            "ingredients": [
                {"inventory_item_name": "Bottled Water (0.5L)", "qty_per_sale": 1, "note": ""},
                {"inventory_item_name": "Espresso Coffee Beans", "qty_per_sale": 0.02, "note": "kg per shot"},
            ],
        },
    ],
}


# ═══════════════════════════════════════════════════════════════════════════
# i18n
# ═══════════════════════════════════════════════════════════════════════════

_AR = {
    "Front Desk": "الاستقبال", "Reservations": "الحجوزات",
    "POS & Inventory": "نقطة البيع والمخزون", "Accounting": "المحاسبة", "Settings": "الإعدادات",
    "New Order": "طلب جديد", "Inventory": "المخزون",
    "Rooms": "الغرف", "General Info": "معلومات عامة", "Filters": "الفلاتر",
    "All Rooms": "كل الغرف", "Available in Period": "متاح في الفترة", "Room View": "عرض الغرفة",
    "From": "من", "To": "إلى", "Occupancy": "الإشغال", "Day": "يوم", "Week": "أسبوع",
    "Month": "شهر", "Custom": "مخصص", "Mark Clean": "نظيف", "Mark Dirty": "غير نظيف",
    "Mark Maintenance": "صيانة", "Edit": "تعديل", "Edit Type": "تعديل النوع", "Edit Price": "تعديل السعر",
    "Room": "الغرفة", "Room Type": "نوع الغرفة", "Base Price": "السعر الأساسي", "Status": "الحالة",
    "Upcoming Reservations": "الحجوزات القادمة", "Apply": "تطبيق", "Occupancy Chart": "مخطط الإشغال",
    "Guests": "الضيوف", "Chart": "مخطط",
    "Check-In Guest": "تسجيل الدخول", "Check-Out & Invoice": "تسجيل الخروج",
    "Preview Invoice": "معاينة الفاتورة", "View Folio": "عرض الحساب", "Modify": "تعديل",
    "Cancel Reservation": "إلغاء الحجز", "No-Show": "لم يحضر", "Delete": "حذف",
    "Guest": "الضيف", "Guest Name": "اسم الضيف", "Source": "المصدر", "Room ID": "رقم الغرفة",
    "Rate Plan": "خطة السعر", "Agreed Nightly Rate": "سعر الليلة",
    "Check-In Date": "تاريخ الوصول", "Check-Out Date": "تاريخ المغادرة",
    "Deposit Amount": "مبلغ التأمين", "Save Reservation": "حفظ الحجز",
    "Guest Folio & Settlement": "حساب الضيف والتسوية",
    "Reservation ID for Billing": "رقم الحجز", "Check Guest Balance Due": "التحقق من الرصيد",
    "Payment Amount": "مبلغ الدفع", "Method": "الطريقة", "Payment Type": "نوع الدفعة",
    "Notes": "ملاحظات", "Record Payment": "تسجيل الدفعة", "Search": "بحث",
    "Target": "الهدف", "Existing Reservation": "حجز موجود", "Walk-in": "زائر مباشر",
    "Load": "تحميل", "Item Grid": "قائمة العناصر", "Custom Item": "عنصر مخصص",
    "Description": "الوصف", "Quantity": "الكمية", "Unit Price": "سعر الوحدة", "Add": "إضافة",
    "Cart": "السلة", "Item": "العنصر", "Qty": "الكمية", "Price": "السعر", "Tax": "ضريبة",
    "Gross": "الإجمالي", "Void": "إلغاء", "Pay": "دفع",
    "Warnings": "تحذيرات", "Low Stock": "مخزون منخفض", "Overstock": "مخزون زائد",
    "Negative Stock": "مخزون سالب", "All Healthy": "كل المخزون سليم",
    "New Item": "عنصر جديد", "Restock": "توريد", "Restock Selected": "توريد المحدد",
    "Recent Movements": "الحركات الأخيرة", "Category": "الفئة", "Stock": "المخزون",
    "Unit": "الوحدة", "Unit Cost": "تكلفة الوحدة", "Sell": "البيع",
    "Record Operational Expense": "تسجيل مصروف",
    "Parameter Name": "اسم المعامل", "Amount": "المبلغ", "VAT Paid": "ضريبة مدفوعة",
    "Record Expense": "تسجيل المصروف", "General Ledger": "دفتر الأستاذ",
    "Refresh": "تحديث", "Recent Expenses": "المصروفات الأخيرة", "Recent Ledger": "الحركات الأخيرة",
    "P&L Statement": "قائمة الأرباح والخسائر", "Cash Flow": "التدفق النقدي",
    "Payment Methods": "طرق الدفع", "VAT Report": "تقرير ضريبة القيمة المضافة",
    "Daily Ops": "العمليات اليومية", "Refund": "استرداد", "Void Expense": "إلغاء مصروف",
    "Unlock Settings": "فتح الإعدادات", "Lock Now": "قفل الآن",
    "Property Information": "معلومات العقار", "Financial & Tax": "المالية والضرائب",
    "Rate Plans": "خطط الأسعار", "Security": "الأمان", "Maintenance": "الصيانة",
    "System": "النظام", "Printers": "الطابعات", "Change Password": "تغيير كلمة المرور",
    "Save Changes": "حفظ التغييرات", "Reset to Defaults": "إعادة إلى الافتراضي",
    "Export Settings": "تصدير الإعدادات", "Import Settings": "استيراد الإعدادات",
    "Detect Printers": "اكتشاف الطابعات", "Test Print": "اختبار الطباعة",
    "Add Item": "إضافة عنصر", "Default": "افتراضي", "Backup Now": "نسخ احتياطي الآن",
    "Save": "حفظ", "Cancel": "إلغاء", "Confirm": "تأكيد", "Close": "إغلاق",
    "Print": "طباعة", "Print Invoice": "طباعة الفاتورة", "Show in USD": "عرض بالدولار",
    "Show in EGP": "عرض بالجنيه", "Super Password": "كلمة المرور الرئيسية",
    "Wrong super password.": "كلمة المرور غير صحيحة.", "Current": "الحالي",
    "Confirm Password": "تأكيد كلمة المرور", "Set Super Password": "تعيين كلمة المرور",
    "Quit App": "إغلاق التطبيق", "Language": "اللغة", "Delete Permanently": "حذف نهائياً",
    "Guest Invoice": "فاتورة الضيف", "Receipt": "إيصال",
    "View History": "عرض السجل", "Merge Selected": "دمج المحدد",
    "Duplicate Suggestions": "اقتراحات مكررة",
    "Accommodation": "الإقامة", "F&B": "المأكولات", "Other": "أخرى",
    "COGS": "تكلفة المبيعات", "Inventory Purchase": "شراء مخزون", "Operating": "تشغيلي",
    "Total Revenue": "إجمالي الإيرادات", "Total Expenses": "إجمالي المصروفات",
    "Net Profit": "صافي الربح", "Recovery Code": "رمز الاستعادة", "Locked": "مقفل",
}

_lang = "en"


def t(key, **kw):
    s = _AR.get(key, key) if _lang == "ar" else key
    return s.format(**kw) if kw else s


def set_language(lang):
    global _lang
    _lang = lang if lang in ("en", "ar") else "en"


# ═══════════════════════════════════════════════════════════════════════════
# INIT / SEED
# ═══════════════════════════════════════════════════════════════════════════


def get_language():
    return _lang

def init_db():
    """Create/migrate schema. Seeds only on first-ever launch."""
    fresh = db.is_fresh()
    action = db.migrate()
    if fresh:
        _seed_defaults()
    elif action == "created":
        _seed_defaults()  # pragma: no cover
    _ensure_metadata()
    migrate_branding_if_needed()


def migrate_branding_if_needed():
    """Preserve legacy installs; avoid forcing wizard on existing branded DBs."""
    try:
        if (get_setting("branding_configured", "false") or "false").lower() == "true":
            return
        prop = (get_setting("property_name", "") or "").strip()
        if prop == "Sukoon Camp":
            apply_branding_settings(prop, get_setting("app_tagline", ""), get_setting("loading_animation", "reveal"), sync_property_name=False)
            return
        if prop and prop not in ("", "My Property"):
            set_setting("app_display_name", get_setting("app_display_name", "") or prop)
            set_setting("branding_configured", "true")
    except Exception:
        pass


def _ensure_metadata():
    from ..db import transaction
    with transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO app_metadata (key, value, hash_signature) VALUES ('gift_attribution', ?, ?)",
            (LEGACY_GIFT_ATTRIBUTION, LEGACY_GIFT_HASH))


def apply_branding_settings(display_name, tagline="", animation="reveal", sync_property_name=True):
    display_name = (display_name or "").strip()
    if not display_name:
        raise ValueError("Display name is required.")
    animation = (animation or "reveal").strip().lower()
    if animation not in {"reveal", "typewriter", "wave", "pulse", "none"}:
        animation = "reveal"
    attribution = gift_attribution_for(display_name)
    sig = gift_hash_for(attribution)
    from ..db import transaction
    with transaction() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('app_display_name', ?)", (display_name,))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('app_tagline', ?)", ((tagline or "").strip(),))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('loading_animation', ?)", (animation,))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('branding_configured', 'true')")
        if sync_property_name:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('property_name', ?)", (display_name,))
        conn.execute(
            "INSERT OR REPLACE INTO app_metadata (key, value, hash_signature) VALUES ('gift_attribution', ?, ?)",
            (attribution, sig),
        )
    _audit("branding_update", "settings", "", display_name)
    return {"display_name": display_name, "tagline": (tagline or "").strip(), "animation": animation, "attribution": attribution}


def _seed_defaults():
    from ..db import transaction
    now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with transaction() as conn:
        for key, spec in SETTINGS_CATALOG.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, spec[2]))
        for r in _SEED.get("rooms", []):
            conn.execute(
                "INSERT INTO rooms (room_number, room_type, base_price, housekeeping_status) VALUES (?, ?, ?, ?)",
                (r["room_number"], r["room_type"], r["base_price"], r.get("housekeeping_status", "Clean")))
        for p in _SEED.get("rate_plans", []):
            conn.execute("INSERT INTO rate_plans (name, price_modifier) VALUES (?, ?)",
                         (p["name"], p["price_modifier"]))
        for i in _SEED.get("inventory", []):
            conn.execute("""INSERT INTO inventory
                (item_name, category, stock_qty, unit, unit_cost, selling_price,
                 low_stock_threshold, overstock_threshold, active, sku, barcode, barcode_type, track_batches, shelf_life_days, default_supplier)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (i["item_name"], i["category"], i.get("stock_qty", 0), i["unit"], i.get("unit_cost", 0), i.get("selling_price", 0),
                 i.get("low_stock_threshold"), i.get("overstock_threshold"), int(i.get("active", 1)),
                 (i.get("sku") or None), (i.get("barcode") or None), i.get("barcode_type", "code128"),
                 int(i.get("track_batches", 0) or 0), i.get("shelf_life_days"), i.get("default_supplier", "")))
            item_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            qty = float(i.get("stock_qty") or 0)
            if abs(qty) > 1e-9:
                lot = f"SEED-{item_id}"
                exp = None
                try:
                    sld = int(i.get("shelf_life_days")) if i.get("shelf_life_days") not in (None, "") else None
                except Exception:
                    sld = None
                if sld and sld > 0:
                    exp = (datetime.now() + timedelta(days=sld)).strftime(DATE_FMT)
                conn.execute("""INSERT INTO inventory_batches
                    (item_id, lot_code, barcode_value, barcode_type, quantity_received, quantity_remaining,
                     unit_cost, received_at, expiry_date, supplier, reference, notes, status, payload_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (item_id, lot, lot, i.get("barcode_type", "code128"), qty, qty, float(i.get("unit_cost") or 0),
                     now_s, exp, i.get("default_supplier", ""), "seed", "Initial seed stock", "active", "{}"))
                bid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                try:
                    payload = json.dumps({"v": 1, "t": "lot", "item": item_id, "name": i["item_name"], "lot": lot, "recv": now_s, "exp": exp or "", "qty": qty, "rem": qty, "cost": float(i.get("unit_cost") or 0)}, separators=(",", ":"), ensure_ascii=False)
                    conn.execute("UPDATE inventory_batches SET payload_json=? WHERE id=?", (payload, bid))
                except Exception:
                    pass
                conn.execute("""INSERT INTO inventory_logs (item_id, change_type, quantity, reference, batch_id, unit_cost, balance_after)
                               VALUES (?, 'OPENING', ?, 'seed opening batch', ?, ?, ?)""",
                            (item_id, qty, bid, float(i.get("unit_cost") or 0), qty))
        for c in _SEED.get("expense_categories", []):
            conn.execute("INSERT OR IGNORE INTO expense_categories (category_name) VALUES (?)", (c,))
        # activities
        try:
            for a in _SEED.get("activities", []):
                conn.execute("""INSERT OR IGNORE INTO activities (name, description, category, unit_price, active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, datetime('now','localtime'), datetime('now','localtime'))""",
                    (a["name"], a.get("description", ""), a.get("category", "Trip"), float(a.get("unit_price", 0) or 0), int(a.get("active", 1) or 0)))
        except Exception:
            pass
        # meals (resolve ingredient names) + sync to inventory/recipes
        try:
            inv_name_to_id = {r["item_name"].strip().lower(): r["id"] for r in conn.execute("SELECT id, item_name FROM inventory").fetchall()}
            for m in _SEED.get("meals", []):
                conn.execute("""INSERT INTO meals (name, description, preparation, selling_price, category, active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'), datetime('now','localtime'))""",
                    (m["name"], m.get("description", ""), m.get("preparation", ""), float(m.get("selling_price", 0) or 0), m.get("category", "Meals"), int(m.get("active", 1) or 0)))
                meal_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                norm_ings = []
                for ing in (m.get("ingredients") or []):
                    iname = str(ing.get("inventory_item_name") or ing.get("item_name") or "").strip().lower()
                    iid = inv_name_to_id.get(iname)
                    if iid is None:
                        continue
                    qps = float(ing.get("qty_per_sale", 0) or 0)
                    conn.execute("INSERT OR REPLACE INTO meal_ingredients (meal_id, inventory_item_id, qty_per_sale, note) VALUES (?, ?, ?, ?)",
                                 (meal_id, iid, qps, ing.get("note", "")))
                    norm_ings.append({"inventory_item_id": iid, "qty_per_sale": qps, "note": ing.get("note", "")})
                # best-effort sync meal -> sellable inventory item/recipe
                try:
                    menu_name = f"Meal: {m['name']}".strip()
                    existing = conn.execute("SELECT id FROM inventory WHERE item_name=?", (menu_name,)).fetchone()
                    inv_id = existing["id"] if existing else None
                    # total ingredient unit cost
                    total_uc = 0.0
                    for ni in norm_ings:
                        ruc = conn.execute("SELECT unit_cost FROM inventory WHERE id=?", (ni["inventory_item_id"],)).fetchone()
                        total_uc += float((ruc["unit_cost"] if ruc else 0) or 0) * float(ni["qty_per_sale"] or 0)
                    if inv_id:
                        conn.execute("UPDATE inventory SET category=?, selling_price=?, unit_cost=?, active=? WHERE id=?",
                                     (m.get("category", "Meals"), float(m.get("selling_price", 0) or 0), round(total_uc, 4), 1, inv_id))
                    else:
                        conn.execute("""INSERT INTO inventory (item_name, category, stock_qty, unit, unit_cost, selling_price, active)
                                        VALUES (?, ?, 0, 'portion', ?, ?, 1)""",
                                     (menu_name, m.get("category", "Meals"), round(total_uc, 4), float(m.get("selling_price", 0) or 0)))
                        inv_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    conn.execute("UPDATE meals SET linked_inventory_id=? WHERE id=?", (inv_id, meal_id))
                    conn.execute("DELETE FROM recipe_components WHERE menu_item_id=?", (inv_id,))
                    for ni in norm_ings:
                        conn.execute("INSERT OR REPLACE INTO recipe_components (menu_item_id, ingredient_item_id, qty_per_sale) VALUES (?,?,?)",
                                     (inv_id, ni["inventory_item_id"], ni["qty_per_sale"]))
                except Exception:
                    pass
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════════════════════════════════════

def get_setting(key, default=""):
    from ..db import connect
    row = db.connect().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    from ..db import transaction
    with transaction() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))


def get_all_settings():
    from ..db import connect
    return {r["key"]: r["value"] for r in db.connect().execute("SELECT key, value FROM settings").fetchall()}


def _validate_setting(key, raw):
    spec = SETTINGS_CATALOG.get(key)
    if not spec:
        return None, f"Unknown setting: {key}"
    label, typ, _default = spec[:3]
    options = spec[5] if len(spec) > 5 else None
    raw = str(raw).strip()
    try:
        if typ in ("str", "text"):
            return raw, None
        if typ == "int":
            v = int(raw)
            if v < 0:
                return None, f"{label} must be >= 0"
            return str(v), None
        if typ in ("float", "pct"):
            v = float(raw)
            if typ == "pct" and (v < 0 or v > 1):
                return None, f"{label} must be between 0 and 1"
            if v < 0:
                return None, f"{label} must be >= 0"
            return str(v), None
        if typ == "bool":
            return ("true" if raw.lower() in ("true", "1", "yes", "on") else "false"), None
        if typ == "enum":
            if options and raw not in options:
                return None, f"{label} must be one of: {', '.join(options)}"
            return raw, None
        return raw, None
    except ValueError:
        return None, f"{label} has invalid value"


def save_settings(changes, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    errors = []
    normalized = {}
    for key, val in changes.items():
        if key == "schema_version":
            continue
        norm, err = _validate_setting(key, val)
        if err:
            errors.append(err)
        else:
            normalized[key] = norm
    if errors:
        return 0, errors
    from ..db import transaction
    with transaction() as conn:
        for key, val in normalized.items():
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, val))
    _audit("settings_save", "settings", "", f"{len(normalized)} keys")
    return len(normalized), []


def reset_to_defaults(password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    from ..db import transaction
    with transaction() as conn:
        for key, spec in SETTINGS_CATALOG.items():
            if key in ("schema_version", "language"):
                continue
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, spec[2]))
    _audit("settings_reset", "settings", "", "")


def export_settings_to_json():
    EXPORT_DIR.mkdir(exist_ok=True)
    data = {k: v for k, v in get_all_settings().items()
            if k in SETTINGS_CATALOG and SETTINGS_CATALOG[k][1] != "internal"}
    path = EXPORT_DIR / f"settings_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


def import_settings_from_json(filepath, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")  # pragma: no cover
    p = Path(filepath)
    if not p.exists():
        raise ValueError(f"File not found: {filepath}")
    data = json.loads(p.read_text(encoding="utf-8"))
    staging, errors, skipped = {}, [], []
    for key, val in data.items():
        if key not in SETTINGS_CATALOG or SETTINGS_CATALOG[key][1] == "internal":
            skipped.append(key)
            continue
        norm, err = _validate_setting(key, val)
        if err:
            errors.append(f"{key}: {err}")  # pragma: no cover
        else:
            staging[key] = norm
    if errors:
        return {"ok": False, "errors": errors, "skipped": skipped, "changes": {}}  # pragma: no cover
    from ..db import transaction
    with transaction() as conn:
        for k, v in staging.items():
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))
    _audit("settings_import", "settings", "", f"{len(staging)} keys")
    return {"ok": True, "errors": [], "skipped": skipped, "changes": staging}


def get_tax_config():
    return {
        "vat_rate": float(get_setting("vat_rate", "0.14")),
        "service_rate": float(get_setting("service_charge_rate", "0.10")),
        "city_per_night": float(get_setting("city_tax_per_night", "0.0")),
        "usd_to_egp": float(get_setting("usd_to_egp", "50.0")),
    }


def calculate_tax(net, nights):
    cfg = get_tax_config()
    service = round(net * cfg["service_rate"], 2)
    vat = round((net + service) * cfg["vat_rate"], 2)
    city = round(nights * cfg["city_per_night"], 2)
    return {"net": round(net, 2), "service": service, "vat": vat, "city_tax": city,
            "gross": round(net + service + vat + city, 2)}


# ═══════════════════════════════════════════════════════════════════════════
# AUDIT LOG
# ═══════════════════════════════════════════════════════════════════════════

def _audit(action, target_type="", target_id="", details=""):
    try:
        from ..db import transaction
        with transaction() as conn:
            conn.execute(
                "INSERT INTO audit_log (action, target_type, target_id, details) VALUES (?, ?, ?, ?)",
                (action, target_type, str(target_id), details))
    except Exception:  # pragma: no cover
        pass  # pragma: no cover — audit must never crash


def get_audit_log(limit=200):
    from ..db import connect
    return db.connect().execute(
        "SELECT id, at, actor, action, target_type, target_id, details FROM audit_log ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()


# ═══════════════════════════════════════════════════════════════════════════
# AUTH — scrypt, session, lockout, recovery
# ═══════════════════════════════════════════════════════════════════════════

_session = {"unlocked": False, "last_activity": None}


def _scrypt_hash(password, salt_hex):
    salt = bytes.fromhex(salt_hex)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return dk.hex()


def has_super_password():
    from ..db import connect
    row = db.connect().execute("SELECT value FROM app_metadata WHERE key='super_password_hash'").fetchone()
    return bool(row and row["value"])


def set_super_password(password):
    if not password or len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    if has_super_password():
        raise ValueError("Super password is already set.")
    salt = secrets.token_hex(16)
    pw_hash = _scrypt_hash(password, salt)
    from ..db import transaction
    with transaction() as conn:
        conn.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('super_password_salt', ?)", (salt,))
        conn.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('super_password_hash', ?)", (pw_hash,))
        conn.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('failed_attempts', '0')")
        conn.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('lockout_until', '')")
        for _ in range(8):
            code = secrets.token_hex(4).upper()
            ch = hashlib.sha256(code.encode()).hexdigest()
            conn.execute("INSERT INTO app_metadata (key, value) VALUES (?, ?)", (f"recovery_{ch}", "1"))
    _audit("password_set")


def verify_password(password):
    if not password:
        return False
    # Web/per-session unlock flows may pass __session__ as a password-proof token.
    # Treat as valid only when the in-process session is currently unlocked.
    if password == "__session__":
        return is_session_unlocked()
    from ..db import connect, transaction

    conn = db.connect()
    rows = {r["key"]: r["value"] for r in conn.execute(
        "SELECT key, value FROM app_metadata WHERE key IN ('super_password_salt','super_password_hash','failed_attempts','lockout_until')")}
    salt = rows.get("super_password_salt")
    stored = rows.get("super_password_hash")
    if not salt or not stored:
        return False
    lockout = rows.get("lockout_until", "")
    if lockout:
        try:
            until = datetime.fromisoformat(lockout)
            if datetime.now() < until:
                return False
        except Exception:
            pass
    actual = _scrypt_hash(password, salt)
    if secrets.compare_digest(actual, stored):
        with transaction() as c:
            c.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('failed_attempts', '0')")
            c.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('lockout_until', '')")
        return True
    attempts = int(rows.get("failed_attempts", "0")) + 1
    limit = int(get_setting("lockout_attempts", "5"))
    with transaction() as c:
        c.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('failed_attempts', ?)", (str(attempts),))
        if attempts >= limit:
            until = (datetime.now() + timedelta(minutes=int(get_setting("lockout_minutes", "15")))).isoformat()
            c.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('lockout_until', ?)", (until,))
    return False


def change_password(current, new, confirm):
    if not verify_password(current):
        raise PermissionError("Current password incorrect.")
    if new != confirm:
        raise ValueError("New passwords do not match.")
    if len(new) < 6:
        raise ValueError("Password must be at least 6 characters.")
    if new == current:
        raise ValueError("New password must differ.")
    salt = secrets.token_hex(16)
    pw_hash = _scrypt_hash(new, salt)
    from ..db import transaction
    with transaction() as conn:
        conn.execute("UPDATE app_metadata SET value=? WHERE key='super_password_salt'", (salt,))
        conn.execute("UPDATE app_metadata SET value=? WHERE key='super_password_hash'", (pw_hash,))
    _audit("password_change")


def generate_recovery_codes(password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    from ..db import transaction
    codes = []
    with transaction() as conn:
        conn.execute("DELETE FROM app_metadata WHERE key LIKE 'recovery_%'")
        for _ in range(8):
            code = secrets.token_hex(4).upper()
            ch = hashlib.sha256(code.encode()).hexdigest()
            conn.execute("INSERT INTO app_metadata (key, value) VALUES (?, '1')", (f"recovery_{ch}",))
            codes.append(code)
    _audit("recovery_codes_generated")
    return codes


def verify_recovery_code(code):
    ch = hashlib.sha256(code.strip().upper().encode()).hexdigest()
    from ..db import connect
    row = db.connect().execute("SELECT 1 FROM app_metadata WHERE key=?", (f"recovery_{ch}",)).fetchone()
    return bool(row)


def use_recovery_code(code, new_password, confirm_password):
    """Consume a recovery code and set a new super password."""
    code_norm = (code or "").strip().upper()
    if not code_norm:
        raise ValueError("Recovery code is required.")
    if new_password != confirm_password:
        raise ValueError("Passwords do not match.")
    if not new_password or len(new_password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    ch = hashlib.sha256(code_norm.encode()).hexdigest()
    key = f"recovery_{ch}"
    from ..db import connect, transaction
    conn = db.connect()
    row = conn.execute("SELECT 1 FROM app_metadata WHERE key=?", (key,)).fetchone()
    if not row:
        raise ValueError("Invalid or already-used recovery code.")
    salt = secrets.token_hex(16)
    pw_hash = _scrypt_hash(new_password, salt)
    with transaction() as c:
        c.execute("DELETE FROM app_metadata WHERE key=?", (key,))
        c.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('super_password_salt', ?)", (salt,))
        c.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('super_password_hash', ?)", (pw_hash,))
        c.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('failed_attempts', '0')")
        c.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('lockout_until', '')")
    _audit("recovery_code_used")
    return True


# In-memory session (per-run)
def unlock_session(password):
    # Explicit password unlock must use real credentials (not __session__ proof token).
    if password == "__session__" or not verify_password(password):
        return False
    _session["unlocked"] = True
    _session["last_activity"] = datetime.now()
    _audit("session_unlock")
    return True


def lock_session():
    _session["unlocked"] = False
    _session["last_activity"] = None


def is_session_unlocked():
    if not _session["unlocked"]:
        return False
    try:
        timeout = int(get_setting("session_timeout_minutes", "20") or 20)
    except Exception:
        timeout = 20
    if timeout < 0:
        timeout = 0
    if _session["last_activity"] and (datetime.now() - _session["last_activity"]).total_seconds() > timeout * 60:
        lock_session()
        return False
    return True


def touch_session():
    if _session["unlocked"]:
        _session["last_activity"] = datetime.now()


# ═══════════════════════════════════════════════════════════════════════════
# LEDGER — append-only with reversals
# ═══════════════════════════════════════════════════════════════════════════

def _post(cur, ref_type, ref_id, entry_type, category, net, service, vat, city_tax, method, description, reverses_id=None, gross_override=None):
    """Single ledger write. Returns the new row id."""
    gross = round(gross_override, 2) if gross_override is not None else round(net + service + vat + city_tax, 2)
    cur.execute("""INSERT INTO general_ledger
        (at, ref_type, ref_id, entry_type, category, net, service, vat, city_tax, gross, method, description, reverses_id)
        VALUES (datetime('now','localtime'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (ref_type, ref_id, entry_type, category, round(net, 2), round(service, 2), round(vat, 2),
         round(city_tax, 2), gross, method, description, reverses_id))
    return cur.lastrowid


def _reverse(cur, ref_type, ref_id, entry_type_filter=None):
    """Reverse all active ledger rows for a given ref. Returns count."""
    q = "SELECT * FROM general_ledger WHERE ref_type=? AND ref_id=? AND reverses_id IS NULL"
    params = [ref_type, ref_id]
    if entry_type_filter:
        placeholders = ",".join("?" * len(entry_type_filter))
        q += f" AND entry_type IN ({placeholders})"
        params.extend(entry_type_filter)
    rows = cur.execute(q, params).fetchall()
    for r in rows:
        _post(cur, r["ref_type"], r["ref_id"], f"VOID_{r['entry_type']}", r["category"],
              -r["net"], -r["service"], -r["vat"], -r["city_tax"], r["method"],
              f"Reversal: {r['description']}", reverses_id=r["id"],
              gross_override=-r["gross"])
    return len(rows)


def _net_types(base_types):
    """Expand entry types to include VOID_* counterparts for signed-net queries."""
    if isinstance(base_types, str):
        base_types = (base_types,)
    out = []
    for t in base_types:
        out.append(t)
        if not t.startswith("VOID_"):
            out.append(f"VOID_{t}")
    return tuple(out)


def _ledger_scalar(conn, expr, where, params=()):
    row = conn.execute(f"SELECT COALESCE({expr}, 0) AS v FROM general_ledger WHERE {where}", params).fetchone()
    return row["v"] if row else 0


def _reservation_charge_net(conn, rid):
    """Net accommodation + POS charge gross for a reservation (includes VOID_*)."""
    types = _net_types(("CHARGE",))
    ph = ",".join("?" * len(types))
    accom = _ledger_scalar(
        conn, "SUM(gross)",
        f"ref_type='RES' AND ref_id=? AND entry_type IN ({ph})",
        (rid, *types),
    )
    pos = _ledger_scalar(
        conn, "SUM(gross)",
        f"ref_type='POS' AND ref_id IN (SELECT id FROM pos_tabs WHERE reservation_id=?) AND entry_type IN ({ph})",
        (rid, *types),
    )
    return round((accom or 0) + (pos or 0), 2)


def _reservation_charge_components(conn, rid):
    """Net accommodation/POS components for a reservation (includes VOID_*)."""
    types = _net_types(("CHARGE",))
    ph = ",".join("?" * len(types))

    def q(expr, ref_sql, ref_params):
        return conn.execute(
            f"SELECT COALESCE(SUM({expr}), 0) AS v FROM general_ledger WHERE {ref_sql} AND entry_type IN ({ph})",
            (*ref_params, *types),
        ).fetchone()["v"] or 0

    accom_net = q("net", "ref_type='RES' AND ref_id=?", (rid,))
    accom_gross = q("gross", "ref_type='RES' AND ref_id=?", (rid,))
    pos_net = q(
        "net",
        "ref_type='POS' AND ref_id IN (SELECT id FROM pos_tabs WHERE reservation_id=?)",
        (rid,),
    )
    pos_gross = q(
        "gross",
        "ref_type='POS' AND ref_id IN (SELECT id FROM pos_tabs WHERE reservation_id=?)",
        (rid,),
    )
    return {
        "accommodation_net": round(accom_net, 2),
        "accommodation_gross": round(accom_gross, 2),
        "accommodation_tax": round(accom_gross - accom_net, 2),
        "pos_net": round(pos_net, 2),
        "pos_gross": round(pos_gross, 2),
        "pos_tax": round(pos_gross - pos_net, 2),
        "total_charges": round(accom_gross + pos_gross, 2),
    }


# ═══════════════════════════════════════════════════════════════════════════
# GUESTS — Arabic-aware fuzzy matching
# ═══════════════════════════════════════════════════════════════════════════

_AR_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7\u06E8\u06EA-\u06ED]")


def _norm_text(s):
    if not s:
        return ""
    s = _AR_DIACRITICS.sub("", s)
    s = (s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
         .replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي"))
    return " ".join(s.lower().split())


def _norm_phone(p):
    if not p:
        return ""
    digits = "".join(c for c in p if c.isdigit())
    if digits.startswith("0020"):
        digits = digits[4:]
    elif digits.startswith("20") and len(digits) > 10:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) > 10:
        digits = digits[1:]
    return digits[-10:]


def _name_score(a, b):
    a_n, b_n = _norm_text(a), _norm_text(b)
    if not a_n or not b_n:
        return 0.0
    direct = difflib.SequenceMatcher(None, a_n, b_n).ratio()
    tok = difflib.SequenceMatcher(None, " ".join(sorted(a_n.split())), " ".join(sorted(b_n.split()))).ratio()
    return max(direct, tok)


def find_guest_matches(name, phone="", email="", threshold=0.82):
    if not name or not name.strip():
        return []
    from ..db import connect
    p_norm = _norm_phone(phone)
    e_norm = (email or "").lower().strip()
    candidates = db.connect().execute("SELECT id, canonical_name, phone, email FROM guests").fetchall()
    scored = []
    for c in candidates:
        score = _name_score(name, c["canonical_name"])
        if p_norm and _norm_phone(c["phone"]) == p_norm:
            score = 1.0
        elif e_norm and (c["email"] or "").lower().strip() == e_norm:
            score = max(score, 0.98)
        if score >= threshold:
            stays = db.connect().execute(
                "SELECT COUNT(*), MAX(check_in_date) FROM reservations WHERE guest_id=?",
                (c["id"],)).fetchone()
            scored.append({
                "id": c["id"], "name": c["canonical_name"], "phone": c["phone"],
                "email": c["email"], "score": round(score, 3),
                "stays": stays[0] or 0, "last_stay": stays[1] or "",
            })
    scored.sort(key=lambda x: (-x["score"], -x["stays"]))
    return scored


def find_or_create_guest(name, phone="", email=""):
    if not name or not name.strip():
        raise ValueError("Guest name is required.")
    matches = find_guest_matches(name, phone, email, threshold=0.95)
    if matches:
        return matches[0]["id"], matches[0]["name"], False
    from ..db import transaction
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO guests (canonical_name, phone, email) VALUES (?, ?, ?)",
            (name.strip(), phone.strip(), email.strip()))
        return cur.lastrowid, name.strip(), True


def get_all_guests(search=""):
    from ..db import connect
    q = f"%{search}%" if search else None
    base_sql = """
        SELECT g.id, g.canonical_name, g.phone, g.email, g.notes,
               COUNT(r.id) AS stays,
               MAX(r.check_in_date) AS last_stay,
               COALESCE(SUM(CASE WHEN r.status NOT IN ('Cancelled','No-Show','Deleted') THEN
                   r.agreed_nightly_rate * MAX(1, CAST(julianday(r.check_out_date)-julianday(r.check_in_date) AS INT)) + r.tax_amount
               ELSE 0 END), 0) AS total
        FROM guests g LEFT JOIN reservations r ON r.guest_id = g.id
        {where}
        GROUP BY g.id ORDER BY g.canonical_name
    """
    if q:
        rows = db.connect().execute(base_sql.format(where="WHERE g.canonical_name LIKE ? OR g.phone LIKE ? OR g.email LIKE ?"), (q, q, q)).fetchall()
    else:
        rows = db.connect().execute(base_sql.format(where=""),).fetchall()
    # enrich with real outstanding (sum balances across non-deleted/cancelled/no-show? balance func already handles)
    out = []
    for r in rows:
        d = dict(r)
        try:
            due = 0.0
            res_ids = db.connect().execute("SELECT id FROM reservations WHERE guest_id=?", (r["id"],)).fetchall()
            for rr in res_ids:
                try:
                    b = calculate_guest_balance(rr["id"])
                    if b and b > 0:
                        due += b
                except Exception:
                    pass
            d["outstanding"] = round(due, 2)
        except Exception:
            d["outstanding"] = 0.0
        out.append(d)
    return out


def get_guest_by_id(guest_id):
    from ..db import connect
    row = db.connect().execute(
        "SELECT id, canonical_name, phone, email, notes FROM guests WHERE id=?", (guest_id,)).fetchone()
    if not row:
        return None
    return {"id": row["id"], "name": row["canonical_name"], "phone": row["phone"],
            "email": row["email"], "notes": row["notes"]}


def get_guest_history_by_id(guest_id):
    from ..db import connect
    return db.connect().execute("""
        SELECT r.id, rm.room_number, r.check_in_date, r.check_out_date, r.status,
               r.agreed_nightly_rate, r.tax_amount
        FROM reservations r JOIN rooms rm ON r.room_id = rm.id
        WHERE r.guest_id=? ORDER BY r.check_in_date DESC
    """, (guest_id,)).fetchall()


def assistant_find_guests(query, limit=10):
    """Read-only guest search for manager assistant (real backend)."""
    q = (query or "").strip()
    if not q:
        return []
    limit = max(1, min(int(limit or 10), 25))
    rows = get_all_guests(q)
    out = []
    for r in rows[:limit]:
        out.append({
            "id": r["id"], "name": r["canonical_name"], "phone": r["phone"], "email": r["email"],
            "stays": r["stays"], "last_stay": r["last_stay"], "total_billed": round(float(r["total"] or 0), 2),
        })
    return out


def assistant_get_guest_last_visit(guest_id):
    """Return the guest's last visit reservation (real backend)."""
    from ..db import connect
    guest = get_guest_by_id(guest_id)
    if not guest:
        return None
    row = db.connect().execute("""
        SELECT r.id, r.guest_id, r.guest_name, rm.room_number, r.check_in_date, r.check_out_date,
               r.status, r.agreed_nightly_rate, r.tax_amount, rp.name AS rate_plan_name
        FROM reservations r
        JOIN rooms rm ON r.room_id = rm.id
        LEFT JOIN rate_plans rp ON r.rate_plan_id = rp.id
        WHERE r.guest_id=? AND r.status != 'Deleted'
        ORDER BY CASE WHEN r.status='Checked Out' THEN 0 ELSE 1 END,
                 r.check_out_date DESC, r.check_in_date DESC, r.id DESC
        LIMIT 1
    """, (guest_id,)).fetchone()
    if not row:
        return {"guest": guest, "last_visit": None, "reason": "No reservations found for this guest."}
    return {
        "guest": guest,
        "last_visit": {k: row[k] for k in row.keys()},
        "definition": "Most recent Checked Out reservation by check-out date; otherwise most recent non-deleted reservation.",
    }


def assistant_get_top_items_for_reservation(reservation_id, limit=5):
    """Top ordered POS items for a reservation, excluding voided items (real backend)."""
    from ..db import connect
    rid = int(reservation_id)
    limit = max(1, min(int(limit or 5), 25))
    rows = db.connect().execute("""
        SELECT COALESCE(NULLIF(item_description, ''), 'Item') AS item_name,
               COALESCE(inventory_item_id, 0) AS inventory_item_id,
               SUM(quantity) AS total_qty,
               ROUND(SUM(quantity * unit_price), 2) AS net_amount,
               ROUND(SUM(tax_amount), 2) AS tax_amount
        FROM pos_tabs
        WHERE reservation_id=? AND voided_at IS NULL
        GROUP BY COALESCE(inventory_item_id, 0), COALESCE(NULLIF(item_description, ''), 'Item')
        ORDER BY total_qty DESC, net_amount DESC, item_name ASC
        LIMIT ?
    """, (rid, limit)).fetchall()
    return [dict(r) for r in rows]


def assistant_get_top_items_last_visit(guest_id, limit=5):
    """What a guest ordered most on their last visit (real backend)."""
    visit = assistant_get_guest_last_visit(guest_id)
    if not visit or not visit.get("last_visit"):
        return {"guest": (visit or {}).get("guest") or get_guest_by_id(guest_id), "last_visit": None, "items": [], "reason": (visit or {}).get("reason") or "No last visit found."}
    rid = visit["last_visit"]["id"]
    items = assistant_get_top_items_for_reservation(rid, limit=limit)
    return {
        "guest": visit["guest"],
        "last_visit": visit["last_visit"],
        "reservation_id": rid,
        "items": items,
        "note": "Non-voided POS items for the last visit reservation, ranked by total quantity." if items else "No non-voided POS items found for the last visit.",
    }


def assistant_reservation_summary(reservation_id):
    """Read-only reservation summary with balance and POS totals (real backend)."""
    from ..db import connect
    rid = int(reservation_id)
    row = db.connect().execute("""
        SELECT r.*, rm.room_number, rp.name AS rate_plan_name, g.canonical_name AS guest_canonical_name
        FROM reservations r
        JOIN rooms rm ON r.room_id = rm.id
        LEFT JOIN rate_plans rp ON r.rate_plan_id = rp.id
        LEFT JOIN guests g ON r.guest_id = g.id
        WHERE r.id=?
    """, (rid,)).fetchone()
    if not row:
        return None
    bal = None
    try:
        bal = _svc_attr("calculate_guest_balance", calculate_guest_balance)(rid)
    except Exception as e:
        bal = {"error": str(e)}
    pos_count = db.connect().execute("SELECT COUNT(*) FROM pos_tabs WHERE reservation_id=? AND voided_at IS NULL", (rid,)).fetchone()[0]
    return {"reservation": {k: row[k] for k in row.keys()}, "balance": bal, "active_pos_item_lines": pos_count}


def update_guest(guest_id, name, phone, email, notes, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if not name or not name.strip():
        raise ValueError("Name is required.")
    from ..db import transaction
    with transaction() as conn:
        conn.execute("""UPDATE guests SET canonical_name=?, phone=?, email=?, notes=?,
                        updated_at=datetime('now','localtime') WHERE id=?""",
                     (name.strip(), phone.strip(), email.strip(), notes.strip(), guest_id))
    _audit("guest_update", "guest", guest_id, name)


def get_duplicate_suggestions():
    """Fast: only compares same first-letter + same phone-tail buckets."""
    from ..db import connect
    guests = db.connect().execute("SELECT id, canonical_name, phone FROM guests").fetchall()
    buckets = {}
    for g in guests:
        key = (_norm_text(g["canonical_name"])[:1] or "?", _norm_phone(g["phone"])[-4:] or "")
        buckets.setdefault(key, []).append(g)
    suggestions = []
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue  # pragma: no cover
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                a, b = bucket[i], bucket[j]
                score = _name_score(a["canonical_name"], b["canonical_name"])
                if _norm_phone(a["phone"]) and _norm_phone(a["phone"]) == _norm_phone(b["phone"]):
                    score = max(score, 1.0)  # pragma: no cover
                if score >= 0.90:
                    suggestions.append({"id_a": a["id"], "name_a": a["canonical_name"],
                                        "id_b": b["id"], "name_b": b["canonical_name"],
                                        "score": round(score, 3)})
    suggestions.sort(key=lambda x: -x["score"])
    return suggestions[:20]


def merge_guests(winner_id, loser_id, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if winner_id == loser_id:
        raise ValueError("Cannot merge into self.")
    w = get_guest_by_id(winner_id)
    l = get_guest_by_id(loser_id)
    if not w or not l:
        raise ValueError("Guest not found.")
    from ..db import transaction
    with transaction() as conn:
        cur = conn.execute("UPDATE reservations SET guest_id=? WHERE guest_id=?", (winner_id, loser_id))
        moved = cur.rowcount
        if l["name"].lower() not in w["notes"].lower():
            nn = (w["notes"] + "\n" if w["notes"] else "") + f"aka: {l['name']}"
            conn.execute("UPDATE guests SET notes=? WHERE id=?", (nn, winner_id))
        conn.execute("DELETE FROM guests WHERE id=?", (loser_id,))
    _audit("guest_merge", "guest", winner_id, f"merged {loser_id}, {moved} reservations")
    return moved



def _list_invoice_files_for_reservation(rid):
    rid = int(rid)
    files = []
    try:
        INVOICE_DIR.mkdir(exist_ok=True)
        for pat in (f"invoice_Res{rid}_*.txt", f"invoice_Res{rid}_*.pdf"):
            for fp in sorted(INVOICE_DIR.glob(pat), reverse=True):
                try:
                    st = fp.stat()
                    files.append({"name": fp.name, "path": str(fp), "size": st.st_size, "modified": datetime.fromtimestamp(st.st_mtime).strftime(DATETIME_FMT)})
                except Exception:
                    files.append({"name": fp.name, "path": str(fp)})
    except Exception:
        pass
    return files


def get_guest_portfolio(guest_id):
    """Comprehensive guest portfolio: profile, stats, trips/reservations, POS, payments, invoices (real data)."""
    from ..db import connect
    gid = int(guest_id)
    guest = get_guest_by_id(gid)
    if not guest:
        return None
    conn = db.connect()
    # reservations/trips (all, including cancelled/no-show for history; mark clearly)
    rows = conn.execute("""
        SELECT r.*, rm.room_number, rm.room_type, rp.name AS rate_plan_name
        FROM reservations r
        LEFT JOIN rooms rm ON r.room_id = rm.id
        LEFT JOIN rate_plans rp ON r.rate_plan_id = rp.id
        WHERE r.guest_id=?
        ORDER BY r.check_in_date DESC, r.id DESC
    """, (gid,)).fetchall()

    trips = []
    all_pos = []
    all_payments = []
    total_nights = 0
    total_charges = 0.0
    total_paid = 0.0
    total_outstanding = 0.0
    completed_stays = 0
    room_counts = {}
    first_visit = None
    last_visit = None

    for r in rows:
        rid = r["id"]
        try:
            nights = _nights(r["check_in_date"], r["check_out_date"])
        except Exception:
            nights = 0
        bd = get_balance_breakdown(rid) or {}
        pos = [dict(x) for x in conn.execute(
            "SELECT id, item_description, quantity, unit_price, tax_amount, voided_at, created_at, batch_id, lot_code, inventory_item_id, activity_id, cost_at_sale FROM pos_tabs WHERE reservation_id=? ORDER BY id",
            (rid,)).fetchall()]
        pos_active = [x for x in pos if not x.get("voided_at")]
        pays = [dict(x) for x in conn.execute(
            "SELECT id, amount, method, payment_type, notes, voided_at, at FROM payments WHERE reservation_id=? ORDER BY id",
            (rid,)).fetchall()]
        invoices = _list_invoice_files_for_reservation(rid)
        charges = float(bd.get("total_charges") or 0)
        paid = float(bd.get("total_paid") or 0)
        due = float(bd.get("balance_due") or 0)
        status = r["status"]
        if status not in ("Cancelled", "No-Show", "Deleted"):
            total_charges += charges
            total_paid += paid
            if due > 0:
                total_outstanding += due
            total_nights += max(0, nights)
        if status == "Checked Out":
            completed_stays += 1
        rn = r["room_number"] or ""
        if rn:
            room_counts[rn] = room_counts.get(rn, 0) + 1
        ci = r["check_in_date"]
        if ci:
            first_visit = ci if not first_visit or ci < first_visit else first_visit
            last_visit = ci if not last_visit or ci > last_visit else last_visit

        trip = {
            "id": rid, "room_number": r["room_number"], "room_type": r["room_type"],
            "check_in_date": r["check_in_date"], "check_out_date": r["check_out_date"],
            "nights": max(0, nights), "status": status, "source": r["source"],
            "rate_plan": r["rate_plan_name"], "agreed_nightly_rate": float(r["agreed_nightly_rate"] or 0),
            "tax_amount": float(r["tax_amount"] or 0), "notes": r["notes"] or "",
            "created_at": r["created_at"],
            "balance": bd, "pos_items": pos, "pos_active": pos_active, "payments": pays,
            "invoices": invoices,
        }
        trips.append(trip)
        for x in pos_active:
            all_pos.append({**x, "reservation_id": rid, "room_number": r["room_number"], "check_in_date": r["check_in_date"]})
        for x in pays:
            if not x.get("voided_at"):
                all_payments.append({**x, "reservation_id": rid, "room_number": r["room_number"], "check_in_date": r["check_in_date"]})

    # spend by category (POS items)
    pos_totals = {}
    for x in all_pos:
        name = (x.get("item_description") or "Item").strip()
        qty = float(x.get("quantity") or 0)
        gross = qty * float(x.get("unit_price") or 0) + float(x.get("tax_amount") or 0)
        pos_totals[name] = pos_totals.get(name, 0.0) + gross
    top_items = sorted([{"item": k, "gross": round(v, 2)} for k, v in pos_totals.items()], key=lambda i: i["gross"], reverse=True)[:10]

    favorite_room = None
    if room_counts:
        favorite_room = sorted(room_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]

    stats = {
        "stays_total": len(trips),
        "completed_stays": completed_stays,
        "total_nights": total_nights,
        "total_charges": round(total_charges, 2),
        "total_paid": round(total_paid, 2),
        "total_outstanding": round(total_outstanding, 2),
        "pos_gross_total": round(sum(t["gross"] for t in top_items) if top_items else 0.0, 2),
        "first_visit": first_visit,
        "last_visit": last_visit,
        "favorite_room": favorite_room,
    }
    # add grand pos total accurately
    stats["pos_gross_total"] = round(sum(float(x.get("quantity") or 0) * float(x.get("unit_price") or 0) + float(x.get("tax_amount") or 0) for x in all_pos), 2)

    return {
        "guest": guest,
        "stats": stats,
        "trips": trips,
        "all_pos": sorted(all_pos, key=lambda x: (x.get("created_at") or "", x.get("id") or 0), reverse=True),
        "all_payments": sorted(all_payments, key=lambda x: (x.get("at") or "", x.get("id") or 0), reverse=True),
        "top_items_overall": top_items,
    }


def get_all_rooms():
    from ..db import connect
    return db.connect().execute(
        "SELECT id, room_number, room_type, base_price, housekeeping_status FROM rooms ORDER BY room_number").fetchall()


def get_room(room_id):
    from ..db import connect
    row = db.connect().execute(
        "SELECT id, room_number, room_type, base_price, housekeeping_status FROM rooms WHERE id=?",
        (room_id,)).fetchone()
    if not row:
        return None
    return {"id": row["id"], "room_number": row["room_number"], "room_type": row["room_type"],
            "base_price": row["base_price"], "housekeeping_status": row["housekeeping_status"]}


def update_housekeeping_status(room_id, status):
    if status not in ("Clean", "Dirty", "Maintenance"):
        raise ValueError("Invalid status.")
    from ..db import transaction
    with transaction() as conn:
        conn.execute("UPDATE rooms SET housekeeping_status=? WHERE id=?", (status, room_id))


def update_room_price(room_id, new_price, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if new_price < 0:
        raise ValueError("Price cannot be negative.")
    from ..db import transaction
    with transaction() as conn:
        conn.execute("UPDATE rooms SET base_price=? WHERE id=?", (new_price, room_id))
    _audit("room_price_change", "room", room_id, f"new={new_price}")


def update_room_type(room_id, new_type, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if not new_type or not new_type.strip():
        raise ValueError("Type cannot be empty.")
    from ..db import transaction
    with transaction() as conn:
        conn.execute("UPDATE rooms SET room_type=? WHERE id=?", (new_type.strip(), room_id))
    _audit("room_type_change", "room", room_id, new_type)


# ═══════════════════════════════════════════════════════════════════════════
# RATE PLANS
# ═══════════════════════════════════════════════════════════════════════════

def get_all_rate_plans():
    from ..db import connect
    return db.connect().execute("SELECT id, name, price_modifier FROM rate_plans ORDER BY id").fetchall()


def add_rate_plan(name, modifier, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if not name or not name.strip():
        raise ValueError("Name is required.")
    from ..db import connect, transaction


    if db.connect().execute("SELECT 1 FROM rate_plans WHERE LOWER(name)=LOWER(?)", (name.strip(),)).fetchone():
        raise ValueError("Name already exists.")
    with transaction() as conn:
        conn.execute("INSERT INTO rate_plans (name, price_modifier) VALUES (?, ?)",
                     (name.strip(), float(modifier)))
    _audit("rate_plan_add", "rate_plan", "", name)


def update_rate_plan(plan_id, name, modifier, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if not name or not name.strip():
        raise ValueError("Name is required.")
    from ..db import connect, transaction


    if db.connect().execute("SELECT 1 FROM rate_plans WHERE LOWER(name)=LOWER(?) AND id!=?",
                         (name.strip(), plan_id)).fetchone():
        raise ValueError("Name already exists.")
    with transaction() as conn:
        conn.execute("UPDATE rate_plans SET name=?, price_modifier=? WHERE id=?",
                     (name.strip(), float(modifier), plan_id))
    _audit("rate_plan_update", "rate_plan", plan_id, name)


def delete_rate_plan(plan_id, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    from ..db import connect, transaction


    used = db.connect().execute("SELECT COUNT(*) FROM reservations WHERE rate_plan_id=?", (plan_id,)).fetchone()[0]
    if used > 0:
        raise ValueError(f"Cannot delete: {used} reservations use it.")
    with transaction() as conn:
        conn.execute("DELETE FROM rate_plans WHERE id=?", (plan_id,))
    _audit("rate_plan_delete", "rate_plan", plan_id, "")


# ═══════════════════════════════════════════════════════════════════════════
# RESERVATIONS
# ═══════════════════════════════════════════════════════════════════════════


def count_reservations_using_plan(plan_id):
    from ..db import connect
    return db.connect().execute(
        "SELECT COUNT(*) FROM reservations WHERE rate_plan_id=?", (plan_id,)
    ).fetchone()[0]

STATUS_TRANSITIONS = {
    "Reserved": ["Checked In", "Cancelled", "No-Show"],
    "Checked In": ["Checked Out"],
    "Checked Out": [],
    "Cancelled": [],
    "No-Show": [],
    "Deleted": [],
}


def _nights(ci, co):
    d1 = datetime.strptime(ci, DATE_FMT)
    d2 = datetime.strptime(co, DATE_FMT)
    return max(1, (d2 - d1).days)


def check_room_availability(room_id, ci, co, exclude_res_id=None):
    from ..db import connect
    q = """SELECT COUNT(*) FROM reservations WHERE room_id=?
           AND status IN ('Reserved','Checked In')
           AND NOT (check_out_date <= ? OR check_in_date >= ?)"""
    p = [room_id, ci, co]
    if exclude_res_id is not None:
        q += " AND id != ?"
        p.append(exclude_res_id)
    return db.connect().execute(q, p).fetchone()[0] == 0


def create_reservation(guest_name, source, room_id, rate_plan_id, agreed_nightly_rate,
                       ci, co, deposit_amount=0.0, guest_id=None):
    if not guest_name or not guest_name.strip():
        raise ValueError("Guest name is required.")
    if not isinstance(room_id, int) or room_id <= 0:
        raise ValueError("Room ID must be a positive integer.")
    if agreed_nightly_rate < 0:
        raise ValueError("Rate cannot be negative.")
    try:
        d1 = datetime.strptime(ci, DATE_FMT)
        d2 = datetime.strptime(co, DATE_FMT)
    except ValueError:
        raise ValueError("Dates must be YYYY-MM-DD.")
    if d2 <= d1:
        raise ValueError("Check-out must be after check-in.")
    nights = _nights(ci, co)
    if nights < int(get_setting("min_nights", "1")):
        raise ValueError(f"Stay must be at least {get_setting('min_nights')} nights.")
    if nights > int(get_setting("max_nights", "365")):
        raise ValueError(f"Stay must be at most {get_setting('max_nights')} nights.")
    if rate_plan_id is not None and not isinstance(rate_plan_id, int):
        rate_plan_id = None
    allow_over = get_setting("allow_overbooking", "false") == "true"
    if not allow_over and not check_room_availability(room_id, ci, co):
        raise ValueError("Room is already booked for these dates.")
    if guest_id is None:
        gid, canon, _ = find_or_create_guest(guest_name)
    else:
        gid, canon = guest_id, guest_name
    if deposit_amount < 0:
        raise ValueError("Deposit cannot be negative.")

    tax = calculate_tax(agreed_nightly_rate * nights, nights)
    from ..db import transaction
    with transaction() as conn:
        cur = conn.execute("""INSERT INTO reservations
            (guest_id, guest_name, source, room_id, rate_plan_id, agreed_nightly_rate, tax_amount,
             check_in_date, check_out_date, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Reserved')""",
            (gid, canon, source, room_id, rate_plan_id, agreed_nightly_rate,
             tax["service"] + tax["vat"] + tax["city_tax"], ci, co))
        rid = cur.lastrowid
        _post(cur, "RES", rid, "CHARGE", "Accommodation",
              tax["net"], tax["service"], tax["vat"], tax["city_tax"], "",
              f"Accommodation Res #{rid} - {canon}")
        if deposit_amount > 0:
            conn.execute("""INSERT INTO payments
                (reservation_id, amount, method, payment_type, notes)
                VALUES (?, ?, 'Cash', 'Deposit', 'Deposit at booking')""",
                (rid, deposit_amount))
            pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            _post(cur, "PAY", pid, "PAYMENT", "", 0, 0, 0, 0, "Cash",
                  f"Deposit Res #{rid}", )
    _audit("reservation_create", "reservation", rid, canon)
    return rid


def update_reservation_status(rid, new_status):
    from ..db import connect, transaction


    row = db.connect().execute("SELECT status, room_id FROM reservations WHERE id=?", (rid,)).fetchone()
    if not row:
        raise ValueError("Reservation not found.")
    current = row["status"]
    if new_status not in STATUS_TRANSITIONS.get(current, []):
        raise ValueError(f"Cannot move from '{current}' to '{new_status}'.")
    with transaction() as conn:
        conn.execute("UPDATE reservations SET status=? WHERE id=?", (new_status, rid))
        if new_status in ("Checked Out", "Cancelled", "No-Show"):
            conn.execute("UPDATE rooms SET housekeeping_status='Dirty' WHERE id=?", (row["room_id"],))
    _audit("reservation_status", "reservation", rid, f"{current} -> {new_status}")


def checkout_requires_override(rid):
    bal = calculate_guest_balance(rid)
    requires = get_setting("checkout_requires_payment", "false") == "true"
    thresh = float(get_setting("checkout_unpaid_threshold_egp", "0.01") or 0.01)
    return bool(requires and bal > thresh), round(bal, 2), thresh


def checkout_reservation(rid, override=False, password=None):
    needs_override, bal, _thresh = checkout_requires_override(rid)
    if needs_override and not override:
        raise ValueError(f"Unpaid balance {fmt(bal)} requires override.")
    if override:
        if not password or not verify_password(password):
            raise PermissionError("Invalid super password.")
        _audit("checkout_override", "reservation", rid, f"balance {bal}")
    update_reservation_status(rid, "Checked Out")
    return {"rid": rid, "balance": round(bal, 2), "override": bool(override)}


def _cancellation_refund_amount(cur, rid, policy):
    """How much of the payments to refund (0 for full keep, or partial)."""
    paid = cur.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE reservation_id=? AND voided_at IS NULL",
        (rid,)).fetchone()[0] or 0
    if policy == "full_refund":
        return paid, paid
    if policy == "keep_deposit":
        dep = cur.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE reservation_id=? AND payment_type='Deposit' AND voided_at IS NULL",
            (rid,)).fetchone()[0] or 0
        return paid, max(0.0, round(paid - dep, 2))
    if policy == "keep_first_night":
        r = cur.execute("SELECT agreed_nightly_rate FROM reservations WHERE id=?", (rid,)).fetchone()
        first = (r["agreed_nightly_rate"] if r else 0) or 0
        return paid, max(0.0, round(paid - first, 2))
    return paid, paid  # pragma: no cover


def estimate_cancellation_outcome(rid, mode):
    """Pure estimate for UI: no mutation, mirrors cancel/no-show policy math."""
    from ..db import connect
    conn = db.connect()
    row = conn.execute(
        "SELECT status, agreed_nightly_rate FROM reservations WHERE id=?",
        (rid,),
    ).fetchone()
    if not row:
        return None
    policy = get_setting("cancel_policy" if mode == "cancel" else "noshow_policy", "full_refund")
    paid, refund = _cancellation_refund_amount(conn, rid, policy)
    return {
        "rid": rid,
        "mode": mode,
        "status": row["status"],
        "policy": policy,
        "total_paid": round(paid, 2),
        "estimated_refund": round(refund, 2),
        "kept_amount": round(max(0.0, paid - refund), 2),
    }


def _reservation_has_payments(rid, conn=None):
    from ..db import connect as _connect
    c = conn or _connect()
    return bool(c.execute(
        "SELECT 1 FROM payments WHERE reservation_id=? AND voided_at IS NULL LIMIT 1", (rid,)
    ).fetchone())


def cancel_reservation(rid, password=None):
    from ..db import connect, transaction


    row = db.connect().execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()
    if not row:
        raise ValueError("Reservation not found.")
    if "Cancelled" not in STATUS_TRANSITIONS.get(row["status"], []):
        raise ValueError(f"Cannot cancel a '{row['status']}' reservation.")
    if _reservation_has_payments(rid) and not verify_password(password or ""):
        raise PermissionError("Super password required to cancel a paid or partially paid reservation.")
    policy = get_setting("cancel_policy", "full_refund")
    with transaction() as conn:
        cur = conn.cursor()
        paid, refund = _cancellation_refund_amount(cur, rid, policy)
        _reverse_charges_for_reservation(cur, rid)
        _void_pos_for_reservation(cur, rid)
        if refund > 0:
            conn.execute("""INSERT INTO payments
                (reservation_id, amount, method, payment_type, notes)
                VALUES (?, ?, 'Cash', 'Refund', ?)""", (rid, -refund, f"Cancel policy: {policy}"))
            pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            _post(cur, "PAY", pid, "REFUND", "", 0, 0, 0, 0, "Cash", f"Refund Res #{rid}",
                  gross_override=-refund)
        conn.execute("UPDATE reservations SET status='Cancelled' WHERE id=?", (rid,))
        conn.execute("UPDATE rooms SET housekeeping_status='Dirty' WHERE id=(SELECT room_id FROM reservations WHERE id=?)", (rid,))
    _audit("reservation_cancel", "reservation", rid, f"policy={policy} refund={refund}")


def mark_no_show(rid, password=None):
    from ..db import connect, transaction


    row = db.connect().execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()
    if not row:
        raise ValueError("Reservation not found.")
    if "No-Show" not in STATUS_TRANSITIONS.get(row["status"], []):
        raise ValueError(f"Cannot mark '{row['status']}' as no-show.")  # pragma: no cover
    if _reservation_has_payments(rid) and not verify_password(password or ""):
        raise PermissionError("Super password required to mark a paid or partially paid reservation as no-show.")
    policy = get_setting("noshow_policy", "keep_first_night")
    with transaction() as conn:
        cur = conn.cursor()
        paid, refund = _cancellation_refund_amount(cur, rid, policy)
        _reverse_charges_for_reservation(cur, rid)
        _void_pos_for_reservation(cur, rid)
        if refund > 0:
            conn.execute("""INSERT INTO payments
                (reservation_id, amount, method, payment_type, notes)
                VALUES (?, ?, 'Cash', 'Refund', ?)""", (rid, -refund, f"No-show policy: {policy}"))
            pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            _post(cur, "PAY", pid, "REFUND", "", 0, 0, 0, 0, "Cash", f"Refund Res #{rid}",
                  gross_override=-refund)
        conn.execute("UPDATE reservations SET status='No-Show' WHERE id=?", (rid,))
        conn.execute("UPDATE rooms SET housekeeping_status='Dirty' WHERE id=(SELECT room_id FROM reservations WHERE id=?)", (rid,))
    _audit("reservation_no_show", "reservation", rid, f"policy={policy} refund={refund}")


def _reverse_charges_for_reservation(cur, rid):
    _reverse(cur, "RES", rid, entry_type_filter=("CHARGE",))


def _void_pos_for_reservation(cur, rid):
    rows = cur.execute(
        "SELECT * FROM pos_tabs WHERE reservation_id=? AND voided_at IS NULL", (rid,)).fetchall()
    for r in rows:
        _void_pos_item_inner(cur, r)


def _void_pos_item_inner(cur, pos_row):
    pid = pos_row["id"]
    _reverse(cur, "POS", pid, entry_type_filter=("CHARGE", "COGS"))

    inv_id = None
    try:
        inv_id = pos_row["inventory_item_id"]
    except Exception:
        inv_id = None
    qty = float(pos_row["quantity"] or 0)

    # Restore recipe ingredient deductions (if any) using recorded OUT_RECIPE logs for this POS.
    # This is required because recipe sales deduct ingredient stock/batches, not the menu item stock.
    try:
        recipe_logs = cur.execute(
            "SELECT id, item_id, quantity, batch_id, unit_cost, reference FROM inventory_logs WHERE change_type='OUT_RECIPE' AND reference LIKE ? ORDER BY id",
            (f"POS #{pid} %",),
        ).fetchall()
    except Exception:
        recipe_logs = []

    if recipe_logs:
        for lg in recipe_logs:
            try:
                lqty = float(lg["quantity"] or 0)
            except Exception:
                lqty = 0.0
            if abs(lqty) <= 1e-12:
                continue
            try:
                bid = lg["batch_id"]
            except Exception:
                bid = None
            if bid:
                cur.execute(
                    "UPDATE inventory_batches SET quantity_remaining = quantity_remaining + ?, status=CASE WHEN quantity_remaining + ? > 0 AND status='depleted' THEN 'active' ELSE status END WHERE id=?",
                    (lqty, lqty, bid),
                )
            cur.execute("UPDATE inventory SET stock_qty = stock_qty + ? WHERE id=?", (lqty, lg["item_id"]))
            cur.execute(
                """INSERT INTO inventory_logs (item_id, change_type, quantity, reference, batch_id, unit_cost)
                   VALUES (?, 'IN_VOID_RECIPE', ?, ?, ?, ?)""",
                (lg["item_id"], lqty, f"void recipe POS #{pid}", bid, lg["unit_cost"] if "unit_cost" in lg.keys() else None),
            )
        # Menu/recipe item itself does not hold physical stock in recipe sales.
    elif inv_id:
        # Standard inventory (or non-recipe) sale: restore menu/inventory item and its batch if recorded.
        bid = None
        try:
            bid = pos_row["batch_id"]
        except Exception:
            bid = None
        lot = None
        try:
            lot = pos_row["lot_code"]
        except Exception:
            lot = None
        if bid:
            cur.execute(
                "UPDATE inventory_batches SET quantity_remaining = quantity_remaining + ?, status=CASE WHEN quantity_remaining + ? > 0 AND status='depleted' THEN 'active' ELSE status END WHERE id=?",
                (qty, qty, bid),
            )
        cur.execute("UPDATE inventory SET stock_qty = stock_qty + ? WHERE id=?", (qty, inv_id))
        ref = f"void POS #{pid}" + (f" lot {lot}" if lot else "")
        cur.execute(
            """INSERT INTO inventory_logs (item_id, change_type, quantity, reference, batch_id)
               VALUES (?, 'IN_VOID', ?, ?, ?)""",
            (inv_id, qty, ref, bid),
        )

    cur.execute("UPDATE pos_tabs SET voided_at=datetime('now','localtime') WHERE id=?", (pid,))


def modify_reservation(rid, new_rate, new_ci, new_co, new_room_id, password=None):
    try:
        d1 = datetime.strptime(new_ci, DATE_FMT)
        d2 = datetime.strptime(new_co, DATE_FMT)
    except ValueError:
        raise ValueError("Dates must be YYYY-MM-DD.")
    if d2 <= d1:
        raise ValueError("Check-out must be after check-in.")  # pragma: no cover
    if new_rate < 0:
        raise ValueError("Rate cannot be negative.")
    nights = _nights(new_ci, new_co)
    if nights < int(get_setting("min_nights", "1")):
        raise ValueError("Below min nights.")  # pragma: no cover
    if nights > int(get_setting("max_nights", "365")):
        raise ValueError("Above max nights.")  # pragma: no cover
    allow_over = get_setting("allow_overbooking", "false") == "true"
    if not allow_over and not check_room_availability(new_room_id, new_ci, new_co, exclude_res_id=rid):
        raise ValueError("Room not available.")
    from ..db import connect, transaction


    row = db.connect().execute(
        "SELECT status, room_id, agreed_nightly_rate, tax_amount FROM reservations WHERE id=?",
        (rid,)).fetchone()
    if not row:
        raise ValueError("Reservation not found.")
    if row["status"] not in ("Reserved", "Checked In"):
        raise ValueError(f"Cannot modify a '{row['status']}' reservation.")
    tax = calculate_tax(new_rate * nights, nights)
    with transaction() as conn:
        cur = conn.cursor()
        _reverse(cur, "RES", rid, entry_type_filter=("CHARGE",))
        _post(cur, "RES", rid, "CHARGE", "Accommodation",
              tax["net"], tax["service"], tax["vat"], tax["city_tax"], "",
              f"Modified accommodation Res #{rid}")
        conn.execute("""UPDATE reservations SET agreed_nightly_rate=?, tax_amount=?,
                        check_in_date=?, check_out_date=?, room_id=? WHERE id=?""",
                     (new_rate, tax["service"] + tax["vat"] + tax["city_tax"],
                      new_ci, new_co, new_room_id, rid))
        if row["room_id"] != new_room_id:
            conn.execute("UPDATE rooms SET housekeeping_status='Dirty' WHERE id=?", (row["room_id"],))
    _audit("reservation_modify", "reservation", rid, f"rate={new_rate} room={new_room_id}")


def delete_reservation(rid, password=None):
    """Only allowed for Reserved with no payments/POS."""
    from ..db import transaction
    with transaction() as conn:
        row = conn.execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()
        if not row:
            raise ValueError("Reservation not found.")
        if row["status"] != "Reserved":
            raise ValueError("Only 'Reserved' reservations can be deleted; cancel others instead.")
        if conn.execute("SELECT 1 FROM payments WHERE reservation_id=? AND voided_at IS NULL", (rid,)).fetchone():
            raise ValueError("Cannot delete a reservation with payments. Cancel instead.")
        if conn.execute("SELECT 1 FROM pos_tabs WHERE reservation_id=? AND voided_at IS NULL", (rid,)).fetchone():
            raise ValueError("Cannot delete a reservation with POS items. Cancel instead.")
        cur = conn.cursor()
        _reverse(cur, "RES", rid)
        conn.execute("UPDATE reservations SET status='Deleted' WHERE id=?", (rid,))
    _audit("reservation_delete", "reservation", rid, "")


def get_reservation(rid):
    from ..db import connect
    return db.connect().execute("SELECT * FROM reservations WHERE id=?", (rid,)).fetchone()



# ═══════════════════════════════════════════════════════════════════════════
# BARCODES / BATCHES / TRACEABILITY
# ═══════════════════════════════════════════════════════════════════════════

def _row_to_dict(row):
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return row


def resolve_barcode(code):
    """Resolve a scanned/entered code to item, batch, or embedded payload."""
    from ..db import connect
    raw = barcodes.normalize_code(code)
    if not raw:
        return {"type": "unknown", "code": "", "item": None, "batch": None, "payload": None}
    payload = barcodes.try_decode_payload(raw)
    lot = raw
    if payload:
        lot = payload.get("lot") or raw
    conn = db.connect()
    batch = conn.execute("SELECT * FROM inventory_batches WHERE lot_code=? OR barcode_value=? ORDER BY id DESC LIMIT 1", (lot, raw)).fetchone()
    item = None
    if batch:
        item = conn.execute("SELECT * FROM inventory WHERE id=?", (batch["item_id"],)).fetchone()
        warn = int(get_setting("expiry_warn_days", "7") or 7)
        status, days = barcodes.expiry_status(batch["expiry_date"], warn)
        b = _row_to_dict(batch)
        b["expiry_state"] = status
        b["days_to_expiry"] = days
        return {"type": "batch", "code": raw, "item": _row_to_dict(item), "batch": b, "payload": payload}
    item = conn.execute("""SELECT * FROM inventory WHERE active=1 AND
        (barcode = ? OR sku = ? OR lower(item_name)=lower(?)) LIMIT 1""", (raw, raw, raw)).fetchone()
    if item:
        return {"type": "item", "code": raw, "item": _row_to_dict(item), "batch": None, "payload": payload}
    # try inactive explicit barcode/sku
    item = conn.execute("""SELECT * FROM inventory WHERE barcode = ? OR sku = ? LIMIT 1""", (raw, raw)).fetchone()
    if item:
        return {"type": "item", "code": raw, "item": _row_to_dict(item), "batch": None, "payload": payload}
    return {"type": "unknown", "code": raw, "item": None, "batch": None, "payload": payload}


def get_inventory_item(item_id):
    from ..db import connect
    return db.connect().execute("SELECT * FROM inventory WHERE id=?", (item_id,)).fetchone()


def get_inventory_items(query="", category="", stock_filter="", include_inactive=False):
    """Web/TUI-friendly inventory listing with barcode/SKU search."""
    from ..db import connect
    q = (query or "").strip() if isinstance(query, str) else ""
    cat = "" if not isinstance(category, str) or category == "All" else category
    sf = "" if not isinstance(stock_filter, str) or stock_filter == "All" else stock_filter
    sql = """SELECT i.*,
        (SELECT COUNT(1) FROM inventory_batches b WHERE b.item_id=i.id AND b.status='active' AND b.quantity_remaining>0) AS active_batches,
        (SELECT MIN(b.expiry_date) FROM inventory_batches b WHERE b.item_id=i.id AND b.status='active' AND b.quantity_remaining>0 AND b.expiry_date IS NOT NULL AND b.expiry_date <> '') AS next_expiry
        FROM inventory i WHERE 1=1"""
    params = []
    if not include_inactive:
        sql += " AND i.active=1"
    if q:
        like = f"%{q}%"
        sql += " AND (i.item_name LIKE ? OR i.category LIKE ? OR IFNULL(i.sku,'') LIKE ? OR IFNULL(i.barcode,'') LIKE ?)"
        params += [like, like, like, like]
    if cat:
        sql += " AND i.category=?"
        params.append(cat)
    low = float(get_setting("low_stock_threshold", "5"))
    over = float(get_setting("overstock_threshold", "100"))
    if sf == "Low":
        sql += " AND i.stock_qty < ? AND i.stock_qty >= 0"
        params.append(low)
    elif sf == "Over":
        sql += " AND i.stock_qty > ?"
        params.append(over)
    elif sf == "Negative":
        sql += " AND i.stock_qty < 0"
    elif sf == "Expiring":
        warn = int(get_setting("expiry_warn_days", "7") or 7)
        sql += " AND EXISTS (SELECT 1 FROM inventory_batches b WHERE b.item_id=i.id AND b.status='active' AND b.quantity_remaining>0 AND b.expiry_date IS NOT NULL AND date(b.expiry_date) <= date('now','localtime', ?))"
        params.append(f"+{warn} days")
    sql += " ORDER BY i.item_name, i.id"
    return db.connect().execute(sql, params).fetchall()


def list_inventory_batches(item_id=None, status=None, expiring_within_days=None, include_depleted=False):
    from ..db import connect
    sql = """SELECT b.*, i.item_name, i.sku, i.unit
             FROM inventory_batches b JOIN inventory i ON i.id=b.item_id WHERE 1=1"""
    params = []
    if item_id is not None:
        sql += " AND b.item_id=?"
        params.append(item_id)
    if status:
        sql += " AND b.status=?"
        params.append(status)
    elif not include_depleted:
        sql += " AND b.status IN ('active','expired')"
    if expiring_within_days is not None:
        sql += " AND b.expiry_date IS NOT NULL AND b.expiry_date <> '' AND date(b.expiry_date) <= date('now','localtime', ?)"
        params.append(f"+{int(expiring_within_days)} days")
    sql += " ORDER BY CASE WHEN b.expiry_date IS NULL OR b.expiry_date='' THEN 1 ELSE 0 END, b.expiry_date, b.received_at, b.id"
    rows = db.connect().execute(sql, params).fetchall()
    warn = int(get_setting("expiry_warn_days", "7") or 7)
    out = []
    for r in rows:
        d = dict(r)
        st, days = barcodes.expiry_status(d.get("expiry_date"), warn)
        d["expiry_state"] = st
        d["days_to_expiry"] = days
        out.append(d)
    return out


def get_inventory_batch(batch_id):
    from ..db import connect
    row = db.connect().execute("""SELECT b.*, i.item_name, i.sku, i.unit, i.barcode AS item_barcode
        FROM inventory_batches b JOIN inventory i ON i.id=b.item_id WHERE b.id=?""", (batch_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    warn = int(get_setting("expiry_warn_days", "7") or 7)
    st, days = barcodes.expiry_status(d.get("expiry_date"), warn)
    d["expiry_state"] = st
    d["days_to_expiry"] = days
    return d


def get_expiring_batches(within_days=None, include_expired=True):
    warn = int(within_days if within_days is not None else get_setting("expiry_warn_days", "7") or 7)
    batches = list_inventory_batches(status=None, expiring_within_days=warn, include_depleted=False)
    if include_expired:
        # also include already expired active
        from ..db import connect
        rows = db.connect().execute("""SELECT b.*, i.item_name, i.sku, i.unit
            FROM inventory_batches b JOIN inventory i ON i.id=b.item_id
            WHERE b.status='active' AND b.quantity_remaining>0 AND b.expiry_date IS NOT NULL AND b.expiry_date<>'' AND date(b.expiry_date) < date('now','localtime')
            ORDER BY b.expiry_date""").fetchall()
        have = {b["id"] for b in batches}
        for r in rows:
            if r["id"] in have:
                continue
            d = dict(r); st, days = barcodes.expiry_status(d.get("expiry_date"), warn); d["expiry_state"]=st; d["days_to_expiry"]=days
            batches.append(d)
    # keep only with remaining
    return [b for b in batches if float(b.get("quantity_remaining") or 0) > 0]


def _unique_lot_code(conn, base):
    code = base
    n = 1
    while conn.execute("SELECT 1 FROM inventory_batches WHERE lot_code=?", (code,)).fetchone():
        n += 1
        code = f"{base}-{n}"
    return code


def create_inventory_batch(item_id, quantity, unit_cost=0.0, received_at=None, expiry_date=None,
                           supplier="", reference="", notes="", lot_code=None, password=None, actor="system"):
    """Receive stock into a traceable batch. Updates aggregate stock and logs movement."""
    if password == "__session__":
        if not is_session_unlocked():
            raise PermissionError("Session is locked. Unlock settings first.")
    elif password is not None and password != "":
        if not verify_password(password):
            raise PermissionError("Invalid super password.")
    try:
        qty = float(quantity)
    except Exception:
        raise ValueError("Quantity must be a number.")
    if qty <= 0:
        raise ValueError("Quantity must be positive.")
    try:
        ucost = float(unit_cost or 0)
    except Exception:
        ucost = 0.0
    if ucost < 0:
        raise ValueError("Unit cost cannot be negative.")
    from ..db import transaction
    with transaction() as conn:
        item = conn.execute("SELECT * FROM inventory WHERE id=?", (item_id,)).fetchone()
        if not item:
            raise ValueError("Item not found.")
        if not item["active"]:
            raise ValueError("Item is inactive.")
        # received_at
        recv = received_at
        if not recv:
            recv = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # expiry auto-suggest
        exp = (expiry_date or "").strip() or None
        if not exp:
            exp = barcodes.suggest_expiry(recv, item["shelf_life_days"]) if "shelf_life_days" in item.keys() else None
        base_lot = barcodes.normalize_code(lot_code) if lot_code else barcodes.make_lot_code(item_id, recv)
        lot = _unique_lot_code(conn, base_lot)
        barcode_value = lot
        btype = (item["barcode_type"] if "barcode_type" in item.keys() and item["barcode_type"] else "code128")
        cur = conn.cursor()
        cur.execute("""INSERT INTO inventory_batches
            (item_id, lot_code, barcode_value, barcode_type, quantity_received, quantity_remaining,
             unit_cost, received_at, expiry_date, supplier, reference, notes, status, payload_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (item_id, lot, barcode_value, btype, qty, qty, ucost, recv, exp,
             (supplier or "").strip(), (reference or "").strip(), (notes or "").strip(), "active", "{}"))
        bid = cur.lastrowid
        # payload
        try:
            batch_row = conn.execute("SELECT * FROM inventory_batches WHERE id=?", (bid,)).fetchone()
            payload = barcodes.encode_payload(barcodes.make_lot_payload(item, batch_row))
            conn.execute("UPDATE inventory_batches SET payload_json=? WHERE id=?", (payload, bid))
        except Exception:
            pass
        # aggregate stock & moving average cost
        old_qty = float(item["stock_qty"] or 0)
        old_cost = float(item["unit_cost"] or 0)
        new_qty = old_qty + qty
        new_cost = round((old_qty * old_cost + qty * ucost) / new_qty, 4) if ucost > 0 and new_qty > 0 else old_cost
        conn.execute("UPDATE inventory SET stock_qty=?, unit_cost=? WHERE id=?", (new_qty, new_cost, item_id))
        conn.execute("""INSERT INTO inventory_logs (item_id, change_type, quantity, reference, batch_id, unit_cost, balance_after)
                       VALUES (?, 'IN_RECEIVE', ?, ?, ?, ?, ?)""",
                    (item_id, qty, reference or f"receive lot {lot}", bid, ucost, new_qty))
        if ucost > 0 and qty > 0:
            total = round(qty * ucost, 2)
            _post(cur, "INV", bid, "ASSET_IN", "Inventory Purchase",
                  total, 0, 0, 0, "Cash", f"Receive {item['item_name']} lot {lot}",
                  gross_override=-total)
        _audit("inventory_batch_receive", "inventory_batch", bid, f"item={item_id} qty={qty} lot={lot}")
        batch = conn.execute("SELECT b.*, i.item_name, i.sku, i.unit FROM inventory_batches b JOIN inventory i ON i.id=b.item_id WHERE b.id=?", (bid,)).fetchone()
        return dict(batch) if batch else {"id": bid, "lot_code": lot}


def receive_inventory_batch(item_id, quantity, unit_cost=0.0, received_at=None, expiry_date=None,
                            supplier="", reference="", notes="", password="", print_label_after=False, printer=None):
    batch = create_inventory_batch(item_id, quantity, unit_cost, received_at, expiry_date, supplier, reference, notes, password=password)
    label = None
    if print_label_after:
        label = print_batch_label(batch.get("id"), printer=printer)
    batch["label_print"] = label
    return batch


def get_label_printer():
    p = (get_setting("label_printer", "") or "").strip()
    if p:
        return p
    return (get_setting("default_printer", "") or "").strip() or None


def _safe_export_filename(base: str, suffix: str = ".txt") -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(base or "label")).strip("._")
    if not safe:
        safe = "label"
    suffix = suffix if str(suffix).startswith(".") else f".{suffix}"
    # keep room for suffix
    return safe[: max(1, 120 - len(suffix))] + suffix


def _print_text_label(text, filename_base, printer=None, fmt="code128"):
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    filename = _safe_export_filename(filename_base, ".txt")
    path = (EXPORT_DIR / filename).resolve()
    base = EXPORT_DIR.resolve()
    if base != path and base not in path.parents:
        raise ValueError("Refusing to write label outside export directory.")
    path.write_text(str(text or ""), encoding="utf-8")
    target = printer if printer is not None else get_label_printer()
    ok, msg = print_to_printer(str(path), target)
    return ok, msg, str(path)


def print_item_label(item_id, printer=None):
    from ..db import connect
    item = db.connect().execute("SELECT * FROM inventory WHERE id=?", (item_id,)).fetchone()
    if not item:
        raise ValueError("Item not found.")
    fmt = (get_setting("label_format", "code128") or "code128").lower()
    text, _ = barcodes.render_label_for_print(fmt, item, None, True)
    return _print_text_label(text, f"item_label_{item_id}", printer=printer, fmt=fmt)


def print_batch_label(batch_id, printer=None):
    from ..db import connect
    row = db.connect().execute("""SELECT b.*, i.item_name, i.sku, i.unit, i.barcode AS item_barcode, i.barcode_type AS item_barcode_type
        FROM inventory_batches b JOIN inventory i ON i.id=b.item_id WHERE b.id=?""", (batch_id,)).fetchone()
    if not row:
        raise ValueError("Batch not found.")
    fmt = (get_setting("label_format", "code128") or "code128").lower()
    include_payload = get_setting("label_include_qr_payload", "true") == "true"
    item = db.connect().execute("SELECT * FROM inventory WHERE id=?", (row["item_id"],)).fetchone()
    text, _ = barcodes.render_label_for_print(fmt, item, row, include_payload=include_payload)
    return _print_text_label(text, f"batch_label_{batch_id}_{row['lot_code']}", printer=printer, fmt=fmt)


def test_label_print(printer=None):
    sample_item = {"id": 0, "item_name": "Sample Item", "sku": "SKU-SAMPLE", "barcode": "SAMPLE123", "barcode_type": "code128", "category": "Sample", "selling_price": 0}
    sample_batch = {"id": 0, "lot_code": "LOT-SAMPLE-0001", "barcode_value": "LOT-SAMPLE-0001", "barcode_type": "code128",
                    "quantity_received": 1, "quantity_remaining": 1, "unit_cost": 0, "received_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "expiry_date": None, "supplier": "", "reference": "test"}
    fmt = (get_setting("label_format", "code128") or "code128").lower()
    text, _ = barcodes.render_label_for_print(fmt, sample_item, sample_batch, True)
    return _print_text_label(text, "label_test", printer=printer, fmt=fmt)


def _select_batches_for_sale(conn, item_id, qty_needed, specific_batch_id=None, allow_negative=False):
    """Return (allocs, overflow_qty) for FEFO sale allocation.

    allocs: list of (batch_row, take_qty) for available batch stock.
    overflow_qty: positive remainder when allow_negative permits selling beyond batch availability.
    """
    warn_days = int(get_setting("expiry_warn_days", "7") or 7)
    block_expired = get_setting("block_sell_expired", "false") == "true"
    qty_needed_f = float(qty_needed)
    if specific_batch_id is not None:
        b = conn.execute("SELECT * FROM inventory_batches WHERE id=? AND item_id=?", (specific_batch_id, item_id)).fetchone()
        if not b:
            raise ValueError("Selected batch not found for item.")
        state, _ = barcodes.expiry_status(b["expiry_date"], warn_days)
        if state == "expired" and block_expired:
            raise ValueError(f"Batch {b['lot_code']} is expired and cannot be sold.")
        avail = float(b["quantity_remaining"] or 0)
        if avail + 1e-9 < qty_needed_f and not allow_negative:
            raise ValueError(f"Insufficient quantity in batch {b['lot_code']}.")
        take = min(avail, qty_needed_f) if avail > 0 else 0.0
        allocs = [(b, take)] if take > 1e-9 else []
        overflow = max(0.0, qty_needed_f - take)
        if overflow > 1e-9 and not allow_negative:
            raise ValueError(f"Insufficient quantity in batch {b['lot_code']}.")
        return allocs, overflow
    rows = conn.execute("""SELECT * FROM inventory_batches
        WHERE item_id=? AND status='active' AND quantity_remaining > 0
        ORDER BY CASE WHEN expiry_date IS NULL OR expiry_date='' THEN 1 ELSE 0 END,
                 date(expiry_date) ASC, datetime(received_at) ASC, id ASC""", (item_id,)).fetchall()
    allocs = []
    remaining = qty_needed_f
    for b in rows:
        state, _ = barcodes.expiry_status(b["expiry_date"], warn_days)
        if state == "expired" and block_expired:
            continue
        avail = float(b["quantity_remaining"] or 0)
        if avail <= 1e-9:
            continue
        take = min(avail, remaining)
        allocs.append((b, take))
        remaining -= take
        if remaining <= 1e-9:
            break
    overflow = max(0.0, remaining)
    if overflow > 1e-9:
        # include blocked expired info in message if relevant
        if block_expired:
            # Expiry block is a safety policy: do not satisfy shortfall via aggregate/negative overflow.
            raise ValueError("Insufficient non-expired batch stock available for the requested quantity.")
        if not allow_negative:
            raise ValueError("Insufficient batch stock available for the requested quantity.")
    return allocs, overflow




# ═══════════════════════════════════════════════════════════════════════════
# POS
# ═══════════════════════════════════════════════════════════════════════════

def _recipe_components(conn, menu_item_id):
    try:
        return conn.execute("""
            SELECT rc.ingredient_item_id, rc.qty_per_sale,
                   i.item_name, i.unit, i.stock_qty, i.unit_cost, i.active
            FROM recipe_components rc
            JOIN inventory i ON i.id = rc.ingredient_item_id
            WHERE rc.menu_item_id = ?
            ORDER BY i.item_name, rc.ingredient_item_id
        """, (menu_item_id,)).fetchall()
    except Exception:
        return []


def _add_pos_item_tx(conn, reservation_id, desc, qty, price, inv_id=None, activity_id=None, batch_id=None, lot_code=None):
    """Insert a POS item and related ledger/stock inside an existing transaction.

    Batch-aware: for tracked/batched inventory items (or when batch_id provided) deduct via FEFO batches
    and record batch_id/lot_code on pos_tabs. When a sale spans multiple batches, it is split into one
    POS line/allocation per batch for exact traceability and correct void restore.
    """
    if not isinstance(qty, int) or qty <= 0:
        raise ValueError("Quantity must be a positive integer.")
    if price < 0:
        raise ValueError("Price cannot be negative.")
    if not desc or not desc.strip():
        raise ValueError("Description is required.")
    if inv_id is not None:
        try:
            inv_id = int(inv_id)
        except Exception:
            inv_id = None

    is_walkin = (reservation_id is None or reservation_id == 0)
    if not is_walkin:
        if not isinstance(reservation_id, int):
            raise ValueError("Invalid reservation ID.")
        r = conn.execute("SELECT status FROM reservations WHERE id=?", (reservation_id,)).fetchone()
        if not r:
            raise ValueError(f"Reservation #{reservation_id} not found.")
        if r["status"] not in ("Reserved", "Checked In"):
            raise ValueError(f"Cannot add POS to a '{r['status']}' reservation.")

    rid = None if is_walkin else reservation_id
    act_id = None
    if activity_id is not None:
        try:
            act_id = int(activity_id)
        except Exception:
            act_id = None

    cur = conn.cursor()

    # Resolve by batch/lot if provided without inv_id
    if batch_id is not None and not inv_id:
        b = conn.execute("SELECT * FROM inventory_batches WHERE id=?", (batch_id,)).fetchone()
        if b:
            inv_id = b["item_id"]
    if lot_code and not batch_id and not inv_id:
        b = conn.execute("SELECT * FROM inventory_batches WHERE lot_code=? OR barcode_value=?", (lot_code, lot_code)).fetchone()
        if b:
            inv_id = b["item_id"]
            batch_id = b["id"]

    if inv_id:
        inv = conn.execute("SELECT * FROM inventory WHERE id=?", (inv_id,)).fetchone()
        if not inv or not inv["active"]:
            raise ValueError("Inventory item not found or inactive.")
        allow_neg = get_setting("allow_negative_stock", "true") == "true"
        recipe = _recipe_components(conn, inv_id)
        # Determine batch tracking: explicit batch, tracked item, or existing active batches
        has_batches = bool(batch_id or lot_code)
        try:
            tracked = int(inv["track_batches"] or 0) == 1
        except Exception:
            tracked = False
        if not has_batches and tracked:
            has_batches = True
        if not has_batches and not recipe:
            has_batches = conn.execute("SELECT 1 FROM inventory_batches WHERE item_id=? AND status='active' AND quantity_remaining>0 LIMIT 1", (inv_id,)).fetchone() is not None

        if recipe:
            # Meal/menu with recipe: deduct ingredients. Allocate FEFO per ingredient when batches exist.
            missing = []
            ing_plans = []
            total_cost_per_unit = 0.0
            for c in recipe:
                need = float(c["qty_per_sale"] or 0) * float(qty)
                if need <= 0:
                    raise ValueError(f"Invalid recipe quantity for ingredient '{c['item_name']}'.")
                if not c["active"]:
                    raise ValueError(f"Recipe ingredient '{c['item_name']}' is inactive.")
                ing_has_batches = conn.execute("SELECT 1 FROM inventory_batches WHERE item_id=? AND status='active' AND quantity_remaining>0 LIMIT 1", (c["ingredient_item_id"],)).fetchone() is not None
                if ing_has_batches:
                    try:
                        allocs, overflow = _select_batches_for_sale(conn, c["ingredient_item_id"], need, specific_batch_id=None, allow_negative=allow_neg)
                    except Exception as e:
                        raise ValueError(f"Insufficient batch stock for ingredient '{c['item_name']}': {e}")
                    if overflow > 1e-9:
                        # For recipe ingredients, avoid unsafe negative batch state; require sufficient tracked stock.
                        raise ValueError(f"Insufficient batch stock for ingredient '{c['item_name']}': need {need:g}, overflow {overflow:g}")
                    if allocs is None:
                        missing.append(f"{c['item_name']} (need {need:g})")
                    else:
                        ing_plans.append((c, need, allocs))
                        bcost = sum(float(a[0]["unit_cost"] or 0) * float(a[1]) for a in allocs)
                        total_cost_per_unit += bcost / max(float(qty), 1)
                else:
                    if (not allow_neg) and float(c["stock_qty"] or 0) < need - 1e-9:
                        missing.append(f"{c['item_name']} (need {need:g}, have {float(c['stock_qty'] or 0):g})")
                    ing_plans.append((c, need, None))
                    total_cost_per_unit += float(c["unit_cost"] or 0) * float(c["qty_per_sale"] or 0)
            if missing:
                raise ValueError("Insufficient ingredient stock: " + "; ".join(missing))
            cost_at_sale = round(total_cost_per_unit, 6)
            net = price * qty
            tax = calculate_tax(net, 0)
            cur.execute("""INSERT INTO pos_tabs
                (reservation_id, inventory_item_id, item_description, quantity, unit_price, tax_amount, cost_at_sale, activity_id, batch_id, lot_code)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (rid, inv_id, desc.strip(), qty, price, tax["service"] + tax["vat"] + tax["city_tax"], cost_at_sale, act_id, None, None))
            pid = cur.lastrowid
            _post(cur, "POS", pid, "CHARGE", "F&B",
                  tax["net"], tax["service"], tax["vat"], tax["city_tax"], "",
                  f"POS: {desc.strip()} x{qty}")
            cogs = round(cost_at_sale * qty, 2)
            if cogs > 0:
                _post(cur, "POS", pid, "COGS", "COGS", cogs, 0, 0, 0, "",
                      f"COGS POS #{pid} (recipe): {desc.strip()} x{qty}")
            for c, need, allocs in ing_plans:
                if allocs:
                    for b, take in allocs:
                        take_f = float(take)
                        conn.execute("UPDATE inventory_batches SET quantity_remaining = quantity_remaining - ?, status = CASE WHEN quantity_remaining - ? <= 0 THEN 'depleted' ELSE status END WHERE id=?",
                                     (take_f, take_f, b["id"]))
                        conn.execute("UPDATE inventory SET stock_qty = stock_qty - ? WHERE id=?", (take_f, c["ingredient_item_id"]))
                        conn.execute("""INSERT INTO inventory_logs (item_id, change_type, quantity, reference, batch_id, unit_cost)
                                       VALUES (?, 'OUT_RECIPE', ?, ?, ?, ?)""",
                                     (c["ingredient_item_id"], take_f, f"POS #{pid} recipe: {desc.strip()} x{qty} lot {b['lot_code']}", b["id"], float(b["unit_cost"] or 0)))
                else:
                    need_f = float(need)
                    conn.execute("UPDATE inventory SET stock_qty = stock_qty - ? WHERE id=?", (need_f, c["ingredient_item_id"]))
                    conn.execute("""INSERT INTO inventory_logs (item_id, change_type, quantity, reference)
                                   VALUES (?, 'OUT_RECIPE', ?, ?)""",
                                 (c["ingredient_item_id"], need_f, f"POS #{pid} recipe: {desc.strip()} x{qty}"))
            return {"pos_id": pid, "pos_ids": [pid], "gross": round(tax["gross"], 2), "tax": round(tax["service"] + tax["vat"] + tax["city_tax"], 2),
                    "recipe_deducted": True, "batch_tracked": bool(any(a[2] for a in ing_plans))}

        allocs = None
        overflow = 0.0
        if has_batches:
            # FEFO batch allocation (or specific batch). Split into per-batch lines for traceability.
            spec = None
            if batch_id:
                try:
                    spec = int(batch_id)
                except Exception:
                    spec = None
            try:
                allocs, overflow = _select_batches_for_sale(conn, inv_id, qty, specific_batch_id=spec, allow_negative=allow_neg)
            except Exception as e:
                # Preserve stock-vs-batch consistency when negative not allowed.
                raise ValueError(str(e))
            # If overflow remains, allow negative by handling remainder as aggregate (unbatched trace) when permitted.
            if overflow > 1e-9 and not allow_neg:
                raise ValueError("Insufficient batch stock available for the requested quantity.")
        else:
            allocs = None
            overflow = 0.0

        if allocs:
            # one line per allocation (POS quantity schema is integer)
            pos_ids = []
            total_gross = 0.0
            total_tax = 0.0
            remaining_qty = int(qty)
            for b, take in allocs:
                if remaining_qty <= 0:
                    break
                take_int_i = int(round(float(take)))
                if take_int_i <= 0:
                    continue
                take_int_i = min(take_int_i, remaining_qty)
                net_l = price * take_int_i
                tax_l = calculate_tax(net_l, 0)
                bcost = float(b["unit_cost"] or inv["unit_cost"] or 0)
                lot_code_val = b["lot_code"] if "lot_code" in b.keys() else None
                desc_l = desc.strip()
                if len(allocs) > 1 or lot_code_val or overflow > 1e-9:
                    desc_l = f"{desc.strip()} [lot {lot_code_val}]" if lot_code_val else f"{desc.strip()} [batch {b['id']}]"
                cur.execute("""INSERT INTO pos_tabs
                    (reservation_id, inventory_item_id, item_description, quantity, unit_price, tax_amount, cost_at_sale, activity_id, batch_id, lot_code)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (rid, inv_id, desc_l, take_int_i, price, tax_l["service"] + tax_l["vat"] + tax_l["city_tax"], bcost, act_id, b["id"], lot_code_val))
                pid = cur.lastrowid
                pos_ids.append(pid)
                total_gross += tax_l["gross"]
                total_tax += (tax_l["service"] + tax_l["vat"] + tax_l["city_tax"])
                _post(cur, "POS", pid, "CHARGE", "F&B",
                      tax_l["net"], tax_l["service"], tax_l["vat"], tax_l["city_tax"], "",
                      f"POS: {desc_l} x{take_int_i}")
                cogs = round(bcost * take_int_i, 2)
                if cogs > 0:
                    _post(cur, "POS", pid, "COGS", "COGS", cogs, 0, 0, 0, "",
                          f"COGS POS #{pid}: {desc_l} x{take_int_i}")
                conn.execute("UPDATE inventory_batches SET quantity_remaining = quantity_remaining - ?, status = CASE WHEN quantity_remaining - ? <= 0 THEN 'depleted' ELSE status END WHERE id=?",
                             (take_int_i, take_int_i, b["id"]))
                conn.execute("UPDATE inventory SET stock_qty = stock_qty - ? WHERE id=?", (take_int_i, inv_id))
                conn.execute("""INSERT INTO inventory_logs (item_id, change_type, quantity, reference, batch_id, unit_cost)
                               VALUES (?, 'OUT_SALE', ?, ?, ?, ?)""",
                             (inv_id, take_int_i, f"POS #{pid} lot {lot_code_val}", b["id"], bcost))
                remaining_qty -= take_int_i
            # Handle overflow remainder when negative stock is allowed (aggregate/unbatched trace for remainder).
            overflow_qty = int(round(float(overflow))) if overflow > 1e-9 else 0
            if overflow_qty > 0 and allow_neg:
                overflow_qty = min(overflow_qty, remaining_qty) if remaining_qty > 0 else overflow_qty
            if remaining_qty > 0:
                if allow_neg and overflow_qty > 0:
                    take_int_i = overflow_qty
                    net_l = price * take_int_i
                    tax_l = calculate_tax(net_l, 0)
                    bcost = float(inv["unit_cost"] or 0)
                    desc_l = f"{desc.strip()} [unbatched]"
                    cur.execute("""INSERT INTO pos_tabs
                        (reservation_id, inventory_item_id, item_description, quantity, unit_price, tax_amount, cost_at_sale, activity_id, batch_id, lot_code)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (rid, inv_id, desc_l, take_int_i, price, tax_l["service"] + tax_l["vat"] + tax_l["city_tax"], bcost, act_id, None, None))
                    pid = cur.lastrowid
                    pos_ids.append(pid)
                    total_gross += tax_l["gross"]
                    total_tax += (tax_l["service"] + tax_l["vat"] + tax_l["city_tax"])
                    _post(cur, "POS", pid, "CHARGE", "F&B",
                          tax_l["net"], tax_l["service"], tax_l["vat"], tax_l["city_tax"], "",
                          f"POS: {desc_l} x{take_int_i}")
                    cogs = round(bcost * take_int_i, 2)
                    if cogs > 0:
                        _post(cur, "POS", pid, "COGS", "COGS", cogs, 0, 0, 0, "",
                              f"COGS POS #{pid}: {desc_l} x{take_int_i}")
                    conn.execute("UPDATE inventory SET stock_qty = stock_qty - ? WHERE id=?", (take_int_i, inv_id))
                    conn.execute("""INSERT INTO inventory_logs (item_id, change_type, quantity, reference, batch_id, unit_cost)
                                   VALUES (?, 'OUT_SALE', ?, ?, ?, ?)""",
                                 (inv_id, take_int_i, f"POS #{pid} unbatched", None, bcost))
                    remaining_qty -= take_int_i
                if remaining_qty != 0:
                    raise ValueError("Batch allocation mismatch for requested quantity.")
            return {"pos_id": pos_ids[0] if pos_ids else None, "pos_ids": pos_ids, "gross": round(total_gross, 2), "tax": round(total_tax, 2),
                    "recipe_deducted": False, "batch_tracked": True}
        elif overflow > 1e-9 and allow_neg:
            # No batch rows available, but negative stock allowed: aggregate fallback
            if not allow_neg and float(inv["stock_qty"] or 0) < qty:
                raise ValueError(f"Insufficient stock: {inv['stock_qty']} available, {qty} requested.")
            cost_at_sale = float(inv["unit_cost"] or 0)
            net = price * qty
            tax = calculate_tax(net, 0)
            cur.execute("""INSERT INTO pos_tabs
                (reservation_id, inventory_item_id, item_description, quantity, unit_price, tax_amount, cost_at_sale, activity_id, batch_id, lot_code)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (rid, inv_id, desc.strip(), qty, price, tax["service"] + tax["vat"] + tax["city_tax"], cost_at_sale, act_id, None, None))
            pid = cur.lastrowid
            _post(cur, "POS", pid, "CHARGE", "F&B",
                  tax["net"], tax["service"], tax["vat"], tax["city_tax"], "",
                  f"POS: {desc.strip()} x{qty}")
            if cost_at_sale > 0:
                cogs = round(cost_at_sale * qty, 2)
                _post(cur, "POS", pid, "COGS", "COGS", cogs, 0, 0, 0, "",
                      f"COGS POS #{pid}: {desc.strip()} x{qty}")
            conn.execute("UPDATE inventory SET stock_qty = stock_qty - ? WHERE id=?", (qty, inv_id))
            conn.execute("""INSERT INTO inventory_logs (item_id, change_type, quantity, reference)
                           VALUES (?, 'OUT', ?, ?)""", (inv_id, qty, f"POS #{pid}"))
            return {"pos_id": pid, "pos_ids": [pid], "gross": round(tax["gross"], 2), "tax": round(tax["service"] + tax["vat"] + tax["city_tax"], 2),
                    "recipe_deducted": False, "batch_tracked": False}
        else:
            # non-batched (or allowed negative with no batches): aggregate behavior
            if not allow_neg and float(inv["stock_qty"] or 0) < qty:
                raise ValueError(f"Insufficient stock: {inv['stock_qty']} available, {qty} requested.")
            cost_at_sale = float(inv["unit_cost"] or 0)
            net = price * qty
            tax = calculate_tax(net, 0)
            cur.execute("""INSERT INTO pos_tabs
                (reservation_id, inventory_item_id, item_description, quantity, unit_price, tax_amount, cost_at_sale, activity_id, batch_id, lot_code)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (rid, inv_id, desc.strip(), qty, price, tax["service"] + tax["vat"] + tax["city_tax"], cost_at_sale, act_id, None, None))
            pid = cur.lastrowid
            _post(cur, "POS", pid, "CHARGE", "F&B",
                  tax["net"], tax["service"], tax["vat"], tax["city_tax"], "",
                  f"POS: {desc.strip()} x{qty}")
            if cost_at_sale > 0:
                cogs = round(cost_at_sale * qty, 2)
                _post(cur, "POS", pid, "COGS", "COGS", cogs, 0, 0, 0, "",
                      f"COGS POS #{pid}: {desc.strip()} x{qty}")
            conn.execute("UPDATE inventory SET stock_qty = stock_qty - ? WHERE id=?", (qty, inv_id))
            conn.execute("""INSERT INTO inventory_logs (item_id, change_type, quantity, reference)
                           VALUES (?, 'OUT', ?, ?)""", (inv_id, qty, f"POS #{pid}"))
            return {"pos_id": pid, "pos_ids": [pid], "gross": round(tax["gross"], 2), "tax": round(tax["service"] + tax["vat"] + tax["city_tax"], 2),
                    "recipe_deducted": False, "batch_tracked": False}

    # non-inventory POS (service/activity without inv_id linkage handled elsewhere)
    net = price * qty
    tax = calculate_tax(net, 0)
    cur.execute("""INSERT INTO pos_tabs
        (reservation_id, inventory_item_id, item_description, quantity, unit_price, tax_amount, cost_at_sale, activity_id, batch_id, lot_code)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (rid, inv_id, desc.strip(), qty, price, tax["service"] + tax["vat"] + tax["city_tax"], 0.0, act_id, None, None))
    pid = cur.lastrowid
    _post(cur, "POS", pid, "CHARGE", "F&B",
          tax["net"], tax["service"], tax["vat"], tax["city_tax"], "",
          f"POS: {desc.strip()} x{qty}")
    return {"pos_id": pid, "pos_ids": [pid], "gross": round(tax["gross"], 2), "tax": round(tax["service"] + tax["vat"] + tax["city_tax"], 2),
            "recipe_deducted": False, "batch_tracked": False}


def add_pos_item(reservation_id, desc, qty, price, inv_id=None, activity_id=None, batch_id=None, lot_code=None):
    from ..db import transaction
    with transaction() as conn:
        return _add_pos_item_tx(conn, reservation_id, desc, qty, price, inv_id, activity_id=activity_id, batch_id=batch_id, lot_code=lot_code)


def complete_walkin_sale(items, method, notes="", payment_type="Payment"):
    """Atomically commit walk-in POS items and a single payment."""
    if not items:
        raise ValueError("Cart is empty.")
    if not isinstance(method, str) or not method.strip():
        raise ValueError("Select a payment method.")
    from ..db import transaction
    with transaction() as conn:
        cur = conn.cursor()
        pos_ids = []
        total_gross = 0.0
        for it in items:
            desc = (it.get("desc") or "").strip()
            qty = int(it.get("qty") or 0)
            price = _round_money(float(it.get("price") or 0))
            inv_id = it.get("inv_id")
            batch_id = it.get("batch_id")
            lot_code = it.get("lot_code")
            res = _svc_attr("_add_pos_item_tx", _add_pos_item_tx)(conn, None, desc, qty, price, inv_id, batch_id=batch_id, lot_code=lot_code)
            ids = res.get("pos_ids") or ([res.get("pos_id")] if res.get("pos_id") else [])
            for pid0 in ids:
                if pid0:
                    pos_ids.append(int(pid0))
            total_gross += _round_money(res.get("gross", 0))
        total_gross = _round_money(total_gross)
        if total_gross <= 0:
            raise ValueError("Sale total must be positive.")
        conn.execute("""INSERT INTO payments
            (reservation_id, amount, method, payment_type, notes) VALUES (NULL, ?, ?, ?, ?)""",
            (total_gross, method, payment_type or "Payment", notes or "Walk-in sale"))
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        _svc_attr("_post", _post)(cur, "WALKIN_PAY", pid, "PAYMENT", "", 0, 0, 0, 0, method,
              f"{payment_type or 'Payment'} via {method} (walk-in)", gross_override=total_gross)
        _audit("walkin_sale", "payment", pid, f"{total_gross} via {method} items={len(pos_ids)}")

        # Build receipt items from committed POS rows for accurate batch/lot/expiry/received traceability.
        receipt_items = []
        if pos_ids:
            ph = ",".join("?" * len(pos_ids))
            rows = conn.execute(f"""
                SELECT p.id, p.item_description, p.quantity, p.unit_price, p.tax_amount, p.batch_id, p.lot_code,
                       b.expiry_date AS batch_expiry_date, b.received_at AS batch_received_at,
                       (SELECT COALESCE(SUM(gross),0) FROM general_ledger WHERE ref_type='POS' AND ref_id=p.id AND entry_type IN ('CHARGE','VOID_CHARGE')) AS gross
                FROM pos_tabs p
                LEFT JOIN inventory_batches b ON b.id = p.batch_id
                WHERE p.id IN ({ph})
                ORDER BY p.id
            """, pos_ids).fetchall()
            for r in rows:
                receipt_items.append({
                    "pos_id": r["id"],
                    "desc": r["item_description"],
                    "qty": r["quantity"],
                    "price": _round_money(r["unit_price"] or 0),
                    "gross": _round_money(r["gross"] or (float(r["quantity"] or 0) * float(r["unit_price"] or 0) + float(r["tax_amount"] or 0))),
                    "lot_code": r["lot_code"],
                    "batch_id": r["batch_id"],
                    "expiry_date": r["batch_expiry_date"],
                    "received_at": r["batch_received_at"],
                })
    receipt_text = _svc_attr("generate_walkin_receipt_text", generate_walkin_receipt_text)(receipt_items, total_gross, method, payment_id=pid)
    try:
        receipt_path = _svc_attr("save_walkin_receipt_file", save_walkin_receipt_file)(receipt_text, pid)
    except Exception:
        receipt_path = ""
    return {"total_gross": total_gross, "pos_ids": pos_ids, "payment_id": pid,
            "receipt_items": receipt_items, "method": method,
            "receipt_text": receipt_text, "receipt_path": receipt_path,
            "receipt_name": Path(receipt_path).name if receipt_path else ""}


def get_pos_items(reservation_id):
    from ..db import connect
    return db.connect().execute("""
        SELECT id, item_description, quantity, unit_price, tax_amount, voided_at,
               batch_id, lot_code, inventory_item_id, cost_at_sale, created_at, activity_id
        FROM pos_tabs WHERE reservation_id=? AND voided_at IS NULL ORDER BY id
    """, (reservation_id,)).fetchall()


def void_pos_item(pos_id, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    from ..db import connect, transaction


    with transaction() as conn:
        row = conn.execute("SELECT * FROM pos_tabs WHERE id=? AND voided_at IS NULL", (pos_id,)).fetchone()
        if not row:
            raise ValueError("POS item not found or already voided.")
        cur = conn.cursor()
        _void_pos_item_inner(cur, row)
        desc = row["item_description"]
        pid = row["id"]
    _audit("pos_void", "pos", pid, desc)


# ═══════════════════════════════════════════════════════════════════════════
# INVENTORY
# ═══════════════════════════════════════════════════════════════════════════

def search_inventory(query="", category="", stock_filter=""):
    # Enhanced to include SKU/barcode search and extra fields (keeps compat)
    return get_inventory_items(query=query, category=category, stock_filter=stock_filter, include_inactive=False)


def get_inventory_categories():
    from ..db import connect
    return [r["category"] for r in db.connect().execute(
        "SELECT DISTINCT category FROM inventory WHERE active=1 ORDER BY category").fetchall()]


def get_inventory_warnings():
    from ..db import connect
    low = float(get_setting("low_stock_threshold", "5"))
    over = float(get_setting("overstock_threshold", "100"))
    items = db.connect().execute(
        "SELECT id, item_name, stock_qty, low_stock_threshold, overstock_threshold FROM inventory WHERE active=1").fetchall()
    neg, lo, ov = [], [], []
    for i in items:
        el = i["low_stock_threshold"] if i["low_stock_threshold"] is not None else low
        eo = i["overstock_threshold"] if i["overstock_threshold"] is not None else over
        if i["stock_qty"] < 0:
            neg.append((i["id"], i["item_name"], i["stock_qty"]))
        elif i["stock_qty"] < el:
            lo.append((i["id"], i["item_name"], i["stock_qty"], el))
        elif i["stock_qty"] > eo:
            ov.append((i["id"], i["item_name"], i["stock_qty"], eo))
    return {"negative": neg, "low": lo, "over": ov}


def get_recent_inventory_logs(limit=20):
    from ..db import connect  # pragma: no cover
    return db.connect().execute("""
        SELECT l.at, i.item_name, l.change_type, l.quantity, l.reference
        FROM inventory_logs l JOIN inventory i ON l.item_id = i.id
        ORDER BY l.id DESC LIMIT ?
    """, (limit,)).fetchall()


def add_inventory_item(name, category, unit, stock, unit_cost, selling_price, low, over, password,
                        sku="", barcode="", barcode_type="code128", track_batches=0, shelf_life_days=None, default_supplier=""):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if not name or not name.strip():
        raise ValueError("Name is required.")
    sku = (sku or "").strip() or None
    barcode = (barcode or "").strip() or None
    try:
        track_batches = int(track_batches or 0)
    except Exception:
        track_batches = 0
    try:
        shelf_life_days = int(shelf_life_days) if shelf_life_days not in (None, "", "None") else None
    except Exception:
        shelf_life_days = None
    from ..db import transaction
    with transaction() as conn:
        cur = conn.cursor()
        cur.execute("""INSERT INTO inventory
            (item_name, category, unit, stock_qty, unit_cost, selling_price,
             low_stock_threshold, overstock_threshold, sku, barcode, barcode_type, track_batches, shelf_life_days, default_supplier)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name.strip(), category.strip(), unit.strip(), float(stock), float(unit_cost),
             float(selling_price), float(low), float(over), sku, barcode, (barcode_type or "code128"), track_batches, shelf_life_days, (default_supplier or "").strip()))
        item_id = cur.lastrowid
        # if initial stock provided, create an opening batch for traceability (idempotent guard)
        try:
            if abs(float(stock)) > 1e-9:
                lot = f"OPEN-{item_id}"
                exists = conn.execute("SELECT id FROM inventory_batches WHERE item_id=? AND lot_code=? AND reference='initial'", (item_id, lot)).fetchone()
                if not exists:
                    now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    exp = None
                    try:
                        sld = int(shelf_life_days) if shelf_life_days not in (None, "", "None") else None
                    except Exception:
                        sld = None
                    if sld and sld > 0:
                        exp = (datetime.now() + timedelta(days=sld)).strftime(DATE_FMT)
                    conn.execute("""INSERT INTO inventory_batches
                        (item_id, lot_code, barcode_value, barcode_type, quantity_received, quantity_remaining,
                         unit_cost, received_at, expiry_date, supplier, reference, notes, status, payload_json)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (item_id, lot, lot, (barcode_type or "code128"), float(stock), float(stock), float(unit_cost),
                         now_s, exp, (default_supplier or "").strip(), "initial", "Initial stock", "active", "{}"))
                    bid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    try:
                        payload = json.dumps({"v":1,"t":"lot","item":item_id,"name":name.strip(),"lot":lot,"recv":now_s,"exp":exp or "","qty":float(stock),"rem":float(stock),"cost":float(unit_cost)}, separators=(",",":"), ensure_ascii=False)
                        conn.execute("UPDATE inventory_batches SET payload_json=? WHERE id=?", (payload, bid))
                    except Exception:
                        pass
                    conn.execute("""INSERT INTO inventory_logs (item_id, change_type, quantity, reference, batch_id, unit_cost, balance_after)
                                   VALUES (?, 'OPENING', ?, 'initial stock', ?, ?, ?)""", (item_id, float(stock), bid, float(unit_cost), float(stock)))
        except Exception:
            pass
    _audit("inventory_add", "inventory", "", name)
    try:
        return item_id
    except Exception:
        return None


def update_inventory_item(item_id, name, category, unit, selling_price, low, over, password,
                           sku=None, barcode=None, barcode_type=None, track_batches=None, shelf_life_days=None, default_supplier=None):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if not name or not name.strip():
        raise ValueError("Name is required.")
    from ..db import transaction
    with transaction() as conn:
        # build dynamic set to keep compat when new args omitted
        sets = ["item_name=?", "category=?", "unit=?", "selling_price=?", "low_stock_threshold=?", "overstock_threshold=?"]
        params = [name.strip(), category.strip(), unit.strip(), float(selling_price), float(low), float(over)]
        if sku is not None:
            sets.append("sku=?"); params.append((sku or "").strip() or None)
        if barcode is not None:
            sets.append("barcode=?"); params.append((barcode or "").strip() or None)
        if barcode_type is not None:
            sets.append("barcode_type=?"); params.append((barcode_type or "code128"))
        if track_batches is not None:
            try: tb = int(track_batches or 0)
            except Exception: tb = 0
            sets.append("track_batches=?"); params.append(tb)
        if shelf_life_days is not None:
            try: sld = int(shelf_life_days) if shelf_life_days not in ("", "None") else None
            except Exception: sld = None
            sets.append("shelf_life_days=?"); params.append(sld)
        if default_supplier is not None:
            sets.append("default_supplier=?"); params.append((default_supplier or "").strip())
        params.append(item_id)
        conn.execute(f"UPDATE inventory SET {', '.join(sets)} WHERE id=?", params)
    _audit("inventory_update", "inventory", item_id, name)


def delete_inventory_item(item_id, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    from ..db import connect, transaction


    row = db.connect().execute("SELECT stock_qty FROM inventory WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise ValueError("Item not found.")
    if row["stock_qty"] != 0:
        raise ValueError("Cannot delete item with non-zero stock.")
    with transaction() as conn:
        conn.execute("DELETE FROM recipe_components WHERE menu_item_id=? OR ingredient_item_id=?", (item_id, item_id))
        conn.execute("UPDATE inventory SET active=0 WHERE id=?", (item_id,))
    _audit("inventory_delete", "inventory", item_id, "")


def get_recipe_components(menu_item_id):
    """Return recipe/BOM components for a sellable menu inventory item."""
    from ..db import connect
    rows = db.connect().execute("""
        SELECT rc.menu_item_id, rc.ingredient_item_id, rc.qty_per_sale,
               i.item_name AS ingredient_name, i.unit, i.stock_qty, i.unit_cost, i.active
        FROM recipe_components rc
        JOIN inventory i ON i.id = rc.ingredient_item_id
        WHERE rc.menu_item_id = ?
        ORDER BY i.item_name, rc.ingredient_item_id
    """, (menu_item_id,)).fetchall()
    return [dict(r) for r in rows]


def set_recipe_components(menu_item_id, components, password):
    """Replace recipe components for a menu item. components=[{ingredient_item_id, qty_per_sale}]."""
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    from ..db import connect, transaction


    conn = db.connect()
    menu = conn.execute("SELECT id, item_name, active FROM inventory WHERE id=?", (menu_item_id,)).fetchone()
    if not menu:
        raise ValueError("Menu item not found.")
    norm = []
    seen = set()
    for c in (components or []):
        iid = c.get("ingredient_item_id", c.get("inv_id", c.get("id")))
        q = c.get("qty_per_sale", c.get("qty", c.get("quantity")))
        try:
            iid = int(iid); q = float(q)
        except Exception:
            raise ValueError("Each component needs numeric ingredient_item_id and qty_per_sale.")
        if iid == int(menu_item_id):
            raise ValueError("A menu item cannot include itself as an ingredient.")
        if iid in seen:
            raise ValueError("Duplicate ingredient in recipe.")
        if q <= 0:
            raise ValueError("qty_per_sale must be greater than 0.")
        ing = conn.execute("SELECT id, item_name, active FROM inventory WHERE id=?", (iid,)).fetchone()
        if not ing:
            raise ValueError(f"Ingredient #{iid} not found.")
        norm.append((iid, q))
        seen.add(iid)
    with transaction() as c2:
        c2.execute("DELETE FROM recipe_components WHERE menu_item_id=?", (menu_item_id,))
        for iid, q in norm:
            c2.execute("""INSERT INTO recipe_components (menu_item_id, ingredient_item_id, qty_per_sale)
                          VALUES (?, ?, ?)""", (menu_item_id, iid, q))
    _audit("recipe_set", "inventory", menu_item_id, f"{menu['item_name']}: {len(norm)} components")
    return get_recipe_components(menu_item_id)


def clear_recipe_components(menu_item_id, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    from ..db import transaction
    with transaction() as conn:
        conn.execute("DELETE FROM recipe_components WHERE menu_item_id=?", (menu_item_id,))
    _audit("recipe_clear", "inventory", menu_item_id)
    return True


def inventory_has_recipe(inv_id):
    try:
        from ..db import connect
        row = db.connect().execute("SELECT 1 FROM recipe_components WHERE menu_item_id=? LIMIT 1", (inv_id,)).fetchone()
        return bool(row)
    except Exception:
        return False


def _normalize_meal_ingredients(conn, ingredients, exclude_inventory_id=None):
    norm = []
    seen = set()
    for c in (ingredients or []):
        iid = c.get("inventory_item_id", c.get("ingredient_item_id", c.get("inv_id", c.get("id"))))
        q = c.get("qty_per_sale", c.get("qty", c.get("quantity")))
        note = (c.get("note") or c.get("notes") or "").strip()
        try:
            iid = int(iid); q = float(q)
        except Exception:
            raise ValueError("Each ingredient needs numeric inventory_item_id and qty_per_sale.")
        if exclude_inventory_id is not None and iid == int(exclude_inventory_id):
            raise ValueError("Meal cannot include its own linked item as an ingredient.")
        if iid in seen:
            raise ValueError("Duplicate ingredient in meal.")
        if q <= 0:
            raise ValueError("Ingredient qty_per_sale must be greater than 0.")
        ing = conn.execute("SELECT id, item_name, active FROM inventory WHERE id=?", (iid,)).fetchone()
        if not ing:
            raise ValueError(f"Ingredient inventory item #{iid} not found.")
        norm.append({"inventory_item_id": iid, "qty_per_sale": q, "note": note, "ingredient_name": ing["item_name"]})
        seen.add(iid)
    return norm


def _sync_meal_to_inventory(conn, meal_id, name, category, selling_price, ingredients):
    """Ensure meal has a linked sellable inventory item and recipe_components for auto deduction."""
    meal = conn.execute("SELECT linked_inventory_id FROM meals WHERE id=?", (meal_id,)).fetchone()
    linked_id = meal["linked_inventory_id"] if meal else None
    if linked_id:
        exists = conn.execute("SELECT id FROM inventory WHERE id=?", (linked_id,)).fetchone()
        if not exists:
            linked_id = None
    if not linked_id:
        conflict = conn.execute("SELECT id FROM inventory WHERE item_name=? COLLATE NOCASE", (name.strip(),)).fetchone()
        if conflict:
            # reuse if it looks like an unlinked meal slot? safer to error
            used = conn.execute("SELECT id FROM meals WHERE linked_inventory_id=?", (conflict["id"],)).fetchone()
            if used and int(used["id"]) != int(meal_id):
                raise ValueError(f"Inventory item name '{name}' is already used by another meal.")
            linked_id = conflict["id"]
            conn.execute("UPDATE inventory SET category=?, selling_price=?, active=1 WHERE id=?",
                         (category or "Meals", float(selling_price or 0), linked_id))
        else:
            cur = conn.cursor()
            cur.execute("""INSERT INTO inventory
                (item_name, category, unit, stock_qty, unit_cost, selling_price, low_stock_threshold, overstock_threshold, active)
                VALUES (?, ?, 'portion', 0, 0, ?, 0, 0, 1)""",
                (name.strip(), category or "Meals", float(selling_price or 0)))
            linked_id = cur.lastrowid
    else:
        conn.execute("UPDATE inventory SET item_name=?, category=?, selling_price=?, active=1 WHERE id=?",
                     (name.strip(), category or "Meals", float(selling_price or 0), linked_id))
    conn.execute("UPDATE meals SET linked_inventory_id=?, updated_at=datetime('now','localtime') WHERE id=?",
                 (linked_id, meal_id))
    # sync recipe_components for deduction engine
    conn.execute("DELETE FROM recipe_components WHERE menu_item_id=?", (linked_id,))
    for ing in ingredients:
        conn.execute("""INSERT INTO recipe_components (menu_item_id, ingredient_item_id, qty_per_sale)
                        VALUES (?, ?, ?)""", (linked_id, ing["inventory_item_id"], ing["qty_per_sale"]))
    return linked_id


def create_meal(name, description, preparation, selling_price, ingredients, password, category="Meals"):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if not name or not str(name).strip():
        raise ValueError("Meal name is required.")
    try:
        price = float(selling_price or 0)
    except Exception:
        raise ValueError("Selling price must be a number.")
    if price < 0:
        raise ValueError("Selling price cannot be negative.")
    from ..db import connect, transaction


    conn = db.connect()
    norm = _normalize_meal_ingredients(conn, ingredients)
    with transaction() as c2:
        try:
            cur = c2.cursor()
            cur.execute("""INSERT INTO meals (name, description, preparation, selling_price, category, active)
                           VALUES (?, ?, ?, ?, ?, 1)""",
                        (str(name).strip(), (description or "").strip(), (preparation or "").strip(), price, (category or "Meals").strip() or "Meals"))
            meal_id = cur.lastrowid
        except Exception as e:
            if "UNIQUE" in str(e).upper():
                raise ValueError("A meal with this name already exists.")
            raise
        for ing in norm:
            c2.execute("""INSERT INTO meal_ingredients (meal_id, inventory_item_id, qty_per_sale, note)
                          VALUES (?, ?, ?, ?)""", (meal_id, ing["inventory_item_id"], ing["qty_per_sale"], ing["note"]))
        linked_id = _sync_meal_to_inventory(c2, meal_id, name, category, price, norm)
    _audit("meal_create", "meal", meal_id, name)
    return {"id": meal_id, "linked_inventory_id": linked_id}


def update_meal(meal_id, name, description, preparation, selling_price, ingredients, password, category="Meals", active=1):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if not name or not str(name).strip():
        raise ValueError("Meal name is required.")
    try:
        price = float(selling_price or 0)
    except Exception:
        raise ValueError("Selling price must be a number.")
    from ..db import connect, transaction


    conn = db.connect()
    row = conn.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone()
    if not row:
        raise ValueError("Meal not found.")
    norm = _normalize_meal_ingredients(conn, ingredients, exclude_inventory_id=row["linked_inventory_id"])
    with transaction() as c2:
        try:
            c2.execute("""UPDATE meals SET name=?, description=?, preparation=?, selling_price=?, category=?, active=?, updated_at=datetime('now','localtime')
                          WHERE id=?""",
                       (str(name).strip(), (description or "").strip(), (preparation or "").strip(), price, (category or "Meals").strip() or "Meals", 1 if active else 0, meal_id))
        except Exception as e:
            if "UNIQUE" in str(e).upper():
                raise ValueError("A meal with this name already exists.")
            raise
        c2.execute("DELETE FROM meal_ingredients WHERE meal_id=?", (meal_id,))
        for ing in norm:
            c2.execute("""INSERT INTO meal_ingredients (meal_id, inventory_item_id, qty_per_sale, note)
                          VALUES (?, ?, ?, ?)""", (meal_id, ing["inventory_item_id"], ing["qty_per_sale"], ing["note"]))
        linked_id = _sync_meal_to_inventory(c2, meal_id, name, category, price, norm)
        if not active and linked_id:
            c2.execute("UPDATE inventory SET active=0 WHERE id=?", (linked_id,))
            c2.execute("DELETE FROM recipe_components WHERE menu_item_id=?", (linked_id,))
    _audit("meal_update", "meal", meal_id, name)
    return {"id": meal_id, "linked_inventory_id": linked_id}


def list_meals(active_only=False):
    from ..db import connect
    q = """SELECT m.*, (SELECT COUNT(*) FROM meal_ingredients mi WHERE mi.meal_id=m.id) AS ingredient_count
           FROM meals m"""
    params = []
    if active_only:
        q += " WHERE m.active=1"
    q += " ORDER BY m.name COLLATE NOCASE, m.id"
    return [dict(r) for r in db.connect().execute(q, params).fetchall()]


def get_meal(meal_id):
    from ..db import connect
    m = db.connect().execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone()
    if not m:
        return None
    ings = db.connect().execute("""
        SELECT mi.inventory_item_id, mi.qty_per_sale, mi.note, i.item_name, i.unit, i.stock_qty, i.active
        FROM meal_ingredients mi JOIN inventory i ON i.id=mi.inventory_item_id
        WHERE mi.meal_id=? ORDER BY i.item_name, mi.inventory_item_id
    """, (meal_id,)).fetchall()
    d = dict(m)
    d["ingredients"] = [dict(r) for r in ings]
    return d


def delete_meal(meal_id, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    from ..db import connect, transaction


    m = db.connect().execute("SELECT name, linked_inventory_id FROM meals WHERE id=?", (meal_id,)).fetchone()
    if not m:
        raise ValueError("Meal not found.")
    with transaction() as c2:
        if m["linked_inventory_id"]:
            c2.execute("DELETE FROM recipe_components WHERE menu_item_id=?", (m["linked_inventory_id"],))
            c2.execute("UPDATE inventory SET active=0 WHERE id=?", (m["linked_inventory_id"],))
        c2.execute("DELETE FROM meal_ingredients WHERE meal_id=?", (meal_id,))
        c2.execute("DELETE FROM meals WHERE id=?", (meal_id,))
    _audit("meal_delete", "meal", meal_id, m["name"])
    return True


def restock_inventory(item_id, qty, unit_cost=0.0, password=""):
    """Restock by creating a received batch (traceable: received_at now). For full control (expiry/supplier) use create/receive_inventory_batch."""
    if password == "__session__":
        if not is_session_unlocked():
            raise PermissionError("Session is locked. Unlock settings first.")
    elif not verify_password(password):
        raise PermissionError("Invalid super password.")
    if not isinstance(item_id, int) or item_id <= 0:
        raise ValueError("Invalid item ID.")
    try:
        qty = float(qty)
    except Exception:
        raise ValueError("Quantity must be positive.")
    if qty <= 0:
        raise ValueError("Quantity must be positive.")
    try:
        unit_cost = float(unit_cost or 0)
    except Exception:
        unit_cost = 0.0
    # prefer batch-tracked receive
    batch = create_inventory_batch(item_id, qty, unit_cost, received_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                   expiry_date=None, supplier="", reference="restock", notes="Restock", password=None)
    _audit("inventory_restock", "inventory", item_id, f"+{qty} @ {unit_cost} lot={batch.get('lot_code') if isinstance(batch, dict) else ''}")
    return batch


# ═══════════════════════════════════════════════════════════════════════════
# PAYMENTS
# ═══════════════════════════════════════════════════════════════════════════

def record_payment(reservation_id, amount, method, notes="", payment_type="Payment"):
    if amount <= 0:
        raise ValueError("Payment amount must be positive.")
    if reservation_id is not None and not isinstance(reservation_id, int):
        raise ValueError("Invalid reservation ID.")
    from ..db import connect, transaction


    if reservation_id is not None:
        r = db.connect().execute("SELECT status FROM reservations WHERE id=?", (reservation_id,)).fetchone()
        if not r:
            raise ValueError("Reservation not found.")
        if r["status"] in ("Cancelled", "No-Show", "Deleted", "Checked Out"):
            raise ValueError(f"Cannot record payment on a '{r['status']}' reservation.")
    with transaction() as conn:
        cur = conn.cursor()
        conn.execute("""INSERT INTO payments
            (reservation_id, amount, method, payment_type, notes) VALUES (?, ?, ?, ?, ?)""",
            (reservation_id, amount, method, payment_type, notes))
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        ref_type = "PAY" if reservation_id else "WALKIN_PAY"
        _post(cur, ref_type, pid, "PAYMENT", "", 0, 0, 0, 0, method,
              f"{payment_type} via {method}" + (f" Res #{reservation_id}" if reservation_id else " (walk-in)"),
              gross_override=amount)
    _audit("payment_record", "payment", pid, f"{amount} via {method}")


def record_refund(reservation_id, amount, method, notes, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if amount <= 0:
        raise ValueError("Refund amount must be positive.")
    from ..db import transaction
    with transaction() as conn:
        cur = conn.cursor()
        paid = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE reservation_id=? AND voided_at IS NULL",
            (reservation_id,)).fetchone()[0] or 0
        if amount > paid:
            raise ValueError(f"Refund exceeds paid amount ({paid:.2f}).")
        conn.execute("""INSERT INTO payments
            (reservation_id, amount, method, payment_type, notes) VALUES (?, ?, ?, 'Refund', ?)""",
            (reservation_id, -amount, method, notes))
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        _post(cur, "PAY", pid, "REFUND", "", 0, 0, 0, 0, method,
              f"Refund via {method} Res #{reservation_id}: {notes}",
              gross_override=-amount)
    _audit("refund", "payment", pid, f"{amount} via {method}")


# ═══════════════════════════════════════════════════════════════════════════
# EXPENSES
# ═══════════════════════════════════════════════════════════════════════════

def get_expense_categories():
    from ..db import connect
    return db.connect().execute("SELECT id, category_name FROM expense_categories ORDER BY category_name").fetchall()


def record_operational_expense(cat_id, param, amount, tax_amount=0.0, notes="", method="Cash"):
    if amount <= 0:
        raise ValueError("Amount must be positive.")
    if tax_amount < 0:
        raise ValueError("VAT cannot be negative.")
    if not isinstance(cat_id, int):
        raise ValueError("Invalid category.")
    from ..db import transaction
    with transaction() as conn:
        cur = conn.cursor()
        conn.execute("""INSERT INTO operational_expenses
            (category_id, parameter_name, net_amount, vat_amount, method, notes)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (cat_id, param, amount, tax_amount, method, notes))
        eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        _post(cur, "EXP", eid, "EXPENSE", "Operating", amount, 0, tax_amount, 0, method,
              f"Expense: {param} - {notes}",
              gross_override=-(amount + tax_amount))
    _audit("expense_add", "expense", eid, f"{param} {amount}")


def void_expense(expense_id, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    from ..db import connect, transaction


    row = db.connect().execute("SELECT * FROM operational_expenses WHERE id=? AND voided_at IS NULL",
                            (expense_id,)).fetchone()
    if not row:
        raise ValueError("Expense not found or already voided.")
    with transaction() as conn:
        cur = conn.cursor()
        _reverse(cur, "EXP", expense_id, entry_type_filter=("EXPENSE",))
        conn.execute("UPDATE operational_expenses SET voided_at=datetime('now','localtime') WHERE id=?",
                     (expense_id,))
    _audit("expense_void", "expense", expense_id, "")


def get_recent_expenses(limit=50):
    from ..db import connect
    return db.connect().execute("""
        SELECT e.id, e.at, c.category_name, e.parameter_name, e.net_amount, e.vat_amount, e.method, e.notes
        FROM operational_expenses e JOIN expense_categories c ON e.category_id = c.id
        WHERE e.voided_at IS NULL ORDER BY e.id DESC LIMIT ?
    """, (limit,)).fetchall()



# ═══════════════════════════════════════════════════════════════════════════
# ACTIVITIES / TRIPS (bookable guest experiences) — ordered like POS, posted to reservation folio/invoice
# ═══════════════════════════════════════════════════════════════════════════

def list_activities(active_only=False):
    from ..db import connect
    q = "SELECT * FROM activities"
    params = ()
    if active_only:
        q += " WHERE active=1"
    q += " ORDER BY name COLLATE NOCASE, id"
    return [dict(r) for r in db.connect().execute(q, params).fetchall()]


def get_activity(activity_id):
    from ..db import connect
    r = db.connect().execute("SELECT * FROM activities WHERE id=?", (int(activity_id),)).fetchone()
    return dict(r) if r else None


def create_activity(name, description="", category="Trip", unit_price=0.0, active=1):
    if not name or not str(name).strip():
        raise ValueError("Activity name is required.")
    try:
        price = float(unit_price or 0)
    except Exception:
        raise ValueError("Price must be a number.")
    if price < 0:
        raise ValueError("Price cannot be negative.")
    from ..db import transaction
    with transaction() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO activities (name, description, category, unit_price, active)
                   VALUES (?, ?, ?, ?, ?)""",
                (str(name).strip(), (description or "").strip(), (category or "Trip").strip() or "Trip", price, 1 if active else 0),
            )
            aid = cur.lastrowid
        except Exception as e:
            if "UNIQUE" in str(e).upper():
                raise ValueError("An activity with this name already exists.")
            raise
    _audit("activity_create", "activity", aid, name)
    return aid


def update_activity(activity_id, name, description="", category="Trip", unit_price=0.0, active=1, password=None):
    if password is not None and password != "" and not verify_password(password):
        raise PermissionError("Invalid super password.")
    if not name or not str(name).strip():
        raise ValueError("Activity name is required.")
    try:
        price = _round_money(float(unit_price or 0))
    except Exception:
        raise ValueError("Price must be a number.")
    if price < 0:
        raise ValueError("Price cannot be negative.")
    from ..db import connect, transaction


    aid = int(activity_id)
    with transaction() as conn:
        row = conn.execute("SELECT id FROM activities WHERE id=?", (aid,)).fetchone()
        if not row:
            raise ValueError("Activity not found.")
        try:
            conn.execute(
                """UPDATE activities SET name=?, description=?, category=?, unit_price=?, active=?, updated_at=datetime('now','localtime') WHERE id=?""",
                (str(name).strip(), (description or "").strip(), (category or "Trip").strip() or "Trip", price, 1 if active else 0, aid),
            )
        except Exception as e:
            if "UNIQUE" in str(e).upper():
                raise ValueError("An activity with this name already exists.")
            raise
    _audit("activity_update", "activity", aid, name)
    return True


def set_activity_active(activity_id, active):
    from ..db import connect, transaction


    aid = int(activity_id)
    with transaction() as conn:
        row = conn.execute("SELECT id FROM activities WHERE id=?", (aid,)).fetchone()
        if not row:
            raise ValueError("Activity not found.")
        conn.execute("UPDATE activities SET active=?, updated_at=datetime('now','localtime') WHERE id=?", (1 if active else 0, aid))
    _audit("activity_toggle", "activity", aid, f"active={1 if active else 0}")
    return True


def book_activity(reservation_id, activity_id, quantity=1, scheduled_at=None, notes=""):
    """Book/order a registered activity/trip for a reservation.
    Creates a POS charge (like POS) linked to the activity so it is added to the reservation folio, balance, and invoice."""
    from ..db import connect
    act = db.connect().execute("SELECT * FROM activities WHERE id=? AND active=1", (int(activity_id),)).fetchone()
    if not act:
        raise ValueError("Activity/trip not found or inactive.")
    try:
        qty = int(quantity or 1)
    except Exception:
        qty = 1
    if qty <= 0:
        raise ValueError("Quantity must be a positive integer.")
    desc = act["name"]
    if scheduled_at:
        desc = f"{desc} ({scheduled_at})"
    if notes:
        n = str(notes).strip()
        if n:
            desc = f"{desc} - {n[:80]}"
    res = add_pos_item(int(reservation_id), desc, qty, _round_money(float(act["unit_price"] or 0)), inv_id=None, activity_id=int(activity_id))
    pos_id = res.get("pos_id") if isinstance(res, dict) else res
    pos_ids = res.get("pos_ids") if isinstance(res, dict) else [pos_id]
    _audit("activity_book", "activity_booking", pos_id, f"res={reservation_id} activity={activity_id} qty={qty}")
    return {"pos_id": pos_id, "pos_ids": pos_ids or [], "reservation_id": int(reservation_id), "activity_id": int(activity_id), "quantity": qty, "unit_price": _round_money(float(act["unit_price"] or 0))}


def list_activity_bookings(activity_id=None, reservation_id=None, guest_id=None, limit=500):
    """List booked activities/trips (POS lines linked to activities) with reservation/guest context."""
    from ..db import connect
    where = ["p.voided_at IS NULL", "p.activity_id IS NOT NULL"]
    params = []
    if activity_id is not None:
        where.append("p.activity_id=?"); params.append(int(activity_id))
    if reservation_id is not None:
        where.append("p.reservation_id=?"); params.append(int(reservation_id))
    if guest_id is not None:
        where.append("r.guest_id=?"); params.append(int(guest_id))
    q = f"""
        SELECT p.id AS pos_id, p.reservation_id, p.activity_id, p.item_description, p.quantity, p.unit_price, p.tax_amount, p.created_at,
               a.name AS activity_name, a.category AS activity_category,
               r.guest_id, r.guest_name, r.check_in_date, r.check_out_date, r.status AS reservation_status,
               rm.room_number
        FROM pos_tabs p
        JOIN activities a ON a.id = p.activity_id
        LEFT JOIN reservations r ON r.id = p.reservation_id
        LEFT JOIN rooms rm ON rm.id = r.room_id
        WHERE {' AND '.join(where)}
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT ?
    """
    params.append(int(limit or 500))
    return [dict(r) for r in db.connect().execute(q, params).fetchall()]


def get_activity_stats(activity_id=None):
    """Per-activity (or overall) booking/order statistics grounded in real POS/activity bookings."""
    from ..db import connect
    params = []
    filt = "WHERE p.voided_at IS NULL AND p.activity_id IS NOT NULL"
    if activity_id is not None:
        filt += " AND p.activity_id=?"; params.append(int(activity_id))
    rows = db.connect().execute(f"""
        SELECT p.activity_id, a.name AS activity_name, a.category, a.unit_price, a.active,
               COUNT(*) AS bookings, COALESCE(SUM(p.quantity),0) AS total_qty,
               COALESCE(SUM(p.quantity*p.unit_price + p.tax_amount),0) AS gross_revenue,
               MIN(p.created_at) AS first_booked, MAX(p.created_at) AS last_booked
        FROM pos_tabs p JOIN activities a ON a.id=p.activity_id
        {filt}
        GROUP BY p.activity_id
        ORDER BY gross_revenue DESC, total_qty DESC
    """, params).fetchall()
    stats = [dict(r) for r in rows]
    # include registered activities with zero bookings when listing all
    if activity_id is None:
        seen = {s["activity_id"] for s in stats}
        for a in db.connect().execute("SELECT id, name, category, unit_price, active FROM activities ORDER BY name").fetchall():
            if a["id"] not in seen:
                stats.append({"activity_id": a["id"], "activity_name": a["name"], "category": a["category"], "unit_price": a["unit_price"], "active": a["active"],
                              "bookings": 0, "total_qty": 0, "gross_revenue": 0.0, "first_booked": None, "last_booked": None})
    return stats


# ═══════════════════════════════════════════════════════════════════════════
# BALANCE & FOLIO
# ═══════════════════════════════════════════════════════════════════════════

def calculate_guest_balance(rid):
    from ..db import connect
    conn = db.connect()
    r = conn.execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()
    if not r or r["status"] in ("Cancelled", "No-Show", "Deleted"):
        return 0.0
    charges = _reservation_charge_net(conn, rid)
    paid = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE reservation_id=? AND voided_at IS NULL",
        (rid,)).fetchone()[0] or 0
    return round(charges - paid, 2)


def get_balance_breakdown(rid):
    from ..db import connect
    conn = db.connect()
    r = conn.execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()
    if not r:
        return None
    if r["status"] in ("Cancelled", "No-Show", "Deleted"):
        return {"total_charges": 0, "total_paid": 0, "balance_due": 0,
                "nights": 0, "accommodation_net": 0, "accommodation_tax": 0,
                "accommodation_gross": 0, "pos_net": 0, "pos_tax": 0, "pos_gross": 0}
    comp = _reservation_charge_components(conn, rid)
    paid = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE reservation_id=? AND voided_at IS NULL",
        (rid,)).fetchone()[0] or 0
    total_charges = comp["total_charges"]
    return {
        "total_charges": round(total_charges, 2),
        "total_paid": round(paid, 2),
        "balance_due": round(total_charges - paid, 2),
        "accommodation_net": comp["accommodation_net"],
        "accommodation_tax": comp["accommodation_tax"],
        "accommodation_gross": comp["accommodation_gross"],
        "pos_net": comp["pos_net"],
        "pos_tax": comp["pos_tax"],
        "pos_gross": comp["pos_gross"],
    }


# ═══════════════════════════════════════════════════════════════════════════
# LEDGER SUMMARY
# ═══════════════════════════════════════════════════════════════════════════

def get_general_ledger_summary():
    from ..db import connect
    conn = db.connect()
    charge_types = _net_types(("CHARGE",))
    exp_types = _net_types(("EXPENSE",))
    cogs_types = _net_types(("COGS",))
    cph = ",".join("?" * len(charge_types))
    eph = ",".join("?" * len(exp_types))
    gph = ",".join("?" * len(cogs_types))

    revenue = conn.execute(
        f"SELECT COALESCE(SUM(net + service), 0) FROM general_ledger WHERE entry_type IN ({cph})",
        charge_types,
    ).fetchone()[0] or 0
    expenses = conn.execute(
        f"SELECT COALESCE(SUM(net), 0) FROM general_ledger WHERE entry_type IN ({eph})",
        exp_types,
    ).fetchone()[0] or 0
    cogs = conn.execute(
        f"SELECT COALESCE(SUM(net), 0) FROM general_ledger WHERE entry_type IN ({gph})",
        cogs_types,
    ).fetchone()[0] or 0

    cash_types = _net_types(("PAYMENT", "REFUND", "EXPENSE", "ASSET_IN"))
    csh = ",".join("?" * len(cash_types))
    cash_in = conn.execute(
        f"""SELECT COALESCE(SUM(CASE WHEN gross > 0 THEN gross ELSE 0 END), 0)
            FROM general_ledger WHERE entry_type IN ({csh})""",
        cash_types,
    ).fetchone()[0] or 0
    cash_out = conn.execute(
        f"""SELECT COALESCE(SUM(CASE WHEN gross < 0 THEN -gross ELSE 0 END), 0)
            FROM general_ledger WHERE entry_type IN ({csh})""",
        cash_types,
    ).fetchone()[0] or 0

    ar = 0.0
    for row in conn.execute(
        "SELECT id FROM reservations WHERE status IN ('Reserved','Checked In','Checked Out')"
    ).fetchall():
        bal = calculate_guest_balance(row["id"])
        if bal > 0:
            ar += bal

    return {
        "billed_revenue": round(revenue, 2),
        "expenses": round(expenses + cogs, 2),
        "net_profit": round(revenue - expenses - cogs, 2),
        "cash_in": round(cash_in, 2),
        "cash_out": round(cash_out, 2),
        "net_cash": round(cash_in - cash_out, 2),
        "ar_balance": round(ar, 2),
    }


def get_recent_ledger(limit=100):
    from ..db import connect
    return db.connect().execute("""
        SELECT id, at, ref_type, ref_id, entry_type, category, gross, vat, description
        FROM general_ledger ORDER BY id DESC LIMIT ?
    """, (limit,)).fetchall()


def verify_app_attribution():
    from ..db import connect
    row = db.connect().execute("SELECT value, hash_signature FROM app_metadata WHERE key='gift_attribution'").fetchone()
    if not row:
        return False, "Attribution record missing."
    stored_value = row["value"] or ""
    stored_hash = row["hash_signature"] or ""
    expected_hash = gift_hash_for(stored_value)
    if stored_hash != expected_hash:
        return False, "Attribution tampered."
    # Accept legacy signature for existing installs, or any correctly signed attribution
    # (display name is user-configurable; integrity is guaranteed by hash/signature).
    return True, stored_value


# ═══════════════════════════════════════════════════════════════════════════
# INVOICES
# ═══════════════════════════════════════════════════════════════════════════

def generate_invoice_text(reservation_id, currency=CURRENCY):
    from ..db import connect
    from . import billing_docs
    conn = db.connect()
    r = conn.execute("""
        SELECT r.*, rm.room_number, rm.room_type FROM reservations r
        JOIN rooms rm ON r.room_id = rm.id WHERE r.id=?
    """, (reservation_id,)).fetchone()
    if not r:
        raise ValueError("Reservation not found.")
    nights = _nights(r["check_in_date"], r["check_out_date"])
    types = _net_types(("CHARGE",))
    ph = ",".join("?" * len(types))
    accom_row = conn.execute(f"""
        SELECT COALESCE(SUM(net),0) AS net, COALESCE(SUM(service),0) AS service,
               COALESCE(SUM(vat),0) AS vat, COALESCE(SUM(city_tax),0) AS city_tax,
               COALESCE(SUM(gross),0) AS gross
        FROM general_ledger
        WHERE ref_type='RES' AND ref_id=? AND entry_type IN ({ph})
    """, (reservation_id, *types)).fetchone()
    pos_items = [dict(x) for x in conn.execute(f"""
        SELECT p.id, p.item_description, p.quantity, p.unit_price, p.tax_amount, p.lot_code, p.batch_id,
               b.expiry_date AS batch_expiry_date, b.received_at AS batch_received_at,
               (SELECT COALESCE(SUM(gross),0) FROM general_ledger
                WHERE ref_type='POS' AND ref_id=p.id AND entry_type IN ({ph})) AS gross
        FROM pos_tabs p
        LEFT JOIN inventory_batches b ON b.id = p.batch_id
        WHERE p.reservation_id=? AND p.voided_at IS NULL
        ORDER BY p.id
    """, (*types, reservation_id)).fetchall()]
    payments = [dict(x) for x in conn.execute("""
        SELECT amount, method, payment_type, notes, at FROM payments
        WHERE reservation_id=? AND voided_at IS NULL ORDER BY id
    """, (reservation_id,)).fetchall()]
    accom = {
        "net": _round_money(accom_row["net"] or 0),
        "service": _round_money(accom_row["service"] or 0),
        "vat": _round_money(accom_row["vat"] or 0),
        "city_tax": _round_money(accom_row["city_tax"] or 0),
        "gross": _round_money(accom_row["gross"] or 0),
    }
    cfg = get_tax_config()
    display_name = get_setting("property_name", "") or get_app_display_name()
    return billing_docs.build_invoice_text(
        dict(r), nights, accom, pos_items, payments,
        currency=currency, usd_to_egp=float(cfg.get("usd_to_egp") or 1.0),
        display_name=display_name,
        address=get_setting("property_address", ""),
        phone=get_setting("property_phone", ""),
        footer_text=get_setting("invoice_footer_text", "Thank you for staying with us!"),
        traceability_text_fn=_traceability_text,
    )


def save_invoice_file(text, reservation_id, currency):
    from . import billing_docs
    return billing_docs.save_invoice_file(text, reservation_id, currency)


def generate_walkin_receipt_text(receipt_items, total_gross, method, payment_id=None, when=None):
    """Receipt text for an atomic walk-in sale (many items on same receipt). Includes batch traceability when available."""
    from . import billing_docs
    return billing_docs.build_walkin_receipt_text(
        receipt_items, total_gross, method, payment_id=payment_id, when=when,
        display_name=get_app_display_name() or "Property Manager",
        traceability_text_fn=_traceability_text,
    )


def save_walkin_receipt_file(text, payment_id):
    """Save walk-in receipt with distinct naming (avoids reservation invoice_Res00000 ambiguity)."""
    from . import billing_docs
    return billing_docs.save_walkin_receipt_file(text, payment_id)


# ═══════════════════════════════════════════════════════════════════════════
# PRINTING
# ═══════════════════════════════════════════════════════════════════════════

def list_printers():
    r = {"queues": [], "default": "", "source": "none"}
    try:
        if platform.system() == "Windows":
            ps = subprocess.run(["powershell", "-NoProfile", "-Command",
                                 "Get-Printer | Select-Object -ExpandProperty Name"],
                                capture_output=True, text=True, timeout=10)
            if ps.returncode == 0:
                r["queues"] = [l.strip() for l in ps.stdout.splitlines() if l.strip()]
                r["source"] = "winspool"
            return r
        res = subprocess.run(["lpstat", "-v"], capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if "device for" in line and ":" in line:
                    q = line.split("device for", 1)[1].split(":", 1)[0].strip()
                    if q:
                        r["queues"].append(q)
            r["source"] = "cups"
            rd = subprocess.run(["lpstat", "-d"], capture_output=True, text=True, timeout=5)
            if rd.returncode == 0 and "system default destination:" in rd.stdout:
                r["default"] = rd.stdout.split(":", 1)[1].strip()
    except Exception:  # pragma: no cover — platform-specific
        pass
    return r


def _print_raw_socket(host, port, filepath):
    p = _validate_print_filepath(filepath)
    size = p.stat().st_size
    # defense-in-depth (validate_print_filepath already caps, but keep local guard)
    if size > 5 * 1024 * 1024:
        raise ValueError("Refusing to send oversized print payload (>5MB).")
    with open(p, "rb") as f:
        data = f.read()
    if not data:
        return
    # normalize line endings for raw text printers; keep payload as-is otherwise
    if b"\x1b" not in data and b"\x1d" not in data and b"^XA" not in data:
        data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(10)
        s.connect((str(host), int(port)))
        s.sendall(data)


def _validate_print_target(target: str):
    t = (target or "").strip()
    if not t:
        return "", None
    if len(t) > 200:
        raise ValueError("Printer target is too long.")
    _sanitize_command_arg(t, "printer target")
    if t.startswith("cups:"):
        name = t[5:].strip()
        if not name or len(name) > 128 or not re.fullmatch(r"[A-Za-z0-9._@\-]+", name):
            raise ValueError("Invalid CUPS printer name.")
        return "cups", name
    if t.startswith("win:"):
        name = t[4:].strip()
        if not name or len(name) > 128 or not re.fullmatch(r"[A-Za-z0-9 ._()#\-]+", name):
            raise ValueError("Invalid Windows printer name.")
        return "win", name
    if t.startswith("raw:"):
        spec = t[4:].strip()
        if ":" not in spec:
            raise ValueError("Raw printer target must be raw:host:port")
        host, port_s = spec.rsplit(":", 1)
        host = host.strip().strip("[]")
        # conservative hostname/IP charset; block whitespace/control and obvious injection chars
        if not host or len(host) > 253 or not re.fullmatch(r"[A-Za-z0-9.\-:]+", host):
            raise ValueError("Invalid raw printer host.")
        try:
            port = int(port_s)
        except Exception:
            raise ValueError("Invalid raw printer port.")
        if port < 1 or port > 65535:
            raise ValueError("Invalid raw printer port.")
        return "raw", (host, port)
    raise ValueError(f"Unsupported printer target: {t}")


def _validate_print_filepath(filepath):
    p = Path(str(filepath or "")).expanduser()
    try:
        p = p.resolve()
    except Exception:
        p = Path(os.path.abspath(str(p)))
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"File not found: {p}")
    if not _is_under_allowed_root(p):
        raise ValueError("Refusing to print files outside approved data/export/invoice directories.")
    try:
        size = p.stat().st_size
    except Exception:
        size = 0
    if size > 5 * 1024 * 1024:
        raise ValueError("Refusing to print oversized file (>5MB).")
    return p


def print_to_printer(filepath, printer=None):
    target = printer if printer is not None else get_setting("default_printer", "")
    try:
        p = _validate_print_filepath(filepath)
        kind, value = _validate_print_target(str(target or ""))
        path_s = _sanitize_command_arg(str(p), "print file path")
        if kind == "":
            if platform.system() == "Windows":
                # Uses OS-registered handler; path is confined and sanitized above.
                os.startfile(path_s, "print")
            else:
                r = subprocess.run(["lp", path_s], capture_output=True, text=True, timeout=15, check=False)
                if r.returncode != 0:
                    err = ((r.stderr or r.stdout or "").strip()) or "unknown printer error"
                    return False, f"Print failed: {paths.redact_sensitive(err, 300)}"
            return True, f"Sent to default printer: {p.name}"
        if kind == "cups":
            r = subprocess.run(["lp", "-d", value, path_s], capture_output=True, text=True, timeout=15, check=False)
            if r.returncode == 0:
                return True, f"Sent to CUPS printer {value}"
            err = ((r.stderr or r.stdout or "").strip()) or "CUPS error"
            return False, f"CUPS error: {paths.redact_sensitive(err, 300)}"
        if kind == "win":
            os.startfile(path_s, "printto", value)
            return True, f"Sent to Windows printer {value}"
        if kind == "raw":
            host, port = value
            _svc_attr("_print_raw_socket", _print_raw_socket)(host, port, str(p))
            return True, f"Sent to raw printer {host}:{port}"
        return False, f"Unsupported target: {paths.redact_sensitive(target, 120)}"
    except subprocess.TimeoutExpired:
        return False, "Print timed out. File was saved to approved exports/invoices directory."
    except FileNotFoundError as e:
        return False, paths.redact_sensitive(str(e), 300)
    except ValueError as e:
        return False, paths.redact_sensitive(str(e), 300)
    except Exception as e:  # pragma: no cover — network/OS
        return False, f"Print error: {paths.redact_sensitive(e, 300)}"


def test_print(printer=None):
    EXPORT_DIR.mkdir(exist_ok=True)
    path = EXPORT_DIR / "printer_test.txt"
    display_name = get_app_display_name().upper()
    path.write_text(f"{display_name} PRINTER TEST\n{datetime.now()}\n", encoding="utf-8")
    return _svc_attr("print_to_printer", print_to_printer)(str(path), printer)


# ═══════════════════════════════════════════════════════════════════════════
# FRONT DESK / OCCUPANCY
# ═══════════════════════════════════════════════════════════════════════════

def get_front_desk_summary():
    today = datetime.now().strftime(DATE_FMT)
    conn = db.connect()
    total = conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
    occ = conn.execute("""SELECT COUNT(*) FROM reservations WHERE status IN ('Reserved','Checked In')
                          AND check_in_date <= ? AND check_out_date > ?""", (today, today)).fetchone()[0]
    clean = conn.execute("SELECT COUNT(*) FROM rooms WHERE housekeeping_status='Clean'").fetchone()[0]
    dirty = conn.execute("SELECT COUNT(*) FROM rooms WHERE housekeeping_status='Dirty'").fetchone()[0]
    maint = conn.execute("SELECT COUNT(*) FROM rooms WHERE housekeeping_status='Maintenance'").fetchone()[0]
    arr = conn.execute("SELECT COUNT(*) FROM reservations WHERE check_in_date=? AND status IN ('Reserved','Checked In')",
                       (today,)).fetchone()[0]
    dep = conn.execute("SELECT COUNT(*) FROM reservations WHERE check_out_date=? AND status IN ('Reserved','Checked In')",
                       (today,)).fetchone()[0]
    in_house = conn.execute("""SELECT COUNT(*) FROM reservations WHERE status='Checked In'
                               AND check_in_date <= ? AND check_out_date >= ?""", (today, today)).fetchone()[0]
    return {"occupancy_pct": round((occ / total * 100) if total else 0, 1), "occupied": occ, "total": total,
            "clean": clean, "dirty": dirty, "maint": maint,
            "arrivals_today": arr, "departures_today": dep, "in_house": in_house}


def get_dashboard_day_snapshot(today=None):
    """Return dashboard day lists (arrivals, departures, in-house) using app DATE_FMT/today semantics."""
    day = str(today or datetime.now().strftime(DATE_FMT))
    conn = db.connect()
    arr = conn.execute(
        "SELECT id, guest_name, room_id, check_in_date, check_out_date, status FROM reservations WHERE check_in_date=? AND status IN ('Reserved','Checked In') ORDER BY id DESC",
        (day,),
    ).fetchall()
    dep = conn.execute(
        "SELECT id, guest_name, room_id, check_in_date, check_out_date, status FROM reservations WHERE check_out_date=? AND status IN ('Reserved','Checked In','Checked Out') ORDER BY id DESC",
        (day,),
    ).fetchall()
    inh = conn.execute(
        "SELECT id, guest_name, room_id, check_in_date, check_out_date, status FROM reservations WHERE status='Checked In' ORDER BY id DESC"
    ).fetchall()
    return {"date": day, "arrivals": arr, "departures": dep, "in_house": inh}


def get_rooms_available_between(ci, co):
    from ..db import connect
    return db.connect().execute("""SELECT r.id, r.room_number, r.room_type, r.base_price, r.housekeeping_status
        FROM rooms r WHERE r.id NOT IN (
            SELECT room_id FROM reservations WHERE status IN ('Reserved','Checked In')
            AND NOT (check_out_date <= ? OR check_in_date >= ?)
        ) ORDER BY r.room_number""", (ci, co)).fetchall()


def get_upcoming_reservations_for_room(room_id):
    from ..db import connect
    today = datetime.now().strftime(DATE_FMT)
    return db.connect().execute("""SELECT id, guest_name, check_in_date, check_out_date, status, agreed_nightly_rate
        FROM reservations WHERE room_id=? AND check_in_date >= ?
        AND status IN ('Reserved','Checked In') ORDER BY check_in_date""", (room_id, today)).fetchall()


def get_occupancy_for_window(start, end):
    from ..db import connect
    d1 = datetime.strptime(start, DATE_FMT)
    d2 = datetime.strptime(end, DATE_FMT)
    days = max(1, (d2 - d1).days + 1)
    total_rooms = db.connect().execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
    trn = total_rooms * days
    rows = db.connect().execute("""SELECT check_in_date, check_out_date FROM reservations
        WHERE status IN ('Reserved','Checked In','Checked Out')
        AND NOT (check_out_date <= ? OR check_in_date >= ?)""", (start, end)).fetchall()
    occupied = 0
    for r in rows:
        a = max(datetime.strptime(r["check_in_date"], DATE_FMT), d1)
        b = min(datetime.strptime(r["check_out_date"], DATE_FMT), d2 + timedelta(days=1))
        if b > a:
            occupied += (b - a).days
    rev = db.connect().execute("""SELECT COALESCE(SUM(net + service), 0) FROM general_ledger
        WHERE entry_type IN ('CHARGE','VOID_CHARGE') AND category='Accommodation' AND ref_type='RES'
        AND ref_id IN (
            SELECT id FROM reservations
            WHERE status IN ('Reserved','Checked In','Checked Out')
            AND NOT (check_out_date <= ? OR check_in_date >= ?)
        )""", (start, end)).fetchone()[0]
    pct = (occupied / trn * 100) if trn else 0
    adr = (rev / occupied) if occupied else 0
    revpar = (rev / trn) if trn else 0
    return {"pct": round(pct, 1), "occupied": occupied, "total_room_nights": trn,
            "adr": round(adr, 2), "revpar": round(revpar, 2)}


# ═══════════════════════════════════════════════════════════════════════════
# CHART DATA
# ═══════════════════════════════════════════════════════════════════════════

def get_daily_revenue_expense_series(start, end):
    from ..db import connect
    conn = db.connect()
    charge_types = _net_types(("CHARGE",))
    cost_types = _net_types(("EXPENSE", "COGS"))
    cph = ",".join("?" * len(charge_types))
    xph = ",".join("?" * len(cost_types))
    rev = {r[0]: r[1] for r in conn.execute(
        f"""SELECT date(at), SUM(net + service) FROM general_ledger
            WHERE entry_type IN ({cph}) AND date(at) BETWEEN date(?) AND date(?)
            GROUP BY date(at)""",
        (*charge_types, start, end),
    ).fetchall()}
    exp = {r[0]: r[1] for r in conn.execute(
        f"""SELECT date(at), SUM(net) FROM general_ledger
            WHERE entry_type IN ({xph}) AND date(at) BETWEEN date(?) AND date(?)
            GROUP BY date(at)""",
        (*cost_types, start, end),
    ).fetchall()}
    d1 = datetime.strptime(start, DATE_FMT)
    d2 = datetime.strptime(end, DATE_FMT)
    out = []
    cur = d1
    while cur <= d2:
        k = cur.strftime(DATE_FMT)
        out.append((k, round(rev.get(k, 0) or 0, 2), round(exp.get(k, 0) or 0, 2)))
        cur += timedelta(days=1)
    return out


def get_daily_profit_series(start, end):
    return [(d, r - e) for d, r, e in get_daily_revenue_expense_series(start, end)]


def get_payment_method_dict(start, end):
    from ..db import connect
    types = _net_types(("PAYMENT", "REFUND"))
    ph = ",".join("?" * len(types))
    return {r[0]: round(r[1] or 0, 2) for r in db.connect().execute(
        f"""SELECT method, SUM(gross) FROM general_ledger
            WHERE entry_type IN ({ph}) AND date(at) BETWEEN date(?) AND date(?)
              AND method IS NOT NULL AND method <> ''
            GROUP BY method""",
        (*types, start, end),
    ).fetchall() if r[0]}


def get_daily_occupancy_series(start, end):
    from ..db import connect
    d1 = datetime.strptime(start, DATE_FMT)
    d2 = datetime.strptime(end, DATE_FMT)
    total_rooms = db.connect().execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
    out = []
    cur = d1
    while cur <= d2:
        day = cur.strftime(DATE_FMT)
        occ = db.connect().execute("""SELECT COUNT(*) FROM reservations
            WHERE status IN ('Reserved','Checked In','Checked Out')
            AND check_in_date <= ? AND check_out_date > ?""", (day, day)).fetchone()[0]
        out.append((day, round((occ / total_rooms * 100) if total_rooms else 0, 1)))
        cur += timedelta(days=1)
    return out


def get_occupancy_heatmap_data(start, end):
    from ..db import connect
    d1 = datetime.strptime(start, DATE_FMT)
    d2 = datetime.strptime(end, DATE_FMT)
    dates = []
    cur = d1
    while cur <= d2:
        dates.append(cur.strftime(DATE_FMT))
        cur += timedelta(days=1)
    rooms = db.connect().execute("SELECT id, room_number FROM rooms ORDER BY room_number").fetchall()
    res = db.connect().execute("""SELECT room_id, check_in_date, check_out_date FROM reservations
        WHERE status IN ('Reserved','Checked In','Checked Out')
        AND NOT (check_out_date <= ? OR check_in_date >= ?)""", (start, end)).fetchall()
    matrix = []
    for room in rooms:
        row = []
        for day in dates:
            occ = any(room["id"] == r["room_id"] and r["check_in_date"] <= day < r["check_out_date"] for r in res)
            row.append(1 if occ else 0)
        matrix.append(row)
    return {"rooms": [r["room_number"] for r in rooms], "dates": dates, "matrix": matrix}


# ═══════════════════════════════════════════════════════════════════════════
# REPORTS
# ═══════════════════════════════════════════════════════════════════════════

def _range_where(start, end, col="at"):
    return f"date({col}) BETWEEN date(?) AND date(?)", [start, end]


def report_pl_statement(start, end):
    from ..db import connect
    conn = db.connect()
    where, params = _range_where(start, end)
    charge_types = _net_types(("CHARGE",))
    cogs_types = _net_types(("COGS",))
    exp_types = _net_types(("EXPENSE",))
    cph = ",".join("?" * len(charge_types))
    gph = ",".join("?" * len(cogs_types))
    eph = ",".join("?" * len(exp_types))

    rev = conn.execute(
        f"""SELECT category, SUM(net) AS net, SUM(service) AS svc
            FROM general_ledger WHERE {where} AND entry_type IN ({cph}) GROUP BY category""",
        (*params, *charge_types),
    ).fetchall()
    cogs = conn.execute(
        f"SELECT COALESCE(SUM(net), 0) FROM general_ledger WHERE {where} AND entry_type IN ({gph})",
        (*params, *cogs_types),
    ).fetchone()[0] or 0
    exp = conn.execute(
        f"""SELECT category, SUM(net) AS net FROM general_ledger
            WHERE {where} AND entry_type IN ({eph}) GROUP BY category""",
        (*params, *exp_types),
    ).fetchall()

    total_rev = sum((r["net"] or 0) + (r["svc"] or 0) for r in rev)
    total_exp = sum((r["net"] or 0) for r in exp)
    gross_profit = total_rev - cogs
    net = gross_profit - total_exp
    lines = [f"P&L   {start} to {end}", "All amounts in EGP", "=" * 60, "REVENUE"]
    for r in rev:
        lines.append(f"  {(r['category'] or 'Other'):<32} {(r['net'] or 0) + (r['svc'] or 0):>15,.2f}")
    lines += [f"  {'TOTAL REVENUE':<32} {total_rev:>15,.2f}", "", "COGS",
              f"  {'COGS':<32} {cogs:>15,.2f}", "",
              f"  {'GROSS PROFIT':<32} {gross_profit:>15,.2f}", "", "EXPENSES"]
    for r in exp:
        lines.append(f"  {(r['category'] or 'Other'):<32} {(r['net'] or 0):>15,.2f}")
    lines += [f"  {'TOTAL EXPENSES':<32} {total_exp:>15,.2f}", "=" * 60,
              f"  {'NET PROFIT':<32} {net:>15,.2f}"]
    return "\n".join(lines)


def report_cash_flow(start, end):
    from ..db import connect
    conn = db.connect()
    where, params = _range_where(start, end)
    cash_types = _net_types(("PAYMENT", "REFUND", "EXPENSE", "ASSET_IN"))
    csh = ",".join("?" * len(cash_types))
    cin = conn.execute(
        f"""SELECT COALESCE(SUM(CASE WHEN gross > 0 THEN gross ELSE 0 END), 0)
            FROM general_ledger WHERE {where} AND entry_type IN ({csh})""",
        (*params, *cash_types),
    ).fetchone()[0] or 0
    cout = conn.execute(
        f"""SELECT COALESCE(SUM(CASE WHEN gross < 0 THEN -gross ELSE 0 END), 0)
            FROM general_ledger WHERE {where} AND entry_type IN ({csh})""",
        (*params, *cash_types),
    ).fetchone()[0] or 0
    return "\n".join([f"CASH FLOW   {start} to {end}", "=" * 60,
                      f"  Cash In:   {cin:>15,.2f}", f"  Cash Out:  {cout:>15,.2f}",
                      f"  NET CASH:  {cin - cout:>15,.2f}"])


def report_payment_methods(start, end):
    d = get_payment_method_dict(start, end)
    lines = [f"PAYMENT METHODS   {start} to {end}", "=" * 60]
    total = 0
    for k, v in d.items():
        lines.append(f"  {k:<32} {v:>15,.2f}")
        total += v
    lines += ["-" * 60, f"  {'TOTAL':<32} {total:>15,.2f}"]
    return "\n".join(lines)


def report_vat(start, end):
    from ..db import connect
    conn = db.connect()
    where, params = _range_where(start, end)
    out_types = _net_types(("CHARGE",))
    in_types = _net_types(("EXPENSE", "ASSET_IN"))
    oph = ",".join("?" * len(out_types))
    iph = ",".join("?" * len(in_types))
    out_vat = conn.execute(
        f"SELECT COALESCE(SUM(vat), 0) FROM general_ledger WHERE {where} AND entry_type IN ({oph})",
        (*params, *out_types),
    ).fetchone()[0] or 0
    in_vat = conn.execute(
        f"SELECT COALESCE(SUM(vat), 0) FROM general_ledger WHERE {where} AND entry_type IN ({iph})",
        (*params, *in_types),
    ).fetchone()[0] or 0
    return "\n".join([f"VAT REPORT   {start} to {end}", "=" * 60,
                      f"  Output VAT (collected):   {out_vat:>15,.2f}",
                      f"  Input VAT (paid):         {in_vat:>15,.2f}",
                      f"  NET VAT PAYABLE:          {out_vat - in_vat:>15,.2f}"])


def report_occupancy(start, end):
    r = get_occupancy_for_window(start, end)
    return "\n".join([f"OCCUPANCY   {start} to {end}", "=" * 60,
                      f"  Total room-nights:      {r['total_room_nights']}",
                      f"  Occupied room-nights:   {r['occupied']}",
                      f"  Occupancy %:            {r['pct']}%",
                      f"  ADR:                    {r['adr']:,.2f} EGP",
                      f"  RevPAR:                 {r['revpar']:,.2f} EGP"])


def report_daily_ops(date):
    from ..db import connect
    arr = db.connect().execute("SELECT id, guest_name FROM reservations WHERE check_in_date=? AND status IN ('Reserved','Checked In')",
                            (date,)).fetchall()
    dep = db.connect().execute("SELECT id, guest_name FROM reservations WHERE check_out_date=? AND status IN ('Reserved','Checked In')",
                            (date,)).fetchall()
    inh = db.connect().execute("""SELECT id, guest_name, check_in_date, check_out_date FROM reservations
        WHERE status='Checked In' AND check_in_date <= ? AND check_out_date >= ?""", (date, date)).fetchall()
    lines = [f"DAILY OPS   {date}", "=" * 60, f"ARRIVALS ({len(arr)}):"]
    for r in arr:
        lines.append(f"  #{r['id']}  {r['guest_name']}")
    lines += ["", f"DEPARTURES ({len(dep)}):"]
    for r in dep:
        lines.append(f"  #{r['id']}  {r['guest_name']}")  # pragma: no cover
    lines += ["", f"IN HOUSE ({len(inh)}):"]
    for r in inh:
        lines.append(f"  #{r['id']}  {r['guest_name']}  ({r['check_in_date']} -> {r['check_out_date']})")  # pragma: no cover
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# BACKUP
# ═══════════════════════════════════════════════════════════════════════════

def backup_now():
    EXPORT_DIR.mkdir(exist_ok=True)
    src = db.connect()
    dst_path = EXPORT_DIR / f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    import sqlite3 as _s
    dst = _s.connect(str(dst_path))
    with dst:
        src.backup(dst)
    dst.close()
    _audit("backup", "db", "", str(dst_path))
    return str(dst_path)





# ═══════════════════════════════════════════════════════════════════════════
# INTEGRITY / HEALTH (Phase 0.4, read-only)
# ═══════════════════════════════════════════════════════════════════════════

def list_walkin_pos(limit=200):
    """Non-voided walk-in POS lines (reservation_id IS NULL), for management/void flows."""
    from ..db import connect
    return db.connect().execute("""
        SELECT p.id, p.created_at, p.item_description, p.quantity, p.unit_price, p.tax_amount,
               p.inventory_item_id, p.voided_at, p.batch_id, p.lot_code, p.cost_at_sale
        FROM pos_tabs p
        WHERE p.reservation_id IS NULL
        ORDER BY p.id DESC LIMIT ?
    """, (int(limit or 200),)).fetchall()


def list_walkin_payments(limit=100):
    """Walk-in payments (reservation_id IS NULL)."""
    from ..db import connect
    return db.connect().execute("""
        SELECT id, at, amount, method, payment_type, notes, voided_at
        FROM payments
        WHERE reservation_id IS NULL
        ORDER BY id DESC LIMIT ?
    """, (int(limit or 100),)).fetchall()


def _integrity_append_issue(issues, typ, detail):
    issues.append({"type": typ, "detail": detail})


def _integrity_check_ledger_refs(conn, issues, warnings):
    try:
        rows = conn.execute("SELECT DISTINCT l.ref_id FROM general_ledger l WHERE l.ref_type='POS' AND l.ref_id NOT IN (SELECT id FROM pos_tabs) LIMIT 20").fetchall()
        if rows:
            _integrity_append_issue(issues, "orphan_ledger_pos_ref", f"ledger POS refs missing pos_tabs ids (sample): {[r[0] for r in rows]}")
    except Exception as e:
        warnings.append(f"pos ref check: {e}")
    try:
        rows = conn.execute("SELECT DISTINCT l.ref_id FROM general_ledger l WHERE l.ref_type='RES' AND l.ref_id NOT IN (SELECT id FROM reservations) LIMIT 20").fetchall()
        if rows:
            _integrity_append_issue(issues, "orphan_ledger_res_ref", f"ledger RES refs missing reservation ids (sample): {[r[0] for r in rows]}")
    except Exception as e:
        warnings.append(f"res ref check: {e}")
    try:
        rows = conn.execute("""
            SELECT DISTINCT l.ref_type, l.ref_id FROM general_ledger l
            WHERE l.ref_type IN ('PAY','WALKIN_PAY') AND l.ref_id NOT IN (SELECT id FROM payments)
            LIMIT 20
        """).fetchall()
        if rows:
            _integrity_append_issue(issues, "orphan_ledger_payment_ref", f"ledger payment refs missing payments (sample): {[(r[0],r[1]) for r in rows]}")
    except Exception as e:
        warnings.append(f"payment ref check: {e}")
    try:
        rows = conn.execute("""
            SELECT p.id, p.reservation_id FROM pos_tabs p
            WHERE p.reservation_id IS NOT NULL AND p.reservation_id NOT IN (SELECT id FROM reservations)
            LIMIT 20
        """).fetchall()
        if rows:
            _integrity_append_issue(issues, "orphan_pos_reservation", f"pos_tabs with missing reservation_id (sample): {[(r[0],r[1]) for r in rows]}")
    except Exception as e:
        warnings.append(f"pos reservation check: {e}")


def _integrity_check_inventory_batches(conn, issues, warnings):
    try:
        rows = conn.execute("""
            SELECT b.id, b.item_id, b.lot_code FROM inventory_batches b
            WHERE b.item_id NOT IN (SELECT id FROM inventory) LIMIT 20
        """).fetchall()
        if rows:
            _integrity_append_issue(issues, "orphan_batch_item", [(r[0], r[1], r[2]) for r in rows])
    except Exception as e:
        warnings.append(f"batch item check: {e}")
    try:
        rows = conn.execute("""
            SELECT id, item_id, lot_code, quantity_remaining FROM inventory_batches
            WHERE quantity_remaining < -1e-9 LIMIT 20
        """).fetchall()
        if rows:
            _integrity_append_issue(issues, "negative_batch_remaining", [(r[0], r[1], r[2], r[3]) for r in rows])
    except Exception as e:
        warnings.append(f"batch remaining check: {e}")
    try:
        rows = conn.execute("""
            SELECT p.id, p.batch_id, p.inventory_item_id FROM pos_tabs p
            WHERE p.batch_id IS NOT NULL AND p.batch_id NOT IN (SELECT id FROM inventory_batches)
            LIMIT 20
        """).fetchall()
        if rows:
            _integrity_append_issue(issues, "orphan_pos_batch", [(r[0], r[1], r[2]) for r in rows])
        rows = conn.execute("""
            SELECT p.id, p.batch_id, p.inventory_item_id, b.item_id
            FROM pos_tabs p JOIN inventory_batches b ON b.id = p.batch_id
            WHERE p.inventory_item_id IS NOT NULL AND p.inventory_item_id != b.item_id
            LIMIT 20
        """).fetchall()
        if rows:
            _integrity_append_issue(issues, "pos_batch_item_mismatch", [(r[0], r[1], r[2], r[3]) for r in rows])
    except Exception as e:
        warnings.append(f"pos batch ref check: {e}")
    try:
        rows = conn.execute("""
            SELECT i.id, i.item_name, i.stock_qty,
                   COALESCE(SUM(b.quantity_remaining), 0) AS batch_remaining,
                   COUNT(b.id) AS batch_count
            FROM inventory i
            LEFT JOIN inventory_batches b ON b.item_id = i.id
            GROUP BY i.id
            HAVING batch_count > 0 AND ABS(i.stock_qty - COALESCE(SUM(b.quantity_remaining), 0)) > 0.0001
            LIMIT 30
        """).fetchall()
        if rows:
            detail = [{"item_id": r[0], "item": r[1], "stock_qty": r[2], "batch_remaining": r[3], "batches": r[4]} for r in rows]
            _integrity_append_issue(issues, "stock_batch_drift", detail)
        rows = conn.execute("""
            SELECT i.id, i.item_name, i.stock_qty
            FROM inventory i
            WHERE ABS(COALESCE(i.stock_qty,0)) > 0.0001
              AND NOT EXISTS (SELECT 1 FROM inventory_batches b WHERE b.item_id = i.id)
            LIMIT 20
        """).fetchall()
        if rows:
            warnings.append({"type": "stock_without_batches", "detail": [(r[0], r[1], r[2]) for r in rows]})
    except Exception as e:
        warnings.append(f"stock/batch drift check: {e}")


def verify_ledger_integrity():
    """Read-only health check: ledger/ref consistency and basic money sanity."""
    from ..db import connect
    conn = db.connect()
    issues = []
    warnings = []
    stats = {}

    try:
        stats["ledger_rows"] = conn.execute("SELECT COUNT(*) FROM general_ledger").fetchone()[0]
        stats["reservations"] = conn.execute("SELECT COUNT(*) FROM reservations").fetchone()[0]
        stats["pos_rows"] = conn.execute("SELECT COUNT(*) FROM pos_tabs").fetchone()[0]
        stats["payments"] = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        stats["void_reversals"] = conn.execute("SELECT COUNT(*) FROM general_ledger WHERE reverses_id IS NOT NULL").fetchone()[0]
    except Exception as e:
        warnings.append(f"stat ledger_rows: {e}")

    _integrity_check_ledger_refs(conn, issues, warnings)

    try:
        bad = []
        for r in conn.execute("SELECT id FROM reservations WHERE status IN ('Reserved','Checked In','Checked Out') ORDER BY id DESC LIMIT 100").fetchall():
            bal = _svc_attr("calculate_guest_balance", calculate_guest_balance)(r["id"])
            if bal < -100000 or bal > 10000000:
                bad.append({"reservation_id": r["id"], "balance": bal})
        if bad:
            warnings.append({"type": "balance_outlier_sample", "detail": bad[:10]})
        stats["balances_sampled"] = min(100, stats.get("reservations", 0))
    except Exception as e:
        warnings.append(f"balance sample: {e}")

    try:
        stats["inventory_items"] = conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0]
        stats["inventory_batches"] = conn.execute("SELECT COUNT(*) FROM inventory_batches").fetchone()[0]
    except Exception as e:
        warnings.append(f"inventory stats: {e}")

    _integrity_check_inventory_batches(conn, issues, warnings)

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "stats": stats,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "note": "Read-only checks. Investigate issues before manual data edits; ledger is append-only.",
    }

# ═══════════════════════════════════════════════════════════════════════════
# ANALYTICS, UNIFIED REPORTS, EXPORT PRESETS (read-only / guarded)
# ═══════════════════════════════════════════════════════════════════════════

ANALYTICS_EXPORT_DIR = EXPORT_DIR / "analytics"
EXPORT_SCHEMA_VERSION = "1.0"

ANALYTICS_PRESETS = {
    "ops_daily": {
        "version": "1",
        "label": "Daily Operations",
        "description": "Arrivals, departures, in-house, occupancy and cash snapshot.",
        "datasets": ["daily_ops", "occupancy_daily", "financial_summary"],
        "formats": ["txt", "json"],
    },
    "finance_monthly": {
        "version": "1",
        "label": "Finance Monthly Pack",
        "description": "P&L, cash flow, VAT, payment methods, AR and daily finance series.",
        "datasets": ["financial_summary", "pl", "cash_flow", "vat", "payment_methods", "financial_daily"],
        "formats": ["txt", "json", "csv"],
    },
    "management_pack": {
        "version": "1",
        "label": "Unified Management Pack",
        "description": "Operations + finance + occupancy + decision-support snapshot.",
        "datasets": ["financial_summary", "pl", "cash_flow", "vat", "payment_methods",
                     "occupancy", "occupancy_daily", "financial_daily", "decision_support_v1"],
        "formats": ["txt", "json", "csv"],
    },
    "ml_features_v1": {
        "version": "1",
        "label": "ML/Decision Features v1",
        "description": "Stable feature-ready daily aggregates for future small models/analysis.",
        "datasets": ["ml_features_v1"],
        "formats": ["json", "csv"],
    },
}


def list_analytics_presets():
    return [{"key": k, **v} for k, v in ANALYTICS_PRESETS.items()]


def _export_metadata(preset, start, end, extra=None):
    md = {
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "preset": preset,
        "preset_version": (ANALYTICS_PRESETS.get(preset) or {}).get("version", "1"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "date_start": start,
        "date_end": end,
        "currency": CURRENCY,
        "db_schema_version": db.SCHEMA_VERSION,
        "db_user_version": db.user_version(),
        "source": "local_sqlite",
    }
    if extra:
        md.update(extra)
    return md


def dataset_financial_summary(start, end):
    summary = _svc_attr("get_general_ledger_summary", get_general_ledger_summary)()
    return {
        "period": {"start": start, "end": end},
        "summary": summary,
        "pl": {
            "revenue_net_plus_service": summary["billed_revenue"],
            "expenses_plus_cogs": summary["expenses"],
            "net_profit": summary["net_profit"],
        },
        "cash": {
            "cash_in": summary["cash_in"],
            "cash_out": summary["cash_out"],
            "net_cash": summary["net_cash"],
        },
        "ar_balance": summary["ar_balance"],
    }


def dataset_financial_daily(start, end):
    rows = []
    for d, rev, exp in get_daily_revenue_expense_series(start, end):
        rows.append({
            "date": d,
            "revenue_net_plus_service": round(rev, 2),
            "expenses_plus_cogs": round(exp, 2),
            "profit": round(rev - exp, 2),
        })
    return rows


def dataset_occupancy_daily(start, end):
    return [{"date": d, "occupancy_pct": pct} for d, pct in get_daily_occupancy_series(start, end)]


def dataset_payment_methods(start, end):
    d = get_payment_method_dict(start, end)
    return [{"method": k, "net_amount": v} for k, v in d.items()]


def dataset_decision_support_v1(start, end):
    fin = _svc_attr("dataset_financial_daily", dataset_financial_daily)(start, end)
    occ_rows = _svc_attr("dataset_occupancy_daily", dataset_occupancy_daily)(start, end)
    occ = {r["date"]: r["occupancy_pct"] for r in occ_rows}
    total_rev = sum(r["revenue_net_plus_service"] for r in fin)
    total_exp = sum(r["expenses_plus_cogs"] for r in fin)
    total_profit = sum(r["profit"] for r in fin)
    avg_occ = round(sum(occ.values()) / len(occ), 2) if occ else 0.0
    best_day = max(fin, key=lambda r: r["profit"], default=None)
    worst_day = min(fin, key=lambda r: r["profit"], default=None)
    neg_days = [r["date"] for r in fin if r["profit"] < 0]
    summary = _svc_attr("get_general_ledger_summary", get_general_ledger_summary)()
    signals = []
    if summary["ar_balance"] > 0:
        signals.append({"type": "cash", "level": "info", "message": f"Outstanding A/R balance: {summary['ar_balance']:.2f} EGP"})
    if neg_days:
        signals.append({"type": "profit", "level": "warning", "message": f"{len(neg_days)} day(s) with negative profit in range"})
    if avg_occ < 35 and occ:
        signals.append({"type": "occupancy", "level": "info", "message": f"Average occupancy is low ({avg_occ}%)"})
    return {
        "kpis": {
            "total_revenue_net_plus_service": round(total_rev, 2),
            "total_expenses_plus_cogs": round(total_exp, 2),
            "total_profit": round(total_profit, 2),
            "average_occupancy_pct": avg_occ,
            "ar_balance": summary["ar_balance"],
            "net_cash_all_time_ledger": summary["net_cash"],
            "negative_profit_days": len(neg_days),
            "best_profit_day": best_day,
            "worst_profit_day": worst_day,
        },
        "signals": signals,
    }


def dataset_ml_features_v1(start, end):
    fin = dataset_financial_daily(start, end)
    occ = {r["date"]: r["occupancy_pct"] for r in dataset_occupancy_daily(start, end)}
    rows = []
    for r in fin:
        rev = r["revenue_net_plus_service"]
        exp = r["expenses_plus_cogs"]
        profit = r["profit"]
        rows.append({
            "date": r["date"],
            "revenue_net_plus_service_egp": rev,
            "expenses_plus_cogs_egp": exp,
            "profit_egp": profit,
            "is_profitable": 1 if profit > 0 else 0,
            "occupancy_pct": occ.get(r["date"], 0.0),
            "expense_ratio": round((exp / rev) if rev else 0.0, 6),
            "profit_margin": round((profit / rev) if rev else 0.0, 6),
        })
    return rows


def _dataset_by_name(name, start, end):
    if name == "financial_summary":
        return dataset_financial_summary(start, end)
    if name == "financial_daily":
        return dataset_financial_daily(start, end)
    if name == "occupancy":
        return get_occupancy_for_window(start, end)
    if name == "occupancy_daily":
        return dataset_occupancy_daily(start, end)
    if name == "payment_methods":
        return dataset_payment_methods(start, end)
    if name == "decision_support_v1":
        return dataset_decision_support_v1(start, end)
    if name == "ml_features_v1":
        return dataset_ml_features_v1(start, end)
    if name == "pl":
        return report_pl_statement(start, end)
    if name == "cash_flow":
        return report_cash_flow(start, end)
    if name == "vat":
        return report_vat(start, end)
    if name == "daily_ops":
        # use end date as "as of" for ops; callers can generate per day if needed
        return report_daily_ops(end)
    return None


def _confined_export_path(path, root=None):
    root = Path(root or _svc_attr("ANALYTICS_EXPORT_DIR", ANALYTICS_EXPORT_DIR)).resolve()
    root.mkdir(parents=True, exist_ok=True)
    p = Path(path).resolve()
    if root != p and root not in p.parents:
        raise ValueError("Refusing to write export outside approved analytics export directory.")
    return p


def _write_json(path, payload, root=None):
    p = _confined_export_path(path, root=_svc_attr("ANALYTICS_EXPORT_DIR", ANALYTICS_EXPORT_DIR))
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _write_csv(path, rows, root=None):
    rows = list(rows or [])
    p = _confined_export_path(path, root=_svc_attr("ANALYTICS_EXPORT_DIR", ANALYTICS_EXPORT_DIR))
    if not rows:
        p.write_text("", encoding="utf-8")
        return
    if isinstance(rows[0], dict):
        fieldnames = []
        for r in rows:
            for k in r.keys():
                if k not in fieldnames:
                    fieldnames.append(k)
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fieldnames})
    else:
        p.write_text(str(rows), encoding="utf-8")


def export_analytics_preset(preset, start, end, formats=None):
    spec = ANALYTICS_PRESETS.get(preset)
    if not spec:
        raise ValueError(f"Unknown analytics preset: {preset}")
    try:
        datetime.strptime(start, DATE_FMT)
        datetime.strptime(end, DATE_FMT)
    except ValueError:
        raise ValueError("Dates must be YYYY-MM-DD.")
    formats = list(formats or spec.get("formats") or ["json"])
    _svc_attr("ANALYTICS_EXPORT_DIR", ANALYTICS_EXPORT_DIR).mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = _svc_attr("ANALYTICS_EXPORT_DIR", ANALYTICS_EXPORT_DIR) / f"{preset}_{start}_{end}_{stamp}"
    metadata = _export_metadata(preset, start, end)
    datasets = {}
    text_sections = []
    for name in spec.get("datasets", []):
        data = _dataset_by_name(name, start, end)
        datasets[name] = data
        if isinstance(data, str):
            text_sections.append(f"## {name}\n{data}\n")
        else:
            text_sections.append(f"## {name}\n{json.dumps(data, indent=2, ensure_ascii=False, default=str)}\n")
    outputs = {}
    payload = {"metadata": metadata, "datasets": datasets}
    if "json" in formats:
        jp = str(base) + ".json"
        _write_json(jp, payload)
        outputs["json"] = jp
    if "txt" in formats:
        tp = str(base) + ".txt"
        header = [f"UNIFIED ANALYTICS REPORT — {preset}", json.dumps(metadata, ensure_ascii=False), "=" * 72, ""]
        _confined_export_path(tp).write_text("\n".join(header + text_sections), encoding="utf-8")
        outputs["txt"] = tp
    if "csv" in formats:
        # write primary tabular dataset(s)
        csv_paths = []
        for name, data in datasets.items():
            if isinstance(data, list) and data and isinstance(data[0], dict):
                cp = str(base) + f"_{name}.csv"
                _write_csv(cp, data)
                csv_paths.append(cp)
            elif isinstance(data, dict):
                # flatten simple dicts where possible
                rows = []
                for k, v in data.items():
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        cp = str(base) + f"_{name}_{k}.csv"
                        _write_csv(cp, v)
                        csv_paths.append(cp)
                    elif not isinstance(v, (list, dict)):
                        rows.append({"key": k, "value": v})
                if rows:
                    cp = str(base) + f"_{name}.csv"
                    _write_csv(cp, rows)
                    csv_paths.append(cp)
        if csv_paths:
            outputs["csv"] = csv_paths
    _audit("analytics_export", "report", "", f"{preset} {start}->{end} {','.join(outputs.keys())}")
    return {"metadata": metadata, "outputs": outputs, "datasets": list(datasets.keys())}


def generate_unified_report(preset="management_pack", start=None, end=None, formats=None):
    now = datetime.now()
    start = start or now.replace(day=1).strftime(DATE_FMT)
    end = end or now.strftime(DATE_FMT)
    return export_analytics_preset(preset, start, end, formats=formats)


def has_operational_data():
    from ..db import connect
    c = db.connect()
    checks = [
        ("reservations", "SELECT 1 FROM reservations LIMIT 1"),
        ("payments", "SELECT 1 FROM payments LIMIT 1"),
        ("pos_tabs", "SELECT 1 FROM pos_tabs LIMIT 1"),
        ("operational_expenses", "SELECT 1 FROM operational_expenses LIMIT 1"),
    ]
    for _name, q in checks:
        if c.execute(q).fetchone():
            return True
    return False


DEMO_DATA_PRESETS = {
    "analysis_small": {
        "label": "Analysis Small (light synthetic)",
        "description": "Small synthetic dataset: ~10 reservations with POS, payments, expenses for quick analysis.",
        "reservations": 10, "days_back": 25, "days_forward": 10, "max_stay": 3,
        "pos_per_res": 2, "book_activities": True, "expenses": 6,
    },
    "business_starter": {
        "label": "Business Starter (recommended)",
        "description": "Richer realistic demo: guests, past/current/future reservations (mixed statuses), POS items & booked activities, deposits/payments, and expenses across categories — ideal for seeding/loading business demo data.",
        "reservations": 24, "days_back": 45, "days_forward": 20, "max_stay": 4,
        "pos_per_res": 3, "book_activities": True, "expenses": 12,
    },
    "hospitality_full": {
        "label": "Hospitality Full (comprehensive)",
        "description": "Comprehensive demo dataset with higher volume across reservations, POS, activities, payments and expenses for demos, training, and reporting.",
        "reservations": 40, "days_back": 60, "days_forward": 30, "max_stay": 5,
        "pos_per_res": 4, "book_activities": True, "expenses": 20,
    },
}


def list_demo_data_presets():
    return [{"key": k, **v} for k, v in DEMO_DATA_PRESETS.items()]


def apply_demo_data_preset(name, password, confirm_text, allow_existing=False):
    """Guarded demo data loader for analysis/prototyping and easier business-data seeding. Never silent."""
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if name not in DEMO_DATA_PRESETS:
        raise ValueError(f"Unknown demo preset: {name}")
    if str(confirm_text).strip().upper() != "APPLY DEMO DATA":
        raise ValueError('Confirmation text must be exactly: APPLY DEMO DATA')
    if has_operational_data() and not allow_existing:
        raise ValueError("Database already has operational data. Refusing to apply demo preset. (Use on a fresh DB or after exporting/backing up.)")

    preset = DEMO_DATA_PRESETS[name]
    from ..db import connect
    conn = db.connect()
    rooms = conn.execute("SELECT id, base_price, room_type FROM rooms ORDER BY id").fetchall()
    inv = conn.execute("SELECT id, item_name, selling_price, unit_cost FROM inventory WHERE active=1 ORDER BY id").fetchall()
    cats = conn.execute("SELECT id, category_name FROM expense_categories ORDER BY id").fetchall()
    plans = conn.execute("SELECT id, name FROM rate_plans ORDER BY id").fetchall()
    if not rooms:
        raise ValueError("No rooms available for demo preset. Add rooms (or import a seed config) first.")
    activities = []
    try:
        activities = conn.execute("SELECT id, name, unit_price FROM activities WHERE active=1 ORDER BY id").fetchall()
    except Exception:
        activities = []

    def _get(row, k, d=None):
        try:
            return row[k]
        except Exception:
            return d

    n_res = int(preset.get("reservations", 12))
    days_back = int(preset.get("days_back", 30))
    days_forward = int(preset.get("days_forward", 14))
    max_stay = max(1, int(preset.get("max_stay", 3)))
    pos_per = max(0, int(preset.get("pos_per_res", 2)))
    n_exp = int(preset.get("expenses", 8))
    do_acts = bool(preset.get("book_activities", True)) and len(activities) > 0

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    created = {"guests": 0, "reservations": 0, "pos_items": 0, "activities_booked": 0, "payments": 0, "expenses": 0}
    first_names = ["Ahmed", "Sara", "Mohamed", "Nour", "Omar", "Laila", "Youssef", "Mona", "Hassan", "Dina", "Karim", "Rana", "Ali", "Fatma", "Tamer", "Salma"]
    last_names = ["Hassan", "Ali", "Ibrahim", "Mahmoud", "Sayed", "Fahmy", "Mostafa", "Khaled", "Adel", "Nabil", "Samir", "Farouk"]
    sources = ["Direct", "Booking.com", "Expedia", "Walk-in", "Corporate", "Phone", "Website"]

    res_ids = []
    for i in range(n_res):
        guest = f"{first_names[i % len(first_names)]} {last_names[(i // 2) % len(last_names)]}"
        # spread stays across past/current/future
        offset = -days_back + (i * max(1, (days_back + days_forward) // max(n_res, 1)))
        ci_dt = today + timedelta(days=offset)
        stay = 1 + (i % max_stay)
        co_dt = ci_dt + timedelta(days=stay)
        ci = ci_dt.strftime(DATE_FMT)
        co = co_dt.strftime(DATE_FMT)
        room = rooms[i % len(rooms)]
        plan = plans[i % len(plans)]["id"] if plans else None
        base_price = float(_get(room, "base_price", 100) or 100)
        # small price variation
        rate = round(base_price + (i % 3) * 10, 2)
        source = sources[i % len(sources)]
        try:
            rid = create_reservation(guest, source, _get(room, "id"), plan, rate, ci, co, 0.0)
        except Exception:
            # fallback: try a different room if unavailable
            placed = False
            for r2 in rooms:
                try:
                    rid = create_reservation(guest, source, _get(r2, "id"), plan, rate, ci, co, 0.0)
                    placed = True
                    break
                except Exception:
                    continue
            if not placed:
                continue
        created["reservations"] += 1
        res_ids.append((rid, ci_dt, co_dt, stay, rate))

        # POS items
        if inv and pos_per > 0:
            for j in range((i % pos_per) + 1):
                item = inv[(i + j) % len(inv)]
                qty = 1 + ((i + j) % 3)
                try:
                    add_pos_item(rid, str(_get(item, "item_name", "Item")), qty, float(_get(item, "selling_price", 10) or 10), _get(item, "id"))
                    created["pos_items"] += 1
                except Exception:
                    pass

        # book activities for some reservations
        if do_acts and (i % 3 != 2):
            act = activities[i % len(activities)]
            try:
                book_activity(rid, _get(act, "id"), 1, _get(act, "name"), _get(act, "unit_price"))
                created["activities_booked"] += 1
            except Exception:
                pass

        # payments: deposit/full/partial depending on timing
        try:
            status_row = db.connect().execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()
            status = status_row["status"] if status_row else "Reserved"
            total_est = round(rate * stay * 1.24, 2)  # rough
            paid = False
            if co_dt < today - timedelta(days=1):
                # past stay: likely fully paid
                amt = max(10.0, round(total_est * (0.9 if i % 4 == 0 else 1.0), 2))
                record_payment(rid, amt, "Card" if i % 2 == 0 else "Cash", "Demo settlement")
                created["payments"] += 1
                paid = True
            elif ci_dt <= today <= co_dt:
                # in-house: deposit/partial
                amt = round(max(20.0, total_est * 0.5), 2)
                record_payment(rid, amt, "Cash", "Demo deposit/partial")
                created["payments"] += 1
            elif i % 2 == 0:
                # future with deposit
                amt = round(max(20.0, total_est * 0.25), 2)
                record_payment(rid, amt, "Bank Transfer", "Demo advance deposit")
                created["payments"] += 1
        except Exception:
            pass

    # set statuses: past -> checked out (if possible), current -> checked in, future -> reserved
    for rid, ci_dt, co_dt, _stay, _rate in res_ids:
        try:
            if co_dt < today - timedelta(days=1):
                try:
                    update_reservation_status(rid, "Checked In")
                except Exception:
                    pass
                try:
                    update_reservation_status(rid, "Checked Out")
                except Exception:
                    pass
            elif ci_dt <= today <= co_dt:
                try:
                    update_reservation_status(rid, "Checked In")
                except Exception:
                    pass
        except Exception:
            pass

    # expenses spread across categories and recent days
    if cats and n_exp > 0:
        expense_names = ["Electricity bill", "Water supply", "Maintenance materials", "Cleaning supplies", "Staff meal supplies", "Fuel", "Laundry chemicals", "Marketing flyers", "License renewal", "Misc purchase", "Beverage restock", "Repairs"]
        methods = ["Cash", "Card", "Bank Transfer"]
        for i in range(n_exp):
            cat = cats[i % len(cats)]
            amt = round(50 + (i * 37) % 400 + (i * 13), 2)
            try:
                record_operational_expense(_get(cat, "id"), expense_names[i % len(expense_names)], amt, 0.0, f"Demo expense {i+1}", methods[i % len(methods)])
                created["expenses"] += 1
            except Exception:
                pass

    # approximate guests count (unique names created via reservations)
    try:
        created["guests"] = db.connect().execute("SELECT COUNT(*) AS c FROM guests").fetchone()["c"]
    except Exception:
        pass

    _audit("demo_preset_apply", "db", "", f"{name} {json.dumps(created, ensure_ascii=False)}")
    return {"preset": name, "label": preset.get("label", name), "created": created}


# ═══════════════════════════════════════════════════════════════════════════
# SEED / MASTER DATA CONFIG (export-edit-upload for first-time setup)
# ═══════════════════════════════════════════════════════════════════════════

SEED_CONFIG_FORMAT = "pm_seed_config"
SEED_CONFIG_VERSION = 2  # v2 adds inventory barcode/batch fields and activities


def export_seed_config():
    """Export editable master/seed data (rooms, rate plans, inventory, expense categories, meals, activities) to JSON."""
    paths.ensure_data_dirs()
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    conn = db.connect()
    rooms = [dict(r) for r in conn.execute(
        "SELECT room_number, room_type, base_price, housekeeping_status FROM rooms ORDER BY room_number").fetchall()]
    plans = [dict(r) for r in conn.execute(
        "SELECT name, price_modifier FROM rate_plans ORDER BY name").fetchall()]
    inv = [dict(r) for r in conn.execute(
        """SELECT item_name, category, stock_qty, unit, unit_cost, selling_price,
                  low_stock_threshold, overstock_threshold, active,
                  sku, barcode, barcode_type, track_batches, shelf_life_days, default_supplier
           FROM inventory ORDER BY category, item_name""").fetchall()]
    cats = [r[0] for r in conn.execute(
        "SELECT category_name FROM expense_categories ORDER BY category_name").fetchall()]
    meals = []
    for m in conn.execute(
        """SELECT id, name, description, preparation, selling_price, category, active
           FROM meals ORDER BY name""").fetchall():
        ings = [dict(r) for r in conn.execute(
            """SELECT i.item_name AS inventory_item_name, mi.qty_per_sale, mi.note
               FROM meal_ingredients mi JOIN inventory i ON i.id = mi.inventory_item_id
               WHERE mi.meal_id = ? ORDER BY i.item_name""", (m["id"],)).fetchall()]
        meals.append({
            "name": m["name"], "description": m["description"] or "", "preparation": m["preparation"] or "",
            "selling_price": float(m["selling_price"] or 0), "category": m["category"] or "Meals",
            "active": int(m["active"] or 0), "ingredients": ings,
        })
    activities = []
    try:
        activities = [dict(r) for r in conn.execute(
            "SELECT name, description, category, unit_price, active FROM activities ORDER BY name").fetchall()]
    except Exception:
        activities = []
    data = {
        "format": SEED_CONFIG_FORMAT,
        "version": SEED_CONFIG_VERSION,
        "exported_at": datetime.now().strftime(DATETIME_FMT),
        "rooms": rooms,
        "rate_plans": plans,
        "inventory": inv,
        "expense_categories": cats,
        "meals": meals,
        "activities": activities,
    }
    path = (EXPORT_DIR / f"seed_config_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json").resolve()
    if EXPORT_DIR.resolve() not in path.parents:
        raise ValueError("Refusing to write seed config outside export directory.")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    _audit("seed_config_export", "db", "", str(path))
    return str(path)


def import_seed_config(filepath, password, confirm_text):
    """Apply uploaded seed/master config JSON. Intended for first-time setup/customization.

    Guarded: requires super password + confirmation text 'APPLY SEED CONFIG'.
    Refused when operational data exists (reservations/payments/POS/expenses), to avoid
    breaking references. Replaces master data (rooms/rate plans/inventory/categories/meals).
    """
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if str(confirm_text).strip().upper() != "APPLY SEED CONFIG":
        raise ValueError("Confirmation text must be exactly: APPLY SEED CONFIG")
    if has_operational_data():
        raise ValueError("Database has operational data. Seed config import is only allowed before operational data exists (use backups/room management instead).")
    p = Path(filepath)
    if not p.exists():
        raise ValueError(f"File not found: {filepath}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"Invalid JSON: {e}")
    if not isinstance(data, dict):
        raise ValueError("Seed config must be a JSON object.")
    rooms = data.get("rooms") or []
    plans = data.get("rate_plans") or []
    inv = data.get("inventory") or []
    cats = data.get("expense_categories") or []
    meals = data.get("meals") or []
    activities = data.get("activities") or []
    if not isinstance(rooms, list) or not isinstance(plans, list) or not isinstance(inv, list) or not isinstance(cats, list) or not isinstance(meals, list) or not isinstance(activities, list):
        raise ValueError("rooms/rate_plans/inventory/expense_categories/meals/activities must be lists.")

    seen_rooms = set()
    norm_rooms = []
    for r in rooms:
        rn = str((r or {}).get("room_number", "")).strip()
        if not rn:
            raise ValueError("Each room requires room_number.")
        if rn in seen_rooms:
            raise ValueError(f"Duplicate room_number: {rn}")
        seen_rooms.add(rn)
        norm_rooms.append({
            "room_number": rn,
            "room_type": str((r or {}).get("room_type", "Standard") or "Standard").strip(),
            "base_price": float((r or {}).get("base_price", 0) or 0),
            "housekeeping_status": str((r or {}).get("housekeeping_status", "Clean") or "Clean").strip() or "Clean",
        })

    seen_plans = set()
    norm_plans = []
    for pl in plans:
        name = str((pl or {}).get("name", "")).strip()
        if not name:
            raise ValueError("Each rate plan requires name.")
        if name in seen_plans:
            raise ValueError(f"Duplicate rate plan: {name}")
        seen_plans.add(name)
        norm_plans.append({"name": name, "price_modifier": float((pl or {}).get("price_modifier", 0) or 0)})

    seen_items = set()
    norm_inv = []
    for it in inv:
        name = str((it or {}).get("item_name", "")).strip()
        if not name:
            raise ValueError("Each inventory item requires item_name.")
        key = name.lower()
        if key in seen_items:
            raise ValueError(f"Duplicate inventory item_name: {name}")
        seen_items.add(key)
        sld = (it or {}).get("shelf_life_days", None)
        try:
            sld = int(sld) if sld not in (None, "") else None
        except Exception:
            sld = None
        norm_inv.append({
            "item_name": name,
            "category": str((it or {}).get("category", "General") or "General").strip(),
            "stock_qty": float((it or {}).get("stock_qty", 0) or 0),
            "unit": str((it or {}).get("unit", "pcs") or "pcs").strip(),
            "unit_cost": float((it or {}).get("unit_cost", 0) or 0),
            "selling_price": float((it or {}).get("selling_price", 0) or 0),
            "low_stock_threshold": (it or {}).get("low_stock_threshold", None),
            "overstock_threshold": (it or {}).get("overstock_threshold", None),
            "active": 1 if int((it or {}).get("active", 1) or 0) else 0,
            "sku": (str((it or {}).get("sku") or "").strip() or None),
            "barcode": (str((it or {}).get("barcode") or "").strip() or None),
            "barcode_type": str((it or {}).get("barcode_type") or "code128").strip() or "code128",
            "track_batches": 1 if int((it or {}).get("track_batches", 0) or 0) else 0,
            "shelf_life_days": sld,
            "default_supplier": str((it or {}).get("default_supplier") or "").strip(),
        })

    norm_cats = []
    seen_cats = set()
    for c in cats:
        name = str(c).strip()
        if not name:
            continue
        if name in seen_cats:
            continue
        seen_cats.add(name)
        norm_cats.append(name)

    norm_meals = []
    seen_meals = set()
    for m in meals:
        name = str((m or {}).get("name", "")).strip()
        if not name:
            continue
        if name.lower() in seen_meals:
            raise ValueError(f"Duplicate meal name: {name}")
        seen_meals.add(name.lower())
        ings = []
        for ing in ((m or {}).get("ingredients") or []):
            iname = str((ing or {}).get("inventory_item_name") or (ing or {}).get("item_name") or "").strip()
            if not iname:
                continue
            ings.append({
                "inventory_item_name": iname,
                "qty_per_sale": float((ing or {}).get("qty_per_sale", 0) or 0),
                "note": str((ing or {}).get("note", "") or ""),
            })
        norm_meals.append({
            "name": name,
            "description": str((m or {}).get("description", "") or ""),
            "preparation": str((m or {}).get("preparation", "") or ""),
            "selling_price": float((m or {}).get("selling_price", 0) or 0),
            "category": str((m or {}).get("category", "Meals") or "Meals"),
            "active": 1 if int((m or {}).get("active", 1) or 0) else 0,
            "ingredients": ings,
        })

    norm_activities = []
    seen_acts = set()
    for a in activities:
        name = str((a or {}).get("name", "")).strip()
        if not name:
            continue
        if name.lower() in seen_acts:
            raise ValueError(f"Duplicate activity name: {name}")
        seen_acts.add(name.lower())
        norm_activities.append({
            "name": name,
            "description": str((a or {}).get("description", "") or ""),
            "category": str((a or {}).get("category", "Trip") or "Trip"),
            "unit_price": float((a or {}).get("unit_price", 0) or 0),
            "active": 1 if int((a or {}).get("active", 1) or 0) else 0,
        })

    from ..db import transaction as db_transaction
    try:
        with db_transaction() as conn:
            conn.execute("DELETE FROM meal_ingredients")
            conn.execute("DELETE FROM meals")
            conn.execute("DELETE FROM recipe_components")
            conn.execute("DELETE FROM inventory_logs")
            conn.execute("DELETE FROM inventory_batches")
            conn.execute("DELETE FROM inventory")
            conn.execute("DELETE FROM expense_categories")
            conn.execute("DELETE FROM rate_plans")
            conn.execute("DELETE FROM rooms")
            try:
                conn.execute("DELETE FROM activities")
            except Exception:
                pass

            for r in norm_rooms:
                conn.execute(
                    "INSERT INTO rooms (room_number, room_type, base_price, housekeeping_status) VALUES (?, ?, ?, ?)",
                    (r["room_number"], r["room_type"], r["base_price"], r["housekeeping_status"]))
            for pl in norm_plans:
                conn.execute("INSERT INTO rate_plans (name, price_modifier) VALUES (?, ?)", (pl["name"], pl["price_modifier"]))
            for it in norm_inv:
                conn.execute(
                    """INSERT INTO inventory (item_name, category, stock_qty, unit, unit_cost, selling_price, low_stock_threshold, overstock_threshold, active,
                                             sku, barcode, barcode_type, track_batches, shelf_life_days, default_supplier)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (it["item_name"], it["category"], it["stock_qty"], it["unit"], it["unit_cost"], it["selling_price"],
                     it["low_stock_threshold"], it["overstock_threshold"], it["active"],
                     it.get("sku"), it.get("barcode"), it.get("barcode_type", "code128"), int(it.get("track_batches", 0) or 0),
                     it.get("shelf_life_days"), it.get("default_supplier", "")))
                item_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                qty = float(it.get("stock_qty") or 0)
                if abs(qty) > 1e-9:
                    lot = f"SEED-{item_id}"
                    exp = None
                    sld = it.get("shelf_life_days")
                    if sld and int(sld) > 0:
                        exp = (datetime.now() + timedelta(days=int(sld))).strftime(DATE_FMT)
                    now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    conn.execute("""INSERT INTO inventory_batches
                        (item_id, lot_code, barcode_value, barcode_type, quantity_received, quantity_remaining,
                         unit_cost, received_at, expiry_date, supplier, reference, notes, status, payload_json)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (item_id, lot, lot, it.get("barcode_type", "code128"), qty, qty, float(it.get("unit_cost") or 0),
                         now_s, exp, it.get("default_supplier", ""), "seed_import", "Seed/import opening stock", "active", "{}"))
                    bid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    try:
                        payload = json.dumps({"v": 1, "t": "lot", "item": item_id, "name": it["item_name"], "lot": lot, "recv": now_s, "exp": exp or "", "qty": qty, "rem": qty, "cost": float(it.get("unit_cost") or 0)}, separators=(",", ":"), ensure_ascii=False)
                        conn.execute("UPDATE inventory_batches SET payload_json=? WHERE id=?", (payload, bid))
                    except Exception:
                        pass
                    conn.execute("""INSERT INTO inventory_logs (item_id, change_type, quantity, reference, batch_id, unit_cost, balance_after)
                                   VALUES (?, 'OPENING', ?, 'seed import opening batch', ?, ?, ?)""",
                                (item_id, qty, bid, float(it.get("unit_cost") or 0), qty))
            for c in norm_cats:
                conn.execute("INSERT OR IGNORE INTO expense_categories (category_name) VALUES (?)", (c,))
            for a in norm_activities:
                try:
                    conn.execute("""INSERT OR IGNORE INTO activities (name, description, category, unit_price, active, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, datetime('now','localtime'), datetime('now','localtime'))""",
                        (a["name"], a["description"], a["category"], float(a["unit_price"] or 0), int(a["active"] or 0)))
                except Exception:
                    pass

            inv_name_to_id = {r["item_name"].strip().lower(): r["id"] for r in conn.execute("SELECT id, item_name FROM inventory").fetchall()}
            for m in norm_meals:
                cur = conn.execute(
                    """INSERT INTO meals (name, description, preparation, selling_price, category, active, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'), datetime('now','localtime'))""",
                    (m["name"], m["description"], m["preparation"], m["selling_price"], m["category"], m["active"]))
                meal_id = cur.lastrowid
                for ing in m["ingredients"]:
                    iid = inv_name_to_id.get(ing["inventory_item_name"].strip().lower())
                    if iid is None:
                        raise ValueError(f"Meal '{m['name']}' ingredient not found in inventory: {ing['inventory_item_name']}")
                    conn.execute(
                        "INSERT OR REPLACE INTO meal_ingredients (meal_id, inventory_item_id, qty_per_sale, note) VALUES (?, ?, ?, ?)",
                        (meal_id, iid, float(ing["qty_per_sale"] or 0), ing["note"] or ""))
    except Exception:
        raise

    try:
        conn2 = db.connect()
        for row in conn2.execute("SELECT id, name, category, selling_price FROM meals").fetchall():
            ings = [dict(r) for r in conn2.execute(
                "SELECT inventory_item_id, qty_per_sale, note FROM meal_ingredients WHERE meal_id=?",
                (row["id"],)).fetchall()]
            with transaction() as c2:
                _sync_meal_to_inventory(c2, row["id"], row["name"], row["category"] or "Meals", float(row["selling_price"] or 0), ings)
    except Exception:
        pass

    summary = {"rooms": len(norm_rooms), "rate_plans": len(norm_plans), "inventory": len(norm_inv),
               "expense_categories": len(norm_cats), "meals": len(norm_meals), "activities": len(norm_activities)}
    _audit("seed_config_import", "db", "", json.dumps(summary, ensure_ascii=False))
    return {"ok": True, "summary": summary}


# ═══════════════════════════════════════════════════════════════════════════
# ROOMS (master data management)
# ═══════════════════════════════════════════════════════════════════════════

def add_room(room_number, room_type, base_price, housekeeping_status="Clean"):
    rn = str(room_number or "").strip()
    rt = str(room_type or "").strip()
    if not rn:
        raise ValueError("Room number is required.")
    if not rt:
        raise ValueError("Room type is required.")
    try:
        price = float(base_price)
    except Exception:
        raise ValueError("Base price must be a number.")
    if price < 0:
        raise ValueError("Base price cannot be negative.")
    status = str(housekeeping_status or "Clean").strip() or "Clean"
    from ..db import transaction
    try:
        with transaction() as conn:
            cur = conn.execute(
                "INSERT INTO rooms (room_number, room_type, base_price, housekeeping_status) VALUES (?, ?, ?, ?)",
                (rn, rt, price, status))
            rid = cur.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError(f"Room number already exists: {rn}")
    _audit("room_add", "room", str(rid), rn)
    return rid


def delete_room(room_id):
    rid = int(room_id)
    row = db.connect().execute("SELECT id, room_number FROM rooms WHERE id=?", (rid,)).fetchone()
    if not row:
        raise ValueError("Room not found.")
    ref = db.connect().execute("SELECT 1 FROM reservations WHERE room_id=? LIMIT 1", (rid,)).fetchone()
    if ref:
        raise ValueError("Cannot delete room: it is referenced by reservations.")
    from ..db import transaction
    try:
        with transaction() as conn:
            conn.execute("DELETE FROM rooms WHERE id=?", (rid,))
    except sqlite3.IntegrityError:
        raise ValueError("Cannot delete room: it is referenced by other records.")
    _audit("room_delete", "room", str(rid), row["room_number"])
    return True


# ═══════════════════════════════════════════════════════════════════════════
# UI HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def fmt(x, unit=CURRENCY):
    return f"{x:,.2f} {unit}"


def selected_id(table):
    if table.row_count == 0:
        return None
    try:
        return int(table.get_row_at(table.cursor_row)[0])
    except (ValueError, TypeError, IndexError):
        return None


def safe_action(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(self, event):
        try:
            fn(self, event)
        except PermissionError:
            self.app.notify(t("Wrong super password."), severity="error")
        except Exception as e:
            self.app.notify(str(e), severity="error")
    return wrapper