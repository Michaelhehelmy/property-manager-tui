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
    yield tmp_path
    try:
        db.close()
    except Exception:
        pass
    monkeypatch.setattr(db, "_conn", None, raising=False)


def test_init_and_migrate_idempotent(temp_db):
    # second init/migrate should be safe
    services.init_db()
    services.init_db()
    assert db.user_version() == db.SCHEMA_VERSION
    res = services.verify_ledger_integrity()
    assert isinstance(res, dict)
    assert "issues" in res and "warnings" in res


def test_fresh_db_integrity_ok(temp_db):
    res = services.verify_ledger_integrity()
    assert res["ok"] is True or res.get("issues") == []
    # stats present
    assert "inventory_items" in res.get("stats", {})
    assert "inventory_batches" in res.get("stats", {})


def test_seeded_inventory_has_consistent_batches(temp_db):
    # after fresh init, any item with nonzero stock should have batch coverage (backfill/seed behavior)
    conn = db.connect()
    items = conn.execute("SELECT id, item_name, stock_qty FROM inventory WHERE ABS(COALESCE(stock_qty,0)) > 0.0001").fetchall()
    for it in items:
        batches = conn.execute("SELECT COUNT(*) c, COALESCE(SUM(quantity_remaining),0) rem FROM inventory_batches WHERE item_id=?", (it["id"],)).fetchone()
        assert batches["c"] >= 1, f"item {it['id']} {it['item_name']} has stock but no batches"
        # allow small float tolerance
        assert abs(float(batches["rem"]) - float(it["stock_qty"])) < 0.01
