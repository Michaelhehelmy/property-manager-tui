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


def test_integrity_after_batch_sales_and_void(temp_db):
    with db.transaction() as conn:
        conn.execute("INSERT INTO inventory (item_name, category, stock_qty, unit, unit_cost, selling_price, track_batches) VALUES ('JuiceX','Bar',0,'bottle',2,5,1)")
        item_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        room_id = conn.execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
        conn.execute("INSERT INTO guests (canonical_name) VALUES ('G')")
        gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO reservations (guest_id, guest_name, room_id, agreed_nightly_rate, check_in_date, check_out_date, status) VALUES (?,?,?,?,?,?,?)",
                     (gid,'G',room_id,100,'2026-01-01','2026-01-03','Checked In'))
        rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    services.create_inventory_batch(item_id, 5, 2.0, received_at="2026-01-01 10:00:00", expiry_date="2026-12-31", password="secret123")
    services.create_inventory_batch(item_id, 3, 2.0, received_at="2026-01-01 11:00:00", expiry_date="2027-01-01", password="secret123")
    res = services.add_pos_item(rid, "JuiceX", 6, 5.0, inv_id=item_id)  # split across batches
    pos_ids = res.get("pos_ids") or [res.get("pos_id")]
    assert len(pos_ids) >= 2

    integ = services.verify_ledger_integrity()
    # no stock/batch drift issues
    issue_types = [i.get('type') for i in (integ.get('issues') or [])]
    assert 'stock_batch_drift' not in issue_types
    assert 'negative_batch_remaining' not in issue_types
    assert 'orphan_pos_batch' not in issue_types

    # void one line and recheck
    services.void_pos_item(pos_ids[0], "secret123")
    integ2 = services.verify_ledger_integrity()
    issue_types2 = [i.get('type') for i in (integ2.get('issues') or [])]
    assert 'stock_batch_drift' not in issue_types2
    assert 'negative_batch_remaining' not in issue_types2
