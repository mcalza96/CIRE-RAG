#!/bin/bash

set -e

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_SCRIPT="$BASE_DIR/tools/ingestion-client/ing.sh"

echo "ℹ️  'ing.sh' en raiz es un wrapper temporal."
echo "ℹ️  La implementacion vive en tools/ingestion-client/ing.sh"

exec "$TARGET_SCRIPT" "$@"
