# Phase 1 - Interface Audit (Onion Architecture Simplification)

## Scope

- Targeted contracts under `app/domain/interfaces/*.py`.
- Goal: identify which abstractions reduce coupling vs which add cognitive overhead.
- Status: audit complete and Phase 2 quick wins partially applied.

## Decision rubric

Each contract is classified with one of:

- `keep`: keep explicit contract (usually infra boundary or real multi-implementation swap).
- `migrate_to_protocol`: keep type boundary but replace `ABC` with structural typing (`Protocol`).
- `delete_inline`: remove interface and depend on concrete class/function directly.

Decision criteria:

1. Implementations: does it have 2+ real implementations (or near-term planned)?
2. External boundary: does it isolate DB/LLM/provider/network concerns?
3. Test seam: is it actively useful for fakes/mocks in tests?
4. Cost: does it force extra files/wiring with little reduction in coupling?

## Inventory and classification (from audit)

| Contract | Kind | Consumers | Implementations | Notes | Decision |
|---|---|---:|---:|---|---|
| `app/domain/interfaces/adversarial_generator_interface.py` (`IAdversarialGenerator`) | ABC | 0 | 0 | No active usage in app layer | `delete_inline` |
| `app/domain/interfaces/clustering.py` (`IClusteringService`) | ABC | 1 (`RaptorProcessor`) | 1 (`GMMClusteringService`) | Algorithm could vary over time, but ABC is heavy for current reality | `migrate_to_protocol` |
| `app/domain/interfaces/embedding_provider.py` (`IEmbeddingProvider`) | ABC | 1 service facade | 2 (`JinaLocalProvider`, `JinaCloudProvider`) | Real provider swap, external boundary | `keep` |
| `app/domain/interfaces/ingestion_dispatcher_interface.py` (`IIngestionDispatcher`) | ABC | 1 use case | 1 (`IngestionDispatcher`) | Thin pass-through contract, internal only | `delete_inline` |
| `app/domain/interfaces/ingestion_policy_interface.py` (`IIngestionPolicy`) | ABC | 1 use case | 1 (`IngestionPolicy`) | Single domain policy implementation, low polymorphism value | `delete_inline` |
| `app/domain/interfaces/llm.py` (`IStructuredEngine`) | ABC | 1 module import path, shared utility role | 1 (`StrictEngine`) | Cross-cutting LLM boundary; useful seam | `migrate_to_protocol` |
| `app/domain/interfaces/retrieval_interface.py` (`IRetrievalRepository`) | ABC | 4+ modules | 1 (`SupabaseRetrievalRepository`) | Critical DB boundary, high fan-out | `keep` |
| `app/domain/interfaces/summarization.py` (`ISummarizationService`) | ABC | 1 (`RaptorProcessor`) | 1 (`SummarizationAgent`) | Useful seam but low current polymorphism | `migrate_to_protocol` |
| `app/domain/interfaces/taxonomy_manager_interface.py` (`ITaxonomyManager`) | Protocol | 1 use case | 1 (`TaxonomyManager`, duck-typed) | Already lightweight and Pythonic | `keep` |

## Baseline metrics (current)

- Interfaces in `app/domain/interfaces`: **9**
- `ABC` contracts: **8**
- `Protocol` contracts: **1**
- Contracts with 2+ implementations: **1** (`IEmbeddingProvider`)
- Contracts with 0 consumers: **1** (`IAdversarialGenerator`)

## Execution status (current repo state)

- Removed interfaces: `IAdversarialGenerator`, `IIngestionDispatcher`, `IIngestionPolicy`.
- Migrated to `Protocol`: `IClusteringService`, `ISummarizationService`, `IStructuredEngine`.
- Kept as `ABC` hard boundaries: `IEmbeddingProvider`, `IRetrievalRepository`.
- Kept as `Protocol`: `ITaxonomyManager`.

Current counts in `app/domain/interfaces`:

- Interface files: **6**
- `ABC` contracts: **2**
- `Protocol` contracts: **4**

## Refactor order for Phase 2 (lowest risk first)

1. Remove dead contract: `IAdversarialGenerator` (`delete_inline`). **Done**.
2. Remove thin wiring contracts: `IIngestionDispatcher`, `IIngestionPolicy`. **Done**.
3. Convert algorithm/service contracts to `Protocol`: `IClusteringService`, `ISummarizationService`. **Done**.
4. Convert `IStructuredEngine` to `Protocol` while preserving `StrictEngine` API. **Done**.
5. Keep and document hard boundaries: `IEmbeddingProvider`, `IRetrievalRepository`, `ITaxonomyManager`. **In progress**.

## Out-of-scope note (tracked for next audit)

Repository interfaces in `app/domain/repositories/*.py` were not changed in this phase.
They should be reviewed in a follow-up pass, but current signal suggests they are more justified because they isolate persistence concerns.
