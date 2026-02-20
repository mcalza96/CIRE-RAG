#!/bin/bash

set -e

# Ensure we are in the project root (one level up from scripts/)
cd "$(dirname "$0")/.."

if command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD=python
else
  echo "❌ Python 3 not found"
  exit 1
fi

echo "📦 Bootstrapping rag-engine..."

if [ ! -d "venv" ]; then
  $PYTHON_CMD -m venv venv
fi

source venv/bin/activate
./venv/bin/python -m pip install -r requirements-core.txt

INSTALL_LOCAL_EMBEDDINGS="${INSTALL_LOCAL_EMBEDDINGS:-auto}"
if [ "$INSTALL_LOCAL_EMBEDDINGS" = "auto" ]; then
  if [ "${JINA_MODE:-CLOUD}" = "LOCAL" ]; then
    INSTALL_LOCAL_EMBEDDINGS="1"
  else
    INSTALL_LOCAL_EMBEDDINGS="0"
  fi
fi

if [ "$INSTALL_LOCAL_EMBEDDINGS" = "1" ] || [ "$INSTALL_LOCAL_EMBEDDINGS" = "true" ]; then
  echo "📦 Installing local embedding runtime (torch/transformers)..."
  ./venv/bin/python -m pip install -r requirements-local.txt
else
  echo "ℹ️ Skipping local embedding runtime. Set INSTALL_LOCAL_EMBEDDINGS=1 to install it."
fi

echo "✅ rag-engine ready"
