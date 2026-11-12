#!/bin/sh
# Safe backup/verify/restore helper wrapper (manual restore). Preserves DB/exports/invoices/PM_DATA_DIR.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
# Ensure src/ is importable (app_core)
export PYTHONPATH="$ROOT/src:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" "$ROOT/scripts/backup_runtime.py" "$@"
