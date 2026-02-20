#!/bin/bash

# Ensure we are in the script's directory
cd "$(dirname "$0")"

# Check for python3
if command -v python3 &>/dev/null; then
    PYTHON_CMD=python3
elif command -v python &>/dev/null; then
    PYTHON_CMD=python
else
    echo "❌ Error: Python not found. Please install Python 3."
    exit 1
fi

echo "🚀 Starting RAG API Service using $PYTHON_CMD..."

if [ -f "venv/bin/python" ]; then
    UVICORN_CMD="venv/bin/python -m uvicorn"
else
    UVICORN_CMD="$PYTHON_CMD -m uvicorn"
fi

PORT_VALUE="${PORT:-8000}"
echo "🌍 Serving API on http://0.0.0.0:${PORT_VALUE}"

# Production-safe defaults (reload disabled unless explicitly enabled)
RELOAD_FLAG="${UVICORN_RELOAD:-false}"
ACCESS_LOG_FLAG="${UVICORN_ACCESS_LOG:-false}"
WORKERS_FLAG="${UVICORN_WORKERS:-1}"
if [ "$RELOAD_FLAG" = "true" ]; then
    if [ "$ACCESS_LOG_FLAG" = "true" ]; then
        $UVICORN_CMD app.main:app --host 0.0.0.0 --port "$PORT_VALUE" --reload --access-log
    else
        $UVICORN_CMD app.main:app --host 0.0.0.0 --port "$PORT_VALUE" --reload --no-access-log
    fi
else
    if [ "$ACCESS_LOG_FLAG" = "true" ]; then
        $UVICORN_CMD app.main:app --host 0.0.0.0 --port "$PORT_VALUE" --workers "$WORKERS_FLAG" --access-log
    else
        $UVICORN_CMD app.main:app --host 0.0.0.0 --port "$PORT_VALUE" --workers "$WORKERS_FLAG" --no-access-log
    fi
fi
