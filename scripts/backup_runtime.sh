#!/bin/sh
# Safe backup/verify helper wrapper. Preserves DB/exports/invoices/PM_DATA_DIR (never deletes runtime data).
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
export PYTHONPATH="$ROOT/src:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" "$ROOT/scripts/backup_runtime.py" "$@"
