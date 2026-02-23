# Contributing

Thanks for contributing to CIRE-RAG.

## Scope

Primary service: `rag-engine` (repo root).

## Development setup

```bash
cp .env.example .env.local
./bootstrap.sh
```

## Quality gates (required)

Run before opening a PR:

```bash
ruff check app tests scripts scripts/run_worker.py
mypy --config-file mypy.ini -m app.domain.schemas.ingestion_schemas -m app.infrastructure.settings -m app.infrastructure.supabase.repositories.atomic_engine -m app.infrastructure.document_parsers.visual_parser
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

- Add/remove direct dependencies in `requirements.in`.
- Keep `requirements.txt` pinned.
- Dependency update PRs are managed via Dependabot.

## Pull request checklist

- Keep PR focused and small.
- Add or update tests for behavior changes.
- Update docs when changing architecture, API behavior, or runbooks.
- Reference ADRs when implementing architecture-level changes.

Project documentation map: `docs/README.md`.
