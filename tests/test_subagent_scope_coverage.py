"""Targeted coverage for subagent scope: migrations, batches/FEFO/expiry,
void/restore, recipe/COGS traceability, seed/demo guards, integrity/doctor, updater.

Deterministic, tmp_path/monkeypatch, no network/external deps.
"""
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app_core import db, doctor, paths, services, updater


# ── helpers ──────────────────────────────────────────────────────────────

def _patch_db(tmp_path, monkeypatch):
    try:
        db.close()
    except Exception:
        pass
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "BASE_DIR", tmp_path)
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "property_manager.db")
    monkeypatch.setattr(paths, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(paths, "INVOICE_DIR", tmp_path / "exports" / "invoices")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "property_manager.db", raising=False)
    monkeypatch.setattr(db, "_conn", None, raising=False)
    monkeypatch.setattr(services, "EXPORT_DIR", tmp_path / "exports", raising=False)
    monkeypatch.setattr(services, "INVOICE_DIR", tmp_path / "exports" / "invoices", raising=False)
    monkeypatch.setattr(services, "ANALYTICS_EXPORT_DIR", tmp_path / "exports" / "analytics", raising=False)
    monkeypatch.setattr(services, "_session", {"unlocked": False, "last_activity": None}, raising=False)
    services.set_language("en")


def _init_fresh(tmp_path, monkeypatch):
    _patch_db(tmp_path, monkeypatch)
    services.init_db()
    with db.transaction() as conn:
        conn.execute(
            "DELETE FROM app_metadata WHERE key IN ('super_password_hash','super_password_salt','failed_attempts','lockout_until','recovery_codes')"
        )
    services.set_super_password("secret123")
    return tmp_path


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    _init_fresh(tmp_path, monkeypatch)
    yield tmp_path
    try:
        db.close()
    except Exception:
        pass
    db._conn = None


def _mk_tracked_item(name="ItemX", stock=0.0, cost=2.0, price=5.0):
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO inventory (item_name, category, stock_qty, unit, unit_cost, selling_price, track_batches, barcode, sku, barcode_type, active)
               VALUES (?, 'Bar', ?, 'unit', ?, ?, 1, ?, ?, 'code128', 1)""",
            (name, float(stock), float(cost), float(price), f"BAR-{name}", f"SKU-{name}"),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _mk_reservation(status="Checked In"):
    room_id = db.connect().execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
    with db.transaction() as conn:
        conn.execute("INSERT INTO guests (canonical_name) VALUES ('Scope Guest')")
        gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO reservations (guest_id, guest_name, room_id, agreed_nightly_rate, check_in_date, check_out_date, status)
               VALUES (?, 'Scope Guest', ?, 100.0, '2026-01-01', '2026-01-03', ?)""",
            (gid, room_id, status),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ── v8 -> v9 migration fixtures/idempotency ─────────────────────────────

def _make_v8_db(path):
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        PRAGMA foreign_keys=OFF;
        CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL,
            stock_qty REAL NOT NULL DEFAULT 0,
            unit TEXT NOT NULL,
            unit_cost REAL NOT NULL DEFAULT 0,
            selling_price REAL NOT NULL DEFAULT 0,
            low_stock_threshold REAL,
            overstock_threshold REAL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE inventory_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            change_type TEXT NOT NULL,
            quantity REAL NOT NULL,
            reference TEXT NOT NULL DEFAULT '',
            at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE pos_tabs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_id INTEGER,
            inventory_item_id INTEGER,
            item_description TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            unit_price REAL NOT NULL,
            tax_amount REAL NOT NULL DEFAULT 0,
            cost_at_sale REAL NOT NULL DEFAULT 0,
            voided_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            activity_id INTEGER
        );
        INSERT INTO inventory (id,item_name,category,stock_qty,unit,unit_cost,selling_price,created_at)
            VALUES (1,'LegacyStock','Bar',9,'bottle',3.0,6.0,'2026-01-01 08:00:00');
        INSERT INTO inventory_logs (item_id,change_type,quantity,reference,at)
            VALUES (1,'IN',9,'legacy receive','2026-01-01 09:00:00');
        INSERT INTO inventory (id,item_name,category,stock_qty,unit,unit_cost,selling_price,created_at)
            VALUES (2,'NoStock','Bar',0,'bottle',1.0,2.0,'2026-01-01 08:00:00');
        PRAGMA user_version=8;
        """
    )
    conn.commit()
    conn.close()


def test_v8_to_v9_migration_backfill_is_idempotent(tmp_path, monkeypatch):
    p = tmp_path / "v8.db"
    _make_v8_db(p)
    _patch_db(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "DB_PATH", p)
    monkeypatch.setattr(db, "DB_PATH", p, raising=False)
    db._conn = None
    try:
        assert db.migrate() == "upgraded"
        conn = db.connect()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        inv_cols = {r[1] for r in conn.execute("PRAGMA table_info(inventory)").fetchall()}
        assert {"sku", "barcode", "track_batches"}.issubset(inv_cols)
        log_cols = {r[1] for r in conn.execute("PRAGMA table_info(inventory_logs)").fetchall()}
        assert "batch_id" in log_cols and "unit_cost" in log_cols
        pos_cols = {r[1] for r in conn.execute("PRAGMA table_info(pos_tabs)").fetchall()}
        assert "batch_id" in pos_cols and "lot_code" in pos_cols
        conn.execute("SELECT 1 FROM inventory_batches LIMIT 1")

        def counts():
            b = conn.execute("SELECT COUNT(*) FROM inventory_batches WHERE item_id=1").fetchone()[0]
            l = conn.execute(
                "SELECT COUNT(*) FROM inventory_logs WHERE item_id=1 AND change_type='OPENING' AND reference LIKE '%v9_legacy_backfill%'"
            ).fetchone()[0]
            return b, l

        b1, l1 = counts()
        assert b1 == 1 and l1 == 1
        row = conn.execute(
            "SELECT lot_code, quantity_received, quantity_remaining, unit_cost FROM inventory_batches WHERE item_id=1"
        ).fetchone()
        assert row["lot_code"].startswith("LEGACY-1")
        assert float(row["quantity_received"]) == 9.0
        assert float(row["quantity_remaining"]) == 9.0
        assert float(row["unit_cost"]) == 3.0
        assert conn.execute("SELECT COUNT(*) FROM inventory_batches WHERE item_id=2").fetchone()[0] == 0

        db._migrate_to_9(conn)
        conn.commit()
        assert counts() == (1, 1)
        assert db.migrate() == "current"
        db._migrate_to_9(db.connect())
        db.connect().commit()
        assert counts() == (1, 1)
    finally:
        try:
            db.close()
        except Exception:
            pass
        db._conn = None


# ── batch stock-vs-quantity invariants & FEFO/expiry edge cases ─────────

def test_batch_stock_quantity_invariant_and_fefo_expiry_edges(fresh):
    services.set_setting("allow_negative_stock", "false")
    services.set_setting("block_sell_expired", "true")
    item_id = _mk_tracked_item("MilkEdge")
    b_expired = services.create_inventory_batch(
        item_id, 4, 1.0, received_at="2025-01-01 10:00:00", expiry_date="2025-01-02", password="secret123"
    )
    b_soon = services.create_inventory_batch(
        item_id, 5, 1.0, received_at="2030-01-02 10:00:00", expiry_date="2030-02-01", password="secret123"
    )
    b_later = services.create_inventory_batch(
        item_id, 6, 1.0, received_at="2030-01-01 10:00:00", expiry_date="2030-12-01", password="secret123"
    )
    conn = db.connect()
    # receive creates matching stock/log invariants
    stock = float(conn.execute("SELECT stock_qty FROM inventory WHERE id=?", (item_id,)).fetchone()[0])
    batch_sum = float(
        conn.execute("SELECT COALESCE(SUM(quantity_remaining),0) FROM inventory_batches WHERE item_id=?", (item_id,)).fetchone()[0]
    )
    assert abs(stock - batch_sum) < 1e-6 and stock == 15.0

    # FEFO/expiry selection via batch allocator (edge cases): expired blocked, earliest expiry first, splits across batches
    allocs = services._select_batches_for_sale(conn, item_id, 3)
    assert [a[0]["id"] for a in allocs] == [b_soon["id"]]
    assert float(allocs[0][1]) == 3.0
    allocs2 = services._select_batches_for_sale(conn, item_id, 8)
    assert [a[0]["id"] for a in allocs2] == [b_soon["id"], b_later["id"]]
    assert [float(a[1]) for a in allocs2] == [5.0, 3.0]

    # cannot allocate more than non-expired remaining
    with pytest.raises(ValueError):
        services._select_batches_for_sale(conn, item_id, 20)
    # specific expired batch selection blocked
    with pytest.raises(ValueError):
        services._select_batches_for_sale(conn, item_id, 1, specific_batch_id=b_expired["id"])

    # direct invariant: stock still equals total remaining after reads
    stock2 = float(conn.execute("SELECT stock_qty FROM inventory WHERE id=?", (item_id,)).fetchone()[0])
    batch_sum2 = float(
        conn.execute("SELECT COALESCE(SUM(quantity_remaining),0) FROM inventory_batches WHERE item_id=?", (item_id,)).fetchone()[0]
    )
    assert abs(stock2 - batch_sum2) < 1e-6
    integ = services.verify_ledger_integrity()
    assert "stock_batch_drift" not in [i.get("type") for i in (integ.get("issues") or [])]


# ── void/restore and recipe/COGS traceability ───────────────────────────

def test_void_restore_and_recipe_cogs_traceability(fresh):
    services.set_setting("allow_negative_stock", "false")
    conn = db.connect()
    item_id = _mk_tracked_item("ColaTrace")
    b1 = services.create_inventory_batch(item_id, 3, 1.0, received_at="2030-01-01 10:00:00", expiry_date="2030-06-01", password="secret123")
    rid = _mk_reservation()

    # Simulate a traced batch sale (POS line + stock/batch deduction + charge/COGS ledger) then void/restore via services/db APIs.
    with db.transaction() as c:
        c.execute("UPDATE inventory SET stock_qty = stock_qty - ? WHERE id=?", (2, item_id))
        c.execute("UPDATE inventory_batches SET quantity_remaining = quantity_remaining - ? WHERE id=?", (2, b1["id"]))
        c.execute(
            "INSERT INTO pos_tabs (reservation_id, inventory_item_id, item_description, quantity, unit_price, tax_amount, cost_at_sale, batch_id, lot_code) VALUES (?,?,?,?,?,?,?,?,?)",
            (rid, item_id, "ColaTrace [lot]", 2, 3.0, 0.0, 1.0, b1["id"], b1["lot_code"]),
        )
        pos_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute(
            "INSERT INTO general_ledger (at, ref_type, ref_id, entry_type, category, net, service, vat, city_tax, gross, method, description, reverses_id, actor) VALUES (datetime('now'),?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("POS", pos_id, "CHARGE", "F&B", 6.0, 0.0, 0.0, 0.0, 6.0, "", "pos charge", None, ""),
        )
        c.execute(
            "INSERT INTO general_ledger (at, ref_type, ref_id, entry_type, category, net, service, vat, city_tax, gross, method, description, reverses_id, actor) VALUES (datetime('now'),?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("POS", pos_id, "COGS", "COGS", 2.0, 0.0, 0.0, 0.0, 2.0, "", "pos cogs", None, ""),
        )
    assert float(conn.execute("SELECT stock_qty FROM inventory WHERE id=?", (item_id,)).fetchone()[0]) == 1.0
    assert float(conn.execute("SELECT quantity_remaining FROM inventory_batches WHERE id=?", (b1["id"],)).fetchone()[0]) == 1.0

    services.void_pos_item(pos_id, "secret123")
    assert float(conn.execute("SELECT stock_qty FROM inventory WHERE id=?", (item_id,)).fetchone()[0]) == 3.0
    assert float(conn.execute("SELECT quantity_remaining FROM inventory_batches WHERE id=?", (b1["id"],)).fetchone()[0]) == 3.0
    row = conn.execute("SELECT voided_at FROM pos_tabs WHERE id=?", (pos_id,)).fetchone()
    assert row["voided_at"] is not None
    assert conn.execute(
        "SELECT COUNT(*) FROM general_ledger WHERE ref_type='POS' AND ref_id=? AND reverses_id IS NOT NULL", (pos_id,)
    ).fetchone()[0] >= 2
    assert conn.execute(
        "SELECT COUNT(*) FROM inventory_logs WHERE item_id=? AND reference LIKE ?", (item_id, f"void POS #{pos_id}%")
    ).fetchone()[0] >= 1

    # Recipe/COGS traceability (data-level, testable via db/services APIs): components, COGS ledger, ingredient deduction logs/batches.
    rum_id = _mk_tracked_item("RumIng", cost=0.02)
    lime_id = _mk_tracked_item("LimeIng", cost=0.01)
    with db.transaction() as c:
        c.execute("INSERT INTO inventory (item_name, category, stock_qty, unit, unit_cost, selling_price, track_batches, active) VALUES ('CocktailMenu','Cocktails',0,'glass',0,12.0,0,1)")
        menu_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute("INSERT INTO recipe_components (menu_item_id, ingredient_item_id, qty_per_sale) VALUES (?,?,?)", (menu_id, rum_id, 50))
        c.execute("INSERT INTO recipe_components (menu_item_id, ingredient_item_id, qty_per_sale) VALUES (?,?,?)", (menu_id, lime_id, 20))
    services.create_inventory_batch(rum_id, 200, 0.02, received_at="2030-01-01 10:00:00", expiry_date="2031-01-01", password="secret123")
    services.create_inventory_batch(lime_id, 100, 0.01, received_at="2030-01-01 10:00:00", expiry_date="2031-01-01", password="secret123")
    comps = services.get_recipe_components(menu_id)
    assert {c["ingredient_item_id"] for c in comps} == {rum_id, lime_id}

    rid2 = _mk_reservation()
    with db.transaction() as c:
        c.execute("INSERT INTO pos_tabs (reservation_id, inventory_item_id, item_description, quantity, unit_price, tax_amount, cost_at_sale) VALUES (?,?,?,?,?,?,?)",
                  (rid2, menu_id, "CocktailMenu", 2, 12.0, 0.0, 0.0))
        rpos = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute("INSERT INTO general_ledger (at, ref_type, ref_id, entry_type, category, net, service, vat, city_tax, gross, method, description, reverses_id, actor) VALUES (datetime('now'),'POS',?,'COGS','COGS',2.4,0,0,0,2.4,'','recipe cogs',NULL,'')", (rpos,))
        c.execute("UPDATE inventory SET stock_qty = stock_qty - 100 WHERE id=?", (rum_id,))
        c.execute("UPDATE inventory SET stock_qty = stock_qty - 40 WHERE id=?", (lime_id,))
        c.execute("UPDATE inventory_batches SET quantity_remaining = quantity_remaining - 100 WHERE item_id=?", (rum_id,))
        c.execute("UPDATE inventory_batches SET quantity_remaining = quantity_remaining - 40 WHERE item_id=?", (lime_id,))
        c.execute("INSERT INTO inventory_logs (item_id, change_type, quantity, reference) VALUES (?, 'OUT_SALE', 100, ?)", (rum_id, f"POS #{rpos} recipe: CocktailMenu x2"))
        c.execute("INSERT INTO inventory_logs (item_id, change_type, quantity, reference) VALUES (?, 'OUT_SALE', 40, ?)", (lime_id, f"POS #{rpos} recipe: CocktailMenu x2"))

    assert float(conn.execute("SELECT stock_qty FROM inventory WHERE id=?", (rum_id,)).fetchone()[0]) == 100.0
    assert float(conn.execute("SELECT stock_qty FROM inventory WHERE id=?", (lime_id,)).fetchone()[0]) == 60.0
    cogs_rows = conn.execute("SELECT COUNT(*) c, COALESCE(SUM(gross),0) g FROM general_ledger WHERE ref_type='POS' AND ref_id=? AND entry_type='COGS'", (rpos,)).fetchone()
    assert cogs_rows["c"] >= 1 and float(cogs_rows["g"]) > 0
    assert conn.execute("SELECT COUNT(*) FROM inventory_logs WHERE reference LIKE ?", (f"POS #{rpos} recipe:%",)).fetchone()[0] >= 2
    assert "stock_batch_drift" not in [i.get("type") for i in (services.verify_ledger_integrity().get("issues") or [])]


# ── seed/demo guards ────────────────────────────────────────────────────

def test_seed_and_demo_guards(fresh):
    with pytest.raises(PermissionError):
        services.apply_demo_data_preset("business_starter", "bad", "APPLY DEMO DATA")
    with pytest.raises(ValueError):
        services.apply_demo_data_preset("business_starter", "secret123", "WRONG")
    result = services.apply_demo_data_preset("business_starter", "secret123", "APPLY DEMO DATA")
    assert result.get("preset") == "business_starter"
    assert services.has_operational_data() is True
    with pytest.raises(ValueError):
        services.apply_demo_data_preset("business_starter", "secret123", "APPLY DEMO DATA")
    exported = services.export_seed_config()
    assert exported
    with pytest.raises(ValueError):
        services.import_seed_config(exported, "secret123", "APPLY SEED CONFIG")


def test_seed_import_requires_correct_confirmation_on_fresh_db(tmp_path, monkeypatch):
    _init_fresh(tmp_path, monkeypatch)
    exported = services.export_seed_config()
    try:
        db.close()
    except Exception:
        pass
    db._conn = None
    Path(paths.DB_PATH).unlink(missing_ok=True)
    _init_fresh(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        services.import_seed_config(exported, "secret123", "WRONG CONFIRM")
    try:
        db.close()
    except Exception:
        pass
    db._conn = None


# ── integrity/doctor helpers ────────────────────────────────────────────

def test_integrity_and_doctor_helpers(fresh):
    integ = services.verify_ledger_integrity()
    assert isinstance(integ, dict) and "issues" in integ and "stats" in integ
    assert "stock_batch_drift" not in [i.get("type") for i in (integ.get("issues") or [])]

    item_id = _mk_tracked_item("DriftA")
    services.create_inventory_batch(item_id, 5, 1.0, received_at="2026-01-01 10:00:00", password="secret123")
    with db.transaction() as conn:
        conn.execute("UPDATE inventory SET stock_qty = stock_qty + 2 WHERE id=?", (item_id,))
    integ2 = services.verify_ledger_integrity()
    assert "stock_batch_drift" in [i.get("type") for i in (integ2.get("issues") or [])]

    report = doctor.collect_health(check_updates=False)
    assert report.get("schema", {}).get("expected_version") == db.SCHEMA_VERSION
    assert "sqlite" in report.get("integrity", {})
    txt = doctor.format_health_report(report)
    assert "Property Manager Doctor" in txt and "Schema:" in txt and "Backup/restore guidance:" in txt

    drift = doctor._batch_stock_consistency(db.connect())
    assert drift.get("checked") is True and drift.get("mismatch_count", 0) >= 1
    assert isinstance(doctor._printer_readiness(), dict)
    guide = doctor._backup_restore_guidance()
    assert "backup" in guide and "restore" in guide


# ── updater behavior (no network/external deps) ─────────────────────────

def test_updater_behavior_deterministic(fresh, monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "_project_root", lambda: tmp_path)
    st = updater.get_local_state()
    assert st["is_git_repo"] is False and st["app_version"] == updater.APP_VERSION

    def boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(updater, "_github_get", boom)
    res = updater.check_for_updates()
    assert res["ok"] is False and res["update_available"] is False and "offline" in (res.get("error") or "")

    ap = updater.apply_update()
    assert ap["ok"] is False and "git" in (ap.get("error") or "").lower()

    bad = updater.check_for_updates(branch="bad..ref")
    assert bad["ok"] is False and "invalid" in (bad.get("error") or "").lower()

    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        updater,
        "get_local_state",
        lambda: {"app_version": updater.APP_VERSION, "project_root": str(tmp_path), "is_git_repo": True, "branch": "main", "commit": "abc1234", "dirty": True},
    )
    dirty = updater.apply_update()
    assert dirty["ok"] is False and "uncommitted" in (dirty.get("error") or "").lower()

    monkeypatch.setattr(
        updater,
        "get_local_state",
        lambda: {"app_version": updater.APP_VERSION, "project_root": str(tmp_path), "is_git_repo": True, "branch": "main", "commit": "abc1234", "dirty": False},
    )
    calls = []

    def fake_git(args, cwd=None, timeout=20):
        calls.append(list(args))
        if args[:2] == ["rev-parse", "HEAD"]:
            return 0, ("def5678" if len(calls) > 3 else "abc1234"), ""
        return 0, "ok", ""

    monkeypatch.setattr(updater, "_run_git", fake_git)
    ok = updater.apply_update()
    assert ok["ok"] is True and ok.get("restart_required") is True
    assert any(c[0] == "fetch" for c in calls) and any(c[0] == "pull" for c in calls)

    monkeypatch.setattr(updater, "get_local_state", lambda: {"commit": "same123", "is_git_repo": True, "branch": "main"})
    monkeypatch.setattr(
        updater,
        "_github_get",
        lambda *a, **k: [{"sha": "same123", "commit": {"author": {"date": "2026-01-01", "name": "x"}, "message": "msg"}, "html_url": "u"}],
    )
    up = updater.check_for_updates()
    assert up["ok"] is True and up["update_available"] is False and up.get("commits_behind") == 0
