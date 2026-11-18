import pytest
from app_core import db, services, paths


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
    try:
        services.set_language("en")
    except Exception:
        pass
    services.init_db()
    with db.transaction() as conn:
        conn.execute("DELETE FROM app_metadata WHERE key IN ('super_password_hash','super_password_salt','failed_attempts','lockout_until','recovery_codes')")
    services.set_super_password("secret123")
    yield tmp_path
    try:
        db.close()
    except Exception:
        pass
    monkeypatch.setattr(db, "_conn", None, raising=False)


def _mk_item(name="Water", track=1):
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO inventory (item_name, category, stock_qty, unit, unit_cost, selling_price, track_batches, barcode, sku, barcode_type)
               VALUES (?, 'Bar', 0, 'bottle', 2.0, 5.0, ?, ?, ?, 'code128')""",
            (name, int(track), f"BAR-{name}", f"SKU-{name}"),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _mk_reservation(status='Checked In'):
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


def _set_setting(key, value):
    try:
        services.set_setting(key, str(value).lower() if isinstance(value, bool) else value)
    except Exception:
        with db.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))


def test_fefo_allocation_prefers_earlier_expiry(temp_db):
    item_id = _mk_item("Juice")
    # later expiry received earlier
    b2 = services.create_inventory_batch(item_id, 5, 2.0, received_at="2026-01-01 10:00:00", expiry_date="2026-12-31", password="secret123")
    b1 = services.create_inventory_batch(item_id, 5, 2.0, received_at="2026-01-02 10:00:00", expiry_date="2026-06-30", password="secret123")
    rid = _mk_reservation()
    res = services.add_pos_item(rid, "Juice", 3, 5.0, inv_id=item_id)
    rows = services.get_pos_items(rid)
    assert len(rows) == 1
    assert rows[0]["batch_id"] == b1["id"]
    assert rows[0]["quantity"] == 3
    batch1 = db.connect().execute("SELECT quantity_remaining FROM inventory_batches WHERE id=?", (b1["id"],)).fetchone()["quantity_remaining"]
    batch2 = db.connect().execute("SELECT quantity_remaining FROM inventory_batches WHERE id=?", (b2["id"],)).fetchone()["quantity_remaining"]
    assert float(batch1) == 2.0
    assert float(batch2) == 5.0


def test_multi_batch_split_and_void_restore(temp_db):
    item_id = _mk_item("Soda")
    b1 = services.create_inventory_batch(item_id, 2, 1.0, received_at="2026-01-01 10:00:00", expiry_date="2026-06-01", password="secret123")
    b2 = services.create_inventory_batch(item_id, 5, 1.2, received_at="2026-01-01 11:00:00", expiry_date="2026-07-01", password="secret123")
    rid = _mk_reservation()
    res = services.add_pos_item(rid, "Soda", 4, 3.0, inv_id=item_id)
    pos_ids = res.get("pos_ids") or [res.get("pos_id")]
    assert len(pos_ids) == 2  # split across two batches
    rows = services.get_pos_items(rid)
    lots = sorted([r["lot_code"] for r in rows])
    assert b1["lot_code"] in lots and b2["lot_code"] in lots
    # quantities 2 and 2
    qtys = sorted([int(r["quantity"]) for r in rows])
    assert qtys == [2, 2]
    # batch remaining
    assert float(db.connect().execute("SELECT quantity_remaining FROM inventory_batches WHERE id=?", (b1["id"],)).fetchone()[0]) == 0.0
    assert float(db.connect().execute("SELECT quantity_remaining FROM inventory_batches WHERE id=?", (b2["id"],)).fetchone()[0]) == 3.0
    stock = float(db.connect().execute("SELECT stock_qty FROM inventory WHERE id=?", (item_id,)).fetchone()[0])
    assert stock == 3.0

    # void first line (2 units) restores correct batch
    services.void_pos_item(pos_ids[0], "secret123")
    # after void, one active POS line remains
    rows2 = services.get_pos_items(rid)
    assert len(rows2) == 1
    # stock and batch restored for voided line
    stock2 = float(db.connect().execute("SELECT stock_qty FROM inventory WHERE id=?", (item_id,)).fetchone()[0])
    assert stock2 == 5.0
    # b1 should be restored to 2 (or b2 depending which was voided; check both sum)
    rem1 = float(db.connect().execute("SELECT quantity_remaining FROM inventory_batches WHERE id=?", (b1["id"],)).fetchone()[0])
    rem2 = float(db.connect().execute("SELECT quantity_remaining FROM inventory_batches WHERE id=?", (b2["id"],)).fetchone()[0])
    assert rem1 + rem2 == 5.0


def test_expired_batch_blocked_when_setting_enabled(temp_db):
    _set_setting("block_sell_expired", "true")
    _set_setting("allow_negative_stock", "false")
    item_id = _mk_item("Milk")
    services.create_inventory_batch(item_id, 5, 1.0, received_at="2025-01-01 10:00:00", expiry_date="2025-01-02", password="secret123")
    rid = _mk_reservation()
    with pytest.raises(ValueError):
        services.add_pos_item(rid, "Milk", 1, 3.0, inv_id=item_id)


def test_specific_batch_selection(temp_db):
    item_id = _mk_item("Tea")
    b1 = services.create_inventory_batch(item_id, 5, 1.0, received_at="2026-01-01 10:00:00", expiry_date="2026-06-01", password="secret123")
    b2 = services.create_inventory_batch(item_id, 5, 1.0, received_at="2026-01-02 10:00:00", expiry_date="2026-07-01", password="secret123")
    rid = _mk_reservation()
    # explicitly sell from b2 (not FEFO first)
    services.add_pos_item(rid, "Tea", 2, 4.0, inv_id=item_id, batch_id=b2["id"])
    rows = services.get_pos_items(rid)
    assert len(rows) == 1
    assert rows[0]["batch_id"] == b2["id"]
    assert float(db.connect().execute("SELECT quantity_remaining FROM inventory_batches WHERE id=?", (b2["id"],)).fetchone()[0]) == 3.0
    assert float(db.connect().execute("SELECT quantity_remaining FROM inventory_batches WHERE id=?", (b1["id"],)).fetchone()[0]) == 5.0
