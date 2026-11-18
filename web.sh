#!/usr/bin/env bash
# Start the Clipper web app (paste links, live progress, clip library) and open it in the browser.
set -euo pipefail
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "Not set up yet: run ./setup.sh first."; exit 1; }
exec .venv/bin/python clip.py web "$@"
