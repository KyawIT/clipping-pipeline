#!/usr/bin/env bash
# One command: prepare locally (free), then open Claude Code interactively to pick + render clips.
# Usage: ./clip.sh <twitch/youtube URL or local file> [--mode stream|podcast]
set -euo pipefail
cd "$(dirname "$0")"
[ $# -ge 1 ] || { echo "usage: ./clip.sh <url-or-file> [--mode stream|podcast]"; exit 1; }

if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "ANTHROPIC_API_KEY is set: Claude Code would bill the API instead of your Pro plan."
  echo "Run 'unset ANTHROPIC_API_KEY' and try again."
  exit 1
fi

LOG=$(mktemp)
.venv/bin/python clip.py prepare "$@" | tee "$LOG"
JOB=$(grep -oP 'READY\s+job=\K\S+' "$LOG" | tail -1)
rm -f "$LOG"
[ -n "$JOB" ] || { echo "prepare did not finish"; exit 1; }

# Interactive session (uses your Pro plan limits, no API key needed)
exec claude "/clip $JOB"
