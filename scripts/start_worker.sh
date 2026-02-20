#!/bin/bash

set -e

cd "$(dirname "$0")"

if command -v python3 &>/dev/null; then
    PYTHON_CMD=python3
elif command -v python &>/dev/null; then
    PYTHON_CMD=python
else
    echo "❌ Error: Python not found. Please install Python 3."
    exit 1
fi

echo "🔥 Starting RAG Worker using $PYTHON_CMD..."

if [ -f "venv/bin/python" ]; then
    exec "venv/bin/python" -u run_worker.py
fi

exec "$PYTHON_CMD" -u run_worker.py
