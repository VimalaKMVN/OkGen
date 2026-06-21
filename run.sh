#!/usr/bin/env bash
# OkGen launcher for macOS / Linux. Run ./run.sh to set up (first run) and start.
set -e
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating environment (first run only)..."
  "$PY" -m venv .venv || {
    echo
    echo "ERROR: Could not create the environment. Is Python 3.9+ installed?"
    echo "Install it from https://www.python.org/downloads/ (or your package manager)."
    exit 1
  }
  echo "Installing OkGen..."
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -e .
fi

echo "Starting OkGen. Opening http://127.0.0.1:8000"
echo "Press Ctrl+C to stop."
# Open the browser shortly after the server starts.
( sleep 2; (open http://127.0.0.1:8000 2>/dev/null || xdg-open http://127.0.0.1:8000 2>/dev/null || true) ) &
exec .venv/bin/python -m okgen.cli serve
