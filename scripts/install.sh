#!/bin/sh
# One-line bootstrap installer for property-manager-tui (Unix-like OSes).
# - Idempotent clone/update (git) with optional pinned ref (branch/tag/commit)
# - Preserves runtime data (DB/exports/invoices/PM_DATA_DIR) across updates/runs
# - Safe defaults: Web runs with --workers 1 (SQLite); strong secret + HTTPS for non-local use
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install.sh | sh
#   wget -qO-  https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/main/scripts/install.sh | sh
#
# Examples:
#   curl -fsSL .../install.sh | MODE=tui sh
#   curl -fsSL .../install.sh | MODE=web HOST=0.0.0.0 PORT=8000 PM_WEB_SECRET_KEY="$(openssl rand -hex 32)" sh
#   curl -fsSL .../install.sh | ACTION=setup sh
#   curl -fsSL .../install.sh | GIT_REF=v1.2.3 sh        # pinned tag/branch/commit
#   curl -fsSL .../install.sh | TAG=v1.2.3 sh            # alias for pinned ref
#   curl -fsSL .../install.sh | COMMIT=<sha> sh          # alias for pinned ref
#   curl -fsSL .../install.sh | REPAIR=1 sh              # recover dirty/stuck git checkout
#
# Env vars: REPO_URL, INSTALL_DIR, BRANCH, GIT_REF, TAG, COMMIT, MODE, ACTION, SKIP_WEB,
#           HOST, PORT, REPAIR/FORCE_RESET, INSTALL_DEV, RUN_TESTS, PYTHON, PM_DATA_DIR, PM_WEB_SECRET_KEY
# ACTION: auto|setup|run|deploy (default: auto). MODE: auto|tui|web|both.
set -eu

REPO_URL="${REPO_URL:-https://github.com/Michaelhehelmy/property-manager-tui.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/property-manager-tui}"
BRANCH="${BRANCH:-main}"
# Pinned ref support: GIT_REF wins, then TAG, then COMMIT, else BRANCH.
GIT_REF="${GIT_REF:-${TAG:-${COMMIT:-$BRANCH}}}"
MODE="${MODE:-auto}"
ACTION="${ACTION:-auto}"
SKIP_WEB="${SKIP_WEB:-auto}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
REPAIR="${REPAIR:-${FORCE_RESET:-0}}"

log() { printf '%s\n' "==> $*"; }
warn() { printf '%s\n' "WARNING: $*" >&2; }
err() { printf '%s\n' "ERROR: $*" >&2; }
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
  # Best-effort: ensure git and CA/fetch tools so we can clone/update.
  if have git; then
    return 0
  fi
  PM="$(detect_pm)"
  log "git not found; attempting to install bootstrap prerequisites (pm=${PM:-unknown})..."
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

print_ops_guidance() {
  log "Ops guidance: SQLite web mode requires --workers 1. Runtime data (DB/exports/invoices/PM_DATA_DIR) is preserved across updates/restarts."
  log "Pinned/reproducible install: set GIT_REF=<tag|commit|branch> (or TAG/COMMIT). For pinned bootstrap, fetch this script from raw/<ref>/scripts/install.sh."
  if [ "${PM_WEB_SECRET_KEY:-}" = "" ] && [ "${HOST:-127.0.0.1}" != "127.0.0.1" ] && [ "${HOST:-127.0.0.1}" != "localhost" ]; then
    warn "PM_WEB_SECRET_KEY is not set and HOST=$HOST is non-local. Set a strong random secret (e.g. openssl rand -hex 32)."
  fi
  if [ "${HOST:-127.0.0.1}" = "0.0.0.0" ] || [ "${HOST:-127.0.0.1}" = "::" ]; then
    warn "Non-local/LAN bind requested. Use HTTPS via a reverse proxy (Caddy/Nginx) for shared/network deployments."
  fi
  log "If web dependency builds fail (e.g. pydantic-core/Rust wheels on constrained environments), use TUI-only: SKIP_WEB=1 MODE=tui."
}

fetch_setup_if_needed() {
  # If running inside an existing checkout with setup.sh, use it.
  if [ -f "$INSTALL_DIR/scripts/setup.sh" ]; then
    return 0
  fi
  # If local setup.sh missing (e.g. piped and clone failed), try to fetch from pinned ref/branch.
  TMP_SETUP="${TMPDIR:-/tmp}/property-manager-tui-setup.sh.$$"
  FETCH_REF="$GIT_REF"
  [ -n "$FETCH_REF" ] || FETCH_REF="$BRANCH"
  SETUP_URL="https://raw.githubusercontent.com/Michaelhehelmy/property-manager-tui/${FETCH_REF}/scripts/setup.sh"
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
  log "repo=$REPO_URL dir=$INSTALL_DIR ref=$GIT_REF mode=$MODE action=$ACTION skip_web=$SKIP_WEB host=$HOST port=$PORT"

  install_bootstrap_deps
  if ! have git; then
    err "git is required but was not found/installed. Install git and re-run."
    exit 1
  fi

  # Idempotent clone or update (never delete runtime data dirs/files).
  if [ -d "$INSTALL_DIR/.git" ]; then
    log "Updating existing checkout in $INSTALL_DIR"
    [ -f "$INSTALL_DIR/.git/index.lock" ] && { warn "Removing stale .git/index.lock"; rm -f "$INSTALL_DIR/.git/index.lock" || true; }
    git -C "$INSTALL_DIR" merge --abort >/dev/null 2>&1 || true
    git -C "$INSTALL_DIR" rebase --abort >/dev/null 2>&1 || true
    git -C "$INSTALL_DIR" cherry-pick --abort >/dev/null 2>&1 || true
    if ! git -C "$INSTALL_DIR" fetch --all --tags --prune; then
      warn "git fetch failed; continuing with existing local checkout."
    fi
    if [ "$REPAIR" = "1" ]; then
      warn "REPAIR=1: resetting local source to origin/$BRANCH (discards local source changes/untracked files inside repo; PM_DATA_DIR/external runtime data unaffected)"
      git -C "$INSTALL_DIR" checkout -B "$BRANCH" "origin/$BRANCH" || git -C "$INSTALL_DIR" checkout "$BRANCH" || { err "Repair checkout failed for branch '$BRANCH'."; exit 1; }
      git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH" || { err "Repair reset failed."; exit 1; }
      git -C "$INSTALL_DIR" clean -fd >/dev/null 2>&1 || true
      # if a different pinned ref was requested, checkout after repair
      if [ -n "$GIT_REF" ] && [ "$GIT_REF" != "$BRANCH" ]; then
        log "Checking out requested ref after repair: $GIT_REF"
        if git -C "$INSTALL_DIR" rev-parse --verify --quiet "refs/remotes/origin/$GIT_REF" >/dev/null; then
          git -C "$INSTALL_DIR" checkout -B "$GIT_REF" "origin/$GIT_REF" || git -C "$INSTALL_DIR" checkout "$GIT_REF"
        else
          git -C "$INSTALL_DIR" checkout "$GIT_REF" || { err "Failed to checkout ref '$GIT_REF' after repair."; exit 1; }
        fi
      fi
    else
      if [ -n "$(git -C "$INSTALL_DIR" status --porcelain 2>/dev/null || true)" ]; then
        warn "Local changes detected in $INSTALL_DIR. Commit/stash/discard them, or rerun with REPAIR=1 to auto-reset local source to origin/$BRANCH."
        git -C "$INSTALL_DIR" status --short --branch 2>/dev/null || true
        err "Working tree is not clean (set REPAIR=1/FORCE_RESET=1 for automatic recovery)."
        exit 1
      fi
      # Best-effort: only fast-forward when on a local branch (avoid breaking detached pinned refs).
      if git -C "$INSTALL_DIR" symbolic-ref -q HEAD >/dev/null 2>&1; then
        git -C "$INSTALL_DIR" pull --ff-only || { err "git pull --ff-only failed. Rerun with REPAIR=1/FORCE_RESET=1 or resolve manually."; exit 1; }
      fi
    fi
  else
    log "Cloning into $INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    # Try a direct branch clone first for speed; fall back to full clone for tags/commits.
    if ! git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR" 2>/dev/null; then
      git clone "$REPO_URL" "$INSTALL_DIR"
    fi
  fi

  # Checkout pinned ref (branch/tag/commit) safely and idempotently (skip if repair already handled).
  if [ -n "$GIT_REF" ] && [ -d "$INSTALL_DIR/.git" ]; then
    if [ "$REPAIR" = "1" ] && [ "$GIT_REF" = "$BRANCH" ]; then
      :
    elif [ "$REPAIR" = "1" ] && [ "$GIT_REF" != "$BRANCH" ]; then
      : # handled after repair above
    else
      log "Checking out requested ref: $GIT_REF"
      git -C "$INSTALL_DIR" fetch --all --tags --prune >/dev/null 2>&1 || true
      if git -C "$INSTALL_DIR" rev-parse --verify --quiet "refs/remotes/origin/$GIT_REF" >/dev/null; then
        git -C "$INSTALL_DIR" checkout -B "$GIT_REF" "origin/$GIT_REF" || git -C "$INSTALL_DIR" checkout "$GIT_REF"
      else
        git -C "$INSTALL_DIR" checkout "$GIT_REF" || {
          err "Failed to checkout ref '$GIT_REF'. Verify branch/tag/commit exists."
          exit 1
        }
      fi
    fi
  fi

  SETUP="$INSTALL_DIR/scripts/setup.sh"
  if [ ! -f "$SETUP" ]; then
    warn "setup.sh not found in checkout; attempting to fetch from GitHub..."
    FETCHED_SETUP=""
    if fetch_setup_if_needed && [ -n "${FETCHED_SETUP:-}" ] && [ -f "$FETCHED_SETUP" ]; then
      SETUP="$FETCHED_SETUP"
    else
      err "Could not locate scripts/setup.sh. Check repo/ref and network access."
      exit 1
    fi
  fi

  print_ops_guidance

  # Export config for setup script (preserve runtime data on updates).
  export REPO_URL INSTALL_DIR BRANCH GIT_REF TAG COMMIT MODE ACTION SKIP_WEB HOST PORT
  [ -n "${INSTALL_DEV:-}" ] && export INSTALL_DEV
  [ -n "${RUN_TESTS:-}" ] && export RUN_TESTS
  [ -n "${PYTHON:-}" ] && export PYTHON
  [ -n "${PM_DATA_DIR:-}" ] && export PM_DATA_DIR
  [ -n "${PM_WEB_SECRET_KEY:-}" ] && export PM_WEB_SECRET_KEY

  log "Running setup script..."
  exec sh "$SETUP"
}

main "$@"