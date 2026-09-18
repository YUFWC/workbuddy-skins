#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
AMBIENT_NODE=""

if [ -n "${WORKBUDDY_NODE:-}" ] && [ -x "$WORKBUDDY_NODE" ]; then
  AMBIENT_NODE="$WORKBUDDY_NODE"
fi

if [ -z "$AMBIENT_NODE" ]; then
  for candidate in "$HOME"/.workbuddy/binaries/node/versions/*/bin/node; do
    if [ -x "$candidate" ]; then AMBIENT_NODE="$candidate"; break; fi
  done
fi

if [ -z "$AMBIENT_NODE" ] && command -v node >/dev/null 2>&1; then
  AMBIENT_NODE="$(command -v node)"
fi

if [ -z "$AMBIENT_NODE" ]; then
  ARCHIVE="/Applications/WorkBuddy.app/Contents/Resources/vendor/node.tar.gz"
  RUNTIME_ROOT="$HOME/Library/Application Support/WorkBuddyAmbientSkin/runtime"
  RUNTIME_NODE="$RUNTIME_ROOT/node"
  if [ ! -x "$RUNTIME_NODE" ]; then
    [ -f "$ARCHIVE" ] || { echo '{"ok":false,"error":"Node.js 22+ was not found"}' >&2; exit 1; }
    /bin/mkdir -p "$RUNTIME_ROOT"
    TEMP_NODE="$RUNTIME_ROOT/node.$$.tmp"
    /usr/bin/tar -xOf "$ARCHIVE" '*/bin/node' > "$TEMP_NODE"
    /bin/chmod 700 "$TEMP_NODE"
    /bin/mv -f "$TEMP_NODE" "$RUNTIME_NODE"
  fi
  AMBIENT_NODE="$RUNTIME_NODE"
fi

MAJOR_VERSION="$($AMBIENT_NODE -p 'Number(process.versions.node.split(".")[0])')"
[ "$MAJOR_VERSION" -ge 22 ] || { echo '{"ok":false,"error":"Node.js 22 or newer is required"}' >&2; exit 1; }

exec "$AMBIENT_NODE" "$SCRIPT_DIR/ambient.mjs" "$@"
