#!/bin/sh
# One-line bootstrap installer for property-manager-tui (Unix-like OSes).
# Detects package manager best-effort, ensures git/curl/wget/ca-certificates,
# clones/updates the repo, and runs scripts/setup.sh.
#
# Usage (one-liner):
#   curl -fsSL https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install.sh | sh
#   wget -qO-  https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install.sh | sh
#
# With options (env vars):
#   curl -fsSL .../install.sh | sh                                 # install/update + launch launcher
#   curl -fsSL .../install.sh | MODE=tui sh                        # prefer TUI
#   curl -fsSL .../install.sh | MODE=web HOST=0.0.0.0 PORT=8000 sh
#   curl -fsSL .../install.sh | SKIP_WEB=1 MODE=tui sh             # iSH/low-resource
#
# Env vars: REPO_URL, INSTALL_DIR, BRANCH, MODE, ACTION, SKIP_WEB, HOST, PORT,
#           INSTALL_DEV, RUN_TESTS, PYTHON, PM_DATA_DIR, PM_WEB_SECRET_KEY
# Default ACTION=auto: first run installs everything and launches launcher;
# re-run checks for updates (git fast-forward), refreshes deps, and launches.
# Use ACTION=setup to install/update only, or ACTION=run to run without updating.
set -eu

REPO_URL="${REPO_URL:-https://github.com/Michaelhehelmy/property-manager-tui.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/property-manager-tui}"
BRANCH="${BRANCH:-main}"
MODE="${MODE:-auto}"
ACTION="${ACTION:-auto}"
SKIP_WEB="${SKIP_WEB:-auto}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

log() { printf '%s\n' "==> $*"; }
warn() { printf '%s\n' "WARNING: $*" >&2; }

have() { command -v "$1" >/dev/null 2>&1; }

detect_pm() {
  PM=""
  if have apk; then PM=apk
  elif have apt-get; then PM=apt
  elif have pkg && [ -n "${PREFIX:-}" ] && echo "${PREFIX}" | grep -q "com.termux"; then PM=pkg
  elif have dnf; then PM=dnf
  elif have yum; then PM=yum
  elif have pacman; then PM=pacman
  elif have zypper; then PM=zypper
  elif have brew; then PM=brew
  elif have pkg; then PM=pkg_generic
  fi
  echo "$PM"
}

install_bootstrap_deps() {
  # Ensure git and CA certs (and curl/wget if missing) so we can fetch/clone.
  if have git; then
    return 0
  fi
  PM="$(detect_pm)"
  log "git not found; attempting to install bootstrap prerequisites (pm=$PM)..."
  case "$PM" in
    apk)
      (apk update && apk add --no-cache git ca-certificates curl wget bash) || warn "apk bootstrap install had issues"
      ;;
    apt)
      SUDO=""; [ "$(id -u 2>/dev/null || echo 1)" != "0" ] && have sudo && SUDO="sudo"
      ($SUDO apt-get update && $SUDO apt-get install -y git ca-certificates curl wget) || warn "apt bootstrap install had issues"
      ;;
    pkg)
      (pkg update -y && pkg install -y git ca-certificates curl wget) || warn "pkg bootstrap install had issues"
      ;;
    dnf)
      SUDO=""; [ "$(id -u 2>/dev/null || echo 1)" != "0" ] && have sudo && SUDO="sudo"
      ($SUDO dnf install -y git ca-certificates curl wget) || warn "dnf bootstrap install had issues"
      ;;
    yum)
      SUDO=""; [ "$(id -u 2>/dev/null || echo 1)" != "0" ] && have sudo && SUDO="sudo"
      ($SUDO yum install -y git ca-certificates curl wget) || warn "yum bootstrap install had issues"
      ;;
    pacman)
      SUDO=""; [ "$(id -u 2>/dev/null || echo 1)" != "0" ] && have sudo && SUDO="sudo"
      ($SUDO pacman -Sy --noconfirm git ca-certificates curl wget) || warn "pacman bootstrap install had issues"
      ;;
    zypper)
      SUDO=""; [ "$(id -u 2>/dev/null || echo 1)" != "0" ] && have sudo && SUDO="sudo"
      ($SUDO zypper --non-interactive install git ca-certificates curl wget) || warn "zypper bootstrap install had issues"
      ;;
    brew)
      (brew install git) || warn "brew bootstrap install had issues"
      ;;
    *)
      warn "No supported package manager detected. Please install 'git' (and python3) manually, then re-run."
      ;;
  esac
}

fetch_setup_if_needed() {
  # If running inside an existing checkout with setup.sh, use it.
  if [ -f "$INSTALL_DIR/scripts/setup.sh" ]; then
    return 0
  fi
  # If we don't have a local setup.sh (e.g. piped and clone failed), try to fetch it.
  TMP_SETUP="${TMPDIR:-/tmp}/property-manager-tui-setup.sh.$$"
  SETUP_URL="https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/${BRANCH}/scripts/setup.sh"
  if have curl; then
    curl -fsSL "$SETUP_URL" -o "$TMP_SETUP" || return 1
  elif have wget; then
    wget -qO "$TMP_SETUP" "$SETUP_URL" || return 1
  else
    return 1
  fi
  chmod +x "$TMP_SETUP" 2>/dev/null || true
  FETCHED_SETUP="$TMP_SETUP"
  return 0
}

main() {
  log "property-manager-tui bootstrap installer"
  log "repo=$REPO_URL dir=$INSTALL_DIR branch=$BRANCH mode=$MODE action=$ACTION skip_web=$SKIP_WEB host=$HOST port=$PORT"

  install_bootstrap_deps
  if ! have git; then
    warn "git is required but was not found/installed. Install git and re-run."
    exit 1
  fi

  # Clone or update
  if [ -d "$INSTALL_DIR/.git" ]; then
    log "Updating existing checkout in $INSTALL_DIR"
    git -C "$INSTALL_DIR" fetch --all --prune || true
    git -C "$INSTALL_DIR" checkout "$BRANCH" || true
    git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH" || git -C "$INSTALL_DIR" pull --ff-only || git -C "$INSTALL_DIR" pull || true
  else
    log "Cloning into $INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
  fi

  SETUP="$INSTALL_DIR/scripts/setup.sh"
  if [ ! -f "$SETUP" ]; then
    warn "setup.sh not found in checkout; attempting to fetch from GitHub..."
    FETCHED_SETUP=""
    if fetch_setup_if_needed && [ -n "${FETCHED_SETUP:-}" ] && [ -f "$FETCHED_SETUP" ]; then
      SETUP="$FETCHED_SETUP"
    else
      warn "Could not locate scripts/setup.sh. Check repo/branch and network access."
      exit 1
    fi
  fi

  # Run setup script with exported config (preserve runtime data on updates).
  export REPO_URL INSTALL_DIR BRANCH MODE ACTION SKIP_WEB HOST PORT
  [ -n "${INSTALL_DEV:-}" ] && export INSTALL_DEV
  [ -n "${RUN_TESTS:-}" ] && export RUN_TESTS
  [ -n "${PYTHON:-}" ] && export PYTHON
  [ -n "${PM_DATA_DIR:-}" ] && export PM_DATA_DIR
  [ -n "${PM_WEB_SECRET_KEY:-}" ] && export PM_WEB_SECRET_KEY

  log "Running setup script..."
  # Use sh for portability; setup.sh is POSIX-sh compatible.
  exec sh "$SETUP"
}

main "$@"