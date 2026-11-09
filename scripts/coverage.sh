#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3}"
export PYTHONPATH="$ROOT/src:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m pytest -c tests/pytest.ini \
  --cov=src/app_core --cov=ui \
  --cov-report=term-missing --cov-report=xml:coverage.xml \
  "$@"
