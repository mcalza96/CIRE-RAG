# Getting Started (RAG Engine)

Guia rapida para probar ingestion y retrieval del engine.

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

## 2) Consultar retrieval

```bash
curl -s -X POST "http://localhost:8000/api/v1/knowledge/retrieve" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"<TENANT_ID>","query":"Que exige ISO sobre registro y evidencias?"}'
```

Si hay ambiguedad de alcance, la respuesta devuelve:

- `mode: AMBIGUOUS_SCOPE`
- `scope_candidates`
- `scope_message` (cuando aplica)

## 3) Consultar contratos retrieval v1

Chunks:

```bash
curl -s -X POST "http://localhost:8000/api/v1/retrieval/chunks" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"<TENANT_ID>","query":"Que exige ISO 9001 sobre registro y evidencias?","chunk_k":8,"fetch_k":40}'
```

Summaries:

```bash
curl -s -X POST "http://localhost:8000/api/v1/retrieval/summaries" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"<TENANT_ID>","query":"Que exige ISO 9001 sobre registro y evidencias?","summary_k":5}'
```

## 4) Health check

```bash
curl -s "http://localhost:8000/health"
```

Para observabilidad y troubleshooting, ver `runbooks/common-incidents.md`.
