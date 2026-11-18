#!/bin/sh
# Best-effort: build a native app for the current OS/arch, package it, generate a
# sanitized report + prefilled GitHub issue URL, and optionally auto-file via gh.
# Privacy-first: no secrets/DB/runtime data included. Run with explicit user consent.
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

log() { printf '%s\n' "==> $*"; }
warn() { printf '%s\n' "WARNING: $*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

# Prefer repo venv python if present
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then PY="$ROOT_DIR/.venv/bin/python";
elif [ -x "$ROOT_DIR/.venv/Scripts/python.exe" ]; then PY="$ROOT_DIR/.venv/Scripts/python.exe";
elif have python3; then PY=python3; elif have python; then PY=python; else
  warn "Python not found. Install Python 3.10+ and retry."; exit 1; fi

OS="$(uname -s 2>/dev/null || echo unknown)"
ARCH="$(uname -m 2>/dev/null || echo unknown)"
case "$OS" in
  Linux*)  os_name=linux ;;
  Darwin*) os_name=macos ;;
  MINGW*|MSYS*|CYGWIN*) os_name=windows ;;
  *) os_name=$(echo "$OS" | tr '[:upper:]' '[:lower:]') ;;
esac
case "$ARCH" in
  x86_64|amd64) arch_name=x64 ;;
  arm64|aarch64) arch_name=arm64 ;;
  *) arch_name="$ARCH" ;;
esac
ARTIFACT_BASENAME="property-manager-${os_name}-${arch_name}"
DIST_DIR="$ROOT_DIR/dist/community-native"
NUITKA_OUT="$ROOT_DIR/dist/nuitka-community"
LOG_FILE="$DIST_DIR/build.log"
REPORT_FILE="$DIST_DIR/community-native-build-report.md"
mkdir -p "$DIST_DIR"

APP_REF="${GIT_REF:-${TAG:-${COMMIT:-$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')}}}"
COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo '')"

log "Community native build (OS=$OS ARCH=$ARCH -> ${ARTIFACT_BASENAME})"
log "This may take a long time and requires a C compiler/toolchain. Build logs are kept locally; the report is sanitized."
log "Installing/ensuring build deps (nuitka, ordered-set, zstandard) in the active Python environment..."
"$PY" -m pip install --upgrade pip >/dev/null 2>&1 || true
"$PY" -m pip install nuitka ordered-set zstandard || { warn "Failed to install Nuitka/build deps. Ensure compiler/toolchain and Python headers are installed."; exit 1; }
NUITKA_VER="$($PY -m nuitka --version 2>/dev/null | head -n1 || echo '')"

BUILD_STATUS=failed
ARTIFACT_PATH=""
CHECKSUM=""
BUILD_CMD="$PY -m nuitka --standalone --assume-yes-for-downloads --enable-plugin=anti-bloat --include-package=app_core --include-package=ui --include-package=uvicorn --include-package=fastapi --include-package=starlette --include-package=anyio --include-package=jinja2 --include-data-dir=ui/web/templates=ui/web/templates --include-data-dir=ui/web/static=ui/web/static --include-data-files=pyproject.toml=pyproject.toml --output-dir=dist/nuitka-community launcher.py"

set +e
log "Building with Nuitka (standalone)..."
rm -rf "$NUITKA_OUT" 2>/dev/null || true
# shellcheck disable=SC2086
$PY -m nuitka \
  --standalone \
  --assume-yes-for-downloads \
  --enable-plugin=anti-bloat \
  --include-package=app_core \
  --include-package=ui \
  --include-package=uvicorn \
  --include-package=fastapi \
  --include-package=starlette \
  --include-package=anyio \
  --include-package=jinja2 \
  --include-data-dir=ui/web/templates=ui/web/templates \
  --include-data-dir=ui/web/static=ui/web/static \
  --include-data-files=pyproject.toml=pyproject.toml \
  --output-dir="$NUITKA_OUT" \
  launcher.py >"$LOG_FILE" 2>&1
RC=$?
set -e
if [ $RC -ne 0 ]; then
  warn "Nuitka build failed (exit $RC). See log: $LOG_FILE"
else
  # Package
  if [ -d "$NUITKA_OUT/launcher.dist" ]; then
    if [ "$os_name" = "windows" ]; then
      ARTIFACT_PATH="$DIST_DIR/${ARTIFACT_BASENAME}.zip"
      if have 7z; then (cd "$NUITKA_OUT" && 7z a "$ARTIFACT_PATH" launcher.dist >/dev/null); else (cd "$NUITKA_OUT" && zip -r "$ARTIFACT_PATH" launcher.dist >/dev/null); fi
    else
      ARTIFACT_PATH="$DIST_DIR/${ARTIFACT_BASENAME}.tar.gz"
      tar -czf "$ARTIFACT_PATH" -C "$NUITKA_OUT" launcher.dist
    fi
  elif [ -f "$NUITKA_OUT/launcher.exe" ]; then
    ARTIFACT_PATH="$DIST_DIR/${ARTIFACT_BASENAME}.zip"
    if have 7z; then (cd "$NUITKA_OUT" && 7z a "$ARTIFACT_PATH" launcher.exe >/dev/null); else (cd "$NUITKA_OUT" && zip -j "$ARTIFACT_PATH" launcher.exe >/dev/null); fi
  fi
  if [ -n "$ARTIFACT_PATH" ] && [ -f "$ARTIFACT_PATH" ]; then
    if have sha256sum; then CHECKSUM="$(sha256sum "$ARTIFACT_PATH" | awk '{print $1}')";
    elif have shasum; then CHECKSUM="$(shasum -a 256 "$ARTIFACT_PATH" | awk '{print $1}')";
    elif have openssl; then CHECKSUM="$(openssl dgst -sha256 "$ARTIFACT_PATH" | awk '{print $NF}')";
    fi
    BUILD_STATUS=success
    log "Built: $ARTIFACT_PATH"
    [ -n "$CHECKSUM" ] && log "SHA256: $CHECKSUM"
  else
    warn "Build finished but packaged artifact was not found."
  fi
fi

TITLE="Community native build: ${os_name}-${arch_name} (${BUILD_STATUS})"
NOTES="Automated community native build attempt from install/build helper. Status=${BUILD_STATUS}. Please review sanitized report; attach/share artifact and checksum via a trusted channel if comfortable."
ISSUE_URL="$($PY scripts/community_native_build_report.py --title "$TITLE" --artifact "${ARTIFACT_PATH}" --checksum "$CHECKSUM" --build-status "$BUILD_STATUS" --app-ref "$APP_REF" --commit "$COMMIT" --build-cmd "$BUILD_CMD" --nuitka-version "$NUITKA_VER" --notes "$NOTES" --log-file "$LOG_FILE" --out "$REPORT_FILE" 2>/dev/null || true)"

log "Sanitized report: $REPORT_FILE"
if [ -n "$ISSUE_URL" ]; then
  log "Prefilled GitHub issue URL (review before submitting):"
  printf '%s\n' "$ISSUE_URL"
  # Best-effort open browser
  if have xdg-open; then xdg-open "$ISSUE_URL" >/dev/null 2>&1 || true;
  elif have open; then open "$ISSUE_URL" >/dev/null 2>&1 || true;
  elif have termux-open-url; then termux-open-url "$ISSUE_URL" >/dev/null 2>&1 || true;
  elif have cmd.exe; then cmd.exe /c start "" "$ISSUE_URL" >/dev/null 2>&1 || true; fi

  # Optional auto-file via gh if available and user consents
  if have gh; then
    printf 'GitHub CLI (gh) detected. Create an issue automatically now? [y/N]: '
    ans=""
    if [ -t 0 ]; then read -r ans; elif [ -r /dev/tty ]; then read -r ans < /dev/tty; fi
    case "$ans" in
      y|Y|yes|YES)
        gh issue create --repo "Michaelhehelmy/property-manager-tui" --title "$TITLE" --body-file "$REPORT_FILE" --label "community-build" || warn "gh issue create failed (auth/permissions?). You can still use the prefilled URL above."
        ;;
    esac
  else
    log "Tip: install and authenticate GitHub CLI (gh) to auto-file; otherwise submit via the prefilled URL and attach/share the artifact+checksum if you wish."
  fi
else
  warn "Could not generate prefilled issue URL. Share $REPORT_FILE manually (review for sensitive info first)."
fi

log "Done. Artifact (if built): ${ARTIFACT_PATH:-none}. No runtime data or secrets were included in the report."
exit 0
