#!/bin/sh
# Update (git fast-forward when on a branch) and run the Web UI.
# Idempotent/data-preserving: never deletes DB/exports/invoices/PM_DATA_DIR.
# SQLite-safe: ALWAYS runs uvicorn with --workers 1.
#
# Usage:
#   sh scripts/update_and_run_web.sh
#   HOST=0.0.0.0 PORT=8000 PM_WEB_SECRET_KEY="$(openssl rand -hex 32)" sh scripts/update_and_run_web.sh
#   INSTALL_DIR=~/property-manager-tui GIT_REF=main sh scripts/update_and_run_web.sh
#
# Notes:
# - For shared/non-local access, set a strong PM_WEB_SECRET_KEY and use HTTPS via reverse proxy.
# - Runtime data is preserved across updates/restarts. Back up before major changes.
set -eu

# Resolve install dir: explicit INSTALL_DIR, or repo root relative to this script.
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd)"
DEFAULT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." 2>/dev/null && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_DIR}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
PYTHON="${PYTHON:-python3}"
GIT_REF="${GIT_REF:-${TAG:-${COMMIT:-}}}"

log() { printf '%s\n' "==> $*"; }
warn() { printf '%s\n' "WARNING: $*" >&2; }
err() { printf '%s\n' "ERROR: $*" >&2; }

validate_web_target() {
  case "$HOST" in
    ""|*[!A-Za-z0-9._:-\[\]]*) warn "Invalid HOST='$HOST'; falling back to 127.0.0.1"; HOST="127.0.0.1" ;;
  esac
  case "$PORT" in
    ''|*[!0-9]*) warn "Invalid PORT='$PORT'; falling back to 8000"; PORT="8000" ;;
    *) if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then warn "Invalid PORT='$PORT'; falling back to 8000"; PORT="8000"; fi ;;
  esac
}

if [ ! -d "$INSTALL_DIR" ]; then
  err "Install directory not found: $INSTALL_DIR"
  err "Set INSTALL_DIR=/path/to/property-manager-tui and re-run (or run scripts/install.sh/setup.sh first)."
  exit 1
fi

cd "$INSTALL_DIR"
validate_web_target

# Update if this is a git checkout (data-preserving). ff-only on branch checkouts.
if [ -d ".git" ] && command -v git >/dev/null 2>&1; then
  log "Git checkout detected; fetching updates..."
  git fetch --all --tags --prune || warn "git fetch failed (continuing with current version)."
  if [ -n "$GIT_REF" ]; then
    log "Checking out requested ref: $GIT_REF"
    if git rev-parse --verify --quiet "refs/remotes/origin/$GIT_REF" >/dev/null; then
      git checkout -B "$GIT_REF" "origin/$GIT_REF" || git checkout "$GIT_REF" || warn "Failed to checkout ref '$GIT_REF' (continuing)."
    else
      git checkout "$GIT_REF" || warn "Failed to checkout ref '$GIT_REF' (continuing)."
    fi
  fi
  if git symbolic-ref -q HEAD >/dev/null 2>&1; then
    git pull --ff-only || warn "Git fast-forward update skipped/failed (continuing with current version)."
  else
    warn "Detached HEAD/pinned ref detected; skipping automatic pull to preserve pinned state."
  fi
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
  err "Virtual environment not found at $INSTALL_DIR/.venv"
  err "Run setup first, e.g.: ACTION=setup sh scripts/setup.sh  (or scripts/install.sh)"
  exit 1
fi

# Ensure src is importable
export PYTHONPATH="$INSTALL_DIR/src:$INSTALL_DIR${PYTHONPATH:+:$PYTHONPATH}"

# Secret key guidance (required for secure sessions in shared/non-local use).
if [ -z "${PM_WEB_SECRET_KEY:-}" ]; then
  if [ "$HOST" = "127.0.0.1" ] || [ "$HOST" = "localhost" ] || [ "$HOST" = "::1" ]; then
    warn "PM_WEB_SECRET_KEY not set; using a temporary local-only default. Set a strong secret for shared/LAN use."
    export PM_WEB_SECRET_KEY="dev-local-$(hostname 2>/dev/null || echo local)-$$"
  else
    warn "PM_WEB_SECRET_KEY is not set for non-local host '$HOST'. Set a strong random secret (e.g. openssl rand -hex 32)."
    warn "Using a temporary fallback secret for this run only; do not use this for production/shared deployments."
    export PM_WEB_SECRET_KEY="temp-change-me-$(hostname 2>/dev/null || echo local)-$$"
  fi
fi

if [ "$HOST" = "0.0.0.0" ] || [ "$HOST" = "::" ] || { [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "localhost" ] && [ "$HOST" != "::1" ]; }; then
  warn "Non-local/shared access: terminate HTTPS at a reverse proxy (Caddy/Nginx/Traefik). Do not expose plain HTTP to the internet."
fi

# Best-effort dependency check hint (non-fatal)
if ! "$PYTHON" -c "import fastapi, uvicorn, jinja2" >/dev/null 2>&1; then
  warn "Web dependencies may be missing. Try: pip install -r requirements-web.txt (or pip install .[web])"
fi

log "Runtime data (DB/exports/invoices/PM_DATA_DIR) is preserved. Backup: python3 scripts/backup_runtime.py --backup"
log "Starting Web UI on http://${HOST}:${PORT} (workers=1). Health: /health /healthz. Press Ctrl+C to stop."
exec "$PYTHON" -m uvicorn ui.web.server:app --host "$HOST" --port "$PORT" --workers 1