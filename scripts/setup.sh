#!/bin/sh
# Dynamic setup/deploy script for property-manager-tui.
# Detects environment (iSH/Alpine, Termux, Debian/Ubuntu, macOS, generic Linux)
# and installs dependencies + prepares/runs the app (TUI or Web).
#
# Idempotent and data-preserving: never deletes DB/exports/invoices/PM_DATA_DIR.
# Web mode is SQLite-safe only with --workers 1.
#
# Usage:
#   sh scripts/setup.sh
#   MODE=tui|web|both|auto ACTION=setup|run|deploy|auto sh scripts/setup.sh
#   REPO_URL=... INSTALL_DIR=... BRANCH=main GIT_REF=<branch|tag|commit> HOST=0.0.0.0 PORT=8000 sh scripts/setup.sh
#
# Examples:
#   SKIP_WEB=1 MODE=tui sh scripts/setup.sh
#   MODE=web HOST=0.0.0.0 PM_WEB_SECRET_KEY='...' ACTION=deploy sh scripts/setup.sh
#   ACTION=run MODE=tui sh scripts/setup.sh
set -eu

REPO_URL="${REPO_URL:-https://github.com/Michaelhehelmy/property-manager-tui.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/property-manager-tui}"
BRANCH="${BRANCH:-main}"
GIT_REF="${GIT_REF:-${TAG:-${COMMIT:-$BRANCH}}}"
MODE="${MODE:-auto}"                # auto|tui|web|both
ACTION="${ACTION:-setup}"           # auto|setup|run|deploy
SKIP_WEB="${SKIP_WEB:-auto}"        # auto|0|1
INSTALL_DEV="${INSTALL_DEV:-0}"
RUN_TESTS="${RUN_TESTS:-0}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
PYTHON="${PYTHON:-python3}"

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

print_security_guidance() {
  # Non-fatal guidance: SQLite + workers=1, strong secret, HTTPS for shared/non-local access.
  if [ "$HOST" = "0.0.0.0" ] || [ "$HOST" = "::" ]; then
    warn "Web host is non-local ($HOST). Use a reverse proxy with HTTPS (Caddy/Nginx/Traefik) for shared/network deployments."
  fi
  if [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "localhost" ] && [ "$HOST" != "::1" ] && [ -z "${PM_WEB_SECRET_KEY:-}" ]; then
    warn "PM_WEB_SECRET_KEY is not set for non-local host '$HOST'. Set a strong random secret (e.g. openssl rand -hex 32)."
  fi
}

detect_env() {
  OS="$(uname -s 2>/dev/null || echo unknown)"
  ARCH="$(uname -m 2>/dev/null || echo unknown)"
  IS_ISH=0; IS_TERMUX=0; IS_ALPINE=0; IS_DEBIAN=0; IS_MAC=0
  PMGR=""
  [ -f /proc/ish/version ] && IS_ISH=1
  [ "${ISH_VERSION:-}" != "" ] && IS_ISH=1
  case "$OS" in
    *iSH*|*ish*) IS_ISH=1 ;;
  esac
  [ -d "${PREFIX:-}/com.termux" ] || [ "${TERMUX_VERSION:-}" != "" ] && IS_TERMUX=1
  [ -f /etc/alpine-release ] && IS_ALPINE=1
  [ -f /etc/debian_version ] && IS_DEBIAN=1
  [ "$OS" = "Darwin" ] && IS_MAC=1
  if command -v apk >/dev/null 2>&1; then PMGR="apk"; IS_ALPINE=1; fi
  if command -v apt-get >/dev/null 2>&1; then PMGR="apt"; [ $IS_ALPINE -eq 0 ] && IS_DEBIAN=1; fi
  if command -v pkg >/dev/null 2>&1 && [ $IS_TERMUX -eq 1 ]; then PMGR="pkg"; fi
  if command -v brew >/dev/null 2>&1 && [ "$PMGR" = "" ]; then PMGR="brew"; fi

  if [ "$SKIP_WEB" = "auto" ]; then
    if [ $IS_ISH -eq 1 ]; then SKIP_WEB=1; else SKIP_WEB=0; fi
  fi
  if [ "$MODE" = "auto" ]; then
    if [ $IS_ISH -eq 1 ] && [ "$SKIP_WEB" = "1" ]; then MODE="tui"; else MODE="both"; fi
  fi
  log "Environment: OS=$OS ARCH=$ARCH pkg=$PMGR ish=$IS_ISH alpine=$IS_ALPINE termux=$IS_TERMUX debian=$IS_DEBIAN mac=$IS_MAC"
  log "Selected: MODE=$MODE ACTION=$ACTION SKIP_WEB=$SKIP_WEB HOST=$HOST PORT=$PORT ref=$GIT_REF"
}

install_system_packages() {
  log "Installing system packages (best-effort, idempotent)..."
  case "$PMGR" in
    apk)
      apk update || warn "apk update failed; continuing..."
      apk add --no-cache \
        bash git ca-certificates curl wget python3 py3-pip py3-virtualenv sqlite tzdata \
        build-base python3-dev libffi-dev openssl-dev \
        || warn "Some apk packages failed; continuing..."
      if [ "$SKIP_WEB" != "1" ]; then
        apk add --no-cache cargo rust || warn "cargo/rust not installed (web deps may fail to build)"
      fi
      ;;
    apt)
      if command -v sudo >/dev/null 2>&1 && [ "$(id -u 2>/dev/null || echo 1)" != "0" ]; then SUDO=sudo; else SUDO=""; fi
      $SUDO apt-get update || warn "apt-get update failed; continuing..."
      $SUDO apt-get install -y \
        bash git ca-certificates curl wget python3 python3-pip python3-venv sqlite3 \
        build-essential python3-dev libffi-dev libssl-dev \
        || warn "Some apt packages failed; continuing..."
      ;;
    pkg)
      pkg update -y || warn "pkg update failed; continuing..."
      pkg install -y git python sqlite build-essential libffi openssl rust binutils || \
        pkg install -y git python sqlite clang libffi openssl || \
        warn "Some pkg packages failed; continuing..."
      ;;
    brew)
      brew install python git sqlite || warn "Some brew packages failed; continuing..."
      ;;
    *)
      warn "No supported package manager detected (apk/apt/pkg/brew). Ensure git, python3, pip, and build deps are installed."
      ;;
  esac
}

clone_or_update() {
  if [ -d "$INSTALL_DIR/.git" ]; then
    log "Updating existing repo in $INSTALL_DIR (data-preserving)"
    git -C "$INSTALL_DIR" fetch --all --tags --prune || warn "git fetch failed; continuing with existing checkout."
    if [ -n "$GIT_REF" ]; then
      log "Checking out requested ref: $GIT_REF"
      if git -C "$INSTALL_DIR" rev-parse --verify --quiet "refs/remotes/origin/$GIT_REF" >/dev/null; then
        git -C "$INSTALL_DIR" checkout -B "$GIT_REF" "origin/$GIT_REF" || git -C "$INSTALL_DIR" checkout "$GIT_REF" || warn "Failed to checkout ref '$GIT_REF'"
      else
        git -C "$INSTALL_DIR" checkout "$GIT_REF" || warn "Failed to checkout ref '$GIT_REF'"
      fi
      # Fast-forward only if on a branch tracking same name
      if git -C "$INSTALL_DIR" symbolic-ref -q HEAD >/dev/null 2>&1; then
        git -C "$INSTALL_DIR" pull --ff-only origin "$GIT_REF" 2>/dev/null || git -C "$INSTALL_DIR" pull --ff-only 2>/dev/null || true
      fi
    else
      git -C "$INSTALL_DIR" checkout "$BRANCH" 2>/dev/null || true
      git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH" 2>/dev/null || git -C "$INSTALL_DIR" pull --ff-only 2>/dev/null || git -C "$INSTALL_DIR" pull 2>/dev/null || warn "git pull failed; continuing."
    fi
  else
    log "Cloning repo into $INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    if ! git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR" 2>/dev/null; then
      git clone "$REPO_URL" "$INSTALL_DIR"
    fi
    if [ -n "$GIT_REF" ]; then
      git -C "$INSTALL_DIR" checkout "$GIT_REF" || warn "Failed to checkout requested ref '$GIT_REF'"
    fi
  fi
  cd "$INSTALL_DIR"
}

setup_venv_and_deps() {
  cd "$INSTALL_DIR"
  if [ ! -d ".venv" ]; then
    log "Creating virtual environment (.venv)..."
    "$PYTHON" -m venv .venv || {
      warn "venv module unavailable, trying virtualenv..."
      "$PYTHON" -m pip install --user virtualenv || true
      "$PYTHON" -m virtualenv .venv
    }
  fi
  # shellcheck disable=SC1091
  . .venv/bin/activate
  python -m pip install --upgrade pip setuptools wheel || warn "pip bootstrap upgrade failed; continuing..."

  log "Installing runtime dependencies..."
  if [ -f requirements.txt ]; then
    python -m pip install -r requirements.txt
  else
    python -m pip install "textual>=0.47"
  fi

  if [ "$SKIP_WEB" = "1" ] || [ "$MODE" = "tui" ]; then
    if [ "$MODE" = "tui" ] && [ "$SKIP_WEB" = "auto" ]; then SKIP_WEB=1; fi
    log "Skipping web dependencies (MODE=$MODE SKIP_WEB=$SKIP_WEB)."
  elif [ -f requirements-web.txt ]; then
    log "Installing web dependencies..."
    if ! python -m pip install -r requirements-web.txt; then
      warn "Web dependency installation failed. TUI/core can still be used. Retry with SKIP_WEB=1 MODE=tui."
      SKIP_WEB=1
      [ "$MODE" = "both" ] && MODE="tui"
    fi
  fi

  if [ "$INSTALL_DEV" = "1" ]; then
    log "Installing dev/test dependencies..."
    if [ -f requirements-dev.txt ]; then
      python -m pip install -r requirements-dev.txt || warn "Dev dependency install had issues."
    else
      python -m pip install "pytest>=7.0" "httpx>=0.24" || warn "Dev dependency install had issues."
    fi
  fi

  # Preserve runtime data across setup/updates (never delete DB/exports/invoices/data dir).
  mkdir -p data exports invoices 2>/dev/null || true
  if [ -n "${PM_DATA_DIR:-}" ]; then
    mkdir -p "$PM_DATA_DIR" "$PM_DATA_DIR/exports" "$PM_DATA_DIR/invoices" 2>/dev/null || true
  fi

  if [ "$RUN_TESTS" = "1" ]; then
    log "Running tests..."
    export PYTHONPATH="$INSTALL_DIR/src:$INSTALL_DIR${PYTHONPATH:+:$PYTHONPATH}"
    python -m pytest -c tests/pytest.ini || warn "Tests failed."
  fi
}

print_hints() {
  validate_web_target
  print_security_guidance
  echo ""
  log "Ready. (dir: $INSTALL_DIR, mode: $MODE, web_skipped: $SKIP_WEB, ref: $GIT_REF)"
  cat <<EOF

Run (activate venv first):
  cd "$INSTALL_DIR"
  . .venv/bin/activate

  # TUI (local terminal)
  python3 launcher.py --tui

  # Web server (client/server; SQLite-safe: ALWAYS --workers 1)
  export PM_WEB_SECRET_KEY='replace-with-a-long-random-secret'
  python3 -m uvicorn ui.web.server:app --host ${HOST} --port ${PORT} --workers 1

Client/server & ops:
  - Same device browser: HOST=127.0.0.1 -> http://127.0.0.1:${PORT}
  - LAN/other devices: HOST=0.0.0.0 -> http://<device-ip>:${PORT} (set strong PM_WEB_SECRET_KEY and use HTTPS reverse proxy)
  - HTTPS: use Caddy/Nginx/Traefik in front of uvicorn for any shared/non-local deployment.
  - Runtime data is preserved: DB, exports, invoices, and PM_DATA_DIR are never deleted by setup/update scripts.
  - Updates (git): Settings → App updates (fast-forward) or git pull --ff-only + re-run ACTION=setup; restart after updates.
  - Health: python3 -m app_core.doctor --json (probes: /health, /healthz)
  - Backup/verify: python3 scripts/backup_runtime.py --backup | --verify <backup.db>
  - Deployment examples: deploy/ (systemd, Caddyfile, nginx) and Dockerfile/docker-compose.yml
EOF
  if [ $IS_ISH -eq 1 ]; then
    echo "  - iSH: TUI is most reliable; web deps/builds may be slow or unavailable (use SKIP_WEB=1)."
  fi
  if [ $IS_TERMUX -eq 1 ]; then
    echo "  - Termux: both TUI and web are supported; bind 0.0.0.0 for LAN access from other devices."
  fi
}

run_deploy() {
  [ -d "$INSTALL_DIR/.venv" ] || { err "Install not found at $INSTALL_DIR. Run ACTION=setup or ACTION=auto first."; exit 1; }
  cd "$INSTALL_DIR"
  # shellcheck disable=SC1091
  . .venv/bin/activate
  export PYTHONPATH="$INSTALL_DIR/src:$INSTALL_DIR${PYTHONPATH:+:$PYTHONPATH}"
  validate_web_target
  case "$MODE" in
    tui)
      log "Launching TUI..."
      exec python3 launcher.py --tui
      ;;
    web)
      if [ "$SKIP_WEB" = "1" ]; then
        err "Web deps were skipped; cannot start web mode. Re-run with SKIP_WEB=0 MODE=web."
        exit 1
      fi
      print_security_guidance
      log "Launching Web server on ${HOST}:${PORT} (workers=1)..."
      log "Runtime data (DB/exports/invoices/PM_DATA_DIR) is preserved. Health: python3 -m app_core.doctor"
      exec python3 -m uvicorn ui.web.server:app --host "$HOST" --port "$PORT" --workers 1
      ;;
    both|auto)
      log "Launching launcher (choose TUI/Web)..."
      exec python3 launcher.py
      ;;
    *)
      err "Unknown MODE=$MODE (use auto|tui|web|both)"
      exit 1
      ;;
  esac
}

main() {
  detect_env
  case "$ACTION" in
    auto)
      # Smart, idempotent: first install -> full setup + launch; existing install -> update/refresh deps + launch.
      if [ -d "$INSTALL_DIR/.git" ] && [ -d "$INSTALL_DIR/.venv" ]; then
        log "Existing install detected: checking for updates and launching..."
        clone_or_update
        setup_venv_and_deps
        run_deploy
      else
        log "No existing install detected: running full setup and launch..."
        install_system_packages
        clone_or_update
        setup_venv_and_deps
        print_hints
        run_deploy
      fi
      ;;
    setup)
      install_system_packages
      clone_or_update
      setup_venv_and_deps
      print_hints
      ;;
    run)
      [ -d "$INSTALL_DIR/.venv" ] || { err "Install not found at $INSTALL_DIR. Run ACTION=setup or ACTION=auto first."; exit 1; }
      run_deploy
      ;;
    deploy)
      install_system_packages
      clone_or_update
      setup_venv_and_deps
      print_hints
      run_deploy
      ;;
    *)
      err "Unknown ACTION=$ACTION (use auto|setup|run|deploy)"
      exit 1
      ;;
  esac
}

main "$@"