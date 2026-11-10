#!/bin/sh
# Setup script for iSH (Alpine Linux) - pulls repo, installs packages, prepares app.
# Usage:
#   sh scripts/setup_ish_alpine.sh            # clone/update + install deps
#   REPO_URL=... INSTALL_DIR=... SKIP_WEB=1 INSTALL_DEV=1 sh scripts/setup_ish_alpine.sh
#
# Notes (iSH):
# - Run in iSH on iOS/iPadOS (Alpine). Network and apk repositories must be working.
# - apk needs root (iSH typically runs as root).
# - Building some web deps (e.g. pydantic-core) can be slow or fail on iSH; set SKIP_WEB=1 if needed.

set -eu

REPO_URL="${REPO_URL:-https://github.com/Michaelhehelmy/property-manager-tui.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/property-manager-tui}"
BRANCH="${BRANCH:-main}"
SKIP_WEB="${SKIP_WEB:-0}"
INSTALL_DEV="${INSTALL_DEV:-0}"
RUN_TESTS="${RUN_TESTS:-0}"

echo "==> iSH/Alpine setup for property-manager-tui"
echo "    REPO_URL=$REPO_URL"
echo "    INSTALL_DIR=$INSTALL_DIR"
echo "    BRANCH=$BRANCH"

# --- System packages (Alpine/apk) ---
if command -v apk >/dev/null 2>&1; then
  echo "==> Installing system packages (apk)..."
  apk update || true
  apk add --no-cache \
    bash git ca-certificates \
    python3 py3-pip py3-virtualenv \
    sqlite tzdata \
    build-base python3-dev libffi-dev openssl-dev cargo rust \
    || echo "WARNING: Some apk packages failed to install; continuing..."
else
  echo "WARNING: apk not found. Install git, python3, pip, and build deps manually."
fi

# --- Clone or update repo ---
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "==> Updating existing repo in $INSTALL_DIR"
  git -C "$INSTALL_DIR" fetch --all --prune || true
  git -C "$INSTALL_DIR" checkout "$BRANCH" || true
  git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH" || git -C "$INSTALL_DIR" pull || true
else
  echo "==> Cloning repo into $INSTALL_DIR"
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# --- Python virtual environment ---
PYTHON="${PYTHON:-python3}"
if [ ! -d ".venv" ]; then
  echo "==> Creating virtual environment (.venv)..."
  "$PYTHON" -m venv .venv || {
    echo "venv module unavailable, trying virtualenv..."
    "$PYTHON" -m pip install --user virtualenv || true
    "$PYTHON" -m virtualenv .venv
  }
fi

# shellcheck disable=SC1091
. .venv/bin/activate

python -m pip install --upgrade pip setuptools wheel || true

# --- Python dependencies ---
echo "==> Installing runtime dependencies..."
if [ -f requirements.txt ]; then
  python -m pip install -r requirements.txt
else
  python -m pip install "textual>=0.47"
fi

if [ "$SKIP_WEB" = "1" ]; then
  echo "==> Skipping web dependencies (SKIP_WEB=1)."
elif [ -f requirements-web.txt ]; then
  echo "==> Installing web dependencies (this may take a while on iSH)..."
  if ! python -m pip install -r requirements-web.txt; then
    echo ""
    echo "WARNING: Web dependency installation failed." >&2
    echo "         You can still use the TUI/core. To skip web deps: SKIP_WEB=1 sh scripts/setup_ish_alpine.sh" >&2
    echo ""
  fi
else
  echo "==> requirements-web.txt not found; skipping web deps."
fi

if [ "$INSTALL_DEV" = "1" ]; then
  echo "==> Installing dev/test dependencies..."
  if [ -f requirements-dev.txt ]; then
    python -m pip install -r requirements-dev.txt
  else
    python -m pip install "pytest>=7.0" "httpx>=0.24"
  fi
fi

# --- Data dirs sanity (app also creates them) ---
mkdir -p data exports invoices 2>/dev/null || true

# --- Optional tests ---
if [ "$RUN_TESTS" = "1" ]; then
  echo "==> Running tests..."
  export PYTHONPATH="$INSTALL_DIR/src:$INSTALL_DIR${PYTHONPATH:+:$PYTHONPATH}"
  python -m pytest -c tests/pytest.ini "$@" || echo "WARNING: Tests failed."
fi

cat <<'EOF'

==> Setup complete.

To run the app (iSH/Alpine):
  cd "$INSTALL_DIR"
  . .venv/bin/activate

  # Launcher (choose TUI or Web)
  python3 launcher.py

  # TUI directly
  python3 launcher.py --tui

  # Web UI (recommended for iOS browser access)
  # Use 0.0.0.0 to reach it from other devices on the same network.
  export PM_WEB_SECRET_KEY='change-this-to-a-long-random-secret'
  python3 -m uvicorn ui.web.server:app --host 0.0.0.0 --port 8000 --workers 1
  # then open http://<ish-device-ip>:8000 in Safari (Add to Home Screen for app-like use)

Notes:
- SQLite DB/data are stored under the project data paths (see src/app_core/paths.py). Back up regularly.
- Keep --workers 1.
- If web deps failed to install on iSH, you can still use the TUI: SKIP_WEB=1 sh scripts/setup_ish_alpine.sh
EOF

# Print resolved install dir in final hint (POSIX-safe)
echo "    (install dir: $INSTALL_DIR)"
