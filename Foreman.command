#!/bin/bash
# Double-click this file in Finder to open the Foreman control panel in your browser.
# (No terminal commands needed.)
cd "$(dirname "$0")" || exit 1

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

echo "Starting Foreman… your browser will open in a moment."
exec "$PY" -m foreman web
