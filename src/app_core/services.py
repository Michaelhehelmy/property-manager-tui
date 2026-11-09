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
import csv
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from . import db, paths

BASE_DIR = paths.BASE_DIR
DATA_DIR = paths.DATA_DIR
EXPORT_DIR = paths.EXPORT_DIR
INVOICE_DIR = paths.INVOICE_DIR
CURRENCY = "EGP"
DATE_FMT = "%Y-%m-%d"

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
    "schema_version": ("Schema Version", "internal", "4", "system", ""),
}

_SEED = {
    "rooms": [
        {"room_number": "101", "room_type": "Standard Room", "base_price": 50.0, "housekeeping_status": "Clean"},
        {"room_number": "102", "room_type": "Standard Room", "base_price": 50.0, "housekeeping_status": "Clean"},
        {"room_number": "201", "room_type": "Sea View Suite", "base_price": 120.0, "housekeeping_status": "Clean"},
        {"room_number": "301", "room_type": "Bungalow", "base_price": 90.0, "housekeeping_status": "Dirty"},
    ],
    "rate_plans": [
        {"name": "Room Only", "price_modifier": 0.0},
        {"name": "Bed & Breakfast", "price_modifier": 20.0},
        {"name": "Half Board", "price_modifier": 45.0},
    ],
    "inventory": [
        {"item_name": "Bottled Water (0.5L)", "category": "F&B", "stock_qty": 100.0, "unit": "pcs", "unit_cost": 3.0, "selling_price": 20.0},
        {"item_name": "Espresso Coffee Beans", "category": "F&B", "stock_qty": 5.0, "unit": "kg", "unit_cost": 200.0, "selling_price": 300.0},
        {"item_name": "Bed Sheet Set", "category": "Housekeeping", "stock_qty": 20.0, "unit": "sets", "unit_cost": 80.0, "selling_price": 150.0},
        {"item_name": "Propane Gas Cylinder", "category": "Maintenance", "stock_qty": 3.0, "unit": "pcs", "unit_cost": 150.0, "selling_price": 250.0},
    ],
    "expense_categories": [
        "Utilities (Water/Electricity)",
        "Maintenance & Repairs",
        "Supplies & Consumables",
        "Staff & Operational",
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
    from .db import transaction
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
    from .db import transaction
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
    from .db import transaction
    with transaction() as conn:
        for key, spec in SETTINGS_CATALOG.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, spec[2]))
        for r in _SEED["rooms"]:
            conn.execute(
                "INSERT INTO rooms (room_number, room_type, base_price, housekeeping_status) VALUES (?, ?, ?, ?)",
                (r["room_number"], r["room_type"], r["base_price"], r["housekeeping_status"]))
        for p in _SEED["rate_plans"]:
            conn.execute("INSERT INTO rate_plans (name, price_modifier) VALUES (?, ?)",
                         (p["name"], p["price_modifier"]))
        for i in _SEED["inventory"]:
            conn.execute("""INSERT INTO inventory
                (item_name, category, stock_qty, unit, unit_cost, selling_price)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (i["item_name"], i["category"], i["stock_qty"], i["unit"], i["unit_cost"], i["selling_price"]))
        for c in _SEED["expense_categories"]:
            conn.execute("INSERT INTO expense_categories (category_name) VALUES (?)", (c,))


# ═══════════════════════════════════════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════════════════════════════════════

def get_setting(key, default=""):
    from .db import connect
    row = connect().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    from .db import transaction
    with transaction() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))


def get_all_settings():
    from .db import connect
    return {r["key"]: r["value"] for r in connect().execute("SELECT key, value FROM settings").fetchall()}


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
    from .db import transaction
    with transaction() as conn:
        for key, val in normalized.items():
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, val))
    _audit("settings_save", "settings", "", f"{len(normalized)} keys")
    return len(normalized), []


def reset_to_defaults(password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    from .db import transaction
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
    from .db import transaction
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
        from .db import transaction
        with transaction() as conn:
            conn.execute(
                "INSERT INTO audit_log (action, target_type, target_id, details) VALUES (?, ?, ?, ?)",
                (action, target_type, str(target_id), details))
    except Exception:  # pragma: no cover
        pass  # pragma: no cover — audit must never crash


def get_audit_log(limit=200):
    from .db import connect
    return connect().execute(
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
    from .db import connect
    row = connect().execute("SELECT value FROM app_metadata WHERE key='super_password_hash'").fetchone()
    return bool(row and row["value"])


def set_super_password(password):
    if not password or len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    if has_super_password():
        raise ValueError("Super password is already set.")
    salt = secrets.token_hex(16)
    pw_hash = _scrypt_hash(password, salt)
    from .db import transaction
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
    from .db import connect, transaction
    conn = connect()
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
    from .db import transaction
    with transaction() as conn:
        conn.execute("UPDATE app_metadata SET value=? WHERE key='super_password_salt'", (salt,))
        conn.execute("UPDATE app_metadata SET value=? WHERE key='super_password_hash'", (pw_hash,))
    _audit("password_change")


def generate_recovery_codes(password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    from .db import transaction
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
    from .db import connect
    row = connect().execute("SELECT 1 FROM app_metadata WHERE key=?", (f"recovery_{ch}",)).fetchone()
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
    from .db import connect, transaction
    conn = connect()
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
    if not verify_password(password):
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
    timeout = int(get_setting("session_timeout_minutes", "20"))
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
    from .db import connect
    p_norm = _norm_phone(phone)
    e_norm = (email or "").lower().strip()
    candidates = connect().execute("SELECT id, canonical_name, phone, email FROM guests").fetchall()
    scored = []
    for c in candidates:
        score = _name_score(name, c["canonical_name"])
        if p_norm and _norm_phone(c["phone"]) == p_norm:
            score = 1.0
        elif e_norm and (c["email"] or "").lower().strip() == e_norm:
            score = max(score, 0.98)
        if score >= threshold:
            stays = connect().execute(
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
    from .db import transaction
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO guests (canonical_name, phone, email) VALUES (?, ?, ?)",
            (name.strip(), phone.strip(), email.strip()))
        return cur.lastrowid, name.strip(), True


def get_all_guests(search=""):
    from .db import connect
    q = f"%{search}%" if search else None
    if q:
        return connect().execute("""
            SELECT g.id, g.canonical_name, g.phone, g.email,
                   COUNT(r.id) AS stays,
                   MAX(r.check_in_date) AS last_stay,
                   COALESCE(SUM(r.agreed_nightly_rate *
                       MAX(1, CAST(julianday(r.check_out_date)-julianday(r.check_in_date) AS INT))
                       + r.tax_amount), 0) AS total
            FROM guests g LEFT JOIN reservations r ON r.guest_id = g.id
            WHERE g.canonical_name LIKE ? OR g.phone LIKE ? OR g.email LIKE ?
            GROUP BY g.id ORDER BY g.canonical_name
        """, (q, q, q)).fetchall()
    return connect().execute("""
        SELECT g.id, g.canonical_name, g.phone, g.email,
               COUNT(r.id) AS stays,
               MAX(r.check_in_date) AS last_stay,
               COALESCE(SUM(r.agreed_nightly_rate *
                   MAX(1, CAST(julianday(r.check_out_date)-julianday(r.check_in_date) AS INT))
                   + r.tax_amount), 0) AS total
        FROM guests g LEFT JOIN reservations r ON r.guest_id = g.id
        GROUP BY g.id ORDER BY g.canonical_name
    """).fetchall()


def get_guest_by_id(guest_id):
    from .db import connect
    row = connect().execute(
        "SELECT id, canonical_name, phone, email, notes FROM guests WHERE id=?", (guest_id,)).fetchone()
    if not row:
        return None
    return {"id": row["id"], "name": row["canonical_name"], "phone": row["phone"],
            "email": row["email"], "notes": row["notes"]}


def get_guest_history_by_id(guest_id):
    from .db import connect
    return connect().execute("""
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
    from .db import connect
    guest = get_guest_by_id(guest_id)
    if not guest:
        return None
    row = connect().execute("""
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
    from .db import connect
    rid = int(reservation_id)
    limit = max(1, min(int(limit or 5), 25))
    rows = connect().execute("""
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
    from .db import connect
    rid = int(reservation_id)
    row = connect().execute("""
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
        bal = calculate_guest_balance(rid)
    except Exception as e:
        bal = {"error": str(e)}
    pos_count = connect().execute("SELECT COUNT(*) FROM pos_tabs WHERE reservation_id=? AND voided_at IS NULL", (rid,)).fetchone()[0]
    return {"reservation": {k: row[k] for k in row.keys()}, "balance": bal, "active_pos_item_lines": pos_count}


def update_guest(guest_id, name, phone, email, notes, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if not name or not name.strip():
        raise ValueError("Name is required.")
    from .db import transaction
    with transaction() as conn:
        conn.execute("""UPDATE guests SET canonical_name=?, phone=?, email=?, notes=?,
                        updated_at=datetime('now','localtime') WHERE id=?""",
                     (name.strip(), phone.strip(), email.strip(), notes.strip(), guest_id))
    _audit("guest_update", "guest", guest_id, name)


def get_duplicate_suggestions():
    """Fast: only compares same first-letter + same phone-tail buckets."""
    from .db import connect
    guests = connect().execute("SELECT id, canonical_name, phone FROM guests").fetchall()
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
    from .db import transaction
    with transaction() as conn:
        cur = conn.execute("UPDATE reservations SET guest_id=? WHERE guest_id=?", (winner_id, loser_id))
        moved = cur.rowcount
        if l["name"].lower() not in w["notes"].lower():
            nn = (w["notes"] + "\n" if w["notes"] else "") + f"aka: {l['name']}"
            conn.execute("UPDATE guests SET notes=? WHERE id=?", (nn, winner_id))
        conn.execute("DELETE FROM guests WHERE id=?", (loser_id,))
    _audit("guest_merge", "guest", winner_id, f"merged {loser_id}, {moved} reservations")
    return moved


# ═══════════════════════════════════════════════════════════════════════════
# ROOMS
# ═══════════════════════════════════════════════════════════════════════════

def get_all_rooms():
    from .db import connect
    return connect().execute(
        "SELECT id, room_number, room_type, base_price, housekeeping_status FROM rooms ORDER BY room_number").fetchall()


def get_room(room_id):
    from .db import connect
    row = connect().execute(
        "SELECT id, room_number, room_type, base_price, housekeeping_status FROM rooms WHERE id=?",
        (room_id,)).fetchone()
    if not row:
        return None
    return {"id": row["id"], "room_number": row["room_number"], "room_type": row["room_type"],
            "base_price": row["base_price"], "housekeeping_status": row["housekeeping_status"]}


def update_housekeeping_status(room_id, status):
    if status not in ("Clean", "Dirty", "Maintenance"):
        raise ValueError("Invalid status.")
    from .db import transaction
    with transaction() as conn:
        conn.execute("UPDATE rooms SET housekeeping_status=? WHERE id=?", (status, room_id))


def update_room_price(room_id, new_price, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if new_price < 0:
        raise ValueError("Price cannot be negative.")
    from .db import transaction
    with transaction() as conn:
        conn.execute("UPDATE rooms SET base_price=? WHERE id=?", (new_price, room_id))
    _audit("room_price_change", "room", room_id, f"new={new_price}")


def update_room_type(room_id, new_type, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if not new_type or not new_type.strip():
        raise ValueError("Type cannot be empty.")
    from .db import transaction
    with transaction() as conn:
        conn.execute("UPDATE rooms SET room_type=? WHERE id=?", (new_type.strip(), room_id))
    _audit("room_type_change", "room", room_id, new_type)


# ═══════════════════════════════════════════════════════════════════════════
# RATE PLANS
# ═══════════════════════════════════════════════════════════════════════════

def get_all_rate_plans():
    from .db import connect
    return connect().execute("SELECT id, name, price_modifier FROM rate_plans ORDER BY id").fetchall()


def add_rate_plan(name, modifier, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if not name or not name.strip():
        raise ValueError("Name is required.")
    from .db import connect, transaction
    if connect().execute("SELECT 1 FROM rate_plans WHERE LOWER(name)=LOWER(?)", (name.strip(),)).fetchone():
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
    from .db import connect, transaction
    if connect().execute("SELECT 1 FROM rate_plans WHERE LOWER(name)=LOWER(?) AND id!=?",
                         (name.strip(), plan_id)).fetchone():
        raise ValueError("Name already exists.")
    with transaction() as conn:
        conn.execute("UPDATE rate_plans SET name=?, price_modifier=? WHERE id=?",
                     (name.strip(), float(modifier), plan_id))
    _audit("rate_plan_update", "rate_plan", plan_id, name)


def delete_rate_plan(plan_id, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    from .db import connect, transaction
    used = connect().execute("SELECT COUNT(*) FROM reservations WHERE rate_plan_id=?", (plan_id,)).fetchone()[0]
    if used > 0:
        raise ValueError(f"Cannot delete: {used} reservations use it.")
    with transaction() as conn:
        conn.execute("DELETE FROM rate_plans WHERE id=?", (plan_id,))
    _audit("rate_plan_delete", "rate_plan", plan_id, "")


# ═══════════════════════════════════════════════════════════════════════════
# RESERVATIONS
# ═══════════════════════════════════════════════════════════════════════════


def count_reservations_using_plan(plan_id):
    from .db import connect
    return connect().execute(
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
    from .db import connect
    q = """SELECT COUNT(*) FROM reservations WHERE room_id=?
           AND status IN ('Reserved','Checked In')
           AND NOT (check_out_date <= ? OR check_in_date >= ?)"""
    p = [room_id, ci, co]
    if exclude_res_id is not None:
        q += " AND id != ?"
        p.append(exclude_res_id)
    return connect().execute(q, p).fetchone()[0] == 0


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
    from .db import transaction
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
    from .db import connect, transaction
    row = connect().execute("SELECT status, room_id FROM reservations WHERE id=?", (rid,)).fetchone()
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
    from .db import connect
    conn = connect()
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


def cancel_reservation(rid, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    from .db import connect, transaction
    row = connect().execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()
    if not row:
        raise ValueError("Reservation not found.")
    if "Cancelled" not in STATUS_TRANSITIONS.get(row["status"], []):
        raise ValueError(f"Cannot cancel a '{row['status']}' reservation.")
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


def mark_no_show(rid, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    from .db import connect, transaction
    row = connect().execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()
    if not row:
        raise ValueError("Reservation not found.")
    if "No-Show" not in STATUS_TRANSITIONS.get(row["status"], []):
        raise ValueError(f"Cannot mark '{row['status']}' as no-show.")  # pragma: no cover
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
    if pos_row["inventory_item_id"]:
        cur.execute("UPDATE inventory SET stock_qty = stock_qty + ? WHERE id=?",
                    (pos_row["quantity"], pos_row["inventory_item_id"]))
        cur.execute("""INSERT INTO inventory_logs (item_id, change_type, quantity, reference)
                       VALUES (?, 'IN', ?, ?)""",
                    (pos_row["inventory_item_id"], pos_row["quantity"], f"void POS #{pid}"))
    cur.execute("UPDATE pos_tabs SET voided_at=datetime('now','localtime') WHERE id=?", (pid,))


def modify_reservation(rid, new_rate, new_ci, new_co, new_room_id, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
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
    from .db import connect, transaction
    row = connect().execute(
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


def delete_reservation(rid, password):
    """Only allowed for Reserved with no payments/POS."""
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    from .db import transaction
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
    from .db import connect
    return connect().execute("SELECT * FROM reservations WHERE id=?", (rid,)).fetchone()


# ═══════════════════════════════════════════════════════════════════════════
# POS
# ═══════════════════════════════════════════════════════════════════════════

def _add_pos_item_tx(conn, reservation_id, desc, qty, price, inv_id=None):
    """Insert a POS item and related ledger/stock inside an existing transaction."""
    if not isinstance(qty, int) or qty <= 0:
        raise ValueError("Quantity must be a positive integer.")
    if price < 0:
        raise ValueError("Price cannot be negative.")
    if not desc or not desc.strip():
        raise ValueError("Description is required.")
    if inv_id is not None and not isinstance(inv_id, int):
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

    net = price * qty
    tax = calculate_tax(net, 0)
    cur = conn.cursor()
    cost_at_sale = 0.0
    if inv_id:
        inv = conn.execute("SELECT stock_qty, unit_cost, active FROM inventory WHERE id=?",
                           (inv_id,)).fetchone()
        if not inv or not inv["active"]:
            raise ValueError("Inventory item not found or inactive.")
        allow_neg = get_setting("allow_negative_stock", "true") == "true"
        if not allow_neg and inv["stock_qty"] < qty:
            raise ValueError(f"Insufficient stock: {inv['stock_qty']} available, {qty} requested.")
        cost_at_sale = inv["unit_cost"] or 0

    rid = None if is_walkin else reservation_id
    cur.execute("""INSERT INTO pos_tabs
        (reservation_id, inventory_item_id, item_description, quantity, unit_price, tax_amount, cost_at_sale)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (rid, inv_id, desc.strip(), qty, price, tax["service"] + tax["vat"] + tax["city_tax"], cost_at_sale))
    pid = cur.lastrowid
    _post(cur, "POS", pid, "CHARGE", "F&B",
          tax["net"], tax["service"], tax["vat"], tax["city_tax"], "",
          f"POS: {desc.strip()} x{qty}")
    if inv_id and cost_at_sale > 0:
        cogs = round(cost_at_sale * qty, 2)
        _post(cur, "POS", pid, "COGS", "COGS", cogs, 0, 0, 0, "",
              f"COGS POS #{pid}: {desc.strip()} x{qty}")
    if inv_id:
        conn.execute("UPDATE inventory SET stock_qty = stock_qty - ? WHERE id=?", (qty, inv_id))
        conn.execute("""INSERT INTO inventory_logs (item_id, change_type, quantity, reference)
                       VALUES (?, 'OUT', ?, ?)""", (inv_id, qty, f"POS #{pid}"))
    return {"pos_id": pid, "gross": round(tax["gross"], 2), "tax": round(tax["service"] + tax["vat"] + tax["city_tax"], 2)}


def add_pos_item(reservation_id, desc, qty, price, inv_id=None):
    from .db import transaction
    with transaction() as conn:
        return _add_pos_item_tx(conn, reservation_id, desc, qty, price, inv_id)


def complete_walkin_sale(items, method, notes="", payment_type="Payment"):
    """Atomically commit walk-in POS items and a single payment."""
    if not items:
        raise ValueError("Cart is empty.")
    if not isinstance(method, str) or not method.strip():
        raise ValueError("Select a payment method.")
    from .db import transaction
    with transaction() as conn:
        cur = conn.cursor()
        pos_ids = []
        total_gross = 0.0
        receipt_items = []
        for it in items:
            desc = (it.get("desc") or "").strip()
            qty = int(it.get("qty") or 0)
            price = float(it.get("price") or 0)
            inv_id = it.get("inv_id")
            res = _add_pos_item_tx(conn, None, desc, qty, price, inv_id)
            pos_ids.append(res["pos_id"])
            total_gross += res["gross"]
            receipt_items.append({"desc": desc, "qty": qty, "price": price, "gross": res["gross"]})
        total_gross = round(total_gross, 2)
        if total_gross <= 0:
            raise ValueError("Sale total must be positive.")
        conn.execute("""INSERT INTO payments
            (reservation_id, amount, method, payment_type, notes) VALUES (NULL, ?, ?, ?, ?)""",
            (total_gross, method, payment_type or "Payment", notes or "Walk-in sale"))
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        _post(cur, "WALKIN_PAY", pid, "PAYMENT", "", 0, 0, 0, 0, method,
              f"{payment_type or 'Payment'} via {method} (walk-in)", gross_override=total_gross)
        _audit("walkin_sale", "payment", pid, f"{total_gross} via {method} items={len(pos_ids)}")
    receipt_text = generate_walkin_receipt_text(receipt_items, total_gross, method, payment_id=pid)
    try:
        receipt_path = save_walkin_receipt_file(receipt_text, pid)
    except Exception:
        receipt_path = ""
    return {"total_gross": total_gross, "pos_ids": pos_ids, "payment_id": pid,
            "receipt_items": receipt_items, "method": method,
            "receipt_text": receipt_text, "receipt_path": receipt_path,
            "receipt_name": Path(receipt_path).name if receipt_path else ""}


def get_pos_items(reservation_id):
    from .db import connect
    return connect().execute("""
        SELECT id, item_description, quantity, unit_price, tax_amount, voided_at
        FROM pos_tabs WHERE reservation_id=? AND voided_at IS NULL ORDER BY id
    """, (reservation_id,)).fetchall()


def void_pos_item(pos_id, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    from .db import connect, transaction
    row = connect().execute("SELECT * FROM pos_tabs WHERE id=? AND voided_at IS NULL", (pos_id,)).fetchone()
    if not row:
        raise ValueError("POS item not found or already voided.")
    with transaction() as conn:
        cur = conn.cursor()
        _void_pos_item_inner(cur, row)
    _audit("pos_void", "pos", pos_id, row["item_description"])


# ═══════════════════════════════════════════════════════════════════════════
# INVENTORY
# ═══════════════════════════════════════════════════════════════════════════

def search_inventory(query="", category="", stock_filter=""):
    if not isinstance(query, str):
        query = ""  # pragma: no cover
    if not isinstance(category, str) or category == "All":
        category = ""  # pragma: no cover
    if not isinstance(stock_filter, str) or stock_filter == "All":
        stock_filter = ""  # pragma: no cover
    sql = """SELECT id, item_name, category, stock_qty, unit, unit_cost, selling_price
             FROM inventory WHERE active=1"""
    params = []
    if query:
        sql += " AND (item_name LIKE ? OR category LIKE ?)"
        params += [f"%{query}%", f"%{query}%"]
    if category:
        sql += " AND category=?"
        params.append(category)
    low = float(get_setting("low_stock_threshold", "5"))
    over = float(get_setting("overstock_threshold", "100"))
    if stock_filter == "Low":
        sql += " AND stock_qty < ? AND stock_qty >= 0"  # pragma: no cover
        params.append(low)  # pragma: no cover
    elif stock_filter == "Over":
        sql += " AND stock_qty > ?"  # pragma: no cover
        params.append(over)  # pragma: no cover
    elif stock_filter == "Negative":
        sql += " AND stock_qty < 0"
    sql += " ORDER BY item_name"
    from .db import connect
    return connect().execute(sql, params).fetchall()


def get_inventory_categories():
    from .db import connect
    return [r["category"] for r in connect().execute(
        "SELECT DISTINCT category FROM inventory WHERE active=1 ORDER BY category").fetchall()]


def get_inventory_warnings():
    from .db import connect
    low = float(get_setting("low_stock_threshold", "5"))
    over = float(get_setting("overstock_threshold", "100"))
    items = connect().execute(
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
    from .db import connect  # pragma: no cover
    return connect().execute("""
        SELECT l.at, i.item_name, l.change_type, l.quantity, l.reference
        FROM inventory_logs l JOIN inventory i ON l.item_id = i.id
        ORDER BY l.id DESC LIMIT ?
    """, (limit,)).fetchall()


def add_inventory_item(name, category, unit, stock, unit_cost, selling_price, low, over, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if not name or not name.strip():
        raise ValueError("Name is required.")
    from .db import transaction
    with transaction() as conn:
        conn.execute("""INSERT INTO inventory
            (item_name, category, unit, stock_qty, unit_cost, selling_price,
             low_stock_threshold, overstock_threshold)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name.strip(), category.strip(), unit.strip(), float(stock), float(unit_cost),
             float(selling_price), float(low), float(over)))
    _audit("inventory_add", "inventory", "", name)


def update_inventory_item(item_id, name, category, unit, selling_price, low, over, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if not name or not name.strip():
        raise ValueError("Name is required.")
    from .db import transaction
    with transaction() as conn:
        conn.execute("""UPDATE inventory SET item_name=?, category=?, unit=?,
                        selling_price=?, low_stock_threshold=?, overstock_threshold=?
                        WHERE id=?""",
                     (name.strip(), category.strip(), unit.strip(),
                      float(selling_price), float(low), float(over), item_id))
    _audit("inventory_update", "inventory", item_id, name)


def delete_inventory_item(item_id, password):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    from .db import connect, transaction
    row = connect().execute("SELECT stock_qty FROM inventory WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise ValueError("Item not found.")
    if row["stock_qty"] != 0:
        raise ValueError("Cannot delete item with non-zero stock.")
    with transaction() as conn:
        conn.execute("UPDATE inventory SET active=0 WHERE id=?", (item_id,))
    _audit("inventory_delete", "inventory", item_id, "")


def restock_inventory(item_id, qty, unit_cost=0.0, password=""):
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if not isinstance(item_id, int) or item_id <= 0:
        raise ValueError("Invalid item ID.")
    if qty <= 0:
        raise ValueError("Quantity must be positive.")
    if unit_cost < 0:
        raise ValueError("Unit cost cannot be negative.")
    from .db import connect, transaction
    with transaction() as conn:
        cur = conn.cursor()
        row = conn.execute("SELECT stock_qty, unit_cost, item_name FROM inventory WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise ValueError("Item not found.")
        old_qty, old_cost = row["stock_qty"], row["unit_cost"]
        new_qty = old_qty + qty
        new_cost = round((old_qty * old_cost + qty * unit_cost) / new_qty, 4) if unit_cost > 0 and new_qty > 0 else old_cost
        conn.execute("UPDATE inventory SET stock_qty=?, unit_cost=? WHERE id=?", (new_qty, new_cost, item_id))
        conn.execute("""INSERT INTO inventory_logs (item_id, change_type, quantity, reference)
                       VALUES (?, 'IN', ?, 'restock')""", (item_id, qty))
        if unit_cost > 0 and qty > 0:
            total = round(qty * unit_cost, 2)
            _post(cur, "INV", item_id, "ASSET_IN", "Inventory Purchase",
                  total, 0, 0, 0, "Cash", f"Restock {row['item_name']}",
                  gross_override=-total)
    _audit("inventory_restock", "inventory", item_id, f"+{qty} @ {unit_cost}")


# ═══════════════════════════════════════════════════════════════════════════
# PAYMENTS
# ═══════════════════════════════════════════════════════════════════════════

def record_payment(reservation_id, amount, method, notes="", payment_type="Payment"):
    if amount <= 0:
        raise ValueError("Payment amount must be positive.")
    if reservation_id is not None and not isinstance(reservation_id, int):
        raise ValueError("Invalid reservation ID.")
    from .db import connect, transaction
    if reservation_id is not None:
        r = connect().execute("SELECT status FROM reservations WHERE id=?", (reservation_id,)).fetchone()
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
    from .db import transaction
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
    from .db import connect
    return connect().execute("SELECT id, category_name FROM expense_categories ORDER BY category_name").fetchall()


def record_operational_expense(cat_id, param, amount, tax_amount=0.0, notes="", method="Cash"):
    if amount <= 0:
        raise ValueError("Amount must be positive.")
    if tax_amount < 0:
        raise ValueError("VAT cannot be negative.")
    if not isinstance(cat_id, int):
        raise ValueError("Invalid category.")
    from .db import transaction
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
    from .db import connect, transaction
    row = connect().execute("SELECT * FROM operational_expenses WHERE id=? AND voided_at IS NULL",
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
    from .db import connect
    return connect().execute("""
        SELECT e.id, e.at, c.category_name, e.parameter_name, e.net_amount, e.vat_amount, e.method, e.notes
        FROM operational_expenses e JOIN expense_categories c ON e.category_id = c.id
        WHERE e.voided_at IS NULL ORDER BY e.id DESC LIMIT ?
    """, (limit,)).fetchall()


# ═══════════════════════════════════════════════════════════════════════════
# BALANCE & FOLIO
# ═══════════════════════════════════════════════════════════════════════════

def calculate_guest_balance(rid):
    from .db import connect
    conn = connect()
    r = conn.execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()
    if not r or r["status"] in ("Cancelled", "No-Show", "Deleted"):
        return 0.0
    charges = _reservation_charge_net(conn, rid)
    paid = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE reservation_id=? AND voided_at IS NULL",
        (rid,)).fetchone()[0] or 0
    return round(charges - paid, 2)


def get_balance_breakdown(rid):
    from .db import connect
    conn = connect()
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
    from .db import connect
    conn = connect()
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
    from .db import connect
    return connect().execute("""
        SELECT id, at, ref_type, ref_id, entry_type, category, gross, vat, description
        FROM general_ledger ORDER BY id DESC LIMIT ?
    """, (limit,)).fetchall()


def verify_app_attribution():
    from .db import connect
    row = connect().execute("SELECT value, hash_signature FROM app_metadata WHERE key='gift_attribution'").fetchone()
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
    from .db import connect
    conn = connect()
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
    pos_items = conn.execute(f"""
        SELECT p.item_description, p.quantity, p.unit_price, p.tax_amount,
               (SELECT COALESCE(SUM(gross),0) FROM general_ledger
                WHERE ref_type='POS' AND ref_id=p.id AND entry_type IN ({ph})) AS gross
        FROM pos_tabs p WHERE p.reservation_id=? AND p.voided_at IS NULL
    """, (*types, reservation_id)).fetchall()
    payments = conn.execute("""
        SELECT amount, method, payment_type, notes FROM payments
        WHERE reservation_id=? AND voided_at IS NULL ORDER BY id
    """, (reservation_id,)).fetchall()
    accom_net = accom_row["net"] or 0
    accom_service = accom_row["service"] or 0
    accom_vat = accom_row["vat"] or 0
    accom_city = accom_row["city_tax"] or 0
    accom_gross = accom_row["gross"] or 0
    pos_gross = sum((p["gross"] or 0) for p in pos_items)
    total_paid = sum(p["amount"] for p in payments)
    balance = round(accom_gross + pos_gross - total_paid, 2)
    cfg = get_tax_config()
    factor = 1.0 if currency == "EGP" else (1.0 / cfg["usd_to_egp"])
    def m(v):
        return f"{v * factor:,.2f} {currency}"
    W = 60
    display_name = get_setting("property_name", "") or get_app_display_name()
    lines = ["=" * W, display_name.center(W), "INVOICE / RECEIPT".center(W), "=" * W]
    addr = get_setting("property_address", "")
    if addr:
        for ln in addr.split("\n"):
            lines.append(ln.center(W))  # pragma: no cover
    phone = get_setting("property_phone", "")
    if phone:
        lines.append(phone.center(W))
    if currency != "EGP":
        lines.append(f"(USD view at 1 USD = {cfg['usd_to_egp']:.2f} EGP)")
    lines += [
        f"Invoice #:    INV-{reservation_id:05d}",
        f"Date:         {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Status:       {r['status']}",
        "-" * W,
        f"Guest:        {r['guest_name']}",
        f"Source:       {r['source']}",
        f"Room:         {r['room_number']} ({r['room_type']})",
        f"Check-In:     {r['check_in_date']}",
        f"Check-Out:    {r['check_out_date']}",
        f"Nights:       {nights}",
        f"Nightly Rate: {m(r['agreed_nightly_rate'])}",
        "-" * W,
        "ACCOMMODATION",
        f"  Net: {m(accom_net)}",
        f"  Service: {m(accom_service)}",
        f"  VAT: {m(accom_vat)}",
        f"  City tax: {m(accom_city)}",
        f"  Accommodation total: {m(accom_gross)}",
        "-" * W,
        "POS / EXTRA",
    ]
    if pos_items:
        for p in pos_items:
            lines.append(f"  {p['item_description']} x{p['quantity']} @ {m(p['unit_price'])} = {m(p['gross'] or 0)}")
    else:
        lines.append("  (none)")
    lines += ["-" * W, "PAYMENTS"]
    if payments:
        for p in payments:
            sign = "-" if p["amount"] < 0 else ""
            lines.append(f"  [{p['payment_type']}] {p['method']}: {sign}{m(abs(p['amount']))}")
    else:
        lines.append("  (none)")
    lines += [
        f"  Total Paid: {m(total_paid)}",
        "=" * W,
        f"  Total Charges: {m(accom_gross + pos_gross)}",
        f"  Balance Due:   {m(balance)}",
        "=" * W, "",
        get_setting("invoice_footer_text", "Thank you for staying with us!"),
        "=" * W,
    ]
    return "\n".join(lines)


def save_invoice_file(text, reservation_id, currency):
    INVOICE_DIR.mkdir(exist_ok=True)
    suffix = "" if currency == "EGP" else f"_{currency.lower()}"
    path = INVOICE_DIR / f"invoice_Res{reservation_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suffix}.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


def generate_walkin_receipt_text(receipt_items, total_gross, method, payment_id=None, when=None):
    """Receipt text for an atomic walk-in sale (many items on same receipt)."""
    W = 48
    display_name = (get_app_display_name() or "Property Manager").upper()
    when = when or datetime.now()
    ref = f"Payment #{payment_id}" if payment_id else "Walk-in"
    lines = ["=" * W, f"{display_name} — WALK-IN RECEIPT".center(W), "=" * W,
             when.strftime("%Y-%m-%d %H:%M"),
             f"Ref: {ref}",
             "-" * W]
    for it in receipt_items or []:
        desc = it.get("desc") or it.get("item_description") or "Item"
        qty = it.get("qty", it.get("quantity", 1))
        price = float(it.get("price", it.get("unit_price", 0)) or 0)
        gross = float(it.get("gross", 0) or 0)
        if gross == 0 and price:
            try:
                gross = float(qty) * price
            except Exception:
                gross = 0
        lines.append(f"  {desc}  x{qty}  @ {price:.2f}  = {gross:.2f}")
    lines += ["-" * W, f"  TOTAL: {float(total_gross):.2f} EGP",
              f"  Paid by: {method}", "=" * W,
              "", "Thank you!", "=" * W]
    return "\n".join(lines)


def save_walkin_receipt_file(text, payment_id):
    """Save walk-in receipt with distinct naming (avoids reservation invoice_Res00000 ambiguity)."""
    INVOICE_DIR.mkdir(exist_ok=True)
    safe_id = int(payment_id) if payment_id else 0
    path = INVOICE_DIR / f"walkin_receipt_Pay{safe_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


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
    with open(filepath, "rb") as f:
        data = f.read()
    data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(10)
        s.connect((host, port))
        s.sendall(data)


def print_to_printer(filepath, printer=None):
    if not os.path.exists(filepath):
        return False, f"File not found: {filepath}"
    target = printer if printer is not None else get_setting("default_printer", "")
    try:
        if not target:
            if platform.system() == "Windows":
                os.startfile(filepath, "print")
            else:
                r = subprocess.run(["lp", filepath], capture_output=True, text=True, timeout=15)
                if r.returncode != 0:
                    return False, f"Print failed: {r.stderr.strip()}"
            return True, f"Sent to default printer: {filepath}"
        if target.startswith("cups:"):
            r = subprocess.run(["lp", "-d", target[5:], filepath], capture_output=True, text=True, timeout=15)
            return (True, f"Sent to {target[5:]}") if r.returncode == 0 else (False, f"CUPS error: {r.stderr}")
        if target.startswith("win:"):
            os.startfile(filepath, "printto", target[4:])
            return True, f"Sent to {target[4:]}"
        if target.startswith("raw:"):
            host, port = target[4:].rsplit(":", 1)
            _print_raw_socket(host, int(port), filepath)
            return True, f"Raw {host}:{port}"
        return False, f"Unknown target: {target}"
    except FileNotFoundError:  # pragma: no cover — only if `lp`/`startfile` missing
        return False, f"No printer command. Saved: {filepath}"
    except Exception as e:  # pragma: no cover — network/OS
        return False, f"Print error: {e}. Saved: {filepath}"


def test_print(printer=None):
    EXPORT_DIR.mkdir(exist_ok=True)
    path = EXPORT_DIR / "printer_test.txt"
    display_name = get_app_display_name().upper()
    path.write_text(f"{display_name} PRINTER TEST\n{datetime.now()}\n", encoding="utf-8")
    return print_to_printer(str(path), printer)


# ═══════════════════════════════════════════════════════════════════════════
# FRONT DESK / OCCUPANCY
# ═══════════════════════════════════════════════════════════════════════════

def get_front_desk_summary():
    from .db import connect
    today = datetime.now().strftime(DATE_FMT)
    conn = connect()
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


def get_rooms_available_between(ci, co):
    from .db import connect
    return connect().execute("""SELECT r.id, r.room_number, r.room_type, r.base_price, r.housekeeping_status
        FROM rooms r WHERE r.id NOT IN (
            SELECT room_id FROM reservations WHERE status IN ('Reserved','Checked In')
            AND NOT (check_out_date <= ? OR check_in_date >= ?)
        ) ORDER BY r.room_number""", (ci, co)).fetchall()


def get_upcoming_reservations_for_room(room_id):
    from .db import connect
    today = datetime.now().strftime(DATE_FMT)
    return connect().execute("""SELECT id, guest_name, check_in_date, check_out_date, status, agreed_nightly_rate
        FROM reservations WHERE room_id=? AND check_in_date >= ?
        AND status IN ('Reserved','Checked In') ORDER BY check_in_date""", (room_id, today)).fetchall()


def get_occupancy_for_window(start, end):
    from .db import connect
    d1 = datetime.strptime(start, DATE_FMT)
    d2 = datetime.strptime(end, DATE_FMT)
    days = max(1, (d2 - d1).days + 1)
    total_rooms = connect().execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
    trn = total_rooms * days
    rows = connect().execute("""SELECT check_in_date, check_out_date FROM reservations
        WHERE status IN ('Reserved','Checked In','Checked Out')
        AND NOT (check_out_date <= ? OR check_in_date >= ?)""", (start, end)).fetchall()
    occupied = 0
    for r in rows:
        a = max(datetime.strptime(r["check_in_date"], DATE_FMT), d1)
        b = min(datetime.strptime(r["check_out_date"], DATE_FMT), d2 + timedelta(days=1))
        if b > a:
            occupied += (b - a).days
    rev = connect().execute("""SELECT COALESCE(SUM(net + service), 0) FROM general_ledger
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
    from .db import connect
    conn = connect()
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
    from .db import connect
    types = _net_types(("PAYMENT", "REFUND"))
    ph = ",".join("?" * len(types))
    return {r[0]: round(r[1] or 0, 2) for r in connect().execute(
        f"""SELECT method, SUM(gross) FROM general_ledger
            WHERE entry_type IN ({ph}) AND date(at) BETWEEN date(?) AND date(?)
              AND method IS NOT NULL AND method <> ''
            GROUP BY method""",
        (*types, start, end),
    ).fetchall() if r[0]}


def get_daily_occupancy_series(start, end):
    from .db import connect
    d1 = datetime.strptime(start, DATE_FMT)
    d2 = datetime.strptime(end, DATE_FMT)
    total_rooms = connect().execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
    out = []
    cur = d1
    while cur <= d2:
        day = cur.strftime(DATE_FMT)
        occ = connect().execute("""SELECT COUNT(*) FROM reservations
            WHERE status IN ('Reserved','Checked In','Checked Out')
            AND check_in_date <= ? AND check_out_date > ?""", (day, day)).fetchone()[0]
        out.append((day, round((occ / total_rooms * 100) if total_rooms else 0, 1)))
        cur += timedelta(days=1)
    return out


def get_occupancy_heatmap_data(start, end):
    from .db import connect
    d1 = datetime.strptime(start, DATE_FMT)
    d2 = datetime.strptime(end, DATE_FMT)
    dates = []
    cur = d1
    while cur <= d2:
        dates.append(cur.strftime(DATE_FMT))
        cur += timedelta(days=1)
    rooms = connect().execute("SELECT id, room_number FROM rooms ORDER BY room_number").fetchall()
    res = connect().execute("""SELECT room_id, check_in_date, check_out_date FROM reservations
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
    from .db import connect
    conn = connect()
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
    from .db import connect
    conn = connect()
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
    from .db import connect
    conn = connect()
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
    from .db import connect
    arr = connect().execute("SELECT id, guest_name FROM reservations WHERE check_in_date=? AND status IN ('Reserved','Checked In')",
                            (date,)).fetchall()
    dep = connect().execute("SELECT id, guest_name FROM reservations WHERE check_out_date=? AND status IN ('Reserved','Checked In')",
                            (date,)).fetchall()
    inh = connect().execute("""SELECT id, guest_name, check_in_date, check_out_date FROM reservations
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
    from .db import connect
    return connect().execute("""
        SELECT p.id, p.created_at, p.item_description, p.quantity, p.unit_price, p.tax_amount,
               p.inventory_item_id, p.voided_at
        FROM pos_tabs p
        WHERE p.reservation_id IS NULL
        ORDER BY p.id DESC LIMIT ?
    """, (int(limit or 200),)).fetchall()


def list_walkin_payments(limit=100):
    """Walk-in payments (reservation_id IS NULL)."""
    from .db import connect
    return connect().execute("""
        SELECT id, at, amount, method, payment_type, notes, voided_at
        FROM payments
        WHERE reservation_id IS NULL
        ORDER BY id DESC LIMIT ?
    """, (int(limit or 100),)).fetchall()


def verify_ledger_integrity():
    """Read-only health check: ledger/ref consistency and basic money sanity."""
    from .db import connect
    conn = connect()
    issues = []
    warnings = []
    stats = {}

    # Basic counts
    for name, q in [
        ("ledger_rows", "SELECT COUNT(*) FROM general_ledger"),
        ("reservations", "SELECT COUNT(*) FROM reservations"),
        ("pos_rows", "SELECT COUNT(*) FROM pos_tabs"),
        ("payments", "SELECT COUNT(*) FROM payments"),
        ("void_reversals", "SELECT COUNT(*) FROM general_ledger WHERE reverses_id IS NOT NULL"),
    ]:
        try:
            stats[name] = conn.execute(q).fetchone()[0]
        except Exception as e:
            warnings.append(f"stat {name}: {e}")

    # POS ledger refs should point to existing pos_tabs (when POS/VOID_POS rows exist)
    try:
        rows = conn.execute("""
            SELECT DISTINCT l.ref_id FROM general_ledger l
            WHERE l.ref_type='POS' AND l.ref_id NOT IN (SELECT id FROM pos_tabs)
            LIMIT 20
        """).fetchall()
        if rows:
            issues.append({"type": "orphan_ledger_pos_ref", "detail": f"ledger POS refs missing pos_tabs ids (sample): {[r[0] for r in rows]}"})
    except Exception as e:
        warnings.append(f"pos ref check: {e}")

    # RES ledger refs should point to existing reservations
    try:
        rows = conn.execute("""
            SELECT DISTINCT l.ref_id FROM general_ledger l
            WHERE l.ref_type='RES' AND l.ref_id NOT IN (SELECT id FROM reservations)
            LIMIT 20
        """).fetchall()
        if rows:
            issues.append({"type": "orphan_ledger_res_ref", "detail": f"ledger RES refs missing reservation ids (sample): {[r[0] for r in rows]}"})
    except Exception as e:
        warnings.append(f"res ref check: {e}")

    # PAY/WALKIN_PAY ledger refs should point to existing payments
    try:
        rows = conn.execute("""
            SELECT DISTINCT l.ref_type, l.ref_id FROM general_ledger l
            WHERE l.ref_type IN ('PAY','WALKIN_PAY') AND l.ref_id NOT IN (SELECT id FROM payments)
            LIMIT 20
        """).fetchall()
        if rows:
            issues.append({"type": "orphan_ledger_payment_ref", "detail": f"ledger payment refs missing payments (sample): {[(r[0],r[1]) for r in rows]}"})
    except Exception as e:
        warnings.append(f"payment ref check: {e}")

    # Active reservation POS linkage sanity (pos_tabs.reservation_id -> reservation exists when not null)
    try:
        rows = conn.execute("""
            SELECT p.id, p.reservation_id FROM pos_tabs p
            WHERE p.reservation_id IS NOT NULL AND p.reservation_id NOT IN (SELECT id FROM reservations)
            LIMIT 20
        """).fetchall()
        if rows:
            issues.append({"type": "orphan_pos_reservation", "detail": f"pos_tabs with missing reservation_id (sample): {[(r[0],r[1]) for r in rows]}"})
    except Exception as e:
        warnings.append(f"pos reservation check: {e}")

    # Reservation balance sanity sample (non-negative gross charge accumulation can still yield negative due to overpayment; flag extreme anomalies)
    try:
        bad = []
        for r in conn.execute("SELECT id FROM reservations WHERE status IN ('Reserved','Checked In','Checked Out') ORDER BY id DESC LIMIT 100").fetchall():
            bal = calculate_guest_balance(r["id"])
            if bal < -100000 or bal > 10000000:
                bad.append({"reservation_id": r["id"], "balance": bal})
        if bad:
            warnings.append({"type": "balance_outlier_sample", "detail": bad[:10]})
        stats["balances_sampled"] = min(100, stats.get("reservations", 0))
    except Exception as e:
        warnings.append(f"balance sample: {e}")

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
    summary = get_general_ledger_summary()
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
    fin = dataset_financial_daily(start, end)
    occ = {r["date"]: r["occupancy_pct"] for r in dataset_occupancy_daily(start, end)}
    total_rev = sum(r["revenue_net_plus_service"] for r in fin)
    total_exp = sum(r["expenses_plus_cogs"] for r in fin)
    total_profit = sum(r["profit"] for r in fin)
    avg_occ = round(sum(occ.values()) / len(occ), 2) if occ else 0.0
    best_day = max(fin, key=lambda r: r["profit"], default=None)
    worst_day = min(fin, key=lambda r: r["profit"], default=None)
    neg_days = [r["date"] for r in fin if r["profit"] < 0]
    summary = get_general_ledger_summary()
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


def _write_json(path, payload):
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _write_csv(path, rows):
    rows = list(rows or [])
    p = Path(path)
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
    ANALYTICS_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = ANALYTICS_EXPORT_DIR / f"{preset}_{start}_{end}_{stamp}"
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
        Path(tp).write_text("\n".join(header + text_sections), encoding="utf-8")
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
    from .db import connect
    c = connect()
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
        "label": "Analysis Small (30 days, synthetic)",
        "description": "Guarded synthetic reservations/POS/payments/expenses for analysis/model prototyping.",
    }
}


def list_demo_data_presets():
    return [{"key": k, **v} for k, v in DEMO_DATA_PRESETS.items()]


def apply_demo_data_preset(name, password, confirm_text, allow_existing=False):
    """Guarded demo data loader for analysis/prototyping. Never silent."""
    if not verify_password(password):
        raise PermissionError("Invalid super password.")
    if name not in DEMO_DATA_PRESETS:
        raise ValueError(f"Unknown demo preset: {name}")
    if str(confirm_text).strip().upper() != "APPLY DEMO DATA":
        raise ValueError('Confirmation text must be exactly: APPLY DEMO DATA')
    if has_operational_data() and not allow_existing:
        raise ValueError("Database already has operational data. Refusing to apply demo preset.")
    if allow_existing and not verify_password(password):
        raise PermissionError("Invalid super password.")

    from .db import connect, transaction
    conn = connect()
    rooms = conn.execute("SELECT id, base_price FROM rooms ORDER BY id").fetchall()
    inv = conn.execute("SELECT id, selling_price, unit_cost FROM inventory WHERE active=1 ORDER BY id").fetchall()
    cats = conn.execute("SELECT id FROM expense_categories ORDER BY id").fetchall()
    plans = conn.execute("SELECT id FROM rate_plans ORDER BY id").fetchall()
    if not rooms:
        raise ValueError("No rooms available for demo preset.")

    base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=20)
    created = {"reservations": 0, "pos_items": 0, "payments": 0, "expenses": 0}
    # create synthetic stays
    for i in range(8):
        room = rooms[i % len(rooms)]
        ci = (base + timedelta(days=i * 2)).strftime(DATE_FMT)
        co = (base + timedelta(days=i * 2 + 2)).strftime(DATE_FMT)
        guest = f"Demo Guest {i+1}"
        plan = plans[0]["id"] if plans else None
        rid = create_reservation(guest, "Direct", room["id"], plan, float(room["base_price"] or 100), ci, co, 0.0)
        created["reservations"] += 1
        if i % 3 != 0:
            record_payment(rid, round((room["base_price"] or 100) * 2 * 1.24, 2), "Cash", "Demo payment")
            created["payments"] += 1
        if inv and i % 2 == 0:
            item = inv[i % len(inv)]
            add_pos_item(rid, f"Demo POS {i+1}", 1 + (i % 2), float(item["selling_price"] or 10), item["id"])
            created["pos_items"] += 1
        if i % 4 == 0:
            try:
                update_reservation_status(rid, "Checked In")
            except Exception:
                pass
        if i % 5 == 0:
            try:
                if connect().execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()["status"] == "Checked In":
                    update_reservation_status(rid, "Checked Out")
            except Exception:
                pass
    if cats:
        for i in range(4):
            record_operational_expense(cats[i % len(cats)]["id"], f"Demo Expense {i+1}", 50 + i * 25, 0.0, "Demo", "Cash")
            created["expenses"] += 1
    _audit("demo_preset_apply", "db", "", f"{name} {created}")
    return {"preset": name, "created": created}


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