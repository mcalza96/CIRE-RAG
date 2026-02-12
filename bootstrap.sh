#!/bin/bash

set -e

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🚀 Bootstrapping Python services..."

"$BASE_DIR/rag-ingestion/bootstrap.sh"

echo "✅ MAS Simple service bootstrapped"
