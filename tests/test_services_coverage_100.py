"""Targeted tests to drive app_core.services to 100% coverage for this module.

Covers missing ranges from baseline, including assistant helpers, recovery code flow,
checkout/POS/walk-in branches, integrity checks, analytics/export, demo presets,
branding/migration helpers, and miscellaneous defensive branches.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from app_core import db, services
from tests.conftest import make_reservation


FIXED_NOW = datetime(2026, 6, 15, 12, 30, 45)


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW


@pytest.fixture
def fixed_now(monkeypatch):
    monkeypatch.setattr(services, "datetime", FixedDatetime)
    return FIXED_NOW


def _raw_exec(sql, params=(), fk_off=False):
    conn = db.connect()
    if fk_off:
        conn.execute("PRAGMA foreign_keys=OFF")
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur
    finally:
        if fk_off:
            conn.execute("PRAGMA foreign_keys=ON")


# ── branding / misc helpers ────────────────────────────────────────────────

def test_branding_and_misc_helpers(clean_db):
    services.set_setting("app_display_name", "")
    services.set_setting("property_name", "Sukoon Camp")
    assert services.get_app_display_name() == "Sukoon Camp" or services.get_app_display_name() == "My Property"
    services.set_setting("property_name", "My Property")
    services.set_setting("app_display_name", "")
    assert services.get_app_display_name() == "My Property"
    services.set_setting("app_tagline", "  Calm stays  ")
    assert services.get_app_tagline() == "Calm stays"
    services.set_setting("loading_animation", "WAVE")
    assert services.get_loading_animation() == "wave"
    services.set_setting("loading_animation", "bogus")
    assert services.get_loading_animation() == "reveal"
    services.set_setting("branding_configured", "true")
    assert services.is_branding_configured() is True
    services.set_setting("branding_configured", "false")
    assert services.is_branding_configured() is False
    assert "Gifted with love" in services.get_gift_attribution()
    assert "My Property" in services.gift_attribution_for(None)
    assert services.gift_hash_for("x")


def test_migrate_branding_branches(clean_db, monkeypatch):
    services.set_setting("branding_configured", "true")
    services.set_setting("property_name", "Anything")
    services.migrate_branding_if_needed()
    assert services.get_setting("branding_configured") == "true"

    services.set_setting("branding_configured", "false")
    services.set_setting("property_name", "Sukoon Camp")
    services.set_setting("app_tagline", "Legacy")
    services.set_setting("loading_animation", "pulse")
    services.migrate_branding_if_needed()
    assert services.is_branding_configured() is True

    services.set_setting("branding_configured", "false")
    services.set_setting("property_name", "Desert Inn")
    services.set_setting("app_display_name", "")
    services.migrate_branding_if_needed()
    assert services.get_setting("app_display_name") == "Desert Inn"
    assert services.is_branding_configured() is True

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(services, "get_setting", boom)
    services.migrate_branding_if_needed()  # swallowed


def test_apply_branding_settings_validation_and_flow(clean_db):
    with pytest.raises(ValueError, match="Display name is required"):
        services.apply_branding_settings("   ")
    out = services.apply_branding_settings("Seaside", " Tag ", "badstyle", sync_property_name=False)
    assert out["animation"] == "reveal"
    assert out["tagline"] == "Tag"
    assert out["display_name"] == "Seaside"
    assert services.get_setting("app_display_name") == "Seaside"
    assert services.get_setting("property_name") != "Seaside"
    out2 = services.apply_branding_settings("Seaside2", "T", "none", sync_property_name=True)
    assert services.get_setting("property_name") == "Seaside2"
    assert out2["attribution"]


def test_net_types_string_branch(clean_db):
    assert services._net_types("CHARGE") == ("CHARGE", "VOID_CHARGE")


# ── recovery codes ─────────────────────────────────────────────────────────

def test_use_recovery_code_flow(unlocked):
    codes = services.generate_recovery_codes("testpass")
    assert len(codes) == 8
    code = codes[0]
    with pytest.raises(ValueError, match="required"):
        services.use_recovery_code("", "newpass1", "newpass1")
    with pytest.raises(ValueError, match="do not match"):
        services.use_recovery_code(code, "newpass1", "mismatch")
    with pytest.raises(ValueError, match="at least 6"):
        services.use_recovery_code(code, "x", "x")
    with pytest.raises(ValueError, match="Invalid or already-used"):
        services.use_recovery_code("NOTAREALCODE", "newpass1", "newpass1")
    assert services.use_recovery_code(code, "newpass1", "newpass1") is True
    assert services.verify_password("newpass1") is True
    with pytest.raises(ValueError):
        services.use_recovery_code(code, "newpass2", "newpass2")


# ── assistant helpers ──────────────────────────────────────────────────────

def test_assistant_find_guests(clean_db):
    assert services.assistant_find_guests("") == []
    assert services.assistant_find_guests("   ") == []
    make_reservation(guest="Alice Search")
    rows = services.assistant_find_guests("Alice", limit=1)
    assert rows and rows[0]["name"] == "Alice Search"
    assert "total_billed" in rows[0] and rows[0]["stays"] >= 1


def test_assistant_guest_visit_and_top_items(clean_db):
    assert services.assistant_get_guest_last_visit(999999) is None
    gid, _, _ = services.find_or_create_guest("NoRes Guest")
    out = services.assistant_get_guest_last_visit(gid)
    assert out and out["last_visit"] is None and "No reservations" in out["reason"]

    rid = make_reservation(guest="Visitor One", room_id=1)
    gid = services.get_all_guests("Visitor One")[0]["id"]
    services.add_pos_item(rid, "Coffee", 2, 10.0)
    services.add_pos_item(rid, "Tea", 1, 5.0)
    visit = services.assistant_get_guest_last_visit(gid)
    assert visit["last_visit"]["id"] == rid
    assert "definition" in visit
    top = services.assistant_get_top_items_for_reservation(rid, limit=99)
    assert any(t["item_name"] == "Coffee" and t["total_qty"] == 2 for t in top)
    last_items = services.assistant_get_top_items_last_visit(gid, limit=5)
    assert last_items["reservation_id"] == rid and last_items["items"]

    rid2 = make_reservation(guest="Visitor Two", room_id=2)
    g2 = services.get_all_guests("Visitor Two")[0]["id"]
    noitems = services.assistant_get_top_items_last_visit(g2)
    assert noitems["items"] == [] and "No non-voided POS items" in noitems["note"]

    missing = services.assistant_get_top_items_last_visit(999999)
    assert missing["last_visit"] is None and missing["items"] == []


def test_assistant_reservation_summary(clean_db, monkeypatch):
    assert services.assistant_reservation_summary(999999) is None
    rid = make_reservation()
    services.add_pos_item(rid, "Snack", 1, 7.0)
    s = services.assistant_reservation_summary(rid)
    assert s["reservation"]["id"] == rid
    assert s["active_pos_item_lines"] >= 1
    assert isinstance(s["balance"], (int, float))

    def bad_balance(_rid):
        raise RuntimeError("boom")
    monkeypatch.setattr(services, "calculate_guest_balance", bad_balance)
    s2 = services.assistant_reservation_summary(rid)
    assert s2["balance"]["error"] == "boom"


# ── checkout / estimate / POS / walk-in ───────────────────────────────────

def test_estimate_cancellation_not_found(clean_db):
    assert services.estimate_cancellation_outcome(999999, "cancel") is None


def test_checkout_override_paths(unlocked):
    rid = make_reservation(deposit=0.0)
    services.update_reservation_status(rid, "Checked In")
    services.set_setting("checkout_requires_payment", "true")
    services.set_setting("checkout_unpaid_threshold_egp", "0.01")
    needs, bal, thresh = services.checkout_requires_override(rid)
    assert needs is True and bal > thresh
    with pytest.raises(ValueError, match="requires override"):
        services.checkout_reservation(rid, override=False)
    with pytest.raises(PermissionError):
        services.checkout_reservation(rid, override=True, password="wrong")
    res = services.checkout_reservation(rid, override=True, password="testpass")
    assert res["override"] is True and res["rid"] == rid
    assert services.get_reservation(rid)["status"] == "Checked Out"
    services.set_setting("checkout_requires_payment", "false")


def test_add_pos_item_invalid_branches(clean_db):
    rid = make_reservation()
    services.add_pos_item(rid, "Coerced", 1, 5.0, inv_id="bad")  # type: ignore
    with pytest.raises(ValueError, match="Invalid reservation ID"):
        services.add_pos_item("x", "Bad", 1, 1.0)  # type: ignore
    with pytest.raises(ValueError, match="not found"):
        services.add_pos_item(999999, "Missing", 1, 1.0)


def test_complete_walkin_sale_and_receipts(clean_db, fixed_now, monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="Cart is empty"):
        services.complete_walkin_sale([], "Cash")
    with pytest.raises(ValueError, match="payment method"):
        services.complete_walkin_sale([{"desc": "A", "qty": 1, "price": 10}], "  ")

    orig_add = services._add_pos_item_tx
    def zero_gross(conn, reservation_id, desc, qty, price, inv_id=None, *a, **k):
        r = orig_add(conn, reservation_id, desc, qty, price, inv_id, *a, **k)
        r["gross"] = 0.0
        return r
    monkeypatch.setattr(services, "_add_pos_item_tx", zero_gross)
    with pytest.raises(ValueError, match="total must be positive"):
        services.complete_walkin_sale([{"desc": "A", "qty": 1, "price": 1.0}], "Cash")
    monkeypatch.setattr(services, "_add_pos_item_tx", orig_add)

    result = services.complete_walkin_sale(
        [{"desc": "Water", "qty": 2, "price": 5.0}, {"desc": "Alt", "qty": 1, "price": 1.0}],
        "Cash", "note", "Payment")
    assert result["payment_id"] > 0 and len(result["pos_ids"]) == 2
    assert Path(result["receipt_path"]).exists()
    assert "WALK-IN RECEIPT" in result["receipt_text"]
    assert result["receipt_name"].endswith(".txt")

    monkeypatch.setattr(services, "save_walkin_receipt_file", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk")))
    res2 = services.complete_walkin_sale([{"desc": "Juice", "qty": 1, "price": 3.0}], "Card")
    assert res2["receipt_path"] == "" and res2["receipt_name"] == ""


def test_receipt_text_branches_and_invoice_address(clean_db, fixed_now):
    text = services.generate_walkin_receipt_text(
        [{"item_description": "Alt", "quantity": 2, "unit_price": 4.0, "gross": 0},
         {"desc": "Weird", "qty": object(), "price": 1.0, "gross": 0}],
        9.0, "Cash", payment_id=None)
    assert "Alt" in text and "Walk-in" in text and "TOTAL" in text
    path0 = services.save_walkin_receipt_file("hi", None)
    assert Path(path0).exists() and "Pay0" in path0

    services.set_setting("property_address", "Line1\nLine2")
    rid = make_reservation()
    inv = services.generate_invoice_text(rid)
    assert "Line1" in inv and "Line2" in inv


def test_inventory_logs(clean_db):
    logs = services.get_recent_inventory_logs(limit=5)
    assert isinstance(logs, list)


def test_report_pl_expense_category_iteration(clean_db, unlocked):
    cats = services.get_expense_categories()
    services.record_operational_expense(cats[0]["id"], "Fix", 12.0, 0.0, "", "Cash")
    txt = services.report_pl_statement("2000-01-01", "2099-12-31")
    assert "TOTAL EXPENSES" in txt and "12.00" in txt


# ── walk-in lists / integrity ──────────────────────────────────────────────

def test_list_walkin_helpers(clean_db, fixed_now):
    assert services.list_walkin_pos(limit=10) == [] or isinstance(services.list_walkin_pos(limit=10), list)
    assert isinstance(services.list_walkin_payments(limit=10), list)
    services.complete_walkin_sale([{"desc": "W", "qty": 1, "price": 4.0}], "Cash")
    pos = services.list_walkin_pos(limit=5)
    pay = services.list_walkin_payments(limit=5)
    assert len(pos) >= 1 and len(pay) >= 1


def test_verify_ledger_integrity_clean_and_issue_branches(clean_db, monkeypatch):
    services.complete_walkin_sale([{"desc": "W", "qty": 1, "price": 4.0}], "Cash")
    rid = make_reservation()
    services.add_pos_item(rid, "Item", 1, 5.0)
    rep = services.verify_ledger_integrity()
    assert rep["ok"] is True
    assert "stats" in rep and rep["note"]

    # orphan ledger POS ref
    _raw_exec("INSERT INTO general_ledger (at, ref_type, ref_id, entry_type, category, net, service, vat, city_tax, gross, method, description, reverses_id) VALUES (datetime('now'), 'POS', 999991, 'CHARGE', 'F&B', 1,0,0,0,1,'','x',NULL)", fk_off=True)
    rep = services.verify_ledger_integrity()
    assert any(i["type"] == "orphan_ledger_pos_ref" for i in rep["issues"])

    _raw_exec("INSERT INTO general_ledger (at, ref_type, ref_id, entry_type, category, net, service, vat, city_tax, gross, method, description, reverses_id) VALUES (datetime('now'), 'RES', 999992, 'CHARGE', 'Accommodation', 1,0,0,0,1,'','x',NULL)", fk_off=True)
    rep = services.verify_ledger_integrity()
    assert any(i["type"] == "orphan_ledger_res_ref" for i in rep["issues"])

    _raw_exec("INSERT INTO general_ledger (at, ref_type, ref_id, entry_type, category, net, service, vat, city_tax, gross, method, description, reverses_id) VALUES (datetime('now'), 'PAY', 999993, 'PAYMENT', '', 0,0,0,0,1,'Cash','x',NULL)", fk_off=True)
    rep = services.verify_ledger_integrity()
    assert any(i["type"] == "orphan_ledger_payment_ref" for i in rep["issues"])

    _raw_exec("INSERT INTO pos_tabs (reservation_id, inventory_item_id, item_description, quantity, unit_price, tax_amount, cost_at_sale) VALUES (999994, NULL, 'orphan', 1, 1, 0, 0)", fk_off=True)
    rep = services.verify_ledger_integrity()
    assert any(i["type"] == "orphan_pos_reservation" for i in rep["issues"])

    # balance outlier warning
    monkeypatch.setattr(services, "calculate_guest_balance", lambda _rid: 10**9)
    rep = services.verify_ledger_integrity()
    assert any(getattr(w, "get", lambda *_: None)("type") == "balance_outlier_sample" or (isinstance(w, dict) and w.get("type") == "balance_outlier_sample") for w in rep["warnings"])


def test_verify_ledger_integrity_exception_branches(clean_db, monkeypatch):
    real_connect = db.connect

    class WrapConn:
        def __init__(self, conn, mode):
            self._c = conn
            self._mode = mode
        def execute(self, sql, *a, **k):
            s = " ".join(str(sql).split())
            if self._mode == "stat" and "SELECT COUNT(*) FROM general_ledger" in s:
                raise RuntimeError("statfail")
            if self._mode == "pos" and "WHERE l.ref_type='POS'" in s and "NOT IN (SELECT id FROM pos_tabs)" in s:
                raise RuntimeError("posfail")
            if self._mode == "res" and "WHERE l.ref_type='RES'" in s and "NOT IN (SELECT id FROM reservations)" in s:
                raise RuntimeError("resfail")
            if self._mode == "pay" and "IN ('PAY','WALKIN_PAY')" in s and "NOT IN (SELECT id FROM payments)" in s:
                raise RuntimeError("payfail")
            if self._mode == "link" and "FROM pos_tabs p" in s and "p.reservation_id NOT IN (SELECT id FROM reservations)" in s:
                raise RuntimeError("linkfail")
            if self._mode == "bal" and "SELECT id FROM reservations WHERE status IN" in s:
                raise RuntimeError("balfail")
            return self._c.execute(sql, *a, **k)
        def __getattr__(self, n):
            return getattr(self._c, n)

    for mode, needle in [
        ("stat", "stat ledger_rows"), ("pos", "pos ref check"), ("res", "res ref check"),
        ("pay", "payment ref check"), ("link", "pos reservation check"), ("bal", "balance sample"),
    ]:
        monkeypatch.setattr(db, "connect", lambda m=mode: WrapConn(real_connect(), m))
        rep = services.verify_ledger_integrity()
        assert any(needle in str(w) for w in rep["warnings"]), (mode, rep["warnings"])
    monkeypatch.setattr(db, "connect", real_connect)


# ── analytics / export / demo ──────────────────────────────────────────────

def test_analytics_presets_and_metadata(clean_db, fixed_now):
    presets = services.list_analytics_presets()
    assert presets and any(p["key"] == "ops_daily" for p in presets)
    md = services._export_metadata("ops_daily", "2026-01-01", "2026-01-31", extra={"x": 1})
    assert md["preset"] == "ops_daily" and md["x"] == 1 and md["currency"] == services.CURRENCY


def test_analytics_datasets_and_dispatch(clean_db, fixed_now):
    start, end = "2026-06-01", "2026-06-15"
    # seed some data for richer branches
    rid = make_reservation(ci="2026-06-10", co="2026-06-12", rate=100.0)
    services.record_payment(rid, 50.0, "Cash", "part")
    cats = services.get_expense_categories()
    services.record_operational_expense(cats[0]["id"], "Ops", 30.0, 0.0, "", "Cash")
    make_reservation(guest="AR Guest", room_id=2, ci="2026-06-11", co="2026-06-13", rate=100.0, deposit=0.0)

    assert "period" in services.dataset_financial_summary(start, end)
    assert isinstance(services.dataset_financial_daily(start, end), list)
    assert isinstance(services.dataset_occupancy_daily(start, end), list)
    assert isinstance(services.dataset_payment_methods(start, end), list)
    ds = services.dataset_decision_support_v1(start, end)
    assert "kpis" in ds and "signals" in ds
    assert any(s["type"] == "cash" for s in ds["signals"])  # AR signal
    # negative profit / low occupancy signals: may or may not trigger depending on seeded data; force with monkeypatch
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(services, "dataset_financial_daily", lambda s, e: [{"date":"2026-06-01","revenue_net_plus_service":1,"expenses_plus_cogs":2,"profit":-1}])
    monkeypatch.setattr(services, "dataset_occupancy_daily", lambda s, e: [{"date":"2026-06-01","occupancy_pct":10}])
    monkeypatch.setattr(services, "get_general_ledger_summary", lambda: {"ar_balance": 5, "net_cash": 1})
    ds2 = services.dataset_decision_support_v1(start, end)
    assert any(s["type"] == "profit" for s in ds2["signals"])
    assert any(s["type"] == "occupancy" for s in ds2["signals"])
    monkeypatch.undo()

    ml = services.dataset_ml_features_v1(start, end)
    assert isinstance(ml, list)

    for name in ["financial_summary","financial_daily","occupancy","occupancy_daily","payment_methods",
                 "decision_support_v1","ml_features_v1","pl","cash_flow","vat","daily_ops"]:
        assert services._dataset_by_name(name, start, end) is not None
    assert services._dataset_by_name("nope", start, end) is None


def test_write_csv_and_export_branches(clean_db, fixed_now, tmp_path, monkeypatch):
    # monkeypatch analytics export dir to tmp
    analytics_dir = tmp_path / "analytics"
    analytics_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(services, "ANALYTICS_EXPORT_DIR", analytics_dir)
    p = analytics_dir / "empty.csv"
    services._write_csv(p, [])
    assert p.read_text() == ""
    p2 = analytics_dir / "rows.csv"
    services._write_csv(p2, [{"a": 1, "b": 2}, {"b": 3}])
    txt = p2.read_text()
    assert "a" in txt and "b" in txt
    p3 = analytics_dir / "other.csv"
    services._write_csv(p3, [1, 2, 3])
    assert "1" in p3.read_text()

    with pytest.raises(ValueError, match="Unknown analytics preset"):
        services.export_analytics_preset("nope", "2026-01-01", "2026-01-02")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        services.export_analytics_preset("ops_daily", "bad", "2026-01-02")

    # export with formats including csv, exercising dict/list flattening
    make_reservation(ci="2026-06-10", co="2026-06-12")
    out = services.export_analytics_preset("finance_monthly", "2026-06-01", "2026-06-15", formats=["json", "txt", "csv"])
    assert "json" in out["outputs"] and "txt" in out["outputs"]
    assert Path(out["outputs"]["json"]).exists()
    assert Path(out["outputs"]["txt"]).exists()
    assert "csv" in out["outputs"]

    # force dict-with-nested-list and simple-row csv branches via custom dataset behavior
    orig = services._dataset_by_name
    def fake_dataset(name, s, e):
        if name == "financial_summary":
            return {"nested": [{"x": 1}], "scalar": 5, "plain": {"y": 2}}
        return orig(name, s, e)
    monkeypatch.setattr(services, "_dataset_by_name", fake_dataset)
    spec = {"version":"1","label":"x","description":"x","datasets":["financial_summary"],"formats":["csv"]}
    monkeypatch.setitem(services.ANALYTICS_PRESETS, "cov_csv", spec)
    try:
        out2 = services.export_analytics_preset("cov_csv", "2026-06-01", "2026-06-15", formats=["csv"])
        assert "csv" in out2["outputs"] and out2["outputs"]["csv"]
    finally:
        services.ANALYTICS_PRESETS.pop("cov_csv", None)
        monkeypatch.setattr(services, "_dataset_by_name", orig)

    # generate_unified_report defaults
    rep = services.generate_unified_report(preset="ops_daily", start="2026-06-01", end="2026-06-15", formats=["json"])
    assert rep["metadata"]["preset"] == "ops_daily"
    rep2 = services.generate_unified_report(preset="ops_daily", start=None, end=None, formats=["json"])
    assert rep2["metadata"]["date_start"]


# ── demo presets ───────────────────────────────────────────────────────────

def test_demo_preset_guards_and_flow(clean_db, fixed_now, monkeypatch):
    assert services.list_demo_data_presets()
    with pytest.raises(PermissionError):
        services.apply_demo_data_preset("analysis_small", "wrong", "APPLY DEMO DATA")
    with pytest.raises(ValueError, match="Unknown demo preset"):
        services.apply_demo_data_preset("nope", "testpass", "APPLY DEMO DATA")
    with pytest.raises(ValueError, match="Confirmation text"):
        services.apply_demo_data_preset("analysis_small", "testpass", "nope")

    # operational data blocks
    make_reservation()
    with pytest.raises(ValueError, match="already has operational data"):
        services.apply_demo_data_preset("analysis_small", "testpass", "APPLY DEMO DATA")
    # clear operational data for next checks
    _raw_exec("DELETE FROM reservations", fk_off=True)
    _raw_exec("DELETE FROM payments", fk_off=True)
    _raw_exec("DELETE FROM pos_tabs", fk_off=True)
    _raw_exec("DELETE FROM operational_expenses", fk_off=True)
    _raw_exec("DELETE FROM general_ledger", fk_off=True)

    # allow_existing path: wrong password still rejected (single verification point)
    with pytest.raises(PermissionError):
        services.apply_demo_data_preset("analysis_small", "wrong", "APPLY DEMO DATA", allow_existing=True)

    # no rooms branch
    _raw_exec("DELETE FROM reservations", fk_off=True)
    _raw_exec("DELETE FROM payments", fk_off=True)
    _raw_exec("DELETE FROM pos_tabs", fk_off=True)
    _raw_exec("DELETE FROM operational_expenses", fk_off=True)
    _raw_exec("DELETE FROM rooms", fk_off=True)
    with pytest.raises(ValueError, match="No rooms available"):
        services.apply_demo_data_preset("analysis_small", "testpass", "APPLY DEMO DATA")


def test_demo_preset_success_and_exception_swallow(clean_db, fixed_now, monkeypatch):
    # fresh db from fixture has no operational data
    orig_update = services.update_reservation_status
    def flaky_update(rid, status):
        if status == "Checked In":
            raise RuntimeError("boom")
        return orig_update(rid, status)
    monkeypatch.setattr(services, "update_reservation_status", flaky_update)
    result = services.apply_demo_data_preset("analysis_small", "testpass", "APPLY DEMO DATA")
    assert result["preset"] == "analysis_small"
    assert result["created"]["reservations"] >= 1
    monkeypatch.setattr(services, "update_reservation_status", orig_update)

    # second run needs allow_existing; trigger checkout exception branch, avoid date conflicts
    services.set_setting("allow_overbooking", "true")
    def flaky_checkout(rid, status):
        r = services.get_reservation(rid)
        if status == "Checked Out" and r and r["status"] == "Checked In":
            raise RuntimeError("boom2")
        return orig_update(rid, status)
    monkeypatch.setattr(services, "update_reservation_status", flaky_checkout)
    result2 = services.apply_demo_data_preset("analysis_small", "testpass", "APPLY DEMO DATA", allow_existing=True)
    assert result2["created"]["reservations"] >= 1
    services.set_setting("allow_overbooking", "false")
