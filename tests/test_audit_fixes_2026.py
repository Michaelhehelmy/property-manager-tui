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
    # clear any pre-set password so tests can set deterministically
    with db.transaction() as conn:
        conn.execute(
            "DELETE FROM app_metadata WHERE key IN "
            "('super_password_hash','super_password_salt','failed_attempts','lockout_until','recovery_codes')"
        )
    yield tmp_path
    try:
        db.close()
    except Exception:
        pass
    monkeypatch.setattr(db, "_conn", None, raising=False)


def _seed_room_inventory():
    conn = db.connect()
    if not conn.execute("SELECT 1 FROM expense_categories WHERE category_name='Supplies'").fetchone():
        with db.transaction() as c:
            c.execute("INSERT INTO expense_categories (category_name) VALUES ('Supplies')")
    with db.transaction() as c:
        c.execute(
            """
            INSERT OR IGNORE INTO inventory
            (item_name, category, stock_qty, unit, unit_cost, selling_price, track_batches, shelf_life_days, sku, barcode)
            VALUES ('WaterTest', 'Bar', 0, 'bottle', 2.0, 5.0, 1, 30, 'SKU-WATER-TEST', 'BAR-WATER-TEST')
            """
        )
    return conn.execute("SELECT id FROM inventory WHERE item_name='WaterTest'").fetchone()["id"]


def test_restock_and_receive_batch_accept_session_sentinel(temp_db):
    item_id = _seed_room_inventory()
    services.set_super_password("secret123")
    assert services.unlock_session("secret123") is True

    # C1 regression: TUI passes __session__
    services.restock_inventory(item_id, 10, unit_cost=2.0, password="__session__")
    item = db.connect().execute("SELECT stock_qty FROM inventory WHERE id=?", (item_id,)).fetchone()
    assert float(item["stock_qty"]) == 10.0
    batches = services.list_inventory_batches(item_id=item_id)
    assert len(batches) >= 1

    services.create_inventory_batch(
        item_id, 5, unit_cost=2.5, supplier="S", reference="R", password="__session__", actor="tui"
    )
    item = db.connect().execute("SELECT stock_qty FROM inventory WHERE id=?", (item_id,)).fetchone()
    assert float(item["stock_qty"]) == 15.0

    services.lock_session()
    with pytest.raises(PermissionError):
        services.restock_inventory(item_id, 1, password="__session__")


def test_get_pos_items_includes_trace_fields(temp_db):
    item_id = _seed_room_inventory()
    services.set_super_password("secret123")
    room_id = db.connect().execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
    with db.transaction() as conn:
        conn.execute("INSERT INTO guests (canonical_name) VALUES ('Guest')")
        gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO reservations (guest_id, guest_name, room_id, agreed_nightly_rate, check_in_date, check_out_date, status)
            VALUES (?, 'Guest', ?, 100.0, '2026-01-01', '2026-01-03', 'Checked In')
            """,
            (gid, room_id),
        )
        rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    services.restock_inventory(item_id, 5, unit_cost=2.0, password="secret123")
    services.add_pos_item(rid, "WaterTest", 2, 5.0, inv_id=item_id)
    rows = services.get_pos_items(rid)
    assert len(rows) == 1
    r = rows[0]
    assert r["item_description"]
    assert int(r["quantity"]) == 2
    assert "lot_code" in r.keys()
    assert "batch_id" in r.keys()


def test_verify_ledger_integrity_includes_batch_stats(temp_db):
    item_id = _seed_room_inventory()
    services.set_super_password("secret123")
    services.restock_inventory(item_id, 3, unit_cost=2.0, password="secret123")
    res = services.verify_ledger_integrity()
    assert "inventory_items" in res["stats"]
    assert "inventory_batches" in res["stats"]
    assert isinstance(res.get("issues"), list)
    assert isinstance(res.get("warnings"), list)
