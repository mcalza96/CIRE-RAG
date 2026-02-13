# Getting Started

## Prerequisites

- Python 3.13+
- Access to a Supabase project
- Local shell with `bash`

## 1) Bootstrap Local Environment

From repository root:

```bash
cp .env.example .env.local
./bootstrap.sh
```

Minimum required variables:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

See full configuration reference: `rag-engine/configuration.md`.

## 2) Run API and Worker

API terminal:

```bash
./start_api.sh
```

Worker terminal:

```bash
venv/bin/python run_worker.py
```

Health check:

```bash
curl http://localhost:8000/health
```

## 3) Validate with Tests

```bash
venv/bin/pytest tests/unit -q
venv/bin/pytest tests/integration -q
```

Extended testing guidance: `rag-engine/testing.md`.

## 4) Explore APIs and Flows

- API and endpoint summary: `rag-engine/README.md`
- HITL quickstart (clarification flow): `rag-engine/getting-started.md`
- Architecture overview: `architecture.md`
- Operational incidents and runbooks: `operations.md`

## 5) Q/A Orchestrator CLI

From repository root:

```bash
./ing.sh
./chat.sh
```

Notes:

- `chat.sh` launches Q/A Orchestrator and lets you choose collection scope and multi-hop mode.
- Existing collections are overwrite-friendly in the ingestion CLI (cleanup + reingestion workflow).
