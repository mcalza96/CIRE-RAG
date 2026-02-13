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

# Start Protocol API (FastAPI) on port 8000
echo "🌍 Serving API on http://0.0.0.0:8000"
RELOAD_FLAG="${UVICORN_RELOAD:-false}"
ACCESS_LOG_FLAG="${UVICORN_ACCESS_LOG:-false}"
if [ "$RELOAD_FLAG" = "true" ]; then
    if [ "$ACCESS_LOG_FLAG" = "true" ]; then
        $UVICORN_CMD app.main:app --host 0.0.0.0 --port 8000 --reload --access-log
    else
        $UVICORN_CMD app.main:app --host 0.0.0.0 --port 8000 --reload --no-access-log
    fi
else
    if [ "$ACCESS_LOG_FLAG" = "true" ]; then
        $UVICORN_CMD app.main:app --host 0.0.0.0 --port 8000 --access-log
    else
        $UVICORN_CMD app.main:app --host 0.0.0.0 --port 8000 --no-access-log
    fi
fi
