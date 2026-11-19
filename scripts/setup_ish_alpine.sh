#!/bin/sh
# Compatibility wrapper (historical name). Delegates to the main cross-platform setup script.
# Kept for backward compatibility with existing docs/commands.
# Idempotent and data-preserving; SQLite web mode requires --workers 1.
set -eu
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" 2>/dev/null && pwd)"
exec sh "$SCRIPT_DIR/setup.sh" "$@"
