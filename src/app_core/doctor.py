"""Operational health/doctor checks for Property Manager.

Provides a read-only snapshot of runtime paths, DB/schema/integrity status,
data directories, update/config/printer readiness, and backup/restore guidance.
Safe to run as: `python -m app_core.doctor`
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import db, paths

_EXPECTED_TABLES = [
    "settings", "rooms", "rate_plans", "guests", "reservations", "inventory",
    "inventory_batches", "inventory_logs", "recipe_components", "meals",
    "meal_ingredients", "activities", "pos_tabs", "expense_categories",
    "operational_expenses", "payments", "general_ledger", "audit_log",
    "app_metadata", "assistant_feedback",
]


def _exists_info(p: Path) -> dict[str, Any]:
    try:
        return {
            "path": str(p),
            "exists": p.exists(),
            "is_dir": p.is_dir() if p.exists() else False,
            "is_file": p.is_file() if p.exists() else False,
            "writable": os.access(str(p), os.W_OK) if p.exists() else os.access(str(p.parent), os.W_OK),
        }
    except Exception as e:
        return {"path": str(p), "exists": False, "error": str(e)}


def _db_integrity_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "integrity_check": None, "foreign_key_violations": None}
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        vals = [r[0] for r in rows] if rows else []
        out["integrity_check"] = vals[:10] if vals else []
        out["ok_integrity"] = (len(vals) == 1 and str(vals[0]).lower() == "ok")
    except Exception as e:
        out["integrity_error"] = str(e)
        out["ok_integrity"] = False
    try:
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        out["foreign_key_violations"] = [tuple(r) for r in fk[:20]]
        out["ok_foreign_keys"] = len(fk) == 0
    except Exception as e:
        out["foreign_key_error"] = str(e)
        out["ok_foreign_keys"] = False
    out["ok"] = bool(out.get("ok_integrity")) and bool(out.get("ok_foreign_keys"))
    return out


def _schema_table_checks(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        missing = [t for t in _EXPECTED_TABLES if t not in present]
        return {"ok": len(missing) == 0, "missing_tables": missing, "present_count": len(present)}
    except Exception as e:
        return {"ok": False, "error": str(e), "missing_tables": _EXPECTED_TABLES[:], "present_count": 0}


def _batch_stock_consistency(conn: sqlite3.Connection) -> dict[str, Any]:
    """Best-effort hook: compare tracked inventory totals vs batch remaining totals."""
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "inventory" not in tables or "inventory_batches" not in tables:
            return {"ok": True, "checked": False, "reason": "tables_missing"}
        q = """
        SELECT i.id, i.item_name, COALESCE(i.stock_qty,0) AS stock_qty,
               COALESCE(SUM(CASE WHEN b.status='active' THEN b.quantity_remaining ELSE 0 END),0) AS batch_remaining,
               COALESCE(i.track_batches,0) AS track_batches
        FROM inventory i
        LEFT JOIN inventory_batches b ON b.item_id = i.id
        GROUP BY i.id
        """
        rows = conn.execute(q).fetchall()
        mismatches = []
        for r in rows:
            stock = float(r["stock_qty"] or 0)
            br = float(r["batch_remaining"] or 0)
            if abs(stock - br) > 1e-6:
                mismatches.append({
                    "item_id": r["id"], "item_name": r["item_name"],
                    "stock_qty": stock, "batch_remaining_active": br,
                    "track_batches": int(r["track_batches"] or 0),
                })
        return {
            "ok": len(mismatches) == 0,
            "checked": True,
            "mismatch_count": len(mismatches),
            "mismatches": mismatches[:20],
            "note": "Mismatches can occur for non-batch-tracked items or legacy data; review before corrective action.",
        }
    except Exception as e:
        return {"ok": False, "checked": False, "error": str(e)}


def _printer_readiness() -> dict[str, Any]:
    """Best-effort printer/config readiness (no device writes)."""
    out: dict[str, Any] = {"ok": True, "configured": False, "checks": []}
    mode = target = ""
    try:
        if getattr(db, "_conn", None) is not None:
            from . import services
            mode = (services.get_setting("printer_mode", "file") or "file").strip().lower()
            target = (services.get_setting("printer_target", "") or "").strip()
            out["configured"] = bool(mode)
            out["mode"] = mode
            out["target_set"] = bool(target)
            if mode in ("tcp", "network", "raw"):
                if not target or ":" not in target:
                    out["checks"].append({"severity": "warn", "code": "printer_target_invalid", "message": "TCP printer target should be host:port"})
                    out["ok"] = False
            elif mode in ("file", ""):
                out["checks"].append({"severity": "info", "code": "printer_file_mode", "message": "Printer mode is file/offline; raw device not required"})
        else:
            out["checks"].append({"severity": "info", "code": "printer_check_skipped", "message": "Printer settings check skipped (app DB not initialized in this process)"})
    except Exception as e:
        out["checks"].append({"severity": "warn", "code": "printer_check_error", "message": str(e)})
    return out


def _backup_restore_guidance() -> dict[str, Any]:
    db_path = Path(paths.DB_PATH)
    data_dir = Path(paths.DATA_DIR)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_name = f"property_manager.backup.{ts}.db"
    wal = Path(str(db_path) + "-wal")
    shm = Path(str(db_path) + "-shm")
    return {
        "runtime_data_preserved": ["DB", "exports", "invoices", "configured data dir (PM_DATA_DIR/DATA_DIR)"],
        "backup": {
            "stop_app_first": True,
            "copy_db_file": str(db_path),
            "suggested_backup_path": str(data_dir / backup_name),
            "cli_helper": "python3 -m app_core.doctor --backup  (or --backup-to <path>)",
            "wal_exists": wal.exists(),
            "shm_exists": shm.exists(),
            "note": "Prefer backups while app is stopped. CLI --backup uses SQLite backup API when possible; verify the backup after creation. Preserve exports/ and invoices/ alongside DB backups.",
        },
        "restore": {
            "stop_app_first": True,
            "replace_db_file": str(db_path),
            "verify_helper": "python3 -m app_core.doctor --verify-backup <path>",
            "steps": [
                "Stop TUI/Web processes (run web with --workers 1).",
                "Create a fresh safety backup of current runtime data if available.",
                "Verify candidate backup: python3 -m app_core.doctor --verify-backup <backup.db>",
                "Copy verified backup over the target DB path (and keep exports/invoices/data dir).",
                "Restart app; it will run safe/idempotent migrations if needed.",
                "Run: python3 -m app_core.doctor --json",
            ],
        },
        "readiness": {
            "db_exists": db_path.exists(),
            "data_dir_exists": data_dir.exists(),
            "data_dir_writable": os.access(str(data_dir), os.W_OK) if data_dir.exists() else os.access(str(data_dir.parent), os.W_OK),
            "recommendation": "Verify backups before restore and never delete runtime data during update/backup/restore operations.",
        },
    }


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def verify_backup_file(backup_path: str | Path) -> dict[str, Any]:
    """Read-only verification of a SQLite backup/candidate DB file."""
    p = Path(backup_path).expanduser()
    out: dict[str, Any] = {"ok": False, "path": str(p), "exists": p.exists()}
    if not p.exists() or not p.is_file():
        out["error"] = "backup file not found"
        return out
    try:
        out["size_bytes"] = p.stat().st_size
    except Exception:
        pass
    conn = None
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        out["schema"] = {"user_version": conn.execute("PRAGMA user_version").fetchone()[0], "tables": _schema_table_checks(conn)}
        out["integrity"] = {"sqlite": _db_integrity_summary(conn)}
        out["consistency"] = {"batch_stock": _batch_stock_consistency(conn)}
        ok = bool(((out.get("integrity") or {}).get("sqlite") or {}).get("ok")) and bool((((out.get("schema") or {}).get("tables") or {}).get("ok", True)))
        out["ok"] = ok
        return out
    except Exception as e:
        out["error"] = str(e)
        return out
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def create_backup(backup_to: str | Path | None = None) -> dict[str, Any]:
    """Create a safe SQLite backup copy (no runtime data deletion)."""
    src = Path(paths.DB_PATH)
    if not src.exists():
        return {"ok": False, "error": f"source DB not found: {src}", "source": str(src)}
    if backup_to:
        dst = Path(backup_to).expanduser()
    else:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = Path(paths.DATA_DIR) / f"property_manager.backup.{ts}.db"
    try:
        dst = dst if dst.is_absolute() else (Path.cwd() / dst)
        _ensure_parent_dir(dst)
        if dst.exists() and dst.is_dir():
            return {"ok": False, "error": f"backup destination is a directory: {dst}", "source": str(src)}
        # Prefer SQLite online backup API for consistency when possible.
        src_conn = sqlite3.connect(str(src), timeout=10.0)
        try:
            dst_conn = sqlite3.connect(str(dst), timeout=10.0)
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()
        verify = verify_backup_file(dst)
        return {
            "ok": bool(verify.get("ok", False)),
            "source": str(src),
            "backup_path": str(dst),
            "verified": verify,
            "note": "Backup created without deleting runtime data. Keep exports/ and invoices/ alongside DB backups.",
        }
    except Exception as e:
        # Fallback to file copy
        try:
            _ensure_parent_dir(dst)
            shutil.copy2(src, dst)
            verify = verify_backup_file(dst)
            return {
                "ok": bool(verify.get("ok", False)),
                "source": str(src),
                "backup_path": str(dst),
                "verified": verify,
                "fallback": "file_copy",
                "warning": f"sqlite backup API failed, used file copy: {e}",
            }
        except Exception as e2:
            return {"ok": False, "error": f"backup failed: {e2}", "source": str(src), "backup_path": str(dst)}


def _config_issues() -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    try:
        if getattr(db, "_conn", None):
            try:
                from . import services
                allow_neg = (services.get_setting("allow_negative_stock", "true") or "true").lower() == "true"
                if allow_neg:
                    issues.append({"severity": "info", "code": "negative_stock_allowed", "message": "Negative stock is allowed by configuration."})
            except Exception:
                pass
        else:
            issues.append({"severity": "info", "code": "db_not_open", "message": "DB connection not open in this process; config/printer/ledger checks limited."})
    except Exception as e:
        issues.append({"severity": "warn", "code": "config_check_error", "message": f"Could not evaluate some config checks: {e}"})
    try:
        for label, p in [("data_dir", Path(paths.DATA_DIR)), ("export_dir", Path(paths.EXPORT_DIR)), ("invoice_dir", Path(paths.INVOICE_DIR))]:
            if p.exists() and not os.access(str(p), os.W_OK):
                issues.append({"severity": "error", "code": "path_not_writable", "message": f"{label} is not writable: {p}"})
    except Exception as e:
        issues.append({"severity": "warn", "code": "path_check_error", "message": f"Path checks failed: {e}"})
    return issues


def collect_health(check_updates: bool = False) -> dict[str, Any]:
    """Collect a read-only health snapshot. Does not delete runtime data."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report: dict[str, Any] = {
        "ok": True,
        "checked_at": now,
        "paths": {
            "project_root": str(getattr(paths, "PROJECT_ROOT", "")),
            "data_dir": str(getattr(paths, "DATA_DIR", "")),
            "db_path": str(getattr(paths, "DB_PATH", "")),
            "export_dir": str(getattr(paths, "EXPORT_DIR", "")),
            "invoice_dir": str(getattr(paths, "INVOICE_DIR", "")),
        },
        "data_dirs": {},
        "db": {},
        "schema": {},
        "integrity": {},
        "consistency": {},
        "readiness": {},
        "backup_restore": _backup_restore_guidance(),
        "update": {},
        "config_issues": [],
        "notes": [],
    }

    try:
        paths.ensure_data_dirs()
    except Exception as e:
        report["notes"].append(f"Could not ensure data dirs: {e}")

    for key, p in [
        ("data_dir", Path(paths.DATA_DIR)),
        ("export_dir", Path(paths.EXPORT_DIR)),
        ("invoice_dir", Path(paths.INVOICE_DIR)),
        ("db_path", Path(paths.DB_PATH)),
    ]:
        report["data_dirs"][key] = _exists_info(p)
    # SQLite sidecars are informational; safe backups are preferred when stopped or via SQLite backup API.
    try:
        dbp = Path(paths.DB_PATH)
        for key, p in [("db_wal", Path(str(dbp) + "-wal")), ("db_shm", Path(str(dbp) + "-shm"))]:
            if p.exists():
                report["data_dirs"][key] = _exists_info(p)
    except Exception:
        pass

    db_path = Path(paths.DB_PATH)
    report["db"]["path"] = str(db_path)
    report["db"]["exists"] = db_path.exists()
    if db_path.exists():
        try:
            try:
                report["db"]["size_bytes"] = db_path.stat().st_size
            except Exception:
                pass
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            try:
                conn.row_factory = sqlite3.Row
                report["schema"]["user_version"] = conn.execute("PRAGMA user_version").fetchone()[0]
                report["schema"]["expected_version"] = getattr(db, "SCHEMA_VERSION", None)
                report["schema"]["matches_expected"] = (
                    report["schema"]["expected_version"] is None
                    or report["schema"]["user_version"] == report["schema"]["expected_version"]
                )
                report["schema"]["tables"] = _schema_table_checks(conn)
                report["integrity"]["sqlite"] = _db_integrity_summary(conn)
                report["consistency"]["batch_stock"] = _batch_stock_consistency(conn)
                try:
                    report["db"]["table_count"] = len([r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()])
                except Exception:
                    pass
                try:
                    report["db"]["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
                    report["db"]["foreign_keys"] = conn.execute("PRAGMA foreign_keys").fetchone()[0]
                    report["db"]["busy_timeout"] = conn.execute("PRAGMA busy_timeout").fetchone()[0]
                except Exception:
                    pass
            finally:
                conn.close()
        except Exception as e:
            report["db"]["error"] = str(e)
            report["ok"] = False
    else:
        report["notes"].append("Database file does not exist yet (fresh install or data dir not initialized).")

    try:
        if db_path.exists() and getattr(db, "_conn", None) is not None:
            try:
                from . import services
                if hasattr(services, "verify_ledger_integrity"):
                    report["integrity"]["ledger"] = services.verify_ledger_integrity()
            except Exception as e:
                report["integrity"]["ledger_error"] = str(e)
        elif db_path.exists():
            report["notes"].append("Ledger integrity check skipped (app DB not initialized in this process). Run from app context for ledger-level checks.")
    except Exception as e:
        report["notes"].append(f"Ledger integrity check unavailable: {e}")

    try:
        from . import updater
        report["update"]["local"] = updater.get_local_state()
        if check_updates:
            try:
                report["update"]["remote"] = updater.check_for_updates()
            except Exception as e:
                report["update"]["remote_error"] = str(e)
        else:
            report["notes"].append("Remote update check skipped (use --check-updates to query GitHub).")
    except Exception as e:
        report["update"]["error"] = str(e)

    report["readiness"]["printer"] = _printer_readiness()
    report["config_issues"] = _config_issues()

    sqlite_ok = ((report.get("integrity") or {}).get("sqlite") or {}).get("ok", True)
    schema_ok = (report.get("schema") or {}).get("matches_expected", True)
    tables_ok = (((report.get("schema") or {}).get("tables") or {}).get("ok", True))
    severe = [i for i in report["config_issues"] if i.get("severity") == "error"]
    if (db_path.exists() and (not sqlite_ok or not schema_ok or not tables_ok)) or severe:
        report["ok"] = False
    # operator guidance notes
    report["notes"].append("Web/server ops: always run uvicorn with --workers 1; runtime data (DB/exports/invoices/PM_DATA_DIR) must be preserved across updates/backups/restores.")
    return report


def format_health_report(report: dict[str, Any]) -> str:
    lines = []
    lines.append(f"Property Manager Doctor — {report.get('checked_at','')}")
    lines.append(f"Overall: {'OK' if report.get('ok') else 'ISSUES DETECTED'}")
    p = report.get("paths", {})
    lines.append("\nPaths:")
    for k in ("project_root", "data_dir", "db_path", "export_dir", "invoice_dir"):
        if k in p:
            lines.append(f"  - {k}: {p[k]}")
    ddirs = report.get("data_dirs", {})
    if ddirs:
        lines.append("\nData dirs/files:")
        for k, v in ddirs.items():
            state = "exists" if v.get("exists") else "missing"
            extra = []
            if v.get("is_dir"): extra.append("dir")
            if v.get("is_file"): extra.append("file")
            if "writable" in v: extra.append("writable" if v["writable"] else "not-writable")
            lines.append(f"  - {k}: {state}" + (f" ({', '.join(extra)})" if extra else "") + f" — {v.get('path','')}")
    sch = report.get("schema", {})
    lines.append("\nSchema:")
    lines.append(f"  - user_version: {sch.get('user_version')}")
    lines.append(f"  - expected_version: {sch.get('expected_version')}")
    lines.append(f"  - matches_expected: {sch.get('matches_expected')}")
    t = sch.get("tables") or {}
    if t:
        lines.append(f"  - tables_ok: {t.get('ok')} (present={t.get('present_count')}, missing={len(t.get('missing_tables') or [])})")
        if t.get("missing_tables"):
            lines.append(f"  - missing_tables: {t.get('missing_tables')}")
    integ = (report.get("integrity") or {}).get("sqlite") or {}
    if integ:
        lines.append("\nSQLite integrity:")
        lines.append(f"  - integrity_check: {integ.get('integrity_check')}")
        lines.append(f"  - foreign_key_violations: {len(integ.get('foreign_key_violations') or [])}")
        for k in ("integrity_error", "foreign_key_error"):
            if integ.get(k): lines.append(f"  - {k}: {integ.get(k)}")
    dbi = report.get("db") or {}
    if dbi.get("journal_mode") is not None or dbi.get("busy_timeout") is not None:
        lines.append(f"  - db: journal_mode={dbi.get('journal_mode')} foreign_keys={dbi.get('foreign_keys')} busy_timeout={dbi.get('busy_timeout')}")
    ledger = (report.get("integrity") or {}).get("ledger")
    if ledger is not None:
        lines.append(f"  - ledger_integrity: {ledger}")
    cons = (report.get("consistency") or {}).get("batch_stock") or {}
    if cons:
        lines.append("\nBatch/stock consistency:")
        lines.append(f"  - ok: {cons.get('ok')} checked={cons.get('checked')} mismatches={cons.get('mismatch_count', 0)}")
        if cons.get("error"): lines.append(f"  - error: {cons.get('error')}")
        if cons.get("mismatches"):
            for m in (cons.get("mismatches") or [])[:5]:
                lines.append(f"  - item {m.get('item_id')} {m.get('item_name')}: stock={m.get('stock_qty')} active_batch_remaining={m.get('batch_remaining_active')}")
    pr = ((report.get("readiness") or {}).get("printer") or {})
    if pr:
        lines.append("\nPrinter readiness:")
        lines.append(f"  - configured: {pr.get('configured')} mode={pr.get('mode','')} target_set={pr.get('target_set', False)}")
        for c in (pr.get("checks") or []):
            lines.append(f"  - [{c.get('severity','info')}] {c.get('code')}: {c.get('message')}")
    upd = report.get("update") or {}
    local = upd.get("local") or {}
    if local:
        lines.append("\nUpdate status (local):")
        for k in ("app_version", "branch", "commit_short", "dirty", "is_git_repo"):
            if k in local: lines.append(f"  - {k}: {local.get(k)}")
        if local.get("remote"):
            try:
                from . import paths as _paths
                lines.append(f"  - remote: {_paths.redact_sensitive(local.get('remote'), 200)}")
            except Exception:
                pass
    remote = upd.get("remote")
    if isinstance(remote, dict):
        lines.append("  - remote_update_available: " + str(remote.get("update_available")))
        if remote.get("error"): lines.append(f"  - remote_error: {remote.get('error')}")
    issues = report.get("config_issues") or []
    lines.append(f"\nConfig issues: {len(issues)}")
    for iss in issues:
        lines.append(f"  - [{iss.get('severity','info')}] {iss.get('code')}: {iss.get('message')}")
    br = report.get("backup_restore") or {}
    if br:
        lines.append("\nBackup/restore guidance:")
        lines.append("  - Runtime data preserved: " + ", ".join(br.get("runtime_data_preserved") or []))
        b = br.get("backup") or {}
        if b:
            lines.append(f"  - Backup: stop_app={b.get('stop_app_first')} db={b.get('copy_db_file')} suggested={b.get('suggested_backup_path')}")
            if b.get("wal_exists") or b.get("shm_exists"):
                lines.append(f"  - WAL/SHM present: wal={bool(b.get('wal_exists'))} shm={bool(b.get('shm_exists'))} (prefer consistent backup/stop app)")
        r = br.get("restore") or {}
        if r:
            lines.append(f"  - Restore: stop_app={r.get('stop_app_first')} replace_db={r.get('replace_db_file')} verify={r.get('verify_helper')}")
        rd = br.get("readiness") or {}
        if rd:
            lines.append(f"  - Readiness: db_exists={rd.get('db_exists')} data_dir_writable={rd.get('data_dir_writable')}")
    notes = report.get("notes") or []
    if notes:
        lines.append("\nNotes:")
        for n in notes:
            lines.append(f"  - {n}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app_core.doctor", description="Property Manager health/doctor checks and safe backup helpers")
    ap.add_argument("--json", action="store_true", help="Output JSON report/result")
    ap.add_argument("--check-updates", action="store_true", help="Also query GitHub for update status (network)")
    ap.add_argument("--strict", action="store_true", help="Exit non-zero on warnings-level risks in batch consistency/printer checks where applicable")
    ap.add_argument("--backup", action="store_true", help="Create a safe backup copy of the SQLite DB (no runtime data deletion)")
    ap.add_argument("--backup-to", help="Destination path for --backup/backup creation")
    ap.add_argument("--verify-backup", help="Verify a SQLite backup/candidate DB file (integrity/schema checks)")
    args = ap.parse_args(argv)

    if args.verify_backup:
        result = verify_backup_file(args.verify_backup)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"Backup verify: {'OK' if result.get('ok') else 'ISSUES/ERROR'}")
            print(f"  - path: {result.get('path')}")
            if result.get("error"):
                print(f"  - error: {result.get('error')}")
            integ = ((result.get("integrity") or {}).get("sqlite") or {})
            if integ:
                print(f"  - integrity_check: {integ.get('integrity_check')}")
                print(f"  - foreign_key_violations: {len(integ.get('foreign_key_violations') or [])}")
            sch = result.get("schema") or {}
            if sch:
                print(f"  - user_version: {sch.get('user_version')}")
        return 0 if result.get("ok") else 1

    if args.backup or args.backup_to:
        result = create_backup(args.backup_to)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"Backup: {'OK' if result.get('ok') else 'FAILED'}")
            print(f"  - source: {result.get('source')}")
            print(f"  - backup_path: {result.get('backup_path')}")
            if result.get("error"):
                print(f"  - error: {result.get('error')}")
            if result.get("warning"):
                print(f"  - warning: {result.get('warning')}")
            v = result.get("verified") or {}
            if v:
                print(f"  - verified_ok: {v.get('ok')}")
        return 0 if result.get("ok") else 1

    report = collect_health(check_updates=bool(args.check_updates))
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(format_health_report(report))
    ok = bool(report.get("ok"))
    if args.strict and ok:
        cons = ((report.get("consistency") or {}).get("batch_stock") or {})
        if cons.get("checked") and not cons.get("ok"):
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
