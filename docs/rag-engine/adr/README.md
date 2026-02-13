# ADR Index

This folder tracks architecture decisions that evolve from the `s.txt` research into executable engineering policy.

## Active ADRs

- `ADR-0001-visual-anchor-late-binding.md`
- `ADR-0002-vlm-lifecycle-and-provider-policy.md`
- `ADR-0003-hybrid-retrieval-vector-plus-structured.md`
- `ADR-0004-table-pruning-before-generation.md`

## Mapping to implementation and PRs

- `ADR-0001` -> ingestion and visual parsing pipeline
  - Files: `app/services/ingestion/pdf_parser.py`, `app/services/ingestion/visual_parser.py`, `app/services/retrieval/atomic_engine.py`
  - PR scope: enforce text-first retrieval over visual summaries + visual hydration guardrails

- `ADR-0002` -> model/provider governance
  - Files: `app/core/config/model_config.py`, `app/core/models/factory.py`, `../configuration.md`
  - PR scope: provider defaults, deprecation policy, compatibility checks

- `ADR-0003` -> retrieval query strategy
  - Files: `app/services/retrieval/atomic_engine.py`, `app/application/services/retrieval_router.py`, SQL RPCs in migrations
  - PR scope: hybrid ranking consistency and threshold tuning

- `ADR-0004` -> context budget controls for tables
  - Files: `app/services/knowledge/gravity_reranker.py`, `app/services/retrieval/atomic_engine.py`, future pruner module
  - PR scope: implement deterministic table-pruning stage before final generation
