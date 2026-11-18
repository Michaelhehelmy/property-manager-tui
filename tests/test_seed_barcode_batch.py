import json
from pathlib import Path
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


def test_export_seed_config_has_v2_fields(temp_db):
    path = services.export_seed_config()
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    assert data.get('version') == 2
    assert 'activities' in data
    assert 'inventory' in data
    assert 'rooms' in data
    # inventory entries should carry barcode/batch fields
    if data['inventory']:
        it = data['inventory'][0]
        for k in ('sku','barcode','barcode_type','track_batches','shelf_life_days','default_supplier'):
            assert k in it


def test_import_seed_config_roundtrip_on_fresh_db(temp_db):
    # export current defaults, then import onto another fresh DB
    exported = services.export_seed_config()
    data = json.loads(Path(exported).read_text(encoding='utf-8'))
    # tweak a room and add an activity to ensure applied
    if data['rooms']:
        data['rooms'][0]['base_price'] = 123.45
    data.setdefault('activities', []).append({
        'name': 'Test Trip', 'description': 'd', 'category': 'Trip', 'unit_price': 50.0, 'active': 1
    })
    Path(paths.EXPORT_DIR).mkdir(parents=True, exist_ok=True)
    p2 = Path(paths.EXPORT_DIR) / 'seed_mod.json'
    p2.write_text(json.dumps(data), encoding='utf-8')

    # reset to fresh DB state (no operational data)
    try:
        db.close()
    except Exception:
        pass
    db._conn = None
    Path(paths.DB_PATH).unlink(missing_ok=True)
    services.init_db()
    with db.transaction() as conn:
        conn.execute("DELETE FROM app_metadata WHERE key IN ('super_password_hash','super_password_salt','failed_attempts','lockout_until','recovery_codes')")
    services.set_super_password("secret123")

    res = services.import_seed_config(str(p2), 'secret123', 'APPLY SEED CONFIG')
    assert res.get('ok') is True
    # verify applied
    conn = db.connect()
    if data['rooms']:
        rn = data['rooms'][0]['room_number']
        row = conn.execute('SELECT base_price FROM rooms WHERE room_number=?', (rn,)).fetchone()
        assert row is not None
        assert abs(float(row['base_price']) - 123.45) < 0.001
    act = conn.execute("SELECT name, unit_price FROM activities WHERE name='Test Trip'").fetchone()
    assert act is not None
    assert abs(float(act['unit_price']) - 50.0) < 0.001
    # inventory with stock should have seed batch created
    inv = conn.execute("SELECT id, stock_qty FROM inventory WHERE ABS(stock_qty) > 0.0001").fetchall()
    for it in inv:
        b = conn.execute("SELECT 1 FROM inventory_batches WHERE item_id=?", (it['id'],)).fetchone()
        assert b is not None


def test_barcode_resolve_item(temp_db):
    with db.transaction() as conn:
        conn.execute("""INSERT INTO inventory (item_name, category, stock_qty, unit, unit_cost, selling_price, track_batches, barcode, sku)
                       VALUES ('ScanItem','Bar',0,'pcs',1,2,0,'CODE123','SKU123')""")
        iid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    r = services.resolve_barcode('CODE123')
    assert r['type'] == 'item'
    assert r['item']['id'] == iid
    # by sku
    r2 = services.resolve_barcode('SKU123')
    assert r2['type'] == 'item'
    assert r2['item']['id'] == iid
