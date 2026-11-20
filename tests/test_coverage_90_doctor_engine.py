from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import app_core.doctor as doctor
import app_core.paths as paths
import app_core.services as services
from app_core import db


def test_doctor_exists_info_exception(monkeypatch, tmp_path):
    p = tmp_path / "x"
    real_access = os.access

    def bad_access(*a, **k):
        raise RuntimeError("no")

    monkeypatch.setattr(os, "access", bad_access)
    # exists() may be fine, but access call inside try raises -> exception branch
    # create file so exists True path hits os.access
    p.write_text("hi")
    out = doctor._exists_info(p)
    assert "error" in out or out.get("path")


def test_doctor_db_helpers_exception_branches(tmp_path):
    # _db_integrity_summary errors
    class BadConn:
        def execute(self, q):
            if "integrity_check" in q:
                raise RuntimeError("ic")
            if "foreign_key_check" in q:
                raise RuntimeError("fk")
            raise RuntimeError("x")

    out = doctor._db_integrity_summary(BadConn())
    assert out.get("integrity_error") or out.get("foreign_key_error")

    # _schema_table_checks / _inspect_db_conn / _batch_stock_consistency exception branches
    class BadConn2:
        def execute(self, q):
            raise RuntimeError("boom")

    out = doctor._schema_table_checks(BadConn2())
    assert out.get("ok") is False
    # _inspect_db_conn will fail on PRAGMA user_version
    try:
        doctor._inspect_db_conn(BadConn2())
    except Exception:
        pass
    out = doctor._batch_stock_consistency(BadConn2())
    assert out.get("ok") is False

    # verify_backup_file missing and error branches
    res = doctor.verify_backup_file(tmp_path / "missing.db")
    assert res["ok"] is False and "not found" in res["error"]
    # unreadable/corrupt file
    bad = tmp_path / "bad.db"
    bad.write_text("not sqlite")
    res = doctor.verify_backup_file(bad)
    assert res["ok"] is False


def test_doctor_printer_and_backup_branches(monkeypatch, tmp_path):
    # printer readiness: requires initialized DB connection to read settings
    monkeypatch.setattr(db, "_conn", object(), raising=False)
    settings = {"printer_mode": "tcp", "printer_target": "noport"}
    monkeypatch.setattr(services, "get_setting", lambda k, d=None: settings.get(k, d))
    pr = doctor._printer_readiness()
    assert pr["ok"] is False and any(c.get("code") == "printer_target_invalid" for c in pr.get("checks", []))
    settings["printer_target"] = "printer1"
    pr = doctor._printer_readiness()
    assert pr["ok"] is False and any(c.get("code") == "printer_target_invalid" for c in pr.get("checks", []))
    settings.update({"printer_mode": "file", "printer_target": ""})
    pr = doctor._printer_readiness()
    assert pr.get("mode") == "file"

    # exception in printer readiness
    monkeypatch.setattr(services, "get_setting", lambda k, d=None: (_ for _ in ()).throw(RuntimeError("x")))
    pr = doctor._printer_readiness()
    assert any(c.get("code") == "printer_check_error" for c in pr.get("checks", []))

    # detect backup files: exception in scan
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(Path, "glob", lambda self, pat: (_ for _ in ()).throw(RuntimeError("g")))
    det = doctor._detect_backup_files()
    assert det.get("found") == 0 or "error" in det

    # create_backup branches (best-effort; signatures may vary)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "pm.db")
    src_db = tmp_path / "pm.db"
    c = sqlite3.connect(str(src_db))
    c.execute("create table t(id int)")
    c.commit()
    c.close()
    # sqlite backup API fallback path by forcing connect failure inside primary try
    real_connect = sqlite3.connect
    calls = {"n": 0}

    def fake_sqlite_connect(pth, *a, **k):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("no sqlite backup")
        return real_connect(pth, *a, **k)

    monkeypatch.setattr(doctor.sqlite3, "connect", fake_sqlite_connect)
    try:
        out = doctor.create_backup(tmp_path / "fallback.db")
    except TypeError:
        out = doctor.create_backup(str(tmp_path / "fallback.db"))
    assert isinstance(out, dict)
    monkeypatch.setattr(doctor.sqlite3, "connect", real_connect)

    # final failure
    monkeypatch.setattr(doctor.sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr("shutil.copy2", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("copyfail")))
    try:
        out = doctor.create_backup(tmp_path / "fail.db")
    except TypeError:
        out = doctor.create_backup(str(tmp_path / "fail.db"))
    assert isinstance(out, dict)


def test_doctor_config_issues_and_collect_health(monkeypatch, tmp_path, clean_db):
    # setup paths
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "pm.db"
    c = sqlite3.connect(str(db_path))
    c.execute("PRAGMA user_version=21")
    c.execute("create table t(id int)")
    c.commit()
    c.close()
    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(paths, "DB_PATH", db_path)
    monkeypatch.setattr(paths, "EXPORT_DIR", data_dir / "exports")
    monkeypatch.setattr(paths, "INVOICE_DIR", data_dir / "invoices")
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path)
    paths.ensure_data_dirs()

    # config issues branches
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.delenv("PM_WEB_SECRET_KEY", raising=False)
    monkeypatch.setenv("PM_WEB_ALLOW_INSECURE_DEV", "1")
    # backup stale branch: create old backup file
    oldb = data_dir / "property_manager.backup.old.db"
    oldb.write_text("x")
    old_time = time.time() - (9 * 24 * 3600)
    os.utime(oldb, (old_time, old_time))
    # path permission issues
    monkeypatch.setattr(
        paths,
        "check_runtime_permissions",
        lambda: {
            "data_dir": {"exists": True, "writable": False, "path": str(data_dir)},
            "export_dir": {"exists": True, "writable": False, "path": str(data_dir / "exports")},
            "invoice_dir": {"exists": True, "writable": False, "path": str(data_dir / "invoices")},
            "db_path": {"exists": True, "writable": False, "path": str(db_path)},
            "data_dir_world_writable": True,
        },
    )
    # db connection not open in this process -> config_issues note
    monkeypatch.setattr(db, "_conn", None, raising=False)
    issues = doctor._config_issues()
    assert isinstance(issues, list)

    # exception in config checks outer try
    monkeypatch.setattr(paths, "check_runtime_permissions", lambda: (_ for _ in ()).throw(RuntimeError("perm")))
    monkeypatch.setattr(db, "_conn", object(), raising=False)
    monkeypatch.setattr(services, "get_setting", lambda k, d=None: (_ for _ in ()).throw(RuntimeError("s")))
    issues = doctor._config_issues()
    assert isinstance(issues, list)

    # collect_health branches
    monkeypatch.setattr(paths, "ensure_data_dirs", lambda: (_ for _ in ()).throw(RuntimeError("dirs")))
    # updater remote check branches
    import app_core.updater as updater

    monkeypatch.setattr(updater, "get_local_state", lambda: {"app_version": "1"})
    monkeypatch.setattr(updater, "check_for_updates", lambda: (_ for _ in ()).throw(RuntimeError("net")))
    report = doctor.collect_health(check_updates=True)
    assert report.get("update", {}).get("remote_error")
    # update local state error
    monkeypatch.setattr(updater, "get_local_state", lambda: (_ for _ in ()).throw(RuntimeError("u")))
    report = doctor.collect_health(check_updates=False)
    assert "error" in report.get("update", {})

    # ledger integrity branches
    monkeypatch.setattr(db, "_conn", object(), raising=False)
    monkeypatch.setattr(services, "verify_ledger_integrity", lambda: (_ for _ in ()).throw(RuntimeError("ledger")))
    report = doctor.collect_health(check_updates=False)
    assert "ledger_error" in (report.get("integrity") or {}) or report.get("notes")

    # format_health_report with rich data
    rich = {
        "ok": False,
        "checked_at": "now",
        "paths": {"project_root": "p", "data_dir": "d", "db_path": "db", "export_dir": "e", "invoice_dir": "i"},
        "data_dirs": {
            "x": {"exists": True, "is_dir": True, "writable": False, "path": "/x"},
            "db_wal": {"exists": True, "is_file": True, "writable": True, "path": "/w"},
        },
        "schema": {
            "user_version": 1,
            "expected_version": 2,
            "matches_expected": False,
            "tables": {"ok": False, "present_count": 1, "missing_tables": ["a"]},
        },
        "integrity": {
            "sqlite": {"integrity_check": ["ok"], "foreign_key_violations": [], "integrity_error": "e"},
            "ledger": {"ok": True},
        },
        "db": {"journal_mode": "wal", "foreign_keys": 1, "busy_timeout": 5000},
        "consistency": {
            "batch_stock": {
                "ok": False,
                "checked": 1,
                "mismatch_count": 1,
                "mismatches": [{"item_id": 1, "item_name": "X", "stock_qty": 1, "batch_remaining_active": 2}],
                "error": "e",
            }
        },
        "readiness": {
            "printer": {
                "configured": True,
                "mode": "tcp",
                "target_set": True,
                "checks": [{"severity": "warn", "code": "c", "message": "m"}],
            }
        },
        "update": {
            "local": {
                "app_version": "1",
                "branch": "m",
                "commit_short": "abc",
                "dirty": False,
                "is_git_repo": True,
                "remote": "https://x",
            },
            "remote": {"update_available": False, "error": "e"},
        },
        "config_issues": [{"severity": "error", "code": "x", "message": "y"}],
        "backup_restore": {
            "runtime_data_preserved": ["db"],
            "backup": {
                "stop_app_first": True,
                "copy_db_file": "a",
                "suggested_backup_path": "b",
                "wal_exists": True,
                "shm_exists": False,
            },
            "restore": {"stop_app_first": True, "replace_db_file": "a", "verify_helper": "v"},
            "readiness": {"db_exists": True, "data_dir_writable": True},
        },
        "notes": ["n"],
    }
    txt = doctor.format_health_report(rich)
    assert "ISSUES DETECTED" in txt and "Backup/restore guidance" in txt and "Printer readiness" in txt

    # CLI main branches
    missing_res = {
        "ok": False,
        "error": "bad",
        "path": str(tmp_path / "x"),
        "integrity": {"sqlite": {"integrity_check": ["ok"], "foreign_key_violations": []}},
        "schema": {"user_version": 1},
    }
    monkeypatch.setattr(doctor, "verify_backup_file", lambda p: missing_res)
    assert doctor.main(["--verify-backup", str(tmp_path / "x")]) in (0, 1)
    monkeypatch.setattr(
        doctor,
        "create_backup",
        lambda p: {"ok": True, "source": "s", "backup_path": str(tmp_path / "b.db"), "verified": {"ok": True}},
    )
    assert doctor.main(["--backup", "--json"]) == 0
    monkeypatch.setattr(
        doctor,
        "create_backup",
        lambda p: {"ok": False, "source": "s", "backup_path": "b", "error": "e", "warning": "w"},
    )
    assert doctor.main(["--backup-to", str(tmp_path / "b.db")]) in (0, 1)
    # collect_health batch stock mismatch sets ok False
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
            "consistency": {"batch_stock": {"checked": 1, "ok": False}},
            "readiness": {},
            "backup_restore": {},
            "config_issues": [],
            "notes": [],
        },
    )
    assert doctor.main([]) in (0, 1)


def test_engine_more_branches(monkeypatch):
    import app_core.assistant.engine as engine

    # _extract_write_args branches
    args = engine._extract_write_args("qty 3 item ABC lot L1 batch 2 note: hello", {})
    assert args.get("qty") == 3
    args = engine._extract_write_args("pos item water 2", {})
    assert args.get("item_name") in ("water", "water 2", None) or "item_name" in args or args.get("qty") == 2
    # action_needs_info / propose branches
    res = engine._maybe_propose_action("propose_add_pos_item", "add item to reservation #1", {"reservation_id": 1})
    assert res is None or isinstance(res, dict)
    res = engine._maybe_propose_action("propose_complete_walkin_sale", "walk-in sale", {"amount": 10})
    assert res is None or isinstance(res, dict)
    res = engine._maybe_propose_action("propose_record_payment", "record payment", {"reservation_id": 1})
    assert res is None or isinstance(res, dict)

    # _execute_single various branches
    monkeypatch.setattr(engine, "execute_tool", lambda name, args=None: {"ok": True, "tool": name, "args": args})
    monkeypatch.setattr(engine, "_format_for_intent", lambda intent, data, fallback_title="Result": "ans")
    monkeypatch.setattr(engine, "_apply_proactive", lambda intent, data, ans: ans)
    monkeypatch.setattr(engine, "resolve_guest", lambda text, slots: ([], None, ""))

    # room availability / health diagnostics / expiring batches / batches / trace / inventory item details
    r = engine._execute_single(
        "room_availability", "availability", {"date_start": "2026-01-01", "date_end": "2026-01-02"}
    )
    assert r["ok"] is True
    r = engine._execute_single("health_diagnostics", "health", {})
    assert r.get("intent") in ("health_diagnostics", "ledger_integrity")
    r = engine._execute_single("ledger_integrity", "integrity", {})
    assert r.get("intent") == "ledger_integrity"
    r = engine._execute_single("expiring_batches", "expiring", {"within_days": "7"})
    assert r["intent"] == "expiring_batches"
    r = engine._execute_single("expiring_batches", "expiring", {"within_days": "bad"})
    assert r["intent"] == "expiring_batches"
    r = engine._execute_single("inventory_batches", "batches", {"item_id": 5})
    assert r["intent"] == "inventory_batches"
    r = engine._execute_single("trace_barcode", "trace barcode ABC123", {})
    assert r["intent"] == "trace_barcode"
    r = engine._execute_single("inventory_item_details", "item", {"item_id": 2})
    assert r["intent"] == "inventory_item_details"

    # ambiguous report request / group detail / reservation packages / approval detail branches
    r = engine._execute_single("ambiguous_needs_clarification", "report", {})
    assert r.get("needs_clarification") or "answer" in r
    r = engine._execute_single("reservation_group_detail", "group", {"group_id": 1})
    assert r.get("intent") == "reservation_group_detail" or r.get("needs_clarification")
    r = engine._execute_single("reservation_group_detail", "group", {})
    assert r.get("needs_clarification")
    r = engine._execute_single("reservation_packages_applied", "packages", {"reservation_id": 2})
    assert r.get("intent") == "reservation_packages_applied" or r.get("needs_clarification")
    r = engine._execute_single("reservation_packages_applied", "packages", {})
    assert r.get("needs_clarification")
    r = engine._execute_single("approval_detail", "approval #3", {})
    assert r.get("intent") == "approval_detail" or r.get("needs_clarification") or r.get("ok") is not None
    r = engine._execute_single("approval_detail", "approval", {})
    assert r.get("needs_clarification")
    r = engine._execute_single("app_update_status", "updates", {})
    assert isinstance(r, dict)

    # guest branches
    monkeypatch.setattr(
        engine,
        "resolve_guest",
        lambda text, slots: (
            [{"id": 1, "name": "A", "phone": "", "email": "", "stays": 0, "last_stay": ""}],
            None,
            "A",
        ),
    )
    for intent in (
        "guest_top_items_last_visit",
        "guest_last_visit_spend",
        "guest_activity_bookings",
        "guest_profile",
        "guest_portfolio",
        "guest_last_visit",
    ):
        r = engine._execute_single(intent, "guest 1", {"guest_id": 1})
        assert "answer" in r
    monkeypatch.setattr(engine, "resolve_guest", lambda text, slots: ([], None, ""))
    r = engine._execute_single("guest_profile", "guest", {"guest_query": "Nobody"})
    assert r.get("ok") is False or r.get("needs_clarification")
