import json
import sqlite3
from pathlib import Path

import pytest

from app_core import db, paths, services


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
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
    monkeypatch.setattr(services, "_session", {"unlocked": False, "last_activity": None}, raising=False)
    services.set_language("en")
    services.init_db()
    with db.transaction() as conn:
        conn.execute(
            "DELETE FROM app_metadata WHERE key IN ('super_password_hash','super_password_salt','failed_attempts','lockout_until')"
        )
    services.set_super_password("secret123")
    yield tmp_path
    try:
        db.close()
    except Exception:
        pass
    db._conn = None


def _mk_item(name="Water", track=1, stock=0, unit_cost=2.0):
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO inventory (item_name, category, stock_qty, unit, unit_cost, selling_price, track_batches, barcode, sku, barcode_type, active)
               VALUES (?, 'Bar', ?, 'bottle', ?, 5.0, ?, ?, ?, 'code128', 1)""",
            (name, float(stock), float(unit_cost), int(track), f"BAR-{name}", f"SKU-{name}"),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _mk_reservation(status="Checked In"):
    room_id = db.connect().execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
    with db.transaction() as conn:
        conn.execute("INSERT INTO guests (canonical_name) VALUES ('Guest')")
        gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO reservations (guest_id, guest_name, room_id, agreed_nightly_rate, check_in_date, check_out_date, status)
               VALUES (?, 'Guest', ?, 100.0, '2026-01-01', '2026-01-03', ?)""",
            (gid, room_id, status),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_migration_init_idempotent_extended(temp_db):
    # Extend coverage: repeated init/migrate and _migrate_to_9 are safe on fresh/current DB
    assert db.user_version() == db.SCHEMA_VERSION
    for _ in range(3):
        assert db.migrate() == "current"
        services.init_db()
    db._migrate_to_9(db.connect())
    db.connect().commit()
    assert db.user_version() == db.SCHEMA_VERSION
    integ = services.verify_ledger_integrity()
    assert integ["ok"] or integ.get("issues") == []
    assert "inventory_batches" in integ.get("stats", {})


def test_v9_backfill_behavior_and_idempotent(tmp_path, monkeypatch):
    # Simulate pre-v9 DB with stock but no batch tables/columns, then migrate and rerun.
    try:
        db.close()
    except Exception:
        pass
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "BASE_DIR", tmp_path)
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "legacy.db")
    monkeypatch.setattr(paths, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(paths, "INVOICE_DIR", tmp_path / "exports" / "invoices")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "legacy.db", raising=False)
    monkeypatch.setattr(db, "_conn", None, raising=False)
    conn = sqlite3.connect(str(tmp_path / "legacy.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
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
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        """
    )
    conn.execute("INSERT INTO inventory (item_name, category, stock_qty, unit, unit_cost, selling_price) VALUES ('Legacy Water', 'Bar', 12, 'bottle', 3.0, 6.0)")
    conn.execute("INSERT INTO inventory (item_name, category, stock_qty, unit, unit_cost, selling_price) VALUES ('Zero Item', 'Bar', 0, 'pcs', 1.0, 2.0)")
    conn.execute("PRAGMA user_version=8")
    conn.commit()
    conn.close()

    db._conn = None
    assert db.migrate() == "upgraded"
    c = db.connect()
    assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    item_id = c.execute("SELECT id FROM inventory WHERE item_name='Legacy Water'").fetchone()[0]
    zero_id = c.execute("SELECT id FROM inventory WHERE item_name='Zero Item'").fetchone()[0]

    b = c.execute("SELECT * FROM inventory_batches WHERE item_id=?", (item_id,)).fetchall()
    assert len(b) == 1
    assert b[0]["lot_code"].startswith("LEGACY-")
    assert float(b[0]["quantity_received"]) == 12.0
    assert float(b[0]["quantity_remaining"]) == 12.0
    assert "v9_legacy_backfill" in (b[0]["notes"] or "")
    logs = c.execute("SELECT * FROM inventory_logs WHERE item_id=? AND change_type='OPENING'", (item_id,)).fetchall()
    assert len(logs) == 1
    assert "v9_legacy_backfill" in (logs[0]["reference"] or "")
    assert c.execute("SELECT COUNT(*) FROM inventory_batches WHERE item_id=?", (zero_id,)).fetchone()[0] == 0

    # rerun paths: no duplicates
    db._migrate_to_9(c)
    c.commit()
    db.migrate()
    db._migrate_to_9(db.connect())
    db.connect().commit()
    assert c.execute("SELECT COUNT(*) FROM inventory_batches WHERE item_id=?", (item_id,)).fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM inventory_logs WHERE item_id=? AND change_type='OPENING'", (item_id,)).fetchone()[0] == 1
    db.close()
    db._conn = None


def test_batch_receive_resolve_expiry_helpers(temp_db):
    item_id = _mk_item("Juice")
    b = services.receive_inventory_batch(item_id, 5, 2.5, received_at="2026-01-01 10:00:00", expiry_date="2026-01-03", supplier="S", reference="R", notes="N", password="secret123")
    assert b["item_id"] == item_id
    assert float(b["quantity_received"]) == 5
    assert b["lot_code"]

    got = services.get_inventory_batch(b["id"])
    assert got["lot_code"] == b["lot_code"]
    assert got["item_name"] == "Juice"

    by_lot = services.resolve_barcode(b["lot_code"])
    assert by_lot["type"] == "batch"
    assert by_lot["batch"]["id"] == b["id"]
    assert by_lot["item"]["id"] == item_id

    exp = services.get_expiring_batches(within_days=3650, include_expired=True)
    assert any(x["id"] == b["id"] for x in exp)
    assert services.get_inventory_batch(b["id"])["expiry_state"] in ("expired", "expiring", "fresh")

    listed = services.list_inventory_batches(item_id=item_id)
    assert any(x["id"] == b["id"] for x in listed)


def test_fefo_void_stock_batch_consistency_edge_cases(temp_db):
    item_id = _mk_item("Soda")
    b1 = services.create_inventory_batch(item_id, 2, 1.0, received_at="2026-01-01 10:00:00", expiry_date="2026-06-01", password="secret123")
    b2 = services.create_inventory_batch(item_id, 5, 1.2, received_at="2026-01-01 11:00:00", expiry_date="2026-07-01", password="secret123")
    rid = _mk_reservation()
    res = services.add_pos_item(rid, "Soda", 4, 3.0, inv_id=item_id)
    pos_ids = res.get("pos_ids") or [res.get("pos_id")]
    assert len(pos_ids) == 2

    def rem(bid):
        return float(db.connect().execute("SELECT quantity_remaining FROM inventory_batches WHERE id=?", (bid,)).fetchone()[0])
    def stock():
        return float(db.connect().execute("SELECT stock_qty FROM inventory WHERE id=?", (item_id,)).fetchone()[0])

    assert rem(b1["id"]) == 0.0 and rem(b2["id"]) == 3.0 and stock() == 3.0
    # void split lines -> full restore
    for pid in pos_ids:
        services.void_pos_item(pid, "secret123")
    assert rem(b1["id"]) == 2.0 and rem(b2["id"]) == 5.0 and stock() == 7.0
    assert services.get_pos_items(rid) == []
    integ = services.verify_ledger_integrity()
    assert not [i for i in integ.get("issues", []) if i.get("type") == "stock_batch_drift"]


def test_seed_export_import_roundtrip_fields(temp_db):
    exported = services.export_seed_config()
    data = json.loads(Path(exported).read_text(encoding="utf-8"))
    assert data.get("version") == 2
    assert {"rooms", "rate_plans", "inventory", "expense_categories", "meals", "activities"}.issubset(data.keys())
    if data["inventory"]:
        inv0 = data["inventory"][0]
        for k in ("sku", "barcode", "barcode_type", "track_batches", "shelf_life_days", "default_supplier"):
            assert k in inv0
    assert any(a.get("name") for a in data.get("activities", []))
    assert any(m.get("name") for m in data.get("meals", []))

    # modify to verify roundtrip of barcode/batch/activity/meal fields
    data["inventory"][0]["barcode"] = "RT-BC-999"
    data["inventory"][0]["sku"] = "RT-SKU-999"
    data["inventory"][0]["track_batches"] = 1
    data.setdefault("activities", []).append({"name": "Roundtrip Trip", "description": "d", "category": "Trip", "unit_price": 77.0, "active": 1})
    # add a simple meal referencing an existing inventory ingredient name
    ing_name = data["inventory"][1]["item_name"] if len(data["inventory"]) > 1 else data["inventory"][0]["item_name"]
    data.setdefault("meals", []).append({
        "name": "Roundtrip Meal", "description": "m", "preparation": "p", "selling_price": 55.0,
        "category": "Meals", "active": 1,
        "ingredients": [{"inventory_item_name": ing_name, "qty_per_sale": 1, "note": "x"}],
    })
    Path(paths.EXPORT_DIR).mkdir(parents=True, exist_ok=True)
    mod = Path(paths.EXPORT_DIR) / "seed_roundtrip.json"
    mod.write_text(json.dumps(data), encoding="utf-8")

    # fresh DB
    db.close(); db._conn = None
    Path(paths.DB_PATH).unlink(missing_ok=True)
    services.init_db()
    with db.transaction() as conn:
        conn.execute("DELETE FROM app_metadata WHERE key IN ('super_password_hash','super_password_salt','failed_attempts','lockout_until')")
    services.set_super_password("secret123")

    res = services.import_seed_config(str(mod), "secret123", "APPLY SEED CONFIG")
    assert res.get("ok") is True
    c = db.connect()
    it = c.execute("SELECT barcode, sku, track_batches FROM inventory WHERE item_name=?", (data["inventory"][0]["item_name"],)).fetchone()
    assert it["barcode"] == "RT-BC-999" and it["sku"] == "RT-SKU-999" and int(it["track_batches"]) == 1
    act = c.execute("SELECT unit_price FROM activities WHERE name='Roundtrip Trip'").fetchone()
    assert act and abs(float(act["unit_price"]) - 77.0) < 0.001
    meal = c.execute("SELECT id FROM meals WHERE name='Roundtrip Meal'").fetchone()
    assert meal is not None
    assert c.execute("SELECT COUNT(*) FROM meal_ingredients WHERE meal_id=?", (meal["id"],)).fetchone()[0] >= 1
    # stock items should get seed batches
    stocked = c.execute("SELECT id FROM inventory WHERE ABS(stock_qty) > 0.0001").fetchall()
    for s in stocked:
        assert c.execute("SELECT 1 FROM inventory_batches WHERE item_id=?", (s["id"],)).fetchone() is not None


def test_demo_preset_refusal_and_import_guard(temp_db):
    # demo preset refusal already exists elsewhere, ensure behavior present
    room_id = db.connect().execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
    with db.transaction() as conn:
        conn.execute("INSERT INTO guests (canonical_name) VALUES ('G')")
        gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO reservations (guest_id, guest_name, room_id, agreed_nightly_rate, check_in_date, check_out_date, status) VALUES (?,?,?,?,?,?,?)",
                     (gid, 'G', room_id, 100.0, '2026-01-01', '2026-01-02', 'Reserved'))
    with pytest.raises(ValueError):
        services.apply_demo_data_preset('business_starter', 'secret123', 'APPLY DEMO DATA')
    path = services.export_seed_config()
    with pytest.raises(ValueError):
        services.import_seed_config(path, 'secret123', 'APPLY SEED CONFIG')


def test_integrity_check_catches_controlled_drift(temp_db):
    # create tracked item with batch then induce stock/batch drift
    item_id = _mk_item("DriftCola", track=1, stock=0)
    services.create_inventory_batch(item_id, 5, 1.0, received_at="2026-01-01 10:00:00", password="secret123")
    ok = services.verify_ledger_integrity()
    assert not [i for i in ok.get("issues", []) if i.get("type") == "stock_batch_drift"]

    # controlled drift: change stock without adjusting batches
    with db.transaction() as conn:
        conn.execute("UPDATE inventory SET stock_qty = stock_qty + 3 WHERE id=?", (item_id,))
    bad = services.verify_ledger_integrity()
    drift = [i for i in bad.get("issues", []) if i.get("type") == "stock_batch_drift"]
    assert drift, f"expected stock_batch_drift, got {bad.get('issues')}"
    assert any(d.get("item_id") == item_id for d in drift[0].get("detail", []))
