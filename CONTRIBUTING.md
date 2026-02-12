# Contributing

Thanks for contributing to CISRE (Cognitive Ingestion & Structured Retrieval Engine).

## Scope

Primary service: `rag-ingestion`.

## Development setup

```bash
cd rag-ingestion
cp .env.example .env.local
./bootstrap.sh
```

## Quality gates (required)

Run before opening a PR:

```bash
cd rag-ingestion
ruff check app tests scripts run_worker.py doc_chat_cli.py
mypy --config-file mypy.ini -m app.schemas.ingestion -m app.core.config.model_config -m app.services.retrieval.engine -m app.services.ingestion.visual_parser
pytest tests/unit tests/integration tests/tools -q
```

Optional local hook setup:

```bash
pre-commit install
pre-commit run --all-files
```

## Python support matrix

| Version | Status | Notes |
|---|---|---|
| 3.11 | Supported | Primary CI target |
| 3.12 | Best effort | Community verification |
| 3.13 | Best effort | Local/dev compatibility |

## Dependency policy

- Add/remove direct dependencies in `rag-ingestion/requirements.in`.
- Keep `rag-ingestion/requirements.txt` pinned.
- Dependency update PRs are managed via Dependabot.

## Pull request checklist

- Keep PR focused and small.
- Add or update tests for behavior changes.
- Update docs when changing architecture, API behavior, or runbooks.
- Reference ADRs when implementing architecture-level changes.

Project documentation map: `docs/README.md`.
