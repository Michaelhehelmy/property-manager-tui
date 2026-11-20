from __future__ import annotations

import io
import json
import os
import runpy
import sqlite3
import sys
import time
from pathlib import Path

import pytest

import app_core.db as db
import app_core.doctor as doctor
import app_core.paths as paths
import app_core.support as support
import app_core.updater as updater
from app_core import services
from app_core.services import billing_docs, reports_metrics
import app_core.services as services_pkg


# ---------------------------------------------------------------------------
# doctor.py missing branches
# ---------------------------------------------------------------------------


def test_doctor_create_backup_missing_source_and_default_dest(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "missing.db")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    res = doctor.create_backup()
    assert res["ok"] is False and "source DB not found" in res["error"]

    # default destination path (timestamped in DATA_DIR)
    src = tmp_path / "src.db"
    c = sqlite3.connect(str(src))
    c.execute("PRAGMA user_version=21")
    c.execute("CREATE TABLE t(id INTEGER)")
    c.commit()
    c.close()
    monkeypatch.setattr(paths, "DB_PATH", src)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(doctor, "verify_backup_file", lambda p: {"ok": True, "path": str(p)})
    created = doctor.create_backup(None)
    assert created["ok"] is True
    assert str(tmp_path) in created["backup_path"]
    assert "property_manager.backup." in Path(created["backup_path"]).name


def test_doctor_detect_backup_files_exception_branches(tmp_path, monkeypatch):
    # candidate stat() exception -> continue (288-289)
    data_dir = tmp_path / "bdir"
    data_dir.mkdir()
    bad = data_dir / "property_manager.bad.db"
    bad.write_text("x", encoding="utf-8")
    good = data_dir / "property_manager.good.db"
    good.write_text("y", encoding="utf-8")
    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    orig_stat = Path.stat

    def stat_hook(self, *a, **k):
        if self.name == "property_manager.bad.db":
            raise RuntimeError("nostat")
        return orig_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", stat_hook)
    try:
        det = doctor._detect_backup_files()
        assert det.get("found") >= 1
        names = [b["name"] for b in det.get("recent_backups", [])]
        assert "property_manager.good.db" in names
        assert "property_manager.bad.db" not in names
    finally:
        monkeypatch.setattr(Path, "stat", orig_stat)

    # outer exception (292-293): Path(...).exists() happens inside outer try
    class BoomPath:
        def __init__(self, p):
            self.p = p

        def exists(self):
            raise RuntimeError("boom-exists")

        def is_dir(self):
            return True

    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "x")
    monkeypatch.setattr(doctor, "Path", BoomPath)
    det = doctor._detect_backup_files()
    assert det.get("found") == 0 and "error" in det


def test_doctor_config_issues_exception_and_ledger_branches(tmp_path, monkeypatch):
    # outer config-check exception 353-354 via proxied db object raising on attr access
    class BadDB:
        @property
        def _conn(self):
            raise RuntimeError("db-attr-fail")

    monkeypatch.setattr(doctor, "db", BadDB())
    # keep rest of checks benign
    monkeypatch.setattr(paths, "check_runtime_permissions", lambda: {}, raising=False)
    monkeypatch.setattr(doctor, "_detect_backup_files", lambda: {"found": 1, "latest_age_hours": 1})
    issues = doctor._config_issues()
    assert any(i.get("code") == "config_check_error" for i in issues)

    # env-check except 384-385
    monkeypatch.setattr(doctor, "db", db)
    monkeypatch.setattr(db, "_conn", None)
    real_env_get = os.environ.get
    calls = {"n": 0}

    def env_get_hook(k, default=None):
        calls["n"] += 1
        # let early checks run, then fail inside env block
        if calls["n"] > 2 and k in ("PM_WEB_SECRET_KEY", "HOST", "PM_WEB_HOST"):
            raise RuntimeError("envfail")
        return real_env_get(k, default)

    monkeypatch.setattr(os.environ, "get", env_get_hook)
    issues = doctor._config_issues()
    assert isinstance(issues, list)  # exception swallowed

    monkeypatch.setattr(os.environ, "get", real_env_get)

    # backup stale inner parse exception 396-397 and outer backup-detect exception 398-399
    monkeypatch.setattr(doctor, "_detect_backup_files", lambda: {"found": 1, "latest_age_hours": "not-a-float"})
    issues = doctor._config_issues()
    assert not any(i.get("code") == "backup_stale" for i in issues)

    monkeypatch.setattr(doctor, "_detect_backup_files", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    issues = doctor._config_issues()
    assert isinstance(issues, list)


def test_doctor_collect_health_io_and_ledger_branches(clean_db, tmp_path, monkeypatch):
    # sidecar detection except 446-447
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "property_manager.db")
    paths.ensure_data_dirs()
    real_path = Path
    state = {"wal_exists_calls": 0}

    class SidecarPath:
        def __init__(self, p):
            self._p = real_path(p)
            self._s = str(p)

        def __getattr__(self, name):
            return getattr(self._p, name)

        def __fspath__(self):
            return str(self._p)

        def __str__(self):
            return str(self._p)

        def __truediv__(self, other):
            return SidecarPath(self._p / other)

        def exists(self):
            if self._s.endswith("-wal"):
                state["wal_exists_calls"] += 1
                # 1st call: _backup_restore_guidance, 2nd: sidecar detection in collect_health
                if state["wal_exists_calls"] >= 2:
                    raise RuntimeError("wal-check-fail")
            return self._p.exists()

    monkeypatch.setattr(doctor, "Path", SidecarPath)
    monkeypatch.setattr(doctor, "_config_issues", lambda: [])
    monkeypatch.setattr(updater, "get_local_state", lambda: {"app_version": "1", "is_git_repo": False})
    rep = doctor.collect_health(check_updates=False)
    assert isinstance(rep, dict)
    monkeypatch.setattr(doctor, "Path", real_path)

    # db stat/table_count/pragma excepts 456-457,472-473,478-479 and note 497
    # restore real db reference (earlier branch replaced doctor.db with a proxy)
    monkeypatch.setattr(doctor, "db", db)
    hdir = tmp_path / "hdir"
    hdir.mkdir(exist_ok=True)
    db_path = hdir / "property_manager.db"
    c = sqlite3.connect(str(db_path))
    c.execute("CREATE TABLE t(id INTEGER)")
    c.execute("PRAGMA user_version=21")
    c.commit()
    c.close()
    monkeypatch.setattr(paths, "DATA_DIR", hdir)
    monkeypatch.setattr(paths, "DB_PATH", db_path)
    monkeypatch.setattr(paths, "EXPORT_DIR", hdir / "exports")
    monkeypatch.setattr(paths, "INVOICE_DIR", hdir / "invoices")
    monkeypatch.setattr(paths, "ensure_data_dirs", lambda: None)
    monkeypatch.setattr(
        doctor,
        "_backup_restore_guidance",
        lambda: {"backup": {}, "restore": {}, "readiness": {}, "runtime_data_preserved": []},
    )
    monkeypatch.setattr(doctor, "_config_issues", lambda: [])

    real_path = Path

    class DBPathWrap:
        def __init__(self, p):
            self._p = real_path(p)
            self._s = str(p)

        def __fspath__(self):
            return str(self._p)

        def __str__(self):
            return str(self._p)

        def __truediv__(self, other):
            return DBPathWrap(self._p / other)

        def __getattr__(self, name):
            return getattr(self._p, name)

        def exists(self):
            return True

        def is_dir(self):
            return False

        def is_file(self):
            return True

        def stat(self, *a, **k):
            # raise to hit 456-457 inside collect_health try block
            raise RuntimeError("nostat")

    def path_factory(p):
        if str(p) == str(db_path):
            return DBPathWrap(p)
        return real_path(p)

    monkeypatch.setattr(doctor, "Path", path_factory)

    real_connect = sqlite3.connect

    class ConnProxy:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, n):
            return getattr(self._real, n)

        def execute(self, sql, *a, **k):
            s = str(sql)
            if "FROM sqlite_master" in s and "type='table'" in s:
                raise RuntimeError("no-count")
            if "PRAGMA journal_mode" in s:
                raise RuntimeError("no-pragmas")
            return self._real.execute(sql, *a, **k)

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: ConnProxy(real_connect(*a, **k)))
    try:
        monkeypatch.setattr(db, "_conn", None)  # triggers ledger-skip note 497
        monkeypatch.setattr(doctor.db, "_conn", None)
        rep = doctor.collect_health(check_updates=False)
        assert "size_bytes" not in rep.get("db", {})
        assert rep.get("db", {}).get("table_count") is None
        assert any("Ledger integrity check skipped" in n for n in rep.get("notes", []))
    finally:
        monkeypatch.setattr(sqlite3, "connect", real_connect)

    # db inspection outer exception 482-484
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("openfail")))
    rep = doctor.collect_health(check_updates=False)
    assert rep.get("ok") is False and "error" in rep.get("db", {})
    monkeypatch.setattr(sqlite3, "connect", real_connect)

    # ledger outer except 498-499: make getattr(db,"_conn",None) raise inside ledger try block
    monkeypatch.setattr(db, "_conn", object())

    class BadConnDB:
        @property
        def _conn(self):
            raise RuntimeError("conn-fail")

    monkeypatch.setattr(doctor, "db", BadConnDB())
    rep = doctor.collect_health(check_updates=False)
    assert any("Ledger integrity check unavailable" in n for n in rep.get("notes", []))


def test_doctor_format_and_main_guard(tmp_path, monkeypatch):
    # format_health_report remote redact exception 594-595
    rep = {
        "ok": True,
        "checked_at": "",
        "paths": {},
        "data_dirs": {},
        "schema": {},
        "integrity": {},
        "consistency": {},
        "readiness": {},
        "backup_restore": {},
        "config_issues": [],
        "notes": [],
        "update": {"local": {"app_version": "1", "is_git_repo": True, "remote": "http://x"}},
    }
    monkeypatch.setattr(paths, "redact_sensitive", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("redact fail")))
    txt = doctor.format_health_report(rep)
    assert "Update status (local)" in txt
    assert "remote:" not in txt  # exception swallowed

    # __main__ guard 686
    monkeypatch.setattr(paths, "redact_sensitive", lambda x, *a, **k: str(x))
    monkeypatch.setattr(sys, "argv", ["app_core.doctor", "--json"])
    monkeypatch.setattr(
        doctor,
        "collect_health",
        lambda check_updates=False: {
            "ok": True,
            "checked_at": "",
            "paths": {},
            "data_dirs": {},
            "schema": {},
            "integrity": {},
            "consistency": {},
            "readiness": {},
            "backup_restore": {},
            "config_issues": [],
            "notes": [],
        },
    )
    with pytest.raises(SystemExit) as ei:
        runpy.run_module("app_core.doctor", run_name="__main__")
    assert ei.value.code == 0


# ---------------------------------------------------------------------------
# paths.py missing branches
# ---------------------------------------------------------------------------


def test_paths_missing_branches(tmp_path, monkeypatch):
    # _is_frozen_runtime return branch 16
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert paths._is_frozen_runtime() is False
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert paths._is_frozen_runtime() is True

    # _detect_project_root frozen-branch exception 30-31
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "", raising=False)
    r = paths._detect_project_root()
    assert isinstance(r, Path)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app.exe"), raising=False)
    real_path = Path
    calls = {"n": 0}

    def path_boom_then_ok(p=""):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real_path(p)

    monkeypatch.setattr(paths, "Path", path_boom_then_ok)
    try:
        r = paths._detect_project_root()
        assert isinstance(r, Path)
    finally:
        monkeypatch.setattr(paths, "Path", real_path)

    # PM_DATA_DIR success path (64)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    d = tmp_path / "pmdata_ok"
    monkeypatch.setenv("PM_DATA_DIR", str(d))
    try:
        out = paths._detect_data_dir()
        assert out == d.resolve() and d.exists()
    finally:
        monkeypatch.delenv("PM_DATA_DIR", raising=False)

    # _resolve_existingish fallbacks 41-45
    class P1:
        def expanduser(self):
            return self

        def resolve(self, strict=False):
            raise RuntimeError("r")

        def absolute(self):
            return Path("/tmp/abs")

    assert paths._resolve_existingish(P1()) == Path("/tmp/abs")

    class P2:
        def expanduser(self):
            return self

        def resolve(self, strict=False):
            raise RuntimeError("r")

        def absolute(self):
            raise RuntimeError("a")

    p2 = P2()
    assert paths._resolve_existingish(p2) is p2

    # is_path_within except 145-146
    assert paths.is_path_within(object(), tmp_path) is False

    # redact_sensitive per-pattern except 167-168 and max_len parse except 171-172
    class BadPat:
        def sub(self, repl, s):
            raise RuntimeError("subfail")

    orig_patterns = paths._REDACT_PATTERNS
    monkeypatch.setattr(paths, "_REDACT_PATTERNS", [(BadPat(), "x")] + list(orig_patterns))
    try:
        out = paths.redact_sensitive("password=secret")
        assert "secret" not in out or "redacted" in out  # later patterns may still redact
    finally:
        monkeypatch.setattr(paths, "_REDACT_PATTERNS", orig_patterns)

    out = paths.redact_sensitive("hello world", max_len=object())
    assert out.startswith("hello")


# ---------------------------------------------------------------------------
# updater.py missing branches
# ---------------------------------------------------------------------------


def test_updater_missing_branches(monkeypatch, tmp_path):
    real_check_for_updates = updater.check_for_updates

    # _project_root fallback branches 45-54 when paths.PROJECT_ROOT access fails
    class BadPaths:
        @property
        def PROJECT_ROOT(self):
            raise RuntimeError("no-root")

    monkeypatch.setattr(updater, "paths", BadPaths())
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app.bin"), raising=False)
    assert updater._project_root() == (tmp_path / "app.bin").parent

    # force exception inside frozen branch -> 52-53
    class BadPathStr:
        def __init__(self, p):
            self.p = p

        def resolve(self):
            raise RuntimeError("no-resolve")

    monkeypatch.setattr(updater, "Path", lambda p="": BadPathStr(p) if "app.bin" in str(p) else Path(p))
    assert isinstance(updater._project_root(), Path)

    monkeypatch.setattr(updater, "Path", Path)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "executable", "", raising=False)
    root = updater._project_root()
    assert isinstance(root, Path)

    # _github_get auth header 152
    captured = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(req, timeout=0):
        captured["auth"] = req.headers.get("Authorization") or req.get_header("Authorization")
        return Resp()

    monkeypatch.setenv("GITHUB_TOKEN", "tok123")
    monkeypatch.setattr(updater.urllib.request, "urlopen", fake_urlopen)
    data = updater._github_get("https://api.github.com/x", timeout=5)
    assert data == {"ok": True}
    assert "Bearer tok123" in (captured.get("auth") or "")

    # _parse_version_tag digit-suffix branch 173 (e.g. "2beta" -> parses leading 2)
    assert updater._parse_version_tag("1.2beta") == (1, 2)

    # _default_update_interval_sec returns 0 when hours <= 0 (215)
    monkeypatch.setattr(services, "get_setting", lambda k, d=None: "0")
    assert updater._default_update_interval_sec() == 0

    # get_cached_update_notice git error propagation 281 and release exception 314
    updater._UPDATE_CACHE["checked_at_ts"] = 0.0
    updater._UPDATE_CACHE["result"] = None
    monkeypatch.setattr(updater, "_default_update_interval_sec", lambda: 3600)
    monkeypatch.setattr(updater, "auto_update_checks_enabled", lambda: True)
    monkeypatch.setattr(updater, "get_local_state", lambda: {"is_git_repo": True, "branch": "main"})
    monkeypatch.setattr(
        updater,
        "check_for_updates",
        lambda timeout=8: {
            "ok": False,
            "update_available": False,
            "error": "git-rate-limit",
            "latest": None,
            "compare_url": None,
            "commits_behind": None,
        },
    )
    monkeypatch.setattr(
        updater, "get_latest_release", lambda timeout=8: (_ for _ in ()).throw(RuntimeError("rel-boom"))
    )
    n = updater.get_cached_update_notice(force=True, timeout=1)
    assert n.get("error") == "git-rate-limit"

    # release exception with no prior error -> 314
    updater._UPDATE_CACHE["checked_at_ts"] = 0.0
    updater._UPDATE_CACHE["result"] = None
    monkeypatch.setattr(
        updater,
        "check_for_updates",
        lambda timeout=8: {
            "ok": True,
            "update_available": False,
            "error": None,
            "latest": None,
            "compare_url": None,
            "commits_behind": 0,
        },
    )
    monkeypatch.setattr(
        updater, "get_latest_release", lambda timeout=8: (_ for _ in ()).throw(RuntimeError("rel-only"))
    )
    n = updater.get_cached_update_notice(force=True, timeout=1)
    assert n.get("error") == "rel-only"

    # changelog loop continue on non-dict commit 383 (restore real function after earlier monkeypatches)
    monkeypatch.setattr(updater, "check_for_updates", real_check_for_updates)
    monkeypatch.setattr(updater, "get_local_state", lambda: {"commit": "old", "is_git_repo": True, "branch": "main"})
    monkeypatch.setattr(
        updater,
        "_github_get",
        lambda url, timeout=12: [
            {"sha": "new2", "commit": {"author": {"date": "d2", "name": "n2"}, "message": "subj2"}, "html_url": "u2"},
            "not-a-dict",
            {"sha": "new1", "commit": {"author": {"date": "d1", "name": "n1"}, "message": "subj1"}, "html_url": "u1"},
            {"sha": "old", "commit": {"author": {"date": "d0", "name": "n0"}, "message": "old"}, "html_url": "u0"},
        ],
    )
    res = updater.check_for_updates()
    assert res["ok"] is True and res["commits_behind"] == 2
    assert [c["commit_short"] for c in res["changelog"]] == ["new2", "new1"]

    # network error path and non-git install note (357)
    monkeypatch.setattr(updater, "get_local_state", lambda: {"commit": None, "is_git_repo": False, "branch": None})
    monkeypatch.setattr(updater, "_github_get", lambda url, timeout=12: (_ for _ in ()).throw(RuntimeError("netfail")))
    res = updater.check_for_updates()
    assert res["ok"] is False and any("Not a git-based install" in n for n in res.get("notes", []))


# ---------------------------------------------------------------------------
# support.py missing branches
# ---------------------------------------------------------------------------


def test_support_missing_branches(tmp_path, monkeypatch):
    # _redact_string pattern sub except 47-48
    class BadPat:
        def sub(self, repl, s):
            raise RuntimeError("subfail")

    orig = support._SENSITIVE_VALUE_PATTERNS
    monkeypatch.setattr(support, "_SENSITIVE_VALUE_PATTERNS", [BadPat()])
    try:
        out = support._redact_string("Bearer abc")
        assert "abc" in out or "<redacted>" in out
    finally:
        monkeypatch.setattr(support, "_SENSITIVE_VALUE_PATTERNS", orig)

    # sanitize_for_report sensitive bool flag branch 68
    assert support.sanitize_for_report(True, "feature_enabled") is True
    assert support.sanitize_for_report(False, "is_configured") is False

    # fallback repr branch 98
    class X:
        pass

    out = support.sanitize_for_report(X(), "safe_field")
    assert isinstance(out, str)

    # readiness is_initialized except 133-134
    monkeypatch.setattr(paths, "check_runtime_permissions", lambda: {}, raising=False)
    monkeypatch.setattr(db, "is_initialized", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    rs = support._safe_readiness_summary()
    assert rs.get("db_initialized") is False

    # doctor summary integrity non-dict passthrough 148
    monkeypatch.setattr(
        "app_core.doctor.collect_health",
        lambda check_updates=False: {
            "ok": True,
            "schema": {},
            "integrity": "opaque",
            "config_issues": [],
            "notes": [],
            "update": {},
        },
    )
    ds = support._safe_doctor_summary()
    assert ds.get("integrity") == "opaque"

    # collect_support_diagnostics updater block exception 251-252
    import app_core.updater as real_upd

    monkeypatch.setattr(real_upd, "get_local_state", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    diag = support.collect_support_diagnostics(context={})
    assert "error" in (diag.get("app") or {})

    # markdown truncation of final text 317 (user fields are individually redacted-capped,
    # so combine large diag JSON payload and multiple large user fields to exceed _MAX_TEXT*2)
    big_diag = {"blob": "B" * (support._MAX_TEXT * 2)}
    big_user = {
        "steps": "S" * (support._MAX_TEXT + 100),
        "expected": "E" * (support._MAX_TEXT + 100),
        "actual": "A" * (support._MAX_TEXT + 100),
        "notes": "N" * (support._MAX_TEXT + 100),
    }
    md = support.format_support_report_markdown(big_diag, user=big_user)
    assert md.endswith("... (truncated)\n")

    # __main__ guard 389
    monkeypatch.setattr(sys, "argv", ["support", "--json"])
    # stub collect/format to be fast
    monkeypatch.setattr(support, "collect_support_diagnostics", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        support,
        "generate_support_bundle",
        lambda *a, **k: {"diagnostics": {"ok": True}, "markdown": "m", "issue": {"url": "u"}},
    )
    with pytest.raises(SystemExit) as ei:
        runpy.run_module("app_core.support", run_name="__main__")
    assert ei.value.code == 0


# ---------------------------------------------------------------------------
# db.py missing branches
# ---------------------------------------------------------------------------


def test_db_migrate_to_17_role_seed_exception(tmp_path, monkeypatch):
    # _migrate_to_17 role insert except 1306-1307
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE audit_log(id INTEGER PRIMARY KEY);
        CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT UNIQUE);
        CREATE TABLE roles(name TEXT PRIMARY KEY, description TEXT, permissions_json TEXT, created_at TEXT);
    """)

    class ProxyConn:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def execute(self, sql, *a, **k):
            if "INSERT OR IGNORE INTO roles" in str(sql):
                raise RuntimeError("seed-fail")
            return self._real.execute(sql, *a, **k)

    db._migrate_to_17(ProxyConn(c))  # should swallow exception

    # migrate() refuses newer schema 1466
    db_path = tmp_path / "newer.db"
    c2 = sqlite3.connect(str(db_path))
    c2.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    c2.commit()
    c2.close()
    db.close()
    monkeypatch.setattr(paths, "DB_PATH", db_path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(db, "_conn", None)
    paths.ensure_data_dirs()
    try:
        with pytest.raises(RuntimeError, match="newer than supported"):
            db.migrate()
    finally:
        db.close()
        db._conn = None


# ---------------------------------------------------------------------------
# billing_docs.py missing branches
# ---------------------------------------------------------------------------


def test_billing_docs_missing_branches(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "INVOICE_DIR", tmp_path / "inv")
    paths.ensure_data_dirs()

    # invalid filename 40
    with pytest.raises(ValueError, match="Invalid invoice filename"):
        billing_docs._write_confined_invoice_file("../evil.txt", "x")

    # simulate write/open failure to hit 53-60 and inner close-except 56-57
    import os as _os

    real_fdopen = _os.fdopen
    real_close = _os.close
    real_open = _os.open

    class FailingFile:
        def __init__(self, fd):
            self.fd = fd

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def write(self, s):
            raise RuntimeError("writefail")

    state = {"opens": 0, "file_fd": None, "close_raises_left": 1}

    def open_track_fail(*a, **k):
        fd = real_open(*a, **k)
        state["opens"] += 1
        if state["opens"] == 2:
            state["file_fd"] = fd
        return fd

    def fdopen_fail(fd, *a, **k):
        return FailingFile(fd)

    def close_raise_filefd(fd):
        # raise once when closing the write fd inside inner except (56-57)
        if state["close_raises_left"] > 0 and state["file_fd"] is not None and fd == state["file_fd"]:
            state["close_raises_left"] -= 1
            real_close(fd)
            raise RuntimeError("closefail")
        return real_close(fd)

    monkeypatch.setattr(_os, "open", open_track_fail)
    monkeypatch.setattr(_os, "fdopen", fdopen_fail)
    monkeypatch.setattr(_os, "close", close_raise_filefd)
    with pytest.raises(ValueError, match="Refusing to write file outside invoice directory"):
        billing_docs._write_confined_invoice_file("ok.txt", "hello")

    # successful write but dir_fd close raises in finally -> 65-66
    monkeypatch.setattr(_os, "open", real_open)
    monkeypatch.setattr(_os, "fdopen", real_fdopen)
    monkeypatch.setattr(_os, "close", real_close)
    state2 = {"dir_fd": None, "opens": 0, "raised": False}

    def open_track(*a, **k):
        fd = real_open(*a, **k)
        state2["opens"] += 1
        if state2["opens"] == 1:
            state2["dir_fd"] = fd
        return fd

    def close_raise_dirfd(fd):
        if not state2["raised"] and state2["dir_fd"] is not None and fd == state2["dir_fd"]:
            state2["raised"] = True
            real_close(fd)
            raise RuntimeError("closefail")
        return real_close(fd)

    monkeypatch.setattr(_os, "open", open_track)
    monkeypatch.setattr(_os, "close", close_raise_dirfd)
    out = billing_docs._write_confined_invoice_file("ok2.txt", "hello")
    assert Path(out).exists()

    monkeypatch.setattr(_os, "open", real_open)
    monkeypatch.setattr(_os, "close", real_close)

    # build_walkin_receipt_text gross fallback exception 220-221
    txt = billing_docs.build_walkin_receipt_text(
        [{"desc": "X", "qty": "bad", "price": 5, "gross": 0}],
        total_gross=0,
        method="Cash",
        payment_id=1,
    )
    assert "WALK-IN RECEIPT" in txt


# ---------------------------------------------------------------------------
# services/__init__.py and reports_metrics.py missing branches
# ---------------------------------------------------------------------------


def test_services_facade_alias_branches(monkeypatch):
    import importlib
    import app_core.services._monolith as mon

    # ensure target attrs exist on monolith so assignment branches (28,32) execute
    if not hasattr(mon, "net_types"):
        monkeypatch.setattr(mon, "net_types", ("net",), raising=False)
    else:
        monkeypatch.setattr(mon, "net_types", getattr(mon, "net_types"))
    if not hasattr(mon, "_print_raw"):
        monkeypatch.setattr(mon, "_print_raw", lambda *a, **k: None, raising=False)

    services_pkg.__dict__.pop("net_types", None)
    services_pkg.__dict__.pop("_print_raw", None)
    importlib.reload(services_pkg)
    assert "net_types" in services_pkg.__dict__ or hasattr(services_pkg, "net_types")
    assert "_print_raw" in services_pkg.__dict__ or hasattr(services_pkg, "_print_raw")


def test_reports_metrics_unparseable_date_branch(clean_db, monkeypatch):
    # 104: continue on rows with unparseable dates (row must pass SQL overlap filter)
    from app_core import db as core_db
    from app_core.domain.dates import parse_date as orig_parse_date

    core_db.execute(
        "INSERT INTO reservations (guest_name, source, room_id, agreed_nightly_rate, check_in_date, check_out_date, status) "
        "VALUES (?,?,?,?,?,?,?)",
        ("BadDates", "Direct", 1, 100.0, "2026-01-02", "2026-01-04", "Reserved"),
    )

    def fake_parse(v):
        if str(v) in ("2026-01-02", "2026-01-04"):
            return None
        return orig_parse_date(v)

    monkeypatch.setattr(reports_metrics, "parse_date", fake_parse)
    out = reports_metrics.occupancy_adr_revpar("2026-01-01", "2026-01-05")
    assert out["occupied_room_nights"] == 0
