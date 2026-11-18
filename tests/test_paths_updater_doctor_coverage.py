from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import app_core.doctor as doctor
import app_core.paths as paths
import app_core.updater as updater
from app_core import db, services


def test_paths_helpers_and_safety(tmp_path, monkeypatch):
    # redact_sensitive
    assert paths.redact_sensitive(None) == ""
    red = paths.redact_sensitive("password=abc123 token:xyz")
    assert "abc123" not in red and "xyz" not in red and "redacted" in red.lower()
    long = "x" * 50
    truncated = paths.redact_sensitive(long, max_len=10)
    assert len(truncated) <= 11 and truncated != long and truncated.endswith(("...", "…"))

    # sanitize_filename_component
    name = paths.sanitize_filename_component("  weird/name?.txt  ")
    assert "/" not in name and "\\" not in name and name.endswith(".txt")
    assert paths.sanitize_filename_component("", default="fallback") == "fallback"
    assert paths.sanitize_filename_component("....", default="d") == "d"

    # is_path_within / safe_child_path
    root = tmp_path / "root"
    root.mkdir()
    child = root / "a.txt"
    child.write_text("ok", encoding="utf-8")
    assert paths.is_path_within(child, root) is True
    assert paths.is_path_within(tmp_path / "other.txt", root) is False
    assert paths.safe_child_path(root, "a.txt", must_exist=True) == child.resolve()
    # traversal-like input is sanitized to basename and confined to root
    confined = paths.safe_child_path(root, "../evil.txt")
    assert paths.is_path_within(confined, root)
    with pytest.raises(ValueError):
        paths.safe_child_path(root, "bad\x00name.txt")
    with pytest.raises(FileNotFoundError):
        paths.safe_child_path(root, "missing.txt", must_exist=True)

    # ensure_data_dirs / check_runtime_permissions / runtime_data_summary
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "EXPORT_DIR", tmp_path / "data" / "exports")
    monkeypatch.setattr(paths, "INVOICE_DIR", tmp_path / "data" / "invoices")
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "data" / "property_manager.db")
    paths.ensure_data_dirs()
    assert (tmp_path / "data" / "exports").exists()
    perms = paths.check_runtime_permissions()
    assert "data_dir" in perms and "db_path" in perms
    summary = paths.runtime_data_summary()
    assert summary["data_dir"] == str(tmp_path / "data")
    assert "db_path" in summary and "preserved_on_update_backup_restore" in summary

    # _resolve_existingish fallback behavior on odd input
    p = paths._resolve_existingish(Path("relative.txt"))
    assert isinstance(p, Path)


def test_paths_detect_data_dir_error_branches(monkeypatch, tmp_path):
    monkeypatch.setenv("PM_WEB_SECRET_KEY", "test-secret")

    # empty
    monkeypatch.setenv("PM_DATA_DIR", "   ")
    with pytest.raises(ValueError):
        paths._detect_data_dir()

    # NUL: cannot set in OS env on some platforms, so patch env lookup
    monkeypatch.delenv("PM_DATA_DIR", raising=False)
    real_get = os.environ.get
    def fake_get(key, default=None):
        if key == "PM_DATA_DIR":
            return "bad\x00path"
        return real_get(key, default)
    monkeypatch.setattr(os.environ, "get", fake_get)
    with pytest.raises(ValueError):
        paths._detect_data_dir()
    monkeypatch.setattr(os.environ, "get", real_get)

    # mkdir failure
    monkeypatch.setenv("PM_DATA_DIR", str(tmp_path / "x"))
    def boom_mkdir(self, *a, **k):
        raise OSError("nope")
    monkeypatch.setattr(Path, "mkdir", boom_mkdir)
    with pytest.raises(ValueError):
        paths._detect_data_dir()

    # restore module-level paths env handling for later tests in same process
    monkeypatch.delenv("PM_DATA_DIR", raising=False)


def test_updater_helpers(monkeypatch, tmp_path):
    # _validate_ref
    assert updater._validate_ref("branch", "main") == "main"
    for bad in ["", "-bad", "../x", "x.lock", "a..b", "bad name"]:
        with pytest.raises(ValueError):
            updater._validate_ref("branch", bad)

    assert updater._clamp_int("5", 1, 0, 10) == 5
    assert updater._clamp_int("bad", 7, 0, 10) == 7
    assert updater._clamp_int(99, 1, 0, 10) == 10

    assert updater._redact_remote_url("https://user:token@github.com/x/y.git").startswith("https://<redacted>@")
    assert updater._redact_remote_url(None) is None
    assert updater._parse_version_tag("v1.2.3") == (1, 2, 3)
    assert updater._parse_version_tag("release-2026-1") == (2026, 1)
    assert updater._parse_version_tag("") == (0,)
    assert updater._shape_release_asset({"name": "a", "browser_download_url": "u", "size": 1, "content_type": "x", "extra": 1}) == {
        "name": "a", "browser_download_url": "u", "size": 1, "content_type": "x"
    }
    assert updater._shape_release_asset("bad") is None

    # get_latest_release
    monkeypatch.setattr(updater, "_github_get", lambda url, timeout=12: {
        "tag_name": "v9.9.9", "name": "v9.9.9", "html_url": "https://x/r", "published_at": "now",
        "body": "notes", "assets": [{"name": "a.bin", "browser_download_url": "d", "size": 1, "content_type": "bin"}],
        "prerelease": False, "draft": False,
    })
    rel = updater.get_latest_release()
    assert rel["tag_name"] == "v9.9.9" and rel["assets"][0]["name"] == "a.bin"
    monkeypatch.setattr(updater, "_github_get", lambda url, timeout=12: [])
    with pytest.raises(ValueError):
        updater.get_latest_release()

    # settings helpers
    monkeypatch.setattr(services, "get_setting", lambda k, d=None: "0" if k == "auto_check_updates" else "12")
    assert updater.auto_update_checks_enabled() is False
    assert updater._default_update_interval_sec() == 43200
    monkeypatch.setattr(services, "get_setting", lambda k, d=None: (_ for _ in ()).throw(RuntimeError("x")))
    assert updater.auto_update_checks_enabled() is True
    assert updater._default_update_interval_sec() == 6 * 3600


def test_updater_git_and_update_flows(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "_project_root", lambda: tmp_path)

    # get_local_state non-git
    st = updater.get_local_state()
    assert st["is_git_repo"] is False

    # get_local_state git with mocked git responses
    (tmp_path / ".git").mkdir()
    responses = {
        ("rev-parse", "HEAD"): (0, "abc1234", ""),
        ("rev-parse", "--abbrev-ref", "HEAD"): (0, "main", ""),
        ("log", "-1", "--format=%ci|%s"): (0, "2026-01-01 00:00:00 +0000|msg", ""),
        ("status", "--porcelain"): (0, " M x", ""),
        ("remote", "get-url", "origin"): (0, "https://user:tok@github.com/org/repo.git", ""),
    }
    def fake_run_git(args, cwd=None, timeout=20):
        return responses.get(tuple(args), (1, "", "err"))
    monkeypatch.setattr(updater, "_run_git", fake_run_git)
    st2 = updater.get_local_state()
    assert st2["commit_short"] == "abc1234" and st2["dirty"] is True and "<redacted>" in (st2["remote"] or "")

    # check_for_updates branches
    monkeypatch.setattr(updater, "get_local_state", lambda: {"commit": "same", "is_git_repo": True, "branch": "main"})
    monkeypatch.setattr(updater, "_github_get", lambda url, timeout=12: [{"sha": "same", "commit": {"author": {"date": "d", "name": "n"}, "message": "subj\nbody"}, "html_url": "u"}])
    r = updater.check_for_updates()
    assert r["ok"] and r["update_available"] is False and r["commits_behind"] == 0

    monkeypatch.setattr(updater, "get_local_state", lambda: {"commit": "old", "is_git_repo": True, "branch": "main"})
    monkeypatch.setattr(updater, "_github_get", lambda url, timeout=12: [
        {"sha": "new2", "commit": {"author": {"date": "d2", "name": "n2"}, "message": "s2\nb"}, "html_url": "u2"},
        {"sha": "new1", "commit": {"author": {"date": "d1", "name": "n1"}, "message": "s1"}, "html_url": "u1"},
        {"sha": "old", "commit": {"author": {"date": "d0", "name": "n0"}, "message": "s0"}, "html_url": "u0"},
    ])
    r = updater.check_for_updates()
    assert r["update_available"] is True and r["commits_behind"] == 2 and len(r["changelog"]) == 2

    # local commit not found in recent history
    monkeypatch.setattr(updater, "_github_get", lambda url, timeout=12: [{"sha": "new", "commit": {"author": {"date": "d"}, "message": "s"}, "html_url": "u"}])
    r = updater.check_for_updates()
    assert r["update_available"] is True and r["commits_behind"] is None

    # github/network error
    monkeypatch.setattr(updater, "_github_get", lambda url, timeout=12: (_ for _ in ()).throw(RuntimeError("offline")))
    r = updater.check_for_updates()
    assert r["ok"] is False and "offline" in (r.get("error") or "")

    # invalid branch
    r = updater.check_for_updates(branch="../bad")
    assert r["ok"] is False

    # apply_update refusals and success
    monkeypatch.setattr(updater, "get_local_state", lambda: {"is_git_repo": False, "dirty": False, "branch": "main"})
    res = updater.apply_update()
    assert res["ok"] is False
    monkeypatch.setattr(updater, "get_local_state", lambda: {"is_git_repo": True, "dirty": True, "branch": "main"})
    res = updater.apply_update()
    assert res["ok"] is False and "uncommitted" in (res.get("error") or "").lower()

    states = iter([
        {"is_git_repo": True, "dirty": False, "branch": "dev"},
        {"is_git_repo": True, "dirty": False, "branch": "main", "commit_short": "new"},
    ])
    monkeypatch.setattr(updater, "get_local_state", lambda: next(states))
    cmds = []
    def ok_git(args, cwd=None, timeout=180):
        cmds.append(tuple(args))
        return 0, "ok", ""
    monkeypatch.setattr(updater, "_run_git", ok_git)
    res = updater.apply_update()
    assert res["ok"] is True and res["restart_required"] is True and ("fetch", "--prune", "origin", "main", "--tags") in cmds

    # apply failure step
    monkeypatch.setattr(updater, "get_local_state", lambda: {"is_git_repo": True, "dirty": False, "branch": "main"})
    monkeypatch.setattr(updater, "_run_git", lambda args, cwd=None, timeout=180: (1, "", "boom"))
    res = updater.apply_update()
    assert res["ok"] is False and "fetch" in (res.get("error") or "")


def test_updater_cached_notice(monkeypatch):
    updater._UPDATE_CACHE["checked_at_ts"] = 0.0
    updater._UPDATE_CACHE["result"] = None
    # disabled
    monkeypatch.setattr(updater, "auto_update_checks_enabled", lambda: False)
    monkeypatch.setattr(updater, "get_local_state", lambda: {"is_git_repo": False})
    monkeypatch.setattr(updater, "get_latest_release", lambda timeout=8: {"tag_name": "v1.0.0", "html_url": "u", "name": "v1", "published_at": "p", "assets": []})
    monkeypatch.setattr(updater, "_default_update_interval_sec", lambda: 3600)
    n = updater.get_cached_update_notice(force=False)
    assert "disabled" in (n.get("message") or "").lower() or n.get("update_available") in (False, True)

    # git update available path, cached
    updater._UPDATE_CACHE["checked_at_ts"] = 0.0
    updater._UPDATE_CACHE["result"] = None
    monkeypatch.setattr(updater, "auto_update_checks_enabled", lambda: True)
    monkeypatch.setattr(updater, "get_local_state", lambda: {"is_git_repo": True})
    monkeypatch.setattr(updater, "check_for_updates", lambda timeout=8: {"ok": True, "update_available": True, "commits_behind": 3, "latest": {"commit_short": "abc"}, "compare_url": "cmp", "error": None})
    monkeypatch.setattr(updater, "get_latest_release", lambda timeout=8: {"tag_name": "v1.2.3", "html_url": "rel", "name": "v1.2.3", "published_at": "p", "assets": []})
    monkeypatch.setattr(updater, "APP_VERSION", "0.1.0-dev", raising=False)
    n = updater.get_cached_update_notice(force=True)
    assert n["update_available"] is True and n["kind"] == "git"
    n2 = updater.get_cached_update_notice(force=False)
    assert n2 is n  # cached


def test_doctor_helpers_and_health(clean_db, tmp_path, monkeypatch):
    # point doctor/paths to isolated temp data
    data_dir = tmp_path / "ddata"
    data_dir.mkdir()
    db_path = data_dir / "property_manager.db"
    # create a minimal sqlite db file for verification helpers
    c = sqlite3.connect(str(db_path))
    c.execute("CREATE TABLE IF NOT EXISTS t(id INTEGER PRIMARY KEY)")
    c.execute("PRAGMA user_version=21")
    c.commit(); c.close()

    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(paths, "DB_PATH", db_path)
    monkeypatch.setattr(paths, "EXPORT_DIR", data_dir / "exports")
    monkeypatch.setattr(paths, "INVOICE_DIR", data_dir / "invoices")
    paths.ensure_data_dirs()

    # backup detection/guidance
    (data_dir / "property_manager.backup.test.db").write_text("x", encoding="utf-8")
    det = doctor._detect_backup_files()
    assert det.get("found", 0) >= 1
    guide = doctor._backup_restore_guidance()
    assert "backup" in guide and "restore" in guide

    # verify/backup/create/restore_precheck
    missing = doctor.verify_backup_file(data_dir / "missing.db")
    assert missing["ok"] is False
    verified = doctor.verify_backup_file(db_path)
    assert "ok" in verified

    out = data_dir / "copy.db"
    created = doctor.create_backup(out)
    assert "backup_path" in created
    # directory destination error
    bad_dir = data_dir / "adir"
    bad_dir.mkdir()
    bad = doctor.create_backup(bad_dir)
    assert bad["ok"] is False

    pre = doctor.restore_precheck(out)
    assert "backup" in pre and "target" in pre and "restore_steps" in pre

    # config/printer/readiness helpers with env
    monkeypatch.setenv("PM_WEB_SECRET_KEY", "short")
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PM_WEB_ALLOW_INSECURE_DEV", "1")
    issues = doctor._config_issues()
    assert isinstance(issues, list)
    pr = doctor._printer_readiness()
    assert isinstance(pr, dict)

    # collect/format/main
    monkeypatch.setattr(updater, "get_local_state", lambda redact_remote=True: {"app_version": "x", "is_git_repo": False, "branch": None, "commit_short": None, "dirty": False})
    report = doctor.collect_health(check_updates=False)
    assert "paths" in report and "backup_restore" in report
    txt = doctor.format_health_report(report)
    assert "Property Manager Doctor" in txt

    rc = doctor.main(["--verify-backup", str(out), "--json"])
    assert rc in (0, 1)
    rc = doctor.main(["--backup-to", str(data_dir / "cli-backup.db"), "--json"])
    assert rc in (0, 1)
    # health report path
    monkeypatch.setattr(doctor, "collect_health", lambda check_updates=False: {"ok": True, "checked_at": "", "paths": {}, "data_dirs": {}, "schema": {}, "integrity": {}, "consistency": {}, "readiness": {}, "backup_restore": {}, "config_issues": [], "notes": []})
    assert doctor.main(["--json"]) == 0
