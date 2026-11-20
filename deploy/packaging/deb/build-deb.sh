#!/usr/bin/env bash
set -euo pipefail

# Build a simple .deb package scaffold for SPMS (web UI).
# Usage: ./deploy/packaging/deb/build-deb.sh [version] [arch]
# Example: ./deploy/packaging/deb/build-deb.sh 1.2.1 amd64
# Notes:
# - Copies application source into /opt/spms (excluding dev/runtime data).
# - Does NOT bundle a Python venv or install pip deps; create venv and install requirements on target.
# - Runtime data defaults to /var/lib/spms (PM_DATA_DIR) and service runs as user 'spms'.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PKG_ROOT="$ROOT_DIR/deploy/packaging/deb/spms"
BUILD_DIR="$ROOT_DIR/dist/deb"
VERSION="${1:-${SPMS_VERSION:-1.2.1}}"
ARCH="${2:-amd64}"
PKG_NAME="spms"
OUT_FILE="$BUILD_DIR/${PKG_NAME}_${VERSION}_${ARCH}.deb"

command -v dpkg-deb >/dev/null 2>&1 || { echo "dpkg-deb is required (install dpkg-dev)." >&2; exit 1; }

rm -rf "$PKG_ROOT/opt/spms" "$PKG_ROOT/var/lib/spms"
mkdir -p "$PKG_ROOT/opt/spms" "$PKG_ROOT/var/lib/spms" "$BUILD_DIR"

# Copy application payload (adjust excludes as needed)
tar -C "$ROOT_DIR" \
  --exclude='./.git' \
  --exclude='./.venv*' \
  --exclude='./venv*' \
  --exclude='./data' \
  --exclude='./exports' \
  --exclude='./invoices' \
  --exclude='./dist' \
  --exclude='./htmlcov' \
  --exclude='./.coverage*' \
  --exclude='./coverage.*' \
  --exclude='./**/__pycache__' \
  --exclude='./.pytest_cache' \
  --exclude='./property-manager-tui' \
  -cf - . | tar -C "$PKG_ROOT/opt/spms" -xf -

# Control file
cat > "$PKG_ROOT/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Maintainer: SPMS Maintainers <noreply@example.com>
Depends: python3 (>= 3.10), python3-venv, python3-pip
Recommends: ca-certificates
Description: SPMS (Super Property Management System) web application
 Local-first property management web app (FastAPI/HTMX, SQLite).
 Installs application files under /opt/spms and a systemd service.
 Create a virtualenv and install dependencies after install, then configure
 /etc/spms/spms.env (PM_WEB_SECRET_KEY, PM_DATA_DIR, host/port/TLS settings).
EOF

chmod 0755 "$PKG_ROOT/DEBIAN/postinst" "$PKG_ROOT/DEBIAN/prerm"
find "$PKG_ROOT" -type d -exec chmod 0755 {} \;

dpkg-deb --build --root-owner-group "$PKG_ROOT" "$OUT_FILE"
echo "Built: $OUT_FILE"