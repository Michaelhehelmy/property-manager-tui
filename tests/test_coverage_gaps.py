"""Closes every remaining branch + internal function."""
import pytest
from unittest.mock import patch, MagicMock
from app_core import services
from app_core import db
from app_core.db import connect, transaction
from conftest import make_reservation


# ═══════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def test_norm_text_basic():
    assert services._norm_text("Hello World") == "hello world"
    assert services._norm_text("  A  B  ") == "a b"


def test_norm_text_arabic():
    assert services._norm_text("أحمد") == "احمد"
    assert services._norm_text("إبراهيم") == "ابراهيم"
    assert services._norm_text("آمنة") == "امنه"
    assert services._norm_text("مصطفى") == "مصطفي"
    assert services._norm_text("فاطمة") == "فاطمه"


def test_norm_text_arabic_diacritics():
    assert services._norm_text("مُحَمَّد") == "محمد"


def test_norm_text_empty():
    assert services._norm_text("") == ""
    assert services._norm_text(None) == ""


def test_norm_phone():
    assert services._norm_phone("+20 100 123 4567") == "1001234567"
    assert services._norm_phone("0020 100 123 4567") == "1001234567"
    assert services._norm_phone("01001234567") == "1001234567"
    assert services._norm_phone("") == ""
    assert services._norm_phone(None) == ""


def test_norm_phone_short():
    # Less than 10 digits — last 10 is still returned (all of them)
    assert services._norm_phone("12345") == "12345"


def test_name_score_exact():
    assert services._name_score("Ahmed", "Ahmed") == 1.0


def test_name_score_different():
    score = services._name_score("Ahmed", "Bob")
    assert score < 0.5


def test_name_score_empty():
    assert services._name_score("", "X") == 0.0
    assert services._name_score("X", "") == 0.0
    assert services._name_score("", "") == 0.0


def test_name_score_token_sort():
    """Token-sorted comparison catches 'Ahmed Ali' vs 'Ali Ahmed'."""
    score = services._name_score("Ahmed Ali", "Ali Ahmed")
    assert score > 0.9


def test_nights_calculation():
    assert services._nights("2026-12-01", "2026-12-03") == 2
    assert services._nights("2026-12-01", "2026-12-02") == 1
    # Same day — force to 1
    assert services._nights("2026-12-01", "2026-12-01") == 1


# ═══════════════════════════════════════════════════════════════════════════
# VALIDATE SETTING — every type
# ═══════════════════════════════════════════════════════════════════════════

def test_validate_str(clean_db):
    norm, err = services._validate_setting("property_name", "  Test  ")
    assert norm == "Test"
    assert err is None


def test_validate_int_valid(clean_db):
    norm, err = services._validate_setting("min_nights", "5")
    assert norm == "5"
    assert err is None


def test_validate_int_negative(clean_db):
    norm, err = services._validate_setting("min_nights", "-1")
    assert norm is None
    assert ">= 0" in err


def test_validate_int_non_number(clean_db):
    norm, err = services._validate_setting("min_nights", "abc")
    assert norm is None


def test_validate_float_valid(clean_db):
    norm, err = services._validate_setting("usd_to_egp", "50.5")
    assert norm == "50.5"
    assert err is None


def test_validate_float_negative(clean_db):
    norm, err = services._validate_setting("city_tax_per_night", "-1")
    assert norm is None


def test_validate_pct_valid(clean_db):
    norm, err = services._validate_setting("vat_rate", "0.15")
    assert norm == "0.15"


def test_validate_pct_above_one(clean_db):
    norm, err = services._validate_setting("vat_rate", "1.5")
    assert norm is None


def test_validate_pct_negative(clean_db):
    norm, err = services._validate_setting("vat_rate", "-0.1")
    assert norm is None


def test_validate_bool_true(clean_db):
    norm, err = services._validate_setting("checkout_requires_payment", "true")
    assert norm == "true"
    assert err is None


def test_validate_bool_yes(clean_db):
    norm, err = services._validate_setting("checkout_requires_payment", "yes")
    assert norm == "true"


def test_validate_bool_false(clean_db):
    norm, err = services._validate_setting("checkout_requires_payment", "false")
    assert norm == "false"


def test_validate_bool_garbage(clean_db):
    norm, err = services._validate_setting("checkout_requires_payment", "xyz")
    assert norm == "false"


def test_validate_enum_valid(clean_db):
    norm, err = services._validate_setting("language", "ar")
    assert norm == "ar"


def test_validate_enum_invalid(clean_db):
    norm, err = services._validate_setting("language", "fr")
    assert norm is None
    assert "must be one of" in err


def test_validate_unknown_key(clean_db):
    norm, err = services._validate_setting("nonexistent_xyz", "x")
    assert norm is None
    assert "Unknown" in err


# ═══════════════════════════════════════════════════════════════════════════
# LANGUAGE
# ═══════════════════════════════════════════════════════════════════════════

def test_set_language_en(clean_db):
    services.set_language("en")
    assert services.get_language() == "en"


def test_set_language_ar(clean_db):
    services.set_language("ar")
    assert services.get_language() == "ar"
    services.set_language("en")


def test_set_language_invalid_defaults_to_en(clean_db):
    services.set_language("fr")
    assert services.get_language() == "en"


def test_translate_key(clean_db):
    services.set_language("en")
    assert services.t("Front Desk") == "Front Desk"


def test_translate_missing_key_returns_key(clean_db):
    services.set_language("en")
    assert services.t("Nonexistent Key") == "Nonexistent Key"


# ═══════════════════════════════════════════════════════════════════════════
# GUESTS — remaining functions
# ═══════════════════════════════════════════════════════════════════════════

def test_get_guest_by_id(clean_db):
    gid, name, _ = services.find_or_create_guest("Test")
    g = services.get_guest_by_id(gid)
    assert g["name"] == "Test"


def test_get_guest_by_id_missing(clean_db):
    assert services.get_guest_by_id(9999) is None


def test_get_guest_history_empty(clean_db):
    gid, _, _ = services.find_or_create_guest("Test")
    assert services.get_guest_history_by_id(gid) == []


def test_get_guest_history_with_bookings(clean_db):
    gid, _, _ = services.find_or_create_guest("Test")
    services.create_reservation("Test", "Direct", 1, None, 100,
                                "2026-12-01", "2026-12-03", guest_id=gid)
    history = services.get_guest_history_by_id(gid)
    assert len(history) == 1
    assert history[0]["status"] == "Reserved"


# ═══════════════════════════════════════════════════════════════════════════
# INVENTORY — remaining
# ═══════════════════════════════════════════════════════════════════════════

def test_get_inventory_categories(clean_db):
    cats = services.get_inventory_categories()
    assert "F&B" in cats
    assert "Housekeeping" in cats


# ═══════════════════════════════════════════════════════════════════════════
# EXPENSES — remaining
# ═══════════════════════════════════════════════════════════════════════════

def test_get_expense_categories(clean_db):
    cats = services.get_expense_categories()
    assert len(cats) == 4


# ═══════════════════════════════════════════════════════════════════════════
# SETTINGS — remaining
# ═══════════════════════════════════════════════════════════════════════════

def test_get_all_settings(clean_db):
    all_s = services.get_all_settings()
    assert "vat_rate" in all_s
    assert "language" in all_s


def test_save_settings_skips_schema_version(clean_db):
    count, errs = services.save_settings({"schema_version": "999"}, "testpass")
    assert count == 0  # skipped
    assert errs == []


# ═══════════════════════════════════════════════════════════════════════════
# LEDGER — remaining
# ═══════════════════════════════════════════════════════════════════════════

def test_get_recent_ledger(clean_db):
    make_reservation()
    recent = services.get_recent_ledger(10)
    assert len(recent) > 0
    assert "entry_type" in recent[0].keys()


def test_get_recent_ledger_limit(clean_db):
    for i in range(4):
        make_reservation(room_id=i + 1, guest=f"Guest{i}")
    recent = services.get_recent_ledger(2)
    assert len(recent) == 2


# ═══════════════════════════════════════════════════════════════════════════
# RESERVATIONS — remaining
# ═══════════════════════════════════════════════════════════════════════════

def test_get_reservation(clean_db):
    rid = make_reservation()
    r = services.get_reservation(rid)
    assert r is not None
    assert r["id"] == rid


def test_get_reservation_not_found(clean_db):
    assert services.get_reservation(9999) is None


def test_cancel_restores_pos_stock(clean_db):
    """Cancelling a reservation with POS items restores stock."""
    rid = make_reservation()
    services.add_pos_item(rid, "Water", 3, 20, 1)
    before = connect().execute("SELECT stock_qty FROM inventory WHERE id=1").fetchone()["stock_qty"]
    services.cancel_reservation(rid, "testpass")
    after = connect().execute("SELECT stock_qty FROM inventory WHERE id=1").fetchone()["stock_qty"]
    assert after == before + 3


def test_cancel_marks_pos_voided(clean_db):
    rid = make_reservation()
    services.add_pos_item(rid, "Water", 1, 20, 1)
    services.cancel_reservation(rid, "testpass")
    row = connect().execute("SELECT voided_at FROM pos_tabs WHERE reservation_id=?", (rid,)).fetchone()
    assert row["voided_at"] is not None


def test_cancel_keep_first_night_policy(clean_db):
    services.set_setting("cancel_policy", "keep_first_night")
    rid = make_reservation(rate=100.0, ci="2026-12-01", co="2026-12-03", deposit=150.0)
    services.cancel_reservation(rid, "testpass")
    refund = connect().execute(
        "SELECT SUM(amount) FROM payments WHERE reservation_id=? AND payment_type='Refund'",
        (rid,)).fetchone()[0]
    # 150 - 100 = 50 refund
    assert abs(refund) == pytest.approx(50.0)


# ═══════════════════════════════════════════════════════════════════════════
# POS — remaining
# ═══════════════════════════════════════════════════════════════════════════

def test_void_custom_item_no_inventory(clean_db):
    """Custom items (no inventory link) void without stock restore."""
    rid = make_reservation()
    result = services.add_pos_item(rid, "Custom Service", 1, 50, None)
    services.void_pos_item(result["pos_id"], "testpass")
    row = connect().execute("SELECT voided_at FROM pos_tabs WHERE id=?", (result["pos_id"],)).fetchone()
    assert row["voided_at"] is not None


def test_add_pos_inactive_inventory_item(clean_db):
    from app_core.db import transaction
    with transaction() as c:
        c.execute("INSERT INTO inventory (item_name, category, stock_qty, unit, active) VALUES ('Dead', 'Test', 10, 'pcs', 0)")
        iid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    rid = make_reservation()
    with pytest.raises(ValueError, match="inactive"):
        services.add_pos_item(rid, "Dead", 1, 10, iid)


def test_add_pos_nonexistent_inventory_item(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError, match="not found"):
        services.add_pos_item(rid, "Ghost", 1, 10, 9999)


def test_add_pos_nonexistent_reservation(clean_db):
    with pytest.raises(ValueError, match="not found"):
        services.add_pos_item(9999, "X", 1, 10, None)


def test_get_pos_items_empty(clean_db):
    rid = make_reservation()
    assert services.get_pos_items(rid) == []


def test_get_pos_items_with_items(clean_db):
    rid = make_reservation()
    services.add_pos_item(rid, "Water", 2, 20, 1)
    items = services.get_pos_items(rid)
    assert len(items) == 1
    assert items[0]["item_description"] == "Water"


# ═══════════════════════════════════════════════════════════════════════════
# PAYMENTS — remaining
# ═══════════════════════════════════════════════════════════════════════════

def test_record_payment_non_int_reservation(clean_db):
    with pytest.raises(ValueError):
        services.record_payment("not-an-int", 100.0, "Cash")


def test_record_payment_nonexistent_reservation(clean_db):
    with pytest.raises(ValueError, match="not found"):
        services.record_payment(9999, 100.0, "Cash")


# ═══════════════════════════════════════════════════════════════════════════
# SESSION — remaining
# ═══════════════════════════════════════════════════════════════════════════

def test_touch_session_unlocked(clean_db):
    services.unlock_session("testpass")
    import time
    time.sleep(0.01)
    before = services._session["last_activity"]
    time.sleep(0.01)
    services.touch_session()
    assert services._session["last_activity"] > before
    services.lock_session()


def test_touch_session_locked(clean_db):
    services.lock_session()
    before = services._session["last_activity"]
    services.touch_session()
    assert services._session["last_activity"] == before


# ═══════════════════════════════════════════════════════════════════════════
# UI HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def test_fmt():
    assert services.fmt(1234.5) == "1,234.50 EGP"
    assert services.fmt(0) == "0.00 EGP"
    assert services.fmt(100, "USD") == "100.00 USD"


def test_selected_id_valid():
    class MockTable:
        row_count = 2
        cursor_row = 0
        def get_row_at(self, i):
            return ("42", "x")
    assert services.selected_id(MockTable()) == 42


def test_selected_id_empty():
    class MockTable:
        row_count = 0
        cursor_row = 0
        def get_row_at(self, i):
            return ("x",)
    assert services.selected_id(MockTable()) is None


def test_selected_id_non_numeric():
    class MockTable:
        row_count = 1
        cursor_row = 0
        def get_row_at(self, i):
            return ("abc",)
    assert services.selected_id(MockTable()) is None


def test_safe_action_success():
    class MockApp:
        def __init__(self):
            self.messages = []
        def notify(self, msg, severity=None):
            self.messages.append((msg, severity))
    class MockSelf:
        def __init__(self):
            self.app = MockApp()

    calls = []
    @services.safe_action
    def handler(self, event):
        calls.append(event)

    m = MockSelf()
    handler(m, "test-event")
    assert calls == ["test-event"]
    assert m.app.messages == []


def test_safe_action_permission_error():
    class MockApp:
        def __init__(self):
            self.messages = []
        def notify(self, msg, severity=None):
            self.messages.append((msg, severity))
    class MockSelf:
        def __init__(self):
            self.app = MockApp()

    @services.safe_action
    def handler(self, event):
        raise PermissionError("bad")

    m = MockSelf()
    handler(m, None)
    assert len(m.app.messages) == 1
    assert m.app.messages[0][1] == "error"


def test_safe_action_generic_exception():
    class MockApp:
        def __init__(self):
            self.messages = []
        def notify(self, msg, severity=None):
            self.messages.append((msg, severity))
    class MockSelf:
        def __init__(self):
            self.app = MockApp()

    @services.safe_action
    def handler(self, event):
        raise RuntimeError("boom")

    m = MockSelf()
    handler(m, None)
    assert len(m.app.messages) == 1
    assert "boom" in m.app.messages[0][0]


# ═══════════════════════════════════════════════════════════════════════════
# AUDIT LOG
# ═══════════════════════════════════════════════════════════════════════════

def test_get_audit_log(clean_db):
    make_reservation()
    log = services.get_audit_log(50)
    assert len(log) > 0
    actions = [r["action"] for r in log]
    assert "reservation_create" in actions


def test_audit_log_limit(clean_db):
    for i in range(3):
        make_reservation(room_id=i + 1, guest=f"G{i}")
    log = services.get_audit_log(2)
    assert len(log) <= 2


# ═══════════════════════════════════════════════════════════════════════════
# DB LAYER
# ═══════════════════════════════════════════════════════════════════════════

def test_migrate_upgraded(clean_db):
    """Test the 'upgraded' branch of migrate()."""
    conn = db.connect()
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    result = db.migrate()
    assert result == "upgraded"
    version = db.connect().execute("PRAGMA user_version").fetchone()[0]
    assert version == db.SCHEMA_VERSION


def test_db_user_version(clean_db):
    assert db.user_version() == db.SCHEMA_VERSION


def test_db_is_not_fresh(clean_db):
    assert db.is_fresh() is False


def test_db_transaction_rollback(clean_db):
    try:
        with db.transaction() as c:
            c.execute("INSERT INTO guests (canonical_name) VALUES ('rollback_test')")
            raise ValueError("force rollback")
    except ValueError:
        pass
    row = connect().execute("SELECT 1 FROM guests WHERE canonical_name='rollback_test'").fetchone()
    assert row is None