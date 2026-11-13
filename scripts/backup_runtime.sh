#!/bin/sh
# Safe backup/verify/restore-precheck helper wrapper. Preserves runtime data (DB/exports/invoices/PM_DATA_DIR); never deletes.
# Usage: sh scripts/backup_runtime.sh --backup | --verify <backup.db> | --restore-precheck <backup.db> | [other supported args]
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." 2>/dev/null && pwd)"
PYTHON="${PYTHON:-python3}"

if [ ! -d "$ROOT" ]; then
  echo "ERROR: Could not resolve project root." >&2
  exit 1
fi
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "ERROR: Python interpreter not found: $PYTHON" >&2
  echo "Set PYTHON=/path/to/python3 and retry." >&2
  exit 1
fi
if [ ! -f "$ROOT/scripts/backup_runtime.py" ]; then
  echo "ERROR: Missing backup helper: $ROOT/scripts/backup_runtime.py" >&2
  exit 1
fi

# Ensure src/ is importable (app_core)
export PYTHONPATH="$ROOT/src:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" "$ROOT/scripts/backup_runtime.py" "$@"