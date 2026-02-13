#!/bin/bash

set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE_DIR="$BASE_DIR/.."
ROOT_DIR="$BASE_DIR/.."

if [ ! -f "$ENGINE_DIR/venv/bin/python" ]; then
  echo "❌ Missing virtualenv in $ENGINE_DIR/venv"
  echo "💡 Run ./bootstrap.sh first"
  exit 1
fi

export PYTHONPATH="$ROOT_DIR:$ENGINE_DIR:${PYTHONPATH:-}"
export RAG_ENGINE_URL="${RAG_ENGINE_URL:-http://localhost:8000}"

echo "🚀 Starting Q/A Orchestrator API on :8001"
"$ENGINE_DIR/venv/bin/python" -m uvicorn orchestrator.runtime.orchestrator_main:app --host 0.0.0.0 --port 8001 --no-access-log
