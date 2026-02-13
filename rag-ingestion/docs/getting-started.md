# Getting Started (HITL Q/A)

Guia rapida para probar el flujo Human-in-the-Loop (HITL) en preguntas/respuestas.

## 1) Levantar API + worker

```bash
cd rag-ingestion
./start_api.sh
```

En otra terminal:

```bash
cd rag-ingestion
venv/bin/python run_worker.py
```

## 2) Enviar una pregunta potencialmente ambigua

```bash
curl -s -X POST "http://localhost:8000/api/v1/knowledge/retrieve" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"<TENANT_ID>","query":"Que exige ISO sobre registro y evidencias?"}'
```

Si hay ambiguedad de alcance, la respuesta devuelve:

- `mode: AMBIGUOUS_SCOPE`
- `scope_candidates`
- `scope_message`

## 3) Aclarar alcance y pedir respuesta final

Usa la aclaracion directamente en la consulta siguiente:

```bash
curl -s -X POST "http://localhost:8000/api/v1/knowledge/answer" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"<TENANT_ID>","query":"Que exige ISO 9001 sobre registro y evidencias?"}'
```

Si el scope queda valido, recibes:

- `answer`
- `citations`
- `mode`

## 4) Caso bloqueado por mismatch de ambito

Cuando la recuperacion detecta fuentes fuera del scope pedido, el endpoint responde con bloqueo seguro:

```json
{
  "answer": "⚠️ Se detectó inconsistencia de ámbito entre la pregunta y las fuentes recuperadas. Reformula indicando explícitamente la norma objetivo.",
  "context_chunks": [],
  "citations": [],
  "mode": "HYBRID"
}
```

## 5) KPI de seguridad de scope

```bash
curl -s "http://localhost:8000/api/v1/knowledge/scope-health?tenant_id=<TENANT_ID>"
```

Este endpoint permite monitorear tasas de aclaracion y bloqueos por mismatch.
