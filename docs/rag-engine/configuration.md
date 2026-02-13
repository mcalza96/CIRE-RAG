# Configuracion

## Resolucion de variables de entorno

El servicio carga variables en este orden (primero mas global, luego mas especifico):

1. `.env` en raiz del repo.
2. `.env.local` en raiz del repo.
3. `.env` en raiz del repo (alias de servicio).
4. `.env.local` en raiz del repo (alias de servicio).

Referencia: `app/core/settings.py`.

## Variables criticas

- `SUPABASE_URL` o `NEXT_PUBLIC_SUPABASE_URL` (obligatoria).
- `SUPABASE_SERVICE_ROLE_KEY` (obligatoria).
- `RAG_SERVICE_SECRET` (requerida para autenticar API v1 en entornos desplegados).
- `RAG_STORAGE_BUCKET` (bucket de Supabase Storage para archivos de ingesta; default `private_assets`).
- `APP_ENV` (`local` | `staging` | `production`; controla politicas de seguridad/guardrails).
- `REDIS_URL` (recomendado para idempotencia cross-instance de `POST /documents`).

Sin estas variables, API y worker no operan de forma correcta.

## Variables de modelos (ejemplo base)

- `VLM_PROVIDER`
- `VLM_MODEL_NAME`
- `CHAT_LLM_PROVIDER`
- `CHAT_LLM_MODEL_NAME`
- `LLM_TEMPERATURE`

Credenciales por proveedor:

- `OPENAI_API_KEY`
- `GEMINI_API_KEY` (o `GOOGLE_GENERATIVE_AI_API_KEY`)
- `GROQ_API_KEY`
- `ANTHROPIC_API_KEY`
- `JINA_API_KEY`

Flags de pipeline cloud/local:

- `INGEST_PARSER_MODE` (`local` | `cloud`)
- `JINA_READER_URL_TEMPLATE` (opcional, usado cuando `INGEST_PARSER_MODE=cloud`)
- `RERANK_MODE` (`local` | `jina` | `hybrid`)
- `AUTHORITY_CLASSIFIER_MODE` (`rules` | `embedding_first`)
- `ATOMIC_USE_HYBRID_RPC` (`true` | `false`; usa RPC SQL unificada para vector + FTS + RRF)
- `ATOMIC_HNSW_EF_SEARCH` (precision/latencia en HNSW; default `80`)
- `RERANK_MAX_CANDIDATES` (tope de candidatos enviados a reranker; default `10`)

## Puertos y ejecucion

- API local: `8000` (ver `start_api.sh`).
- Health endpoint: `/health`.

## Despliegue cloud (API + worker)

- Target API recomendado: `api_image`.
- Target worker recomendado: `worker_cloud_image` (sin dependencias locales pesadas).
- Target `worker_image`: solo cuando necesitas runtime local con `torch/transformers`.

Variables recomendadas en Railway/Render:

- `APP_ENV=production`
- `JINA_MODE=CLOUD`
- `JINA_API_KEY`
- `SUPABASE_URL` (o `NEXT_PUBLIC_SUPABASE_URL`)
- `SUPABASE_SERVICE_ROLE_KEY`
- `RAG_SERVICE_SECRET` (token de auth para API v1)
- `REDIS_URL` (idempotencia distribuida)
- `PORT` (API, default `8000`)
- `UVICORN_WORKERS=1` (default recomendado para contenedores pequenos)

Comportamiento de auth por entorno:

- `APP_ENV=local`: auth deshabilitada para DX local.
- `APP_ENV=staging|production`: auth obligatoria en endpoints de producto/debug (`Authorization: Bearer ...` o `X-Service-Secret`).

Comportamiento de idempotencia (`POST /documents`):

- Con `REDIS_URL` valido: persistencia distribuida de `Idempotency-Key`.
- Sin Redis: fallback en memoria por proceso (util para local, no ideal para multi-replica).

Compose local recomendado:

- `docker compose up --build -d api worker`
- Perfil pesado local: `docker compose --profile local-heavy up --build -d worker-local`

## Concurrencia (Fase 4)

- `WORKER_CONCURRENCY`: cantidad de documentos procesados en paralelo por worker (default `3`).
- `EMBEDDING_CONCURRENCY`: cantidad de llamadas concurrentes a embeddings/chunk-and-encode (default `5`).

Recomendacion inicial:

- Entornos pequenos: `WORKER_CONCURRENCY=2`, `EMBEDDING_CONCURRENCY=3`
- Entornos medianos: `WORKER_CONCURRENCY=3`, `EMBEDDING_CONCURRENCY=5`

## Presupuesto visual (estabilizacion)

- `VISUAL_ROUTER_MAX_VISUAL_RATIO`: ratio maximo de paginas visuales por documento (default `0.35`).
- `VISUAL_ROUTER_MAX_VISUAL_PAGES`: tope duro de paginas visuales por documento (default `12`).

Objetivo: reducir latencia y costo VLM mientras se estabiliza ingestion multimodal.

## Seguridad operativa minima

- No commitear `.env`, `.env.local`, keys o tokens.
- Rotar `RAG_SERVICE_SECRET` fuera de desarrollo.
- Limitar `SUPABASE_SERVICE_ROLE_KEY` a entornos backend controlados.
