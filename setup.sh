#!/usr/bin/env bash
# One-time setup for the clipping pipeline (Linux x64).
# Usage: ./setup.sh            (add --demucs to also install vocal separation)
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# --- system tools --------------------------------------------------------------
say "Checking system tools"
command -v ffmpeg  >/dev/null || die "ffmpeg missing -> sudo apt install ffmpeg"
command -v ffprobe >/dev/null || die "ffprobe missing -> sudo apt install ffmpeg"
command -v node    >/dev/null || die "Node.js missing -> install Node 18+ (e.g. via nvm)"
command -v npm     >/dev/null || die "npm missing"
NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]')
[ "$NODE_MAJOR" -ge 18 ] || die "Node 18+ required (found $(node -v))"
command -v unzip >/dev/null || die "unzip missing -> sudo apt install unzip"
command -v curl  >/dev/null || die "curl missing -> sudo apt install curl"

# WhisperX / torch work best on Python 3.10-3.12
PY=""
for c in python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null; then PY="$c"; break; fi
done
[ -n "$PY" ] || die "Python 3.10+ missing"
echo "Using $($PY --version) ($PY)"
if ! "$PY" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)'; then
  echo "WARNING: WhisperX is best supported on Python 3.10-3.12. If pip fails, install python3.12 and rerun."
fi

# --- python venv -----------------------------------------------------------------
say "Creating Python venv (.venv) and installing WhisperX, yt-dlp, OpenCV (can take a few minutes)"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip wheel
.venv/bin/pip install -r requirements.txt
if [[ "${1:-}" == "--demucs" ]]; then
  .venv/bin/pip install demucs
fi
.venv/bin/python - <<'EOF'
import torch
print("CUDA GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none (CPU mode, use a smaller model in config.yaml)")
EOF

# --- TwitchDownloaderCLI (Twitch chat replay) --------------------------------------
say "Installing TwitchDownloaderCLI into ./bin"
mkdir -p bin
if [ ! -x bin/TwitchDownloaderCLI ]; then
  URL=$(curl -fsSL https://api.github.com/repos/lay295/TwitchDownloader/releases/latest \
        | .venv/bin/python -c 'import sys,json; a=json.load(sys.stdin)["assets"]; print(next(x["browser_download_url"] for x in a if "Linux-x64" in x["name"] and x["name"].endswith(".zip")))')
  echo "Downloading $URL"
  curl -fsSL "$URL" -o /tmp/tdcli.zip
  unzip -o -q /tmp/tdcli.zip -d bin
  chmod +x bin/TwitchDownloaderCLI
  rm -f /tmp/tdcli.zip
else
  echo "already installed"
fi

# --- Remotion -------------------------------------------------------------------
say "Installing Remotion (npm)"
(cd remotion && npm install --no-audit --no-fund)
(cd remotion && npx remotion browser ensure) || echo "Could not pre-download Chrome headless shell; Remotion will retry on first render."

mkdir -p jobs layouts
chmod +x clip.py clip.sh web.sh 2>/dev/null || true

say "Done!"
cat <<'EOF'
Next steps:
  1. Log in to Claude Code once:   claude    (then type /login)
  2. Make clips:                    ./web.sh   (web app)   or   ./clip.sh <video link>
  Your clips end up in jobs/<name>/out/

EOF
