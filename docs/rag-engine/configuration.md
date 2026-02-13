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
- `RAG_SERVICE_SECRET` (requerida para endpoint institucional).
- `RAG_STORAGE_BUCKET` (bucket de Supabase Storage para archivos de ingesta; default `private_assets`).

Sin estas variables, API y worker no operan de forma correcta.

## Variables de modelos (ejemplo base)

- `VLM_PROVIDER`
- `VLM_MODEL_NAME`
- `CHAT_LLM_PROVIDER`
- `CHAT_LLM_MODEL_NAME`
- `LLM_TEMPERATURE`

Variables usadas por Q/A Orchestrator (`orchestrator/runtime/qa_orchestrator` y `orchestrator/chat_cli.py`):

- `RAG_ENGINE_URL` (base URL del rag-engine para el orquestador; default `http://localhost:8000`).
- `CHAT_LLM_PROVIDER` / `CHAT_LLM_MODEL_NAME` cuando se enruta por stack API.

Credenciales por proveedor:

- `OPENAI_API_KEY`
- `GEMINI_API_KEY` (o `GOOGLE_GENERATIVE_AI_API_KEY`)
- `GROQ_API_KEY`
- `ANTHROPIC_API_KEY`
- `JINA_API_KEY`

## Puertos y ejecucion

- API local: `8000` (ver `start_api.sh`).
- Orchestrator API local: `8001` (ver `../orchestrator/start_api.sh`).
- Health endpoint: `/health`.

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
