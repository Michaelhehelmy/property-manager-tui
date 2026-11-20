#!/usr/bin/env bash
set -euo pipefail

# Build an RPM package scaffold for SPMS.
# Usage: ./deploy/packaging/rpm/build-rpm.sh [version]
# Requires: rpm-build, tar

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SPEC_SRC="$ROOT_DIR/deploy/packaging/rpm/SPECS/spms.spec"
VERSION="${1:-${SPMS_VERSION:-1.2.1}}"
NAME="spms"
TOPDIR="${RPM_TOPDIR:-$ROOT_DIR/dist/rpm/rpmbuild}"
TARBALL_DIR="$TOPDIR/SOURCES"
SPECDIR="$TOPDIR/SPECS"
OUT_DIR="$ROOT_DIR/dist/rpm"

command -v rpmbuild >/dev/null 2>&1 || { echo "rpmbuild is required (install rpm-build)." >&2; exit 1; }

mkdir -p "$TARBALL_DIR" "$SPECDIR" "$TOPDIR/BUILD" "$TOPDIR/RPMS" "$TOPDIR/SRPMS" "$OUT_DIR"

# Create source tarball expected by spec (%setup -q -n spms-<version>)
TMP_STAGE="$(mktemp -d)"
trap 'rm -rf "$TMP_STAGE"' EXIT
SRC_STAGE="$TMP_STAGE/${NAME}-${VERSION}"
mkdir -p "$SRC_STAGE"
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
  -cf - . | tar -C "$SRC_STAGE" -xf -
tar -C "$TMP_STAGE" -czf "$TARBALL_DIR/${NAME}-${VERSION}.tar.gz" "${NAME}-${VERSION}"

# Use provided spec but allow overriding Version via --define
cp "$SPEC_SRC" "$SPECDIR/spms.spec"

rpmbuild --define "_topdir $TOPDIR" --define "version $VERSION" -ba "$SPECDIR/spms.spec"

find "$TOPDIR/RPMS" "$TOPDIR/SRPMS" -type f -name '*.rpm' -exec cp -f {} "$OUT_DIR/" \;
echo "RPMs copied to: $OUT_DIR"