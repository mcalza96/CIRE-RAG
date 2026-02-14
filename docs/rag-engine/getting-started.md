# Getting Started (RAG Engine)

Guia rapida para probar el contrato recomendado (`/api/v1/chat`, `/api/v1/documents`, `/api/v1/management`).

## 1) Levantar API + worker

```bash
cd .
./start_api.sh
```

En otra terminal:

```bash
cd .
venv/bin/python run_worker.py
```

## 2) Health check

```bash
curl -s "http://localhost:8000/health"
```

## 3) Preguntar via contrato recomendado

```bash
curl -s -X POST "http://localhost:8000/api/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: <TENANT_ID>" \
  -d '{"tenant_id":"<TENANT_ID>","message":"Que exige ISO sobre registro y evidencias?","max_context_chunks":8}'
```

Si hay ambiguedad de alcance, la respuesta devuelve:

- `requires_scope_clarification: true`
- `scope_warnings`

## 4) Diagnostico retrieval (debug)

Chunks:

```bash
curl -s -X POST "http://localhost:8000/api/v1/debug/retrieval/chunks" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: <TENANT_ID>" \
  -d '{"tenant_id":"<TENANT_ID>","query":"Que exige ISO 9001 sobre registro y evidencias?","chunk_k":8,"fetch_k":40}'
```

Summaries:

```bash
curl -s -X POST "http://localhost:8000/api/v1/debug/retrieval/summaries" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: <TENANT_ID>" \
  -d '{"tenant_id":"<TENANT_ID>","query":"Que exige ISO 9001 sobre registro y evidencias?","summary_k":5}'
```

## 5) Retrieval oficial (avanzado)

Hybrid:

```bash
curl -s -X POST "http://localhost:8000/api/v1/retrieval/hybrid" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: <TENANT_ID>" \
  -d '{"tenant_id":"<TENANT_ID>","query":"Que exige ISO 9001 7.5.3?","k":8,"fetch_k":40}'
```

Validate scope:

```bash
curl -s -X POST "http://localhost:8000/api/v1/retrieval/validate-scope" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: <TENANT_ID>" \
  -d '{"tenant_id":"<TENANT_ID>","query":"Que exige ISO 9001 7.5.3?"}'
```

## 6) Endpoints legacy retirados

No usar:

- `POST /api/v1/knowledge/retrieve`
- `POST /api/v1/retrieval/chunks`
- `POST /api/v1/retrieval/summaries`

Para observabilidad y troubleshooting, ver `runbooks/common-incidents.md`.
