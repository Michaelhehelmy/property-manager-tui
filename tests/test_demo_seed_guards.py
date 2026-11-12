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


def test_apply_demo_preset_refused_with_operational_data(temp_db):
    # create minimal operational data (reservation)
    room_id = db.connect().execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
    with db.transaction() as conn:
        conn.execute("INSERT INTO guests (canonical_name) VALUES ('G')")
        gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO reservations (guest_id, guest_name, room_id, agreed_nightly_rate, check_in_date, check_out_date, status) VALUES (?,?,?,?,?,?,?)",
                     (gid, 'G', room_id, 100.0, '2026-01-01', '2026-01-02', 'Reserved'))
    with pytest.raises(ValueError):
        services.apply_demo_data_preset('business_starter', 'secret123', 'APPLY DEMO DATA')


def test_import_seed_config_requires_no_operational_data(temp_db):
    # export is fine
    path = services.export_seed_config()
    # create operational data
    room_id = db.connect().execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
    with db.transaction() as conn:
        conn.execute("INSERT INTO guests (canonical_name) VALUES ('G')")
        gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO reservations (guest_id, guest_name, room_id, agreed_nightly_rate, check_in_date, check_out_date, status) VALUES (?,?,?,?,?,?,?)",
                     (gid, 'G', room_id, 100.0, '2026-01-01', '2026-01-02', 'Reserved'))
    with pytest.raises(ValueError):
        services.import_seed_config(path, 'secret123', 'APPLY SEED CONFIG')
