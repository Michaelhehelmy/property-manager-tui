#!/bin/sh
# Update (git fast-forward) and run the Web UI in one command.
# Usage:
#   sh scripts/update_and_run_web.sh
#   HOST=0.0.0.0 PORT=8000 sh scripts/update_and_run_web.sh
#   INSTALL_DIR=~/property-manager-tui sh scripts/update_and_run_web.sh
#
# Notes:
# - Web server runs with --workers 1 (SQLite). Runtime data (DB/exports/invoices/PM_DATA_DIR) is preserved.
# - Set PM_WEB_SECRET_KEY to a strong random value for non-local/shared access.
set -eu

# Resolve install dir: explicit INSTALL_DIR, or repo root relative to this script.
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd)"
DEFAULT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." 2>/dev/null && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_DIR}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
PYTHON="${PYTHON:-python3}"

log() { printf '%s\n' "==> $*"; }
warn() { printf '%s\n' "WARNING: $*" >&2; }

if [ ! -d "$INSTALL_DIR" ]; then
  warn "Install directory not found: $INSTALL_DIR"
  warn "Set INSTALL_DIR=/path/to/property-manager-tui and re-run."
  exit 1
fi

cd "$INSTALL_DIR"

# Update if this is a git checkout (fast-forward only; preserves local runtime data).
if [ -d ".git" ] && command -v git >/dev/null 2>&1; then
  log "Updating repo (git pull --ff-only)..."
  git pull --ff-only || warn "Git update skipped/failed (continuing with current version)."
else
  log "Skipping git update (not a git repo or git unavailable)."
fi

# Activate venv if present
if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  . ".venv/bin/activate"
elif [ -f ".venv/Scripts/activate" ]; then
  # shellcheck disable=SC1091
  . ".venv/Scripts/activate"
else
  warn "Virtual environment not found at $INSTALL_DIR/.venv"
  warn "Run setup first, e.g.: ACTION=setup sh scripts/setup.sh  (or scripts/install.sh)"
  exit 1
fi

# Ensure src is importable
export PYTHONPATH="$INSTALL_DIR/src:$INSTALL_DIR${PYTHONPATH:+:$PYTHONPATH}"

# Secret key (required for sessions). Use existing or set a dev default with warning.
if [ -z "${PM_WEB_SECRET_KEY:-}" ]; then
  warn "PM_WEB_SECRET_KEY not set; using a temporary local default. Set a strong secret for shared/LAN use."
  export PM_WEB_SECRET_KEY="dev-change-me-$(hostname 2>/dev/null || echo local)"
fi

# Best-effort dependency check hint (non-fatal)
if ! "$PYTHON" -c "import fastapi, uvicorn, jinja2" >/dev/null 2>&1; then
  warn "Web dependencies may be missing. Try: pip install .[web]  (or install fastapi uvicorn jinja2 python-multipart itsdangerous)"
fi

log "Starting Web UI on http://${HOST}:${PORT} (workers=1). Press Ctrl+C to stop."
exec "$PYTHON" -m uvicorn ui.web.server:app --host "$HOST" --port "$PORT" --workers 1