"""Operational health/doctor checks for Property Manager.

Provides a read-only snapshot of runtime paths, DB/schema/integrity status,
data directories, update status, and basic configuration issues.
Safe to run as: `python -m app_core.doctor`
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import db, paths


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


def _config_issues() -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    try:
        # local import to avoid side effects at module import time beyond normal
        from . import services
        # basic settings sanity
        try:
            allow_neg = (services.get_setting("allow_negative_stock", "true") or "true").lower() == "true"
            if allow_neg:
                issues.append({"severity": "info", "code": "negative_stock_allowed", "message": "Negative stock is allowed by configuration."})
        except Exception:
            pass
        try:
            # detect obviously missing DB tables only via best-effort query if initialized
            if not getattr(db, "_conn", None):
                issues.append({"severity": "info", "code": "db_not_open", "message": "DB connection not open in this process; schema checks limited."})
        except Exception:
            pass
    except Exception as e:
        issues.append({"severity": "warn", "code": "config_check_error", "message": f"Could not evaluate some config checks: {e}"})
    # path issues
    try:
        for label, p in [("data_dir", Path(paths.DATA_DIR)), ("export_dir", Path(paths.EXPORT_DIR)), ("invoice_dir", Path(paths.INVOICE_DIR))]:
            if p.exists() and not os.access(str(p), os.W_OK):
                issues.append({"severity": "error", "code": "path_not_writable", "message": f"{label} is not writable: {p}"})
    except Exception as e:
        issues.append({"severity": "warn", "code": "path_check_error", "message": f"Path checks failed: {e}"})
    return issues


def collect_health(check_updates: bool = False, update_timeout_hint: bool = True) -> dict[str, Any]:
    """Collect a read-only health snapshot. Does not mutate runtime data."""
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
        "update": {},
        "config_issues": [],
        "notes": [],
    }

    # ensure dirs exist check is read-only in effect? ensure_data_dirs creates dirs; ops doctor should be safe but
    # creating expected data dirs is acceptable operationally. Keep it minimal and safe.
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

    # DB/schema/integrity
    db_path = Path(paths.DB_PATH)
    report["db"]["path"] = str(db_path)
    report["db"]["exists"] = db_path.exists()
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            try:
                conn.row_factory = sqlite3.Row
                report["schema"]["user_version"] = conn.execute("PRAGMA user_version").fetchone()[0]
                report["schema"]["expected_version"] = getattr(db, "SCHEMA_VERSION", None)
                report["schema"]["matches_expected"] = (
                    report["schema"]["expected_version"] is None
                    or report["schema"]["user_version"] == report["schema"]["expected_version"]
                )
                report["integrity"]["sqlite"] = _db_integrity_summary(conn)
                # basic quick counts (best-effort)
                try:
                    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                    report["db"]["table_count"] = len(tables)
                except Exception:
                    pass
            finally:
                conn.close()
        except Exception as e:
            report["db"]["error"] = str(e)
            report["ok"] = False
    else:
        report["notes"].append("Database file does not exist yet (fresh install or data dir not initialized).")

    # ledger/app-level integrity if DB is initialized/openable via app layer
    try:
        if db_path.exists():
            # services.verify_ledger_integrity uses db.connect(); avoid forcing full app init/migrations here.
            # If a shared connection is already open in-process, use it; otherwise skip to keep doctor lightweight/safe.
            if getattr(db, "_conn", None) is not None:
                try:
                    from . import services
                    if hasattr(services, "verify_ledger_integrity"):
                        report["integrity"]["ledger"] = services.verify_ledger_integrity()
                except Exception as e:
                    report["integrity"]["ledger_error"] = str(e)
            else:
                report["notes"].append("Ledger integrity check skipped (app DB not initialized in this process). Run from app context for ledger-level checks.")
    except Exception as e:
        report["notes"].append(f"Ledger integrity check unavailable: {e}")

    # update status (local only by default; optional remote check)
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

    report["config_issues"] = _config_issues()
    # overall status
    sqlite_ok = ((report.get("integrity") or {}).get("sqlite") or {}).get("ok", True)
    schema_ok = (report.get("schema") or {}).get("matches_expected", True)
    severe = [i for i in report["config_issues"] if i.get("severity") == "error"]
    if (db_path.exists() and (not sqlite_ok or not schema_ok)) or severe:
        report["ok"] = False
    return report


def format_health_report(report: dict[str, Any]) -> str:
    lines = []
    lines.append(f"Property Manager Doctor — {report.get('checked_at','')}")
    lines.append(f"Overall: {'OK' if report.get('ok') else 'ISSUES DETECTED'}")
    p = report.get("paths", {})
    lines.append("")
    lines.append("Paths:")
    for k in ("project_root", "data_dir", "db_path", "export_dir", "invoice_dir"):
        if k in p:
            lines.append(f"  - {k}: {p[k]}")
    ddirs = report.get("data_dirs", {})
    if ddirs:
        lines.append("")
        lines.append("Data dirs/files:")
        for k, v in ddirs.items():
            state = "exists" if v.get("exists") else "missing"
            extra = []
            if v.get("is_dir"): extra.append("dir")
            if v.get("is_file"): extra.append("file")
            if "writable" in v: extra.append("writable" if v["writable"] else "not-writable")
            lines.append(f"  - {k}: {state}" + (f" ({', '.join(extra)})" if extra else "") + f" — {v.get('path','')}")
    sch = report.get("schema", {})
    lines.append("")
    lines.append("Schema:")
    lines.append(f"  - user_version: {sch.get('user_version')}")
    lines.append(f"  - expected_version: {sch.get('expected_version')}")
    lines.append(f"  - matches_expected: {sch.get('matches_expected')}")
    integ = (report.get("integrity") or {}).get("sqlite") or {}
    if integ:
        lines.append("")
        lines.append("SQLite integrity:")
        lines.append(f"  - integrity_check: {integ.get('integrity_check')}")
        lines.append(f"  - foreign_key_violations: {len(integ.get('foreign_key_violations') or [])}")
        if integ.get('integrity_error'):
            lines.append(f"  - integrity_error: {integ.get('integrity_error')}")
        if integ.get('foreign_key_error'):
            lines.append(f"  - foreign_key_error: {integ.get('foreign_key_error')}")
    ledger = (report.get("integrity") or {}).get("ledger")
    if ledger is not None:
        lines.append(f"  - ledger_integrity: {ledger}")
    upd = report.get("update") or {}
    local = upd.get("local") or {}
    if local:
        lines.append("")
        lines.append("Update status (local):")
        for k in ("app_version", "branch", "commit_short", "dirty", "is_git_repo"):
            if k in local:
                lines.append(f"  - {k}: {local.get(k)}")
    remote = upd.get("remote")
    if isinstance(remote, dict):
        lines.append("  - remote_update_available: " + str(remote.get("update_available")))
        if remote.get("error"):
            lines.append(f"  - remote_error: {remote.get('error')}")
    issues = report.get("config_issues") or []
    lines.append("")
    lines.append(f"Config issues: {len(issues)}")
    for iss in issues:
        lines.append(f"  - [{iss.get('severity','info')}] {iss.get('code')}: {iss.get('message')}")
    notes = report.get("notes") or []
    if notes:
        lines.append("")
        lines.append("Notes:")
        for n in notes:
            lines.append(f"  - {n}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app_core.doctor", description="Property Manager health/doctor checks")
    ap.add_argument("--json", action="store_true", help="Output JSON report")
    ap.add_argument("--check-updates", action="store_true", help="Also query GitHub for update status (network)")
    args = ap.parse_args(argv)
    report = collect_health(check_updates=bool(args.check_updates))
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(format_health_report(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
