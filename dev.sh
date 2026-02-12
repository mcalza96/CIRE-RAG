#!/bin/bash

set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cleanup() {
  echo ""
  echo "🛑 Deteniendo stack de desarrollo..."
  "$BASE_DIR/stack.sh" down
}

trap cleanup INT TERM

echo "🚀 Levantando stack de desarrollo (API + workers)..."
"$BASE_DIR/stack.sh" up

echo ""
echo "📜 Logs en vivo (Ctrl+C para detener todo):"
echo "   - $BASE_DIR/.logs/rag-api.log"
echo "   - $BASE_DIR/.logs/rag-worker.log"
echo "   - $BASE_DIR/.logs/community-worker.log"
echo "   - $BASE_DIR/.logs/audit-api.log"

touch "$BASE_DIR/.logs/rag-api.log" "$BASE_DIR/.logs/rag-worker.log" "$BASE_DIR/.logs/community-worker.log" "$BASE_DIR/.logs/audit-api.log"
tail -f "$BASE_DIR/.logs/rag-api.log" "$BASE_DIR/.logs/rag-worker.log" "$BASE_DIR/.logs/community-worker.log" "$BASE_DIR/.logs/audit-api.log"
