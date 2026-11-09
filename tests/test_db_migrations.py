"""DB migration coverage (idempotent upgrade paths)."""
import sqlite3
from app_core import db


def _reset(monkeypatch, path):
    try:
        db.close()
    except Exception:
        pass
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "_conn", None)
    # minimal schema at v4 without recipe_components
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("PRAGMA user_version=4")
    conn.commit()
    conn.close()


def test_migrate_upgrades_v4_to_v5(tmp_path, monkeypatch):
    p = tmp_path / "v4.db"
    _reset(monkeypatch, p)
    try:
        res = db.migrate()
        assert res == "upgraded"
        conn = db.connect()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        # recipe_components table created
        conn.execute("SELECT 1 FROM recipe_components LIMIT 1")
    finally:
        db.close()


def test_migrate_current_when_at_version(tmp_path, monkeypatch):
    p = tmp_path / "v5.db"
    try:
        db.close()
    except Exception:
        pass
    monkeypatch.setattr(db, "DB_PATH", p)
    monkeypatch.setattr(db, "_conn", None)
    conn = db.connect()
    conn.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(f"PRAGMA user_version={db.SCHEMA_VERSION}")
    conn.commit()
    assert db.migrate() == "current"
    db.close()
