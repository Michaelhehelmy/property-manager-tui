from app_core import db, services, paths


def test_migrate_to_9_idempotent_backfill(tmp_path, monkeypatch):
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
    services.init_db()

    conn = db.connect()
    # pick an item with stock and batches from seed, record counts
    row = conn.execute("""
        SELECT i.id, i.stock_qty,
               (SELECT COUNT(*) FROM inventory_batches b WHERE b.item_id=i.id) bc,
               (SELECT COUNT(*) FROM inventory_logs l WHERE l.item_id=i.id) lc
        FROM inventory i
        WHERE ABS(COALESCE(i.stock_qty,0)) > 0.0001
        ORDER BY i.id LIMIT 1
    """).fetchone()
    assert row is not None
    item_id = row["id"]
    bc_before = conn.execute("SELECT COUNT(*) FROM inventory_batches WHERE item_id=?", (item_id,)).fetchone()[0]
    lc_before = conn.execute("SELECT COUNT(*) FROM inventory_logs WHERE item_id=?", (item_id,)).fetchone()[0]

    # call migrate_to_9 again - should be safe/idempotent
    db._migrate_to_9(conn)
    conn.commit()

    bc_after = conn.execute("SELECT COUNT(*) FROM inventory_batches WHERE item_id=?", (item_id,)).fetchone()[0]
    lc_after = conn.execute("SELECT COUNT(*) FROM inventory_logs WHERE item_id=?", (item_id,)).fetchone()[0]
    # no duplicate batches/logs for items that already have batches
    assert bc_after == bc_before
    assert lc_after == lc_before

    # full migrate/init also safe
    services.init_db()
    integ = services.verify_ledger_integrity()
    assert 'stock_batch_drift' not in [i.get('type') for i in (integ.get('issues') or [])]

    try:
        db.close()
    except Exception:
        pass
    db._conn = None
