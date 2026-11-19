#!/bin/sh
# Best-effort: build a native app for the current OS/arch, package it, generate a
# sanitized report + prefilled GitHub issue URL, and optionally auto-file via gh.
# Privacy-first: no secrets/DB/runtime data included. Run with explicit user consent.
#
# Outputs (best-effort, by platform):
# - Windows: single-file .exe
# - Linux:   single-file binary (+ AppImage if tools available)
# - macOS:   .dmg containing a .app bundle
set -eu

ROOT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
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
ARTIFACT_BASENAME="spms-${os_name}-${arch_name}"
DIST_DIR="$ROOT_DIR/dist/community-native"
NUITKA_OUT="$ROOT_DIR/dist/nuitka-community"
LOG_FILE="$DIST_DIR/build.log"
REPORT_FILE="$DIST_DIR/community-native-build-report.md"
mkdir -p "$DIST_DIR"

APP_REF="${GIT_REF:-${TAG:-${COMMIT:-$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')}}}"
COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo '')"

log "Community native build (OS=$OS ARCH=$ARCH -> ${ARTIFACT_BASENAME})"
log "Targets: Windows=.exe, Linux=single binary (+AppImage best-effort), macOS=.dmg (app bundle)."
log "This may take a long time and requires a C compiler/toolchain. Build logs are kept locally; the report is sanitized."
log "Installing/ensuring build deps (nuitka, ordered-set, zstandard, imageio) in the active Python environment..."
"$PY" -m pip install --upgrade "pip>=24.3.1" >/dev/null 2>&1 || true
"$PY" -m pip install nuitka ordered-set zstandard imageio || { warn "Failed to install Nuitka/build deps. Ensure compiler/toolchain and Python headers are installed."; exit 1; }
NUITKA_VER="$($PY -m nuitka --version 2>/dev/null | head -n1 || echo '')"

BUILD_STATUS=failed
ARTIFACT_PATH=""
SECONDARY_ARTIFACT=""
CHECKSUM=""
BUILD_CMD=""

set +e
log "Building with Nuitka..."
rm -rf "$NUITKA_OUT" 2>/dev/null || true
mkdir -p "$NUITKA_OUT"

if [ "$os_name" = "macos" ]; then
  BUILD_CMD="$PY -m nuitka --standalone --macos-create-app-bundle --macos-app-name=SPMS --assume-yes-for-downloads --enable-plugin=anti-bloat --include-package=app_core --include-package=ui --include-package=uvicorn --include-package=fastapi --include-package=starlette --include-package=anyio --include-package=jinja2 --include-package=itsdangerous --include-package=python_multipart --include-package=multipart --include-package=pydantic --include-package=pydantic_core --include-data-dir=ui/web/templates=ui/web/templates --include-data-dir=ui/web/static=ui/web/static --include-data-files=pyproject.toml=pyproject.toml --output-dir=\"$NUITKA_OUT\" --output-filename=spms launcher.py"
  "$PY" -m nuitka \
    --standalone \
    --macos-create-app-bundle \
    --macos-app-name=SPMS \
    --assume-yes-for-downloads \
    --enable-plugin=anti-bloat \
    --include-package=app_core \
    --include-package=ui \
    --include-package=uvicorn \
    --include-package=fastapi \
    --include-package=starlette \
    --include-package=anyio \
    --include-package=jinja2 \
    --include-package=itsdangerous \
    --include-package=python_multipart \
    --include-package=multipart \
    --include-package=pydantic \
    --include-package=pydantic_core \
    --include-data-dir=ui/web/templates=ui/web/templates \
    --include-data-dir=ui/web/static=ui/web/static \
    --include-data-files=pyproject.toml=pyproject.toml \
    --output-dir="$NUITKA_OUT" \
    --output-filename=spms \
    launcher.py >"$LOG_FILE" 2>&1
  RC=$?
else
  BUILD_CMD="$PY -m nuitka --onefile --assume-yes-for-downloads --enable-plugin=anti-bloat --include-package=app_core --include-package=ui --include-package=uvicorn --include-package=fastapi --include-package=starlette --include-package=anyio --include-package=jinja2 --include-package=itsdangerous --include-package=python_multipart --include-package=multipart --include-package=pydantic --include-package=pydantic_core --include-data-dir=ui/web/templates=ui/web/templates --include-data-dir=ui/web/static=ui/web/static --include-data-files=pyproject.toml=pyproject.toml --output-dir=\"$NUITKA_OUT\" --output-filename=spms launcher.py"
  EXTRA_ONEFILE_ARGS=""
  if [ "$os_name" = "windows" ]; then
    EXTRA_ONEFILE_ARGS="--windows-console-mode=force"
  fi
  # shellcheck disable=SC2086
  "$PY" -m nuitka \
    --onefile \
    $EXTRA_ONEFILE_ARGS \
    --assume-yes-for-downloads \
    --enable-plugin=anti-bloat \
    --include-package=app_core \
    --include-package=ui \
    --include-package=uvicorn \
    --include-package=fastapi \
    --include-package=starlette \
    --include-package=anyio \
    --include-package=jinja2 \
    --include-package=itsdangerous \
    --include-package=python_multipart \
    --include-package=multipart \
    --include-package=pydantic \
    --include-package=pydantic_core \
    --include-data-dir=ui/web/templates=ui/web/templates \
    --include-data-dir=ui/web/static=ui/web/static \
    --include-data-files=pyproject.toml=pyproject.toml \
    --output-dir="$NUITKA_OUT" \
    --output-filename=spms \
    launcher.py >"$LOG_FILE" 2>&1
  RC=$?
fi
set -e

if [ $RC -ne 0 ]; then
  warn "Nuitka build failed (exit $RC). See log: $LOG_FILE"
else
  # Package per platform
  if [ "$os_name" = "windows" ]; then
    EXE_PATH=""
    for c in "$NUITKA_OUT/spms.exe" "$NUITKA_OUT/launcher.exe"; do
      if [ -f "$c" ]; then EXE_PATH="$c"; break; fi
    done
    if [ -z "$EXE_PATH" ]; then
      EXE_PATH="$(find "$NUITKA_OUT" -maxdepth 3 -type f -name '*.exe' 2>/dev/null | head -n1 || true)"
    fi
    if [ -n "$EXE_PATH" ] && [ -f "$EXE_PATH" ]; then
      ARTIFACT_PATH="$DIST_DIR/${ARTIFACT_BASENAME}.exe"
      cp "$EXE_PATH" "$ARTIFACT_PATH"
    else
      warn "Built output .exe not found in $NUITKA_OUT"
    fi
  elif [ "$os_name" = "macos" ]; then
    APP_PATH=""
    for c in "$NUITKA_OUT/SPMS.app" "$NUITKA_OUT/spms.app" "$NUITKA_OUT/launcher.app"; do
      if [ -d "$c" ]; then APP_PATH="$c"; break; fi
    done
    if [ -z "$APP_PATH" ]; then
      APP_PATH="$(find "$NUITKA_OUT" -maxdepth 3 -type d -name '*.app' 2>/dev/null | head -n1 || true)"
    fi
    if [ -n "$APP_PATH" ] && [ -d "$APP_PATH" ] && have hdiutil; then
      ARTIFACT_PATH="$DIST_DIR/${ARTIFACT_BASENAME}.dmg"
      rm -f "$ARTIFACT_PATH" 2>/dev/null || true
      hdiutil create -volname "SPMS" -srcfolder "$APP_PATH" -ov -format UDZO "$ARTIFACT_PATH" >/dev/null 2>&1 || true
      if [ ! -f "$ARTIFACT_PATH" ]; then
        warn "DMG packaging failed; falling back to tar.gz app bundle"
        ARTIFACT_PATH="$DIST_DIR/${ARTIFACT_BASENAME}.tar.gz"
        tar -czf "$ARTIFACT_PATH" -C "$(dirname "$APP_PATH")" "$(basename "$APP_PATH")"
      fi
    elif [ -n "$APP_PATH" ] && [ -d "$APP_PATH" ]; then
      warn "hdiutil not available; packaging .app bundle as tar.gz"
      ARTIFACT_PATH="$DIST_DIR/${ARTIFACT_BASENAME}.tar.gz"
      tar -czf "$ARTIFACT_PATH" -C "$(dirname "$APP_PATH")" "$(basename "$APP_PATH")"
    else
      warn "Built .app bundle not found in $NUITKA_OUT"
    fi
  else
    # Linux/other unix: onefile binary (+ best-effort AppImage)
    BIN_PATH=""
    for c in "$NUITKA_OUT/spms.bin" "$NUITKA_OUT/spms" "$NUITKA_OUT/launcher.bin" "$NUITKA_OUT/launcher"; do
      if [ -f "$c" ]; then BIN_PATH="$c"; break; fi
    done
    if [ -z "$BIN_PATH" ]; then
      BIN_PATH="$(find "$NUITKA_OUT" -maxdepth 3 -type f \( -name 'spms*' -o -name 'launcher*' \) ! -name '*.py' 2>/dev/null | head -n1 || true)"
    fi
    if [ -n "$BIN_PATH" ] && [ -f "$BIN_PATH" ]; then
      ARTIFACT_PATH="$DIST_DIR/${ARTIFACT_BASENAME}"
      cp "$BIN_PATH" "$ARTIFACT_PATH"
      chmod +x "$ARTIFACT_PATH" 2>/dev/null || true

      # Best-effort AppImage (if appimagetool is available)
      APPIMAGE_OUT="$DIST_DIR/${ARTIFACT_BASENAME}.AppImage"
      WORKDIR_APP="$DIST_DIR/_appimage_tmp"
      rm -rf "$WORKDIR_APP" 2>/dev/null || true
      mkdir -p "$WORKDIR_APP/AppDir/usr/bin" "$WORKDIR_APP/AppDir/usr/share/applications" "$WORKDIR_APP/AppDir/usr/share/icons/hicolor/256x256/apps" 2>/dev/null || true
      cp "$BIN_PATH" "$WORKDIR_APP/AppDir/usr/bin/spms"
      chmod +x "$WORKDIR_APP/AppDir/usr/bin/spms" 2>/dev/null || true
      if [ -f "$ROOT_DIR/ui/web/static/icons/icon-512.png" ]; then
        cp "$ROOT_DIR/ui/web/static/icons/icon-512.png" "$WORKDIR_APP/AppDir/spms.png" 2>/dev/null || true
        cp "$ROOT_DIR/ui/web/static/icons/icon-512.png" "$WORKDIR_APP/AppDir/usr/share/icons/hicolor/256x256/apps/spms.png" 2>/dev/null || true
      fi
      cat > "$WORKDIR_APP/AppDir/spms.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=SPMS
Comment=Super Property Management System
Exec=spms
Icon=spms
Terminal=true
Categories=Office;Finance;
DESKTOP
      cp "$WORKDIR_APP/AppDir/spms.desktop" "$WORKDIR_APP/AppDir/usr/share/applications/spms.desktop" 2>/dev/null || true
      cat > "$WORKDIR_APP/AppDir/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/spms" "$@"
APPRUN
      chmod +x "$WORKDIR_APP/AppDir/AppRun" 2>/dev/null || true
      APPIMAGE_TOOL=""
      if have appimagetool; then APPIMAGE_TOOL="$(command -v appimagetool)"; fi
      if [ -n "$APPIMAGE_TOOL" ]; then
        if ARCH="$(uname -m)" "$APPIMAGE_TOOL" "$WORKDIR_APP/AppDir" "$APPIMAGE_OUT" >/dev/null 2>&1; then
          SECONDARY_ARTIFACT="$APPIMAGE_OUT"
          log "AppImage: $APPIMAGE_OUT"
        else
          warn "AppImage packaging failed (binary artifact still available)."
        fi
      else
        warn "appimagetool not found; skipping AppImage (single binary artifact still available)."
      fi
      rm -rf "$WORKDIR_APP" 2>/dev/null || true
    else
      warn "Built binary not found in $NUITKA_OUT"
    fi
  fi

  if [ -n "$ARTIFACT_PATH" ] && [ -f "$ARTIFACT_PATH" ]; then
    if have sha256sum; then CHECKSUM="$(sha256sum "$ARTIFACT_PATH" | awk '{print $1}')";
    elif have shasum; then CHECKSUM="$(shasum -a 256 "$ARTIFACT_PATH" | awk '{print $1}')";
    elif have openssl; then CHECKSUM="$(openssl dgst -sha256 "$ARTIFACT_PATH" | awk '{print $NF}')";
    fi
    BUILD_STATUS=success
    log "Built: $ARTIFACT_PATH"
    [ -n "$CHECKSUM" ] && log "SHA256: $CHECKSUM"
    if [ -n "$SECONDARY_ARTIFACT" ] && [ -f "$SECONDARY_ARTIFACT" ]; then
      log "Secondary artifact: $SECONDARY_ARTIFACT"
    fi
  else
    warn "Build finished but packaged artifact was not found."
  fi
fi

TITLE="Community native build: ${os_name}-${arch_name} (${BUILD_STATUS})"
NOTES="Automated community native build attempt. Status=${BUILD_STATUS}. Outputs target single-file distributions where supported (Windows .exe, Linux binary/AppImage best-effort, macOS .dmg). Review sanitized report; attach/share artifact and checksum via a trusted channel if comfortable."
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
        gh issue create --repo "Michaelhehelmy/spms" --title "$TITLE" --body-file "$REPORT_FILE" --label "community-build" || warn "gh issue create failed (auth/permissions?). You can still use the prefilled URL above."
        ;;
    esac
  else
    log "Tip: install and authenticate GitHub CLI (gh) to auto-file; otherwise submit via the prefilled URL and attach/share the artifact+checksum if you wish."
  fi
else
  warn "Could not generate prefilled issue URL. Share $REPORT_FILE manually (review for sensitive info first)."
fi

log "Done. Artifact (if built): ${ARTIFACT_PATH:-none}. Secondary: ${SECONDARY_ARTIFACT:-none}. No runtime data or secrets were included in the report."
exit 0
