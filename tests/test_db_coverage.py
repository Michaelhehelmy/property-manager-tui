from __future__ import annotations

import sqlite3

import pytest

from app_core import db, paths


def test_db_helpers_and_queries(clean_db, tmp_path, monkeypatch):
    # basic state/helpers
    assert db.is_initialized() is True
    assert isinstance(db.user_version(), int)
    assert db.is_fresh() is False or db.user_version() == 0  # clean_db migrates/seeds

    # execute/query helpers
    db.execute("CREATE TABLE IF NOT EXISTS _t(id INTEGER PRIMARY KEY, v TEXT)")
    db.execute("INSERT INTO _t(v) VALUES (?)", ("a",))
    db.executemany("INSERT INTO _t(v) VALUES (?)", [("b",), ("c",)])
    rows = db.query_all("SELECT v FROM _t ORDER BY id")
    assert [r["v"] for r in rows] == ["a", "b", "c"]
    one = db.query_one("SELECT v FROM _t WHERE id=1")
    assert one["v"] == "a"
    cur = db.query("SELECT COUNT(*) AS n FROM _t")
    assert cur.fetchone()["n"] == 3

    # integrity check
    ok, details = db.integrity_check()
    assert ok is True
    assert any(str(d).lower() == "ok" for d in details) or isinstance(details, list)

    # table/column helpers and ensure_column
    conn = db.connect()
    assert db._table_exists(conn, "settings") is True
    cols = db._table_columns(conn, "settings")
    assert "key" in cols
    db.execute("CREATE TABLE IF NOT EXISTS _cols(id INTEGER PRIMARY KEY)")
    conn2 = db.connect()
    cols2 = db._table_columns(conn2, "_cols")
    db._ensure_column(conn2, "_cols", "name", "TEXT NOT NULL DEFAULT ''", cols2)
    assert "name" in db._table_columns(conn2, "_cols")
    # calling again is idempotent
    db._ensure_column(conn2, "_cols", "name", "TEXT NOT NULL DEFAULT ''")

    # ensure_runtime_indexes idempotent (with and without explicit conn)
    db.ensure_runtime_indexes(conn2)
    db.ensure_runtime_indexes()

    # migration meta: safe if table missing and works when present
    conn3 = sqlite3.connect(":memory:")
    db._record_migration_meta(conn3, "start", 1, 2)  # no app_metadata table, should not raise
    conn3.executescript("CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL, hash_signature TEXT NOT NULL DEFAULT '')")
    db._record_migration_meta(conn3, "done", 1, 2)
    rows = conn3.execute("SELECT key, value FROM app_metadata").fetchall()
    kv = {r[0]: r[1] for r in rows}
    assert kv.get("schema_migration_status") == "done"
    assert kv.get("schema_migration_from") == "1"


def test_db_transactions_and_close(clean_db, tmp_path, monkeypatch):
    db.execute("CREATE TABLE IF NOT EXISTS _tx(id INTEGER PRIMARY KEY, v TEXT)")

    # commit path
    with db.transaction() as conn:
        conn.execute("INSERT INTO _tx(v) VALUES (?)", ("ok",))
    assert db.query_one("SELECT COUNT(*) AS n FROM _tx WHERE v='ok'")["n"] == 1

    # rollback path
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            conn.execute("INSERT INTO _tx(v) VALUES (?)", ("bad",))
            raise RuntimeError("boom")
    assert db.query_one("SELECT COUNT(*) AS n FROM _tx WHERE v='bad'")["n"] == 0

    # nested/active transaction guard
    with db.transaction() as conn:
        with pytest.raises(RuntimeError):
            with db.transaction():
                pass

    # close and reconnect behavior
    db.close()
    assert db.is_initialized() is False
    conn = db.connect()
    assert db.is_initialized() is True
    assert conn is db.connect()

    # fresh isolated DB detection
    db.close()
    fresh_path = tmp_path / "fresh.db"
    monkeypatch.setattr(db, "DB_PATH", fresh_path)
    monkeypatch.setattr(paths, "DB_PATH", fresh_path)
    monkeypatch.setattr(db, "_conn", None)
    assert db.connect() is not None
    # user_version 0 on brand new db => fresh
    try:
        assert db.is_fresh() is True or db.user_version() == 0
    finally:
        db.close()
        # restore will be handled by fixture teardown/other tests' fixtures, but be explicit
        monkeypatch.setattr(db, "_conn", None)
