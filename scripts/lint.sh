#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3}"
export PYTHONPATH="$ROOT/src:$ROOT${PYTHONPATH:+:$PYTHONPATH}"

# Lint/static checks (dead-code & quality gates). Keep rules pragmatic/non-disruptive.
exec "$PYTHON" -m ruff check "$@" .
