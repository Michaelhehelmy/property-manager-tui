from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from app_core import db, doctor, paths, services, updater


def test_updater_run_git_and_state_branches(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "_project_root", lambda: tmp_path)

    # _run_git error handling (75,77,91,92)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    rc, out, err = updater._run_git(["status"])
    assert rc == 127 and "git not found" in err

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd="git", timeout=1)))
    rc, out, err = updater._run_git(["status"], timeout=1)
    assert rc == 124 and "timed out" in err

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    rc, out, err = updater._run_git(["status"])
    assert rc == 1 and "boom" in err

    # _redact_remote_url exception fallback
    class BadStr:
        def __str__(self):
            raise RuntimeError("x")
    assert updater._redact_remote_url(BadStr()) == "<redacted>"

    # get_local_state git fields with varied responses
    (tmp_path / ".git").mkdir(exist_ok=True)
    responses = {
        ("rev-parse", "HEAD"): (0, "deadbeef", ""),
        ("rev-parse", "--abbrev-ref", "HEAD"): (0, "", ""),  # empty branch
        ("log", "-1", "--format=%ci|%s"): (0, "2026-01-02|subject", ""),
        ("status", "--porcelain"): (0, "", ""),
        ("remote", "get-url", "origin"): (1, "", ""),  # no remote
    }
    monkeypatch.setattr(updater, "_run_git", lambda args, cwd=None, timeout=20: responses.get(tuple(args), (1, "", "")))
    st = updater.get_local_state()
    assert st["is_git_repo"] is True
    assert st["commit_short"] == "deadbee"
    assert st["branch"] is None
    assert st["dirty"] is False
    assert st["remote"] is None


def test_updater_check_apply_and_cached_notice_branches(monkeypatch, tmp_path):
    # invalid branch refusal (343-345)
    res = updater.check_for_updates(branch="../bad")
    assert res["ok"] is False and "Invalid branch" in (res.get("error") or "")

    # no commits returned
    monkeypatch.setattr(updater, "get_local_state", lambda: {"commit": "abc", "is_git_repo": True, "branch": "main"})
    monkeypatch.setattr(updater, "_github_get", lambda url, timeout=12: [])
    res = updater.check_for_updates()
    assert res["ok"] is False and "No commits returned" in (res.get("error") or "")

    # local commit unknown branch -> changelog context and notes
    monkeypatch.setattr(updater, "_github_get", lambda url, timeout=12: [
        {"sha": "new", "commit": {"author": {"date": "d", "name": "n"}, "message": "s\nb"}, "html_url": "u"}
    ])
    monkeypatch.setattr(updater, "get_local_state", lambda: {"commit": None, "is_git_repo": False, "branch": None})
    res = updater.check_for_updates()
    assert res["update_available"] is True and any("Local git commit unknown" in n for n in res.get("notes", []))

    # apply_update invalid branch
    monkeypatch.setattr(updater, "get_local_state", lambda: {"is_git_repo": True, "dirty": False, "branch": "main"})
    bad = updater.apply_update(branch="../bad")
    assert bad["ok"] is False and "Invalid branch" in (bad.get("error") or "")

    # apply_update pull failure path (470-473)
    monkeypatch.setattr(updater, "_project_root", lambda: tmp_path)
    calls = []
    def fake_git(args, cwd=None, timeout=180):
        calls.append(tuple(args))
        if args[:2] == ["pull", "--ff-only"]:
            return 1, "", "conflict"
        return 0, "ok", ""
    monkeypatch.setattr(updater, "_run_git", fake_git)
    monkeypatch.setattr(updater, "get_local_state", lambda: {"is_git_repo": True, "dirty": False, "branch": "main", "commit": "x"})
    res = updater.apply_update()
    assert res["ok"] is False and "pull" in (res.get("error") or "")
    assert res.get("local_after") is not None

    # cached update notice branches
    updater._UPDATE_CACHE["checked_at_ts"] = 0.0
    updater._UPDATE_CACHE["result"] = None
    monkeypatch.setattr(updater, "_default_update_interval_sec", lambda: 3600)
    monkeypatch.setattr(updater, "auto_update_checks_enabled", lambda: True)

    # git repo with no git update but release newer (304-305, 310-311 not hit here)
    monkeypatch.setattr(updater, "get_local_state", lambda: {"is_git_repo": True, "branch": "main"})
    monkeypatch.setattr(updater, "check_for_updates", lambda timeout=8: {
        "ok": True, "update_available": False, "commits_behind": 0, "latest": None, "compare_url": None, "error": None
    })
    monkeypatch.setattr(updater, "get_latest_release", lambda timeout=8: {
        "tag_name": "v9.9.9", "name": "v9.9.9", "html_url": "https://example/release", "published_at": "now", "assets": []
    })
    monkeypatch.setattr(updater, "APP_VERSION", "1.0.0", raising=False)
    n = updater.get_cached_update_notice(force=True, timeout=1)
    assert n["update_available"] is True and n["kind"] == "release"
    assert "New release available" in n.get("message", "")

    # git update plus latest release appends release tag (310-311)
    updater._UPDATE_CACHE["checked_at_ts"] = 0.0
    updater._UPDATE_CACHE["result"] = None
    monkeypatch.setattr(updater, "check_for_updates", lambda timeout=8: {
        "ok": True, "update_available": True, "commits_behind": 2, "latest": {"commit_short": "abc"}, "compare_url": "cmp", "error": None
    })
    monkeypatch.setattr(updater, "get_latest_release", lambda timeout=8: {
        "tag_name": "v2.0.0", "name": "v2.0.0", "html_url": "https://example/release2", "published_at": "now", "assets": []
    })
    n = updater.get_cached_update_notice(force=True, timeout=1)
    assert n["kind"] == "git" and "Latest release: v2.0.0" in n.get("message", "")

    # zero-version parse fallback branch (306-309)
    updater._UPDATE_CACHE["checked_at_ts"] = 0.0
    updater._UPDATE_CACHE["result"] = None
    monkeypatch.setattr(updater, "check_for_updates", lambda timeout=8: {"ok": True, "update_available": False, "error": None})
    monkeypatch.setattr(updater, "get_latest_release", lambda timeout=8: {"tag_name": "custom", "name": "custom", "html_url": "u", "published_at": "", "assets": []})
    monkeypatch.setattr(updater, "APP_VERSION", "not-semver", raising=False)
    n = updater.get_cached_update_notice(force=True, timeout=1)
    assert n["update_available"] is True and n["kind"] == "release"

    # git exception captured (282-283) and release exception captured when no prior error (312-314)
    updater._UPDATE_CACHE["checked_at_ts"] = 0.0
    updater._UPDATE_CACHE["result"] = None
    monkeypatch.setattr(updater, "check_for_updates", lambda timeout=8: (_ for _ in ()).throw(RuntimeError("git fail")))
    monkeypatch.setattr(updater, "get_latest_release", lambda timeout=8: (_ for _ in ()).throw(RuntimeError("rel fail")))
    n = updater.get_cached_update_notice(force=True, timeout=1)
    assert n.get("error") in ("git fail", "rel fail")


def test_doctor_core_helpers_and_branches(tmp_path, monkeypatch):
    # _exists_info exception path
    orig_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        info = doctor._exists_info(tmp_path / "x")
        assert info.get("exists") is False and "error" in info
    finally:
        monkeypatch.setattr(Path, "exists", orig_exists)

    # verify_backup_file stat exception (154-155), close exception (170-171), and sqlite error (163-165)
    dbp = tmp_path / "ok.db"
    c = sqlite3.connect(str(dbp))
    c.execute("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    c.commit(); c.close()
    orig_stat = Path.stat
    real_stat = orig_stat(dbp)
    class _BadSize:
        st_mode = real_stat.st_mode
        st_mtime = getattr(real_stat, "st_mtime", 0)
        @property
        def st_size(self):
            raise RuntimeError("nostat")
    def stat_hook(self, *a, **k):
        if self == dbp:
            return _BadSize()
        return orig_stat(self, *a, **k)
    monkeypatch.setattr(Path, "stat", stat_hook)
    try:
        v = doctor.verify_backup_file(dbp)
        assert "size_bytes" not in v and "error" not in v  # stat failure swallowed
    finally:
        monkeypatch.setattr(Path, "stat", orig_stat)
    # close exception: wrap sqlite3.connect to return conn whose close raises
    real_connect = sqlite3.connect
    class CloseFailConn:
        def __init__(self, real):
            self.real = real
        def __getattr__(self, n):
            return getattr(self.real, n)
        def cursor(self, *a, **k):
            return self.real.cursor(*a, **k)
        def execute(self, *a, **k):
            return self.real.execute(*a, **k)
        def close(self):
            self.real.close()
            raise RuntimeError("close boom")
    def conn_factory(*a, **k):
        return CloseFailConn(real_connect(*a, **k))
    monkeypatch.setattr(sqlite3, "connect", conn_factory)
    v = doctor.verify_backup_file(dbp)
    assert "path" in v  # should complete despite close exception

    # connect failure branch
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("open fail")))
    v = doctor.verify_backup_file(dbp)
    assert v["ok"] is False and "error" in v

    # restore_precheck permission helper exception and notes
    monkeypatch.setattr(sqlite3, "connect", real_connect)
    monkeypatch.setattr(paths, "DB_PATH", dbp)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "check_runtime_permissions", lambda: (_ for _ in ()).throw(RuntimeError("perm fail")), raising=False)
    pre = doctor.restore_precheck(dbp)
    assert "target" in pre and "backup" in pre

    # _detect_backup_files branches
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "nodir")
    det = doctor._detect_backup_files()
    assert det.get("found") == 0
    d = tmp_path / "backups"; d.mkdir()
    (d / "property_manager.backup.1.db").write_text("x", encoding="utf-8")
    monkeypatch.setattr(paths, "DATA_DIR", d)
    det = doctor._detect_backup_files()
    assert det.get("found") >= 1

    # printer readiness branches (configured tcp invalid, file mode, db not initialized, error)
    monkeypatch.setattr(db, "_conn", None)
    pr = doctor._printer_readiness()
    assert any(c.get("code") == "printer_check_skipped" for c in pr.get("checks", []))

    class S:
        pass
    monkeypatch.setattr(db, "_conn", S())
    monkeypatch.setattr(services, "get_setting", lambda k, d="": "tcp" if k == "printer_mode" else "badtarget")
    pr = doctor._printer_readiness()
    assert pr.get("ok") is False
    monkeypatch.setattr(services, "get_setting", lambda k, d="": "file" if k == "printer_mode" else "")
    pr = doctor._printer_readiness()
    assert any(c.get("code") == "printer_file_mode" for c in pr.get("checks", []))
    monkeypatch.setattr(services, "get_setting", lambda k, d="": (_ for _ in ()).throw(RuntimeError("cfg fail")))
    pr = doctor._printer_readiness()
    assert any(c.get("code") == "printer_check_error" for c in pr.get("checks", []))

    # _config_issues branches (db not open note, env warnings, backup stale)
    monkeypatch.setattr(db, "_conn", None)
    monkeypatch.setenv("PM_WEB_SECRET_KEY", "short")
    monkeypatch.setenv("HOST", "1.2.3.4")
    monkeypatch.setenv("PM_WEB_COOKIE_SECURE", "0")
    monkeypatch.setenv("PM_WEB_ALLOW_INSECURE_DEV", "1")
    monkeypatch.setattr(doctor, "_detect_backup_files", lambda: {"found": 1, "latest_age_hours": 999})
    monkeypatch.setattr(paths, "check_runtime_permissions", lambda: {
        "data_dir": {"exists": True, "writable": False, "path": "d"},
        "export_dir": {"exists": True, "writable": True},
        "invoice_dir": {"exists": True, "writable": True},
        "db_path": {"exists": True, "writable": False, "path": "db"},
        "data_dir_world_writable": True,
    }, raising=False)
    issues = doctor._config_issues()
    codes = {i.get("code") for i in issues}
    assert "db_not_open" in codes
    assert "web_secret_weak" in codes or "web_secret_missing" in codes
    assert "backup_stale" in codes
    assert "path_not_writable" in codes

    # create_backup sqlite backup API failure fallback to file copy (244-257)
    monkeypatch.setattr(paths, "DB_PATH", dbp)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    def connect_fail_for_backup(path, *a, **k):
        if str(path).endswith("copy-fallback.db"):
            raise RuntimeError("cant open dst")
        return real_connect(path, *a, **k)
    # easier: make sqlite3.connect raise on destination? create_backup opens src then dst; simulate first dst open failing by raising on second call
    seq = {"n": 0}
    def conn_seq(*a, **k):
        seq["n"] += 1
        if seq["n"] == 2:
            raise RuntimeError("dst fail")
        return real_connect(*a, **k)
    monkeypatch.setattr(sqlite3, "connect", conn_seq)
    out = tmp_path / "copy-fallback.db"
    created = doctor.create_backup(out)
    assert "fallback" in created or "backup_path" in created

    # collect_health / format / main strict and verify/backup CLI formatting
    monkeypatch.setattr(sqlite3, "connect", real_connect)
    monkeypatch.setattr(doctor, "_detect_backup_files", lambda: {"found": 0, "recent_backups": []})
    monkeypatch.setattr(updater, "get_local_state", lambda: {"app_version": "1", "branch": "m", "commit_short": "abc", "dirty": False, "is_git_repo": True, "remote": "https://user:tok@x/y"})
    monkeypatch.setattr(doctor, "_config_issues", lambda: [])
    rep = doctor.collect_health(check_updates=False)
    rep["consistency"] = {"batch_stock": {"ok": False, "checked": True, "mismatch_count": 1, "mismatches": [{"item_id":1,"item_name":"X","stock_qty":1,"batch_remaining_active":0}]}}
    txt = doctor.format_health_report(rep)
    assert "SPMS Doctor" in txt and "Batch/stock consistency" in txt

    # main --strict with checked mismatch
    monkeypatch.setattr(doctor, "collect_health", lambda check_updates=False: {"ok": True, "checked_at": "", "paths": {}, "data_dirs": {}, "schema": {}, "integrity": {}, "consistency": {"batch_stock": {"checked": True, "ok": False}}, "readiness": {}, "backup_restore": {}, "config_issues": [], "notes": []})
    assert doctor.main(["--strict"]) == 1

    # main verify/backup human output branches
    monkeypatch.setattr(doctor, "verify_backup_file", lambda p: {"ok": False, "path": str(p), "error": "bad", "integrity": {"sqlite": {"integrity_check": ["ok"], "foreign_key_violations": []}}, "schema": {"user_version": 1}})
    assert doctor.main(["--verify-backup", str(dbp)]) == 1
    monkeypatch.setattr(doctor, "create_backup", lambda p=None: {"ok": True, "source": "s", "backup_path": str(p or "b"), "warning": "w", "verified": {"ok": True}})
    assert doctor.main(["--backup", "--backup-to", str(tmp_path / "x.db")]) == 0
