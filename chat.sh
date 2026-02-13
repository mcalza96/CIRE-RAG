#!/bin/bash
# chat.sh — Launch Q/A Orchestrator chat CLI (HTTP split mode)

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAG_URL="${RAG_URL:-${RAG_SERVICE_URL:-http://localhost:8000}}"
ORCH_URL="${ORCH_URL:-${ORCHESTRATOR_URL:-http://localhost:8001}}"
REGISTRY_FILE="$BASE_DIR/.rag_tenants.json"

check_service_health() {
    local service_name="$1"
    local health_url="$2"
    if ! curl -fsS --max-time 2 "$health_url" >/dev/null 2>&1; then
        echo "❌ $service_name no está disponible en $health_url"
        return 1
    fi
    return 0
}

choose_tenant_and_collection() {
    local docs_json
    if ! docs_json=$(curl -fsS --max-time 4 "$RAG_URL/api/v1/ingestion/documents?limit=500" 2>/dev/null); then
        echo "❌ No pude leer documentos desde $RAG_URL/api/v1/ingestion/documents"
        echo "💡 Verifica que la API RAG esté activa."
        return 1
    fi

    local tenant_payload
    tenant_payload=$(DOCS_JSON="$docs_json" REGISTRY_FILE="$REGISTRY_FILE" python3 - <<'PY'
import json
import os
from pathlib import Path

raw = os.environ.get("DOCS_JSON", "[]")
try:
    docs = json.loads(raw)
except Exception:
    docs = []

registry = {}
registry_path = os.environ.get("REGISTRY_FILE", "")
if registry_path:
    p = Path(registry_path)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for t in data.get("tenants", []):
                tid = str(t.get("id", "")).strip()
                tname = str(t.get("name", "")).strip()
                if tid:
                    registry[tid] = tname or tid
        except Exception:
            pass

tenants = {}
for d in docs:
    meta = d.get("metadata") or {}
    tenant_id = d.get("institution_id") or meta.get("institution_id") or meta.get("tenant_id")
    if not tenant_id:
        continue
    tenant_id = str(tenant_id)

    entry = tenants.setdefault(tenant_id, {"name": registry.get(tenant_id) or meta.get("tenant_name") or tenant_id, "collections": {}})
    collections = entry["collections"]
    collection_id = d.get("collection_id") or meta.get("collection_id") or meta.get("folder_id") or "__all__"
    collection_name = d.get("collection_name") or meta.get("collection_name") or meta.get("folder_name") or str(collection_id)
    collections[str(collection_id)] = str(collection_name)

for tenant_id in sorted(tenants.keys()):
    tname = tenants[tenant_id].get("name") or tenant_id
    print(f"TENANT|{tenant_id}|{tname}")
    for cid, cname in sorted(tenants[tenant_id]["collections"].items(), key=lambda x: x[1].lower()):
        print(f"COLLECTION|{tenant_id}|{cid}|{cname}")
PY
)

    local tenants=()
    local tenant_names=()
    local line
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        if [[ "$line" == TENANT\|* ]]; then
            local payload="${line#TENANT|}"
            tenants+=("${payload%%|*}")
            tenant_names+=("${payload#*|}")
        fi
    done <<< "$tenant_payload"

    if (( ${#tenants[@]} == 0 )); then
        echo "❌ No hay tenants institucionales con documentos disponibles."
        echo "💡 Ingresa primero documentos con ./ing.sh"
        return 1
    fi

    echo ""
    echo "🏢 Selecciona tenant:" 
    local i
    for ((i=0; i<${#tenants[@]}; i++)); do
        printf "[%d] %s (%s)\n" "$((i+1))" "${tenant_names[$i]}" "${tenants[$i]}"
    done

    local tenant_opt
    read -r -p "📝 Tenant [1]: " tenant_opt
    [[ -z "$tenant_opt" ]] && tenant_opt=1
    if ! [[ "$tenant_opt" =~ ^[0-9]+$ ]] || (( tenant_opt < 1 || tenant_opt > ${#tenants[@]} )); then
        echo "❌ Opción de tenant inválida"
        return 1
    fi
    CHAT_TENANT_ID="${tenants[$((tenant_opt-1))]}"

    local collection_rows=""
    local collections_json
    if collections_json=$(curl -fsS --max-time 4 "$RAG_URL/api/v1/ingestion/collections?tenant_id=$CHAT_TENANT_ID" 2>/dev/null); then
        collection_rows=$(COLLECTIONS_JSON="$collections_json" python3 - <<'PY'
import json
import os

raw = os.environ.get("COLLECTIONS_JSON", "[]")
try:
    collections = json.loads(raw)
except Exception:
    collections = []

for item in collections:
    if not isinstance(item, dict):
        continue
    cid = str(item.get("id") or item.get("collection_key") or "").strip()
    ckey = str(item.get("collection_key") or "").strip()
    cname = str(item.get("name") or ckey or cid).strip()
    if not cid:
        continue
    if ckey:
        print(f"{cid}|{cname} ({ckey})")
    else:
        print(f"{cid}|{cname}")
PY
)
    fi

    if [[ -z "$collection_rows" ]]; then
        collection_rows=$(printf "%s\n" "$tenant_payload" | python3 - "$CHAT_TENANT_ID" <<'PY'
import sys

tenant = sys.argv[1]
for raw in sys.stdin:
    line = raw.strip()
    if not line.startswith("COLLECTION|"):
        continue
    _, t, cid, cname = line.split("|", 3)
    if t == tenant:
        print(f"{cid}|{cname}")
PY
)
    fi

    local collection_ids=()
    local collection_names=()
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        collection_ids+=("${line%%|*}")
        collection_names+=("${line#*|}")
    done <<< "$collection_rows"

    echo ""
    echo "📁 Selecciona carpeta/colección de búsqueda:"
    echo "[1] Todo el tenant"
    for ((i=0; i<${#collection_ids[@]}; i++)); do
        printf "[%d] %s\n" "$((i+2))" "${collection_names[$i]}"
    done

    local collection_opt
    read -r -p "📝 Colección [1]: " collection_opt
    [[ -z "$collection_opt" ]] && collection_opt=1
    if ! [[ "$collection_opt" =~ ^[0-9]+$ ]] || (( collection_opt < 1 || collection_opt > ${#collection_ids[@]} + 1 )); then
        echo "❌ Opción de colección inválida"
        return 1
    fi

    if (( collection_opt == 1 )); then
        CHAT_COLLECTION_ID=""
        CHAT_COLLECTION_NAME=""
    else
        local idx=$((collection_opt-2))
        CHAT_COLLECTION_ID="${collection_ids[$idx]}"
        CHAT_COLLECTION_NAME="${collection_names[$idx]}"
        [[ "$CHAT_COLLECTION_ID" == "__all__" ]] && CHAT_COLLECTION_ID=""
    fi

    return 0
}

CLI_DIR="$BASE_DIR/orchestrator"
SCRIPT="chat_cli.py"
ENGINE_DIR="$BASE_DIR"

if ! check_service_health "RAG API" "$RAG_URL/health"; then
    echo "💡 Ejecuta primero ./dev.sh o ./stack.sh up"
    exit 1
fi

if ! check_service_health "Orchestrator API" "$ORCH_URL/health"; then
    echo "❌ Orchestrator API no está disponible en $ORCH_URL/health"
    echo "💡 Ejecuta ./stack.sh up y verifica logs con ./stack.sh logs orchestrator-api"
    exit 1
fi

if ! choose_tenant_and_collection; then
    exit 1
fi

CLI_ARGS=(--tenant-id "$CHAT_TENANT_ID")
if [ -n "${CHAT_COLLECTION_ID:-}" ]; then
    CLI_ARGS+=(--collection-id "$CHAT_COLLECTION_ID" --collection-name "$CHAT_COLLECTION_NAME")
fi
CLI_ARGS+=(--orchestrator-url "$ORCH_URL")

VENV_PYTHON="$ENGINE_DIR/venv/bin/python3"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌ Error: No se encontró el entorno virtual en $ENGINE_DIR/venv"
    exit 1
fi

export PYTHONPATH="$PYTHONPATH:$CLI_DIR:$ENGINE_DIR"
cd "$CLI_DIR" || exit 1
"$VENV_PYTHON" "$SCRIPT" "${CLI_ARGS[@]}" "$@"
