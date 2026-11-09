#!/usr/bin/env bash
# Install runtime + web + dev dependencies and run tests for property-manager-tui.
# Works on Linux/Termux. Uses the active Python (activate a venv first if desired).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
PIP="$PYTHON -m pip"

echo "==> Repo: $ROOT"
echo "==> Python: $($PYTHON --version 2>&1)"
echo "==> Pip: $($PIP --version 2>&1 | head -1)"

echo ""
echo "==> Installing runtime dependencies (requirements.txt)..."
if [[ -f requirements.txt ]]; then
  $PIP install -r requirements.txt
else
  echo "requirements.txt not found; installing textual..."
  $PIP install "textual>=0.47"
fi

echo ""
if [[ "${SKIP_WEB:-0}" == "1" ]]; then
  echo "==> Skipping web dependencies (SKIP_WEB=1)."
elif [[ -f requirements-web.txt ]]; then
  echo "==> Installing web dependencies (requirements-web.txt)..."
  if ! $PIP install -r requirements-web.txt; then
    echo ""
    echo "WARNING: Web dependency installation failed (common on Termux/Android due to pydantic-core/Rust wheel builds)." >&2
    echo "         Core/TUI and the existing test suite do NOT require FastAPI." >&2
    echo "         To skip this step: SKIP_WEB=1 bash scripts/install_deps_and_test.sh" >&2
    echo "         Workarounds (if you need the web UI): try a venv, install Rust toolchain for Termux if available," >&2
    echo "         or use compatible prebuilt wheels/versions for your platform." >&2
    echo ""
  fi
else
  echo "requirements-web.txt not found; skipping web deps."
fi

echo ""
echo "==> Installing dev/test dependencies (requirements-dev.txt)..."
if [[ -f requirements-dev.txt ]]; then
  $PIP install -r requirements-dev.txt
else
  echo "requirements-dev.txt not found; installing pytest httpx..."
  $PIP install "pytest>=7.0" "httpx>=0.24"
fi

echo ""
echo "==> Running tests (pytest)..."
# conftest.py adds src/root to sys.path; PYTHONPATH is set for clarity.
export PYTHONPATH="$ROOT/src:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec $PYTHON -m pytest -c tests/pytest.ini "$@"
