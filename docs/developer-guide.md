# Developer Guide

## Development Workflow

1. Create a focused branch.
2. Keep changes scoped to one concern.
3. Add or update tests for changed behavior.
4. Update docs in the same PR.

Contribution policy: `../CONTRIBUTING.md`.

## Local Quality Gates

Run from repo root:

```bash
ruff check app tests scripts run_worker.py
mypy --config-file mypy.ini -m app.schemas.ingestion -m app.core.config.model_config -m app.services.retrieval.atomic_engine -m app.services.ingestion.visual_parser
pytest tests/unit tests/integration tests/tools -q
```

Optional hooks:

```bash
pre-commit install
pre-commit run --all-files
```

## Testing Strategy

- Unit tests: fast validation of isolated logic.
- Integration tests: service component interactions.
- Stress tests: performance and robustness scenarios.
- Evaluation tests: retrieval/answer quality benchmarks.

Reference: `rag-engine/testing.md`.

## Dependency Management

- Edit direct dependencies in `requirements.in`.
- Keep pinned versions in `requirements.txt`.
- Use focused dependency update PRs.

## Documentation Rules

- Project navigation lives in `docs/*`.
- Service internals live in `docs/rag-engine/*`.
- Architecture-level decisions must reference ADRs in `docs/rag-engine/adr/README.md`.
