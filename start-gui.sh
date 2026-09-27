#!/usr/bin/env bash
# start-gui.sh — inicia a GUI do olx-deals em 127.0.0.1:8765
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${OLX_GUI_PORT:-8765}"
HOST="${OLX_GUI_HOST:-127.0.0.1}"
exec "$DIR/.venv/bin/python" "$DIR/app.py" --host "$HOST" --port "$PORT" "$@"
