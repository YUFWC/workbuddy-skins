#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
ENTRY="$SCRIPT_DIR/workbuddy-ambient.sh"
APP="/Applications/WorkBuddy.app"
PORT="9347"
THEME="paper-aurora"
LOG_ROOT="$HOME/Library/Application Support/WorkBuddyAmbientSkin"
LOG_FILE="$LOG_ROOT/apply.log"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --theme)
      [ "$#" -ge 2 ] || { echo "--theme requires a value" >&2; exit 2; }
      THEME="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$LOG_ROOT"
exec > >(tee -a "$LOG_FILE") 2>&1

on_error() {
  code=$?
  echo
  echo "Apply failed (exit $code). Log: $LOG_FILE"
  if [ -t 0 ]; then read -r -p "Press Enter to close..." _; fi
  exit "$code"
}
trap on_error ERR

echo
echo "WorkBuddy Ambient Skin"
echo "Theme: $THEME"
echo "Port:  $PORT"
echo

[ -d "$APP" ] || { echo "WorkBuddy.app was not found in /Applications" >&2; exit 1; }
"$ENTRY" doctor >/dev/null
"$ENTRY" list | /usr/bin/grep -q "\"id\": \"$THEME\"" || { echo "Theme not found: $THEME" >&2; exit 1; }

echo "[1/4] Closing WorkBuddy..."
/usr/bin/osascript -e 'tell application "WorkBuddy" to quit' >/dev/null 2>&1 || true
for _ in $(seq 1 10); do
  /usr/bin/pgrep -f '/Applications/WorkBuddy\.app/Contents/' >/dev/null 2>&1 || break
  sleep 1
done
/usr/bin/pkill -9 -f '/Applications/WorkBuddy\.app/Contents/' >/dev/null 2>&1 || true
for _ in $(seq 1 30); do
  /usr/bin/pgrep -f '/Applications/WorkBuddy\.app/Contents/' >/dev/null 2>&1 || break
  sleep 0.1
done
if /usr/bin/pgrep -f '/Applications/WorkBuddy\.app/Contents/' >/dev/null 2>&1; then
  echo "WorkBuddy processes did not stop" >&2
  exit 1
fi
sleep 2

echo "[2/4] Starting WorkBuddy with CDP..."
NO_PROXY="localhost,127.0.0.1,::1" no_proxy="localhost,127.0.0.1,::1" nohup "$APP/Contents/MacOS/Electron" --remote-debugging-port="$PORT" >> "$LOG_ROOT/workbuddy-launch.log" 2>&1 &
disown

echo "[3/4] Waiting for CDP and renderer..."
cdp_ready=false
for _ in $(seq 1 30); do
  if /usr/bin/curl --noproxy '*' -fsS --max-time 1 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
    cdp_ready=true; break
  fi
  sleep 1
done
$cdp_ready || { echo "CDP did not become ready on port $PORT" >&2; exit 1; }

renderer_ready=false
for _ in $(seq 1 30); do
  if /usr/bin/curl --noproxy '*' -fsS --max-time 1 "http://127.0.0.1:$PORT/json/list" 2>/dev/null | /usr/bin/grep -q 'renderer/index.html'; then
    renderer_ready=true; break
  fi
  sleep 1
done
$renderer_ready || { echo "WorkBuddy renderer did not become ready" >&2; exit 1; }

echo "[4/4] Injecting and verifying theme..."
"$ENTRY" inject --theme "$THEME" >/dev/null
"$ENTRY" verify

trap - ERR
echo
echo "Skin applied successfully: $THEME"
