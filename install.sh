#!/usr/bin/env bash
# Linux / macOS installer
set -e
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python)
[ -z "$PY" ] && { echo "Python 3.9+ is required. Install it first (e.g. sudo apt install python3 python3-venv)."; exit 1; }
"$PY" -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q
echo "Downloading the browser Omni controls (about 150 MB, one time)..."
.venv/bin/python -m playwright install chromium || echo "Browser download failed; browser tool will be unavailable until you run: .venv/bin/python -m playwright install chromium"
[ -f .env ] || cp .env.example .env
echo ""
echo "Installed. Now open .env, paste your API key, then run:  ./run.sh"
