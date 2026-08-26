#!/usr/bin/env bash
# One command to run the growth bot WITH the live HUD dashboard.
#
#   ./live.sh --comments 300 --likes 150 --follows 75
#
# - Starts dashboard.py in the background (reuses it if one is already up)
# - Runs growth_bot.py in the foreground with any flags you pass
# - Ctrl+C stops the bot AND the dashboard it started
set -u
cd "$(dirname "$0")"

PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY=python

PORT="${DASHBOARD_PORT:-8787}"
mkdir -p logs

DASH_PID=""
if curl -sf -o /dev/null --max-time 1 "http://127.0.0.1:${PORT}/"; then
  echo "📊 Dashboard already running → http://127.0.0.1:${PORT}"
else
  "$PY" dashboard.py >> logs/dashboard.log 2>&1 &
  DASH_PID=$!
  sleep 0.6
  if kill -0 "$DASH_PID" 2>/dev/null; then
    echo "📊 Dashboard started → http://127.0.0.1:${PORT}"
  else
    echo "⚠️  Dashboard failed to start (see logs/dashboard.log) — continuing with bot only"
    DASH_PID=""
  fi
fi

cleanup() {
  # Only stop the dashboard if this script started it
  [ -n "$DASH_PID" ] && kill "$DASH_PID" 2>/dev/null
}
trap cleanup EXIT INT TERM

echo "🤖 Starting growth bot… (Ctrl+C stops bot + dashboard)"
"$PY" growth_bot.py "$@"
