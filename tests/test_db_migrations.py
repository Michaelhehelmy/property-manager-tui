"""DB migration coverage (idempotent upgrade paths)."""
import sqlite3
from app_core import db


def _reset_v4(monkeypatch, path):
    try:
        db.close()
    except Exception:
        pass
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "_conn", None)
    # minimal schema at v4 without recipe_components/meals
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("PRAGMA user_version=4")
    conn.commit()
    conn.close()


def test_migrate_upgrades_v4_to_current(tmp_path, monkeypatch):
    p = tmp_path / "v4.db"
    _reset_v4(monkeypatch, p)
    try:
        res = db.migrate()
        assert res == "upgraded"
        conn = db.connect()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        # v5 table
        conn.execute("SELECT 1 FROM recipe_components LIMIT 1")
        # v6 tables
        conn.execute("SELECT 1 FROM meals LIMIT 1")
        conn.execute("SELECT 1 FROM meal_ingredients LIMIT 1")
    finally:
        db.close()


def test_migrate_upgrades_v5_to_v6(tmp_path, monkeypatch):
    p = tmp_path / "v5.db"
    try:
        db.close()
    except Exception:
        pass
    monkeypatch.setattr(db, "DB_PATH", p)
    monkeypatch.setattr(db, "_conn", None)
    conn = sqlite3.connect(str(p))
    conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS recipe_components (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        menu_item_id INTEGER NOT NULL,
        ingredient_item_id INTEGER NOT NULL,
        qty_per_sale REAL NOT NULL
    );
    """)
    conn.execute("PRAGMA user_version=5")
    conn.commit()
    conn.close()
    try:
        assert db.migrate() == "upgraded"
        conn = db.connect()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        conn.execute("SELECT 1 FROM meals LIMIT 1")
    finally:
        db.close()


def test_migrate_current_when_at_version(tmp_path, monkeypatch):
    p = tmp_path / "cur.db"
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
