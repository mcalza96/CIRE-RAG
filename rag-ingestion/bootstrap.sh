#!/bin/bash

set -e

cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD=python
else
  echo "❌ Python 3 not found"
  exit 1
fi

echo "📦 Bootstrapping rag-ingestion..."

if [ ! -d "venv" ]; then
  $PYTHON_CMD -m venv venv
fi

source venv/bin/activate
python -m pip install -r requirements.txt

echo "✅ rag-ingestion ready"
