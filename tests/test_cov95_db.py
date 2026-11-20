from __future__ import annotations

import sqlite3
import threading

import pytest

from app_core import db, paths


def test_db_connect_double_check_and_isolation_level(monkeypatch, tmp_path):
    # exercise double-check branch (line 47) via custom lock entering sets _conn
    db.close()
    orig_lock = db._lock
    orig_conn = db._conn

    class FakeLock:
        def __enter__(self):
            db._conn = "sentinel-conn"
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    try:
        db._lock = FakeLock()
        db._conn = None
        assert db.connect() == "sentinel-conn"
    finally:
        db._conn = orig_conn
        db._lock = orig_lock
        db.close()

    # isolation_level setter exception path (54-55) and trusted_schema pragma exception (36-38)
    db.close()
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "pragma.db")
    paths.ensure_data_dirs()

    real_connect = sqlite3.connect

    class FakeConn:
        def __init__(self, inner):
            self._inner = inner
            self.row_factory = None
            self._isolation = "DEFERRED"

        @property
        def isolation_level(self):
            return self._isolation

        @isolation_level.setter
        def isolation_level(self, value):
            raise RuntimeError("nope")

        @property
        def in_transaction(self):
            return getattr(self._inner, "in_transaction", False)

        def execute(self, sql, *a, **k):
            if "trusted_schema" in str(sql):
                raise RuntimeError("unsupported")
            return self._inner.execute(sql, *a, **k)

        def close(self):
            return self._inner.close()

    def fake_sqlite_connect(*a, **k):
        return FakeConn(real_connect(*a, **k))

    monkeypatch.setattr(sqlite3, "connect", fake_sqlite_connect)
    try:
        c = db.connect()
        assert c is not None
        assert db.is_initialized() is True
    finally:
        db.close()
        monkeypatch.setattr(sqlite3, "connect", real_connect)


def test_db_close_rollback_and_exception_paths(clean_db, monkeypatch):
    conn = db.connect()

    # close rolls back if in_transaction and swallows rollback/close exceptions (67,68-69)
    class FakeConn:
        in_transaction = True
        closed = False

        def execute(self, sql):
            if "ROLLBACK" in sql:
                raise RuntimeError("rollback fail")
            return None

        def close(self):
            self.closed = True
            raise RuntimeError("close fail")

    fake = FakeConn()
    db._conn = fake
    # close() swallows rollback exceptions but re-raises close() exceptions (while still clearing _conn)
    with pytest.raises(RuntimeError, match="close fail"):
        db.close()
    assert db._conn is None

    # restore clean state for subsequent tests by reopening
    _ = db.connect()


def test_db_transaction_rollback_close_exception_and_fetch_cursor_close(clean_db, monkeypatch):
    # transaction rollback exception suppressed (98-99)
    class RBFailConn:
        def __init__(self, real):
            self.real = real
            self.in_transaction = False

        def execute(self, sql, *a, **k):
            if sql == "BEGIN IMMEDIATE":
                self.in_transaction = True
                return self.real.execute(sql, *a, **k)
            if sql == "ROLLBACK":
                self.in_transaction = False
                raise RuntimeError("rb fail")
            if sql == "COMMIT":
                self.in_transaction = False
            return self.real.execute(sql, *a, **k)

    real = db.connect()
    proxy = RBFailConn(real)
    monkeypatch.setattr(db, "connect", lambda: proxy)
    with pytest.raises(ValueError):
        with db.transaction():
            raise ValueError("boom")

    # _fetch_cursor close exception suppressed (128-129)
    class BadCur:
        def fetchone(self):
            return {"x": 1}

        def fetchall(self):
            return [{"x": 1}]

        def close(self):
            raise RuntimeError("no")

    assert db._fetch_cursor(BadCur(), one=True) == {"x": 1}
    assert db._fetch_cursor(BadCur(), one=False) == [{"x": 1}]


def test_db_integrity_check_error_branches(clean_db, monkeypatch):
    # max_errors parse fallback (154-155)
    ok, details = db.integrity_check(max_errors="bad")
    assert isinstance(ok, bool) and isinstance(details, list)

    real_conn = db.connect()

    class ConnICFail:
        def execute(self, sql, *a, **k):
            if "integrity_check" in sql:
                raise RuntimeError("ic fail")
            return real_conn.execute(sql, *a, **k)

    monkeypatch.setattr(db, "connect", lambda: ConnICFail())
    ok, details = db.integrity_check()
    assert ok is False and any("integrity_check error" in str(d) for d in details)

    class ConnFKFail:
        def execute(self, sql, *a, **k):
            if "foreign_key_check" in sql:
                raise RuntimeError("fk fail")
            return real_conn.execute(sql, *a, **k)

    monkeypatch.setattr(db, "connect", lambda: ConnFKFail())
    ok, details = db.integrity_check()
    assert ok is False and any("foreign_key_check error" in str(d) for d in details)

    # foreign key violations detail branch (169-170)
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        "CREATE TABLE p(id INTEGER PRIMARY KEY);"
        "CREATE TABLE c(id INTEGER PRIMARY KEY, pid INTEGER REFERENCES p(id));"
    )
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("INSERT INTO c(id,pid) VALUES(1,999)")
    monkeypatch.setattr(db, "connect", lambda: conn)
    ok, details = db.integrity_check()
    assert ok is False
    assert any("foreign_key_violations=" in str(d) for d in details)


def test_db_runtime_indexes_and_migrate_edge_branches(tmp_path, monkeypatch):
    # ensure_runtime_indexes own-conn branch and exception swallowing (218,219 and try/except)
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "idx.db")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    paths.ensure_data_dirs()
    db.close()
    monkeypatch.setattr(db, "DB_PATH", paths.DB_PATH)
    monkeypatch.setattr(db, "_conn", None)
    c = db.connect()
    # bad SQL should be swallowed by ensure_runtime_indexes implementation? it iterates fixed stmts
    db.ensure_runtime_indexes(c)
    db.ensure_runtime_indexes()  # own conn path

    # _migrate_to_10 exception branches (941-942 index create, 946-947 backfill update)
    c2 = sqlite3.connect(":memory:")
    c2.row_factory = sqlite3.Row
    c2.executescript(
        "CREATE TABLE reservations(id INTEGER PRIMARY KEY, source TEXT, market_segment TEXT, notes TEXT, booking_notes TEXT DEFAULT '');"
        "INSERT INTO reservations(id,source,market_segment,notes,booking_notes) VALUES(1,'D','', 'legacy note','');"
        "CREATE TABLE inventory(id INTEGER PRIMARY KEY, stock_qty REAL, unit_cost REAL, created_at TEXT);"
        "CREATE TABLE inventory_logs(id INTEGER PRIMARY KEY, item_id INTEGER, change_type TEXT, reference TEXT, at TEXT);"
        "CREATE TABLE inventory_batches(id INTEGER PRIMARY KEY, item_id INTEGER, lot_code TEXT UNIQUE, barcode_value TEXT, barcode_type TEXT, quantity_received REAL, quantity_remaining REAL, unit_cost REAL, received_at TEXT, expiry_date TEXT, supplier TEXT, reference TEXT, notes TEXT, status TEXT, payload_json TEXT, created_at TEXT);"
        "CREATE TABLE pos_tabs(id INTEGER PRIMARY KEY);"
    )

    class ConnRaise:
        def __init__(self, real):
            self._real = real
        def __getattr__(self, name):
            return getattr(self._real, name)
        def execute(self, sql, *a, **k):
            s = str(sql)
            if "idx_res_source_segment" in s or "UPDATE reservations SET booking_notes" in s:
                raise RuntimeError("boom")
            return self._real.execute(sql, *a, **k)
        def executescript(self, sql):
            return self._real.executescript(sql)

    db._migrate_to_10(ConnRaise(c2))  # should swallow targeted exceptions

    # _migrate_to_10 legacy backfill lot collision branch (1018)
    c3 = sqlite3.connect(":memory:")
    c3.row_factory = sqlite3.Row
    c3.executescript(
        "CREATE TABLE inventory(id INTEGER PRIMARY KEY, stock_qty REAL, unit_cost REAL, created_at TEXT);"
        "CREATE TABLE inventory_logs(id INTEGER PRIMARY KEY, item_id INTEGER, change_type TEXT, reference TEXT, at TEXT, quantity REAL, batch_id INTEGER, unit_cost REAL, balance_after REAL);"
        "CREATE TABLE inventory_batches(id INTEGER PRIMARY KEY, item_id INTEGER, lot_code TEXT UNIQUE, barcode_value TEXT, barcode_type TEXT, quantity_received REAL, quantity_remaining REAL, unit_cost REAL, received_at TEXT, expiry_date TEXT, supplier TEXT, reference TEXT, notes TEXT, status TEXT, payload_json TEXT, created_at TEXT);"
        "CREATE TABLE pos_tabs(id INTEGER PRIMARY KEY);"
    )
    c3.execute("INSERT INTO inventory(id,stock_qty,unit_cost,created_at) VALUES(1,5,2,'2026-01-01 00:00:00')")
    c3.execute("INSERT INTO inventory_batches(id,item_id,lot_code,barcode_value,barcode_type,quantity_received,quantity_remaining,unit_cost,received_at,expiry_date,supplier,reference,notes,status,payload_json,created_at) VALUES(99,2,'LEGACY-1','LEGACY-1','code128',1,1,1,'2026-01-01','','','x','x','active','{}','2026-01-01')")
    db._migrate_to_10(c3)
    lots = [r[0] for r in c3.execute("SELECT lot_code FROM inventory_batches WHERE item_id=1").fetchall()]
    assert lots and lots[0].startswith("LEGACY-1-")

    # other migration try/except pass blocks (1158-1159,1306-1307,1418-1419)
    c4 = sqlite3.connect(":memory:")
    c4.execute("CREATE TABLE payments(id INTEGER PRIMARY KEY, shift_id INTEGER)")
    c4.execute("CREATE TABLE roles(name TEXT PRIMARY KEY, description TEXT, permissions_json TEXT, created_at TEXT)")
    c4.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT, role TEXT, active INTEGER)")
    class ConnRaise2:
        def __init__(self, real):
            self._real = real
        def __getattr__(self, name):
            return getattr(self._real, name)
        def execute(self, sql, *a, **k):
            s = str(sql)
            if "idx_payments_idempotency" in s or "idx_payments_shift" in s or "INSERT OR IGNORE INTO roles" in s:
                raise RuntimeError("x")
            return self._real.execute(sql, *a, **k)
        def executescript(self, sql):
            return self._real.executescript(sql)
    w4 = ConnRaise2(c4)
    db._migrate_to_13(w4)
    db._migrate_to_17(w4)
    db._migrate_to_20(w4)
    db.close()


def test_db_migrate_control_flow_branches(tmp_path, monkeypatch):
    # fresh create with failure re-raised (1476-1477)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "m1")
    monkeypatch.setattr(paths, "DB_PATH", paths.DATA_DIR / "property_manager.db")
    paths.ensure_data_dirs()
    db.close()
    monkeypatch.setattr(db, "DB_PATH", paths.DB_PATH)
    monkeypatch.setattr(db, "_conn", None)
    monkeypatch.setattr(db, "_SCHEMA", "CREATE TABLE broken(")  # invalid SQL
    with pytest.raises(Exception):
        db.migrate()
    db.close()

    # restore schema from module for subsequent operations by reloading would be heavy; instead set to minimal valid schema
    monkeypatch.setattr(db, "_SCHEMA", "CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL); PRAGMA user_version=0;")

    # upgrade path: active transaction rollback guard (1461-1463), BEGIN failure (1483-1484), exception rollback path (1527-1532)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "m2")
    monkeypatch.setattr(paths, "DB_PATH", paths.DATA_DIR / "property_manager.db")
    paths.ensure_data_dirs()
    db.close()
    monkeypatch.setattr(db, "DB_PATH", paths.DB_PATH)
    monkeypatch.setattr(db, "_conn", None)
    real_conn = db.connect()
    real_conn.execute("PRAGMA user_version = 1")

    calls = {"rollback": 0, "begin_fail": 0, "in_tx_checks": 0}

    class ProxyConn:
        def __init__(self, real):
            self._real = real
        def __getattr__(self, name):
            return getattr(self._real, name)
        @property
        def in_transaction(self):
            # first check in migrate() -> True to hit defensive rollback (1461), later -> real state
            calls["in_tx_checks"] += 1
            if calls["in_tx_checks"] == 1:
                return True
            return getattr(self._real, "in_transaction", False)
        def execute(self, sql, *a, **k):
            s = str(sql)
            if s == "BEGIN IMMEDIATE" and calls["begin_fail"] == 0:
                calls["begin_fail"] += 1
                raise RuntimeError("begin fail")
            if s == "ROLLBACK":
                calls["rollback"] += 1
            return self._real.execute(sql, *a, **k)
        def executescript(self, sql):
            return self._real.executescript(sql)

    proxy = ProxyConn(real_conn)
    orig_connect = db.connect
    monkeypatch.setattr(db, "connect", lambda: proxy)
    # force failure during migration to trigger exception handling/rollback (1527-1532)
    monkeypatch.setattr(db, "_migrate_to_5", lambda c: (_ for _ in ()).throw(RuntimeError("m5 boom")))
    try:
        with pytest.raises(RuntimeError, match="m5 boom"):
            db.migrate()
        assert calls["rollback"] >= 1  # defensive + exception rollback paths
    finally:
        monkeypatch.setattr(db, "connect", orig_connect)
        monkeypatch.setattr(db, "_migrate_to_5", db._migrate_to_5, raising=False)  # monkeypatch will restore later; keep explicit no-op safe
    db.close()

    # current schema branch exception swallowing (1538-1539)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "m3")
    monkeypatch.setattr(paths, "DB_PATH", paths.DATA_DIR / "property_manager.db")
    paths.ensure_data_dirs()
    db.close()
    monkeypatch.setattr(db, "DB_PATH", paths.DB_PATH)
    monkeypatch.setattr(db, "_conn", None)
    c3 = db.connect()
    c3.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION}")
    monkeypatch.setattr(db, "ensure_runtime_indexes", lambda _c=None: (_ for _ in ()).throw(RuntimeError("x")))
    assert db.migrate() == "current"
    db.close()
