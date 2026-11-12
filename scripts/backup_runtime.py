#!/usr/bin/env python3
"""Safe runtime backup/verify/restore helper for Property Manager.

Creates or verifies SQLite DB backups without deleting runtime data.
Always preserves DB/exports/invoices and configured data dir (PM_DATA_DIR).
Restore is a manual, offline operation (no destructive one-click restore).

Examples:
  python3 scripts/backup_runtime.py --backup
  python3 scripts/backup_runtime.py --backup --to /path/to/backup.db
  python3 scripts/backup_runtime.py --verify /path/to/backup.db
  python3 scripts/backup_runtime.py --doctor --json
  python3 scripts/backup_runtime.py --doctor --check-updates
  ./scripts/backup_runtime.sh --backup
  scripts\\backup_runtime.cmd --verify backup.db
  powershell -ExecutionPolicy Bypass -File scripts\\backup_runtime.ps1 --backup
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (str(SRC), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app_core import doctor, paths  # noqa: E402


def _print_restore_guidance() -> None:
    print("\nRestore guidance (manual, offline - no automatic destructive restore):")
    print("1. Stop the app/server (exit TUI or stop uvicorn; run with --workers 1 only).")
    print("2. Identify the active DB path (PM_DATA_DIR or default data/; see doctor output).")
    print("3. Back up current DB before replacing (optional but recommended).")
    print("4. Copy the verified backup over the active DB file (and restore related exports/invoices if needed).")
    print("5. Start the app - DB migrations run on startup (additive).")
    print("6. Verify with: python3 -m app_core.doctor --json  (or this script --doctor).")
    print("Tip: Use --verify <backup> first to confirm integrity/schema before restoring.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Safe backup/verify/health helper (preserves runtime data; restore is manual/offline)",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Runtime data preserved: DB, exports, invoices, PM_DATA_DIR/data dir.\n"
            "Web ops: always run uvicorn with --workers 1 (SQLite).\n"
            "Restore: manual/offline only - see guidance below or README 'Backups & restore'."
        ),
    )
    ap.add_argument("--backup", action="store_true", help="Create a safe SQLite backup (idempotent; no runtime data deleted)")
    ap.add_argument("--to", dest="backup_to", help="Destination backup path (optional)")
    ap.add_argument("--verify", dest="verify_path", help="Verify a backup/candidate DB file (read-only)")
    ap.add_argument("--verify-backup", dest="verify_backup_path", help="Alias for --verify")
    ap.add_argument("--doctor", action="store_true", help="Run doctor health checks (read-only)")
    ap.add_argument("--check-updates", action="store_true", help="With --doctor, query remote update status")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--restore-guide", action="store_true", help="Show manual restore guidance and exit")
    args = ap.parse_args(argv)

    if args.restore_guide:
        _print_restore_guidance()
        return 0

    verify_path = args.verify_path or args.verify_backup_path

    try:
        if verify_path:
            res = doctor.verify_backup_file(verify_path)
        elif args.backup or args.backup_to:
            paths.ensure_data_dirs()
            res = doctor.create_backup(args.backup_to)
        elif args.doctor:
            paths.ensure_data_dirs()
            res = doctor.collect_health(check_updates=bool(args.check_updates))
        else:
            ap.print_help()
            print("\nRuntime data preserved: DB, exports, invoices, PM_DATA_DIR/data dir.")
            print("Web ops: always run uvicorn with --workers 1 (SQLite).")
            print("Restore is manual/offline - use --restore-guide for steps.")
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
                if isinstance(v, dict) and v:
                    print(f"  - verified_ok: {v.get('ok')}")
                _print_restore_guidance()
            elif verify_path:
                print(f"Verify backup: {'OK' if res.get('ok') else 'ISSUES/ERROR'}")
                if res.get("path"):
                    print(f"  - path: {res.get('path')}")
                if res.get("error"):
                    print(f"  - error: {res.get('error')}")
                if res.get("ok"):
                    print("  - Backup appears readable/integrity-checked; safe to use for manual restore if needed.")
                _print_restore_guidance()
            elif args.doctor:
                try:
                    print(doctor.format_health_report(res))
                except Exception:
                    print(json.dumps(res, indent=2, default=str))
                _print_restore_guidance()
            else:
                print(json.dumps(res, indent=2, default=str))

        return 0 if res.get("ok") else 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
