#!/usr/bin/env python3
"""Safe runtime backup/verify helper for Property Manager.

Creates or verifies SQLite DB backups without deleting runtime data.
Always preserves DB/exports/invoices and configured data dir (PM_DATA_DIR).

Examples:
  python3 scripts/backup_runtime.py --backup
  python3 scripts/backup_runtime.py --backup --to /path/to/backup.db
  python3 scripts/backup_runtime.py --verify /path/to/backup.db
  python3 scripts/backup_runtime.py --doctor --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (str(SRC), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app_core import doctor, paths  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Safe backup/verify/health helper (preserves runtime data)")
    ap.add_argument("--backup", action="store_true", help="Create a safe SQLite backup")
    ap.add_argument("--to", dest="backup_to", help="Destination backup path")
    ap.add_argument("--verify", dest="verify_path", help="Verify a backup/candidate DB file")
    ap.add_argument("--doctor", action="store_true", help="Run doctor health checks")
    ap.add_argument("--check-updates", action="store_true", help="With --doctor, query remote update status")
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args(argv)

    if args.verify_path:
        res = doctor.verify_backup_file(args.verify_path)
    elif args.backup or args.backup_to:
        paths.ensure_data_dirs()
        res = doctor.create_backup(args.backup_to)
    elif args.doctor:
        paths.ensure_data_dirs()
        res = doctor.collect_health(check_updates=bool(args.check_updates))
    else:
        ap.print_help()
        print("\nRuntime data preserved: DB, exports, invoices, PM_DATA_DIR/data dir.")
        print("Web ops: always run uvicorn with --workers 1.")
        return 2

    if args.json:
        print(json.dumps(res, indent=2, default=str))
    else:
        if "backup_path" in res or args.backup or args.backup_to:
            print(f"Backup: {'OK' if res.get('ok') else 'FAILED'}")
            for k in ("source", "backup_path", "error", "warning", "note"):
                if k in res and res.get(k) is not None:
                    print(f"  - {k}: {res.get(k)}")
            v = res.get("verified") or {}
            if v:
                print(f"  - verified_ok: {v.get('ok')}")
        elif args.verify_path:
            print(f"Verify backup: {'OK' if res.get('ok') else 'ISSUES/ERROR'}")
            print(f"  - path: {res.get('path')}")
            if res.get("error"):
                print(f"  - error: {res.get('error')}")
        else:
            print(doctor.format_health_report(res))

    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
