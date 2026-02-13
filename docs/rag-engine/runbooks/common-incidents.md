# Runbook: Incidentes comunes

## 1) API no inicia

Sintoma:

- `./start_api.sh` falla al arrancar.

Pasos:

1. Validar virtualenv: `ls venv/bin/activate`.
2. Si falta, ejecutar `./bootstrap.sh`.
3. Verificar variables de entorno minimas (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`).
4. Probar health: `curl http://localhost:8000/health`.

## 2) Worker no procesa documentos

Sintoma:

- Se crean documentos pero no avanzan de estado.

Pasos:

1. Ejecutar worker en foreground: `venv/bin/python run_worker.py`.
2. Confirmar credenciales Supabase en entorno.
3. Verificar que existan jobs `ingest_document` en `job_queue`.
4. Revisar logs del worker para `fetch_next_job` y estado final del job (`completed`/`failed`).
5. Verificar que el documento este en estado `queued` para retry.

## 3) Endpoint institucional devuelve 401

Sintoma:

- `POST /api/v1/ingestion/institutional` responde `Unauthorized`.

Pasos:

1. Confirmar header `X-Service-Secret` en request.
2. Confirmar valor esperado en `RAG_SERVICE_SECRET`.
3. Alinear secreto entre caller y servicio.

## 4) Retrieval con resultados vacios

Sintoma:

- `POST /api/v1/knowledge/retrieve` retorna poco contexto o vacio.

Pasos:

1. Confirmar que tenant/documentos existen en BD.
2. Revisar que ingestion previa haya finalizado correctamente.
3. Validar proveedor de embeddings (`JINA_MODE`, `JINA_API_KEY` si aplica).
4. Ejecutar pruebas unit/integration del modulo de retrieval.
