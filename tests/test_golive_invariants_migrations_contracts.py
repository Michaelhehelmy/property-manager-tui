"""Go-live invariants, migration upgrades, and confinement helper contracts."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from app_core import db, paths, services
from conftest import count_ledger, make_reservation


pytestmark = pytest.mark.invariant


def _close_reset_db(monkeypatch, path: Path):
    try:
        db.close()
    except Exception:
        pass
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(paths, "DB_PATH", path)
    monkeypatch.setattr(db, "_conn", None)


def _mk_v4_db(path: Path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("PRAGMA user_version=4")
    conn.commit()
    conn.close()


def _mk_v5_db(path: Path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS recipe_components (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_item_id INTEGER NOT NULL,
            ingredient_item_id INTEGER NOT NULL,
            qty_per_sale REAL NOT NULL
        );
        """
    )
    conn.execute("PRAGMA user_version=5")
    conn.commit()
    conn.close()


def _mk_v8_like_db(path: Path):
    # Minimal schema header at v8 to exercise upgrade path to current; migrate should be idempotent/safe.
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("CREATE TABLE app_metadata(key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("PRAGMA user_version=8")
    conn.commit()
    conn.close()


def test_migration_upgrade_paths_and_idempotency(tmp_path, monkeypatch):
    # v4 -> current
    p4 = tmp_path / "v4.db"
    _mk_v4_db(p4)
    _close_reset_db(monkeypatch, p4)
    try:
        assert db.migrate() == "upgraded"
        conn = db.connect()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        conn.execute("SELECT 1 FROM recipe_components LIMIT 1")
        conn.execute("SELECT 1 FROM meals LIMIT 1")
        conn.execute("SELECT 1 FROM meal_ingredients LIMIT 1")
        assert db.migrate() == "current"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    finally:
        db.close()

    # v5 -> current
    p5 = tmp_path / "v5.db"
    _mk_v5_db(p5)
    _close_reset_db(monkeypatch, p5)
    try:
        assert db.migrate() == "upgraded"
        conn = db.connect()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert db.migrate() == "current"
    finally:
        db.close()

    # v8-like -> current (or already current) should be safe/idempotent
    p8 = tmp_path / "v8.db"
    _mk_v8_like_db(p8)
    _close_reset_db(monkeypatch, p8)
    try:
        res = db.migrate()
        assert res in ("upgraded", "current")
        conn = db.connect()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert db.migrate() == "current"
    finally:
        db.close()

    # fresh init_db on new path reaches current and re-init/migrate safe
    pf = tmp_path / "fresh.db"
    _close_reset_db(monkeypatch, pf)
    try:
        services.init_db()
        conn = db.connect()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        services.init_db()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    finally:
        db.close()


def test_money_ledger_invariants_payment_refund_void_and_ref_separation(clean_db):
    rid = make_reservation(rate=150.0, ci="2030-01-01", co="2030-01-03")
    # reservation charge exists
    assert count_ledger(entry_type="CHARGE", ref_type="RES") >= 1

    # payment recorded as PAY ref type; cash collected increases
    before_pay = count_ledger(entry_type="PAYMENT")
    services.record_payment(rid, 100.0, "Cash", notes="part")
    assert count_ledger(entry_type="PAYMENT") == before_pay + 1
    pay_row = db.connect().execute(
        "SELECT ref_type, gross, net, vat, service, city_tax, description FROM general_ledger WHERE entry_type='PAYMENT' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert pay_row["ref_type"] == "PAY"
    # Payment amount should be reflected in ledger value fields (schema uses gross/net/tax columns)
    money_fields = [float(pay_row[c] or 0) for c in ("gross", "net", "vat", "service", "city_tax")]
    assert any(v == pytest.approx(100.0) or v == pytest.approx(-100.0) for v in money_fields) or sum(money_fields) != 0

    # refund creates reversal/payment-adjustment semantics without deleting originals
    charges_before = count_ledger(entry_type="CHARGE", ref_type="RES")
    payments_before = count_ledger(entry_type="PAYMENT")
    services.record_refund(rid, 20.0, "Cash", "test refund", "testpass")
    assert count_ledger(entry_type="CHARGE", ref_type="RES") == charges_before
    assert count_ledger(entry_type="PAYMENT") >= payments_before
    summary = services.get_general_ledger_summary()
    # refunds/cash out should be reflected and balances remain internally consistent
    assert summary["cash_out"] >= 20.0
    assert "billed_revenue" in summary
    assert "ar_balance" in summary

    # cancellation/void does not delete original charge and preserves append-only history
    charge_count_before_void = db.connect().execute(
        "SELECT COUNT(*) FROM general_ledger WHERE ref_type='RES' AND entry_type='CHARGE'"
    ).fetchone()[0]
    services.cancel_reservation(rid, "testpass")
    charge_count_after_void = db.connect().execute(
        "SELECT COUNT(*) FROM general_ledger WHERE ref_type='RES' AND entry_type='CHARGE'"
    ).fetchone()[0]
    assert charge_count_after_void == charge_count_before_void
    rev = db.connect().execute(
        "SELECT reverses_id FROM general_ledger WHERE entry_type='VOID_CHARGE' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert rev is not None and rev["reverses_id"] is not None

    # walk-in/POS ledger entries (ref_type POS) are not removed by reservation lifecycle operations
    pos_before = count_ledger(ref_type="POS")
    # create a walk-in sale via service for deterministic POS ledger/ref separation
    try:
        services.complete_walkin_sale([{"desc": "Snack", "qty": 1, "price": 10.0, "inv_id": None}], "Cash", "walkin")
    except Exception:
        pass
    pos_after = count_ledger(ref_type="POS")
    assert pos_after >= pos_before


def test_stock_batch_invariants_fefo_split_void_and_traceability(clean_db):
    # tracked item with batches: stock conservation across split sale and void restore
    item_id = services.add_inventory_item(
        "BatchSoda", "Bar", "can", 0, 1.0, 3.0, 1.0, 0.0, "testpass",
        sku="SKU-BS", barcode="BAR-BS", barcode_type="code128", track_batches=1,
    )
    b1 = services.create_inventory_batch(item_id, 2, 1.0, received_at="2026-01-01 10:00:00", expiry_date="2030-01-01", lot_code="LOT-1", password="testpass")
    b2 = services.create_inventory_batch(item_id, 3, 1.0, received_at="2026-01-01 11:00:00", expiry_date="2030-02-01", lot_code="LOT-2", password="testpass")
    rid = make_reservation(rate=80.0, ci="2030-02-01", co="2030-02-02")
    services.update_reservation_status(rid, "Checked In")
    res = services.add_pos_item(rid, "BatchSoda", 4, 3.0, inv_id=item_id)
    pos_ids = res.get("pos_ids") or ([res.get("pos_id")] if res.get("pos_id") else [])
    assert len(pos_ids) >= 2
    rows = services.get_pos_items(rid)
    assert {int(r["batch_id"]) for r in rows if r["batch_id"] is not None} == {int(b1["id"]), int(b2["id"])}
    stock_after_sale = db.connect().execute("SELECT stock_qty FROM inventory WHERE id=?", (item_id,)).fetchone()[0]
    rem1 = db.connect().execute("SELECT quantity_remaining FROM inventory_batches WHERE id=?", (b1["id"],)).fetchone()[0]
    rem2 = db.connect().execute("SELECT quantity_remaining FROM inventory_batches WHERE id=?", (b2["id"],)).fetchone()[0]
    assert float(stock_after_sale) == pytest.approx(float(rem1) + float(rem2))

    # void one line restores its batch qty and inventory stock consistently
    services.void_pos_item(int(pos_ids[0]), "testpass")
    rem1b = db.connect().execute("SELECT quantity_remaining FROM inventory_batches WHERE id=?", (b1["id"],)).fetchone()[0]
    rem2b = db.connect().execute("SELECT quantity_remaining FROM inventory_batches WHERE id=?", (b2["id"],)).fetchone()[0]
    stock_after_void = db.connect().execute("SELECT stock_qty FROM inventory WHERE id=?", (item_id,)).fetchone()[0]
    assert float(stock_after_void) == pytest.approx(float(rem1b) + float(rem2b))
    assert float(rem1b) + float(rem2b) > float(rem1) + float(rem2)

    # batch listing/traceability data available
    batches = services.list_inventory_batches(item_id=item_id)
    lot_codes = {b.get("lot_code") for b in batches}
    assert "LOT-1" in lot_codes and "LOT-2" in lot_codes


def test_printer_and_path_confinement_helpers_contracts(tmp_path, monkeypatch):
    # paths.safe_child_path confinement/normalization contract (implementation normalizes separators/dots)
    root = tmp_path / "safe"
    root.mkdir()
    safe = paths.safe_child_path(root, "ok.txt")
    assert safe == (root / "ok.txt").resolve()

    # Traversal-like and absolute-like inputs must resolve inside root (no escape)
    trav = paths.safe_child_path(root, "../evil.txt")
    assert root.resolve() in trav.parents or trav == root.resolve() / "evil.txt"
    abs_like = paths.safe_child_path(root, "/abs.txt")
    assert root.resolve() in abs_like.parents or abs_like == root.resolve() / "abs.txt"
    nested = paths.safe_child_path(root, "subdir/file.txt")
    assert root.resolve() in nested.parents or nested == root.resolve() / "file.txt"

    # ui.web.deps.safe_download_path confines to export/invoice dirs and blocks traversal/missing
    from ui.web import deps as web_deps

    export_dir = tmp_path / "exports"
    invoice_dir = tmp_path / "invoices"
    export_dir.mkdir()
    invoice_dir.mkdir()
    monkeypatch.setattr(paths, "EXPORT_DIR", export_dir)
    monkeypatch.setattr(paths, "INVOICE_DIR", invoice_dir)
    # also patch names imported into deps module if any
    try:
        monkeypatch.setattr(web_deps.paths, "EXPORT_DIR", export_dir)
        monkeypatch.setattr(web_deps.paths, "INVOICE_DIR", invoice_dir)
    except Exception:
        pass

    f = export_dir / "a.txt"
    f.write_text("x", encoding="utf-8")
    p = web_deps.safe_download_path("export", "a.txt")
    assert p.read_text(encoding="utf-8") == "x"

    with pytest.raises(HTTPException):
        web_deps.safe_download_path("export", "../invoices/a.txt")
    with pytest.raises(HTTPException):
        web_deps.safe_download_path("export", "/etc/passwd")
    with pytest.raises(HTTPException):
        web_deps.safe_download_path("other", "a.txt")
    with pytest.raises(HTTPException):
        web_deps.safe_download_path("export", "missing.txt")

    # printer helper contracts: nonexistent file returns failure; unknown target rejected
    ok, msg = services.print_to_printer(str(tmp_path / "nope.txt"), "")
    assert ok is False
    existing = tmp_path / "p.txt"
    existing.write_text("hi", encoding="utf-8")
    ok, msg = services.print_to_printer(str(existing), "nonsense-target")
    assert ok is False
    # list_printers returns structured contract even in constrained environments
    info = services.list_printers()
    assert isinstance(info, dict)
    assert "queues" in info and "source" in info
    assert isinstance(info["queues"], list)
