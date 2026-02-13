# Phase 1 - Interface Audit (Simplification Snapshot)

## Scope

- Reviewed contracts under `app/domain/interfaces/*.py`.
- Goal: keep only boundaries that reduce real maintenance cost.
- Approach: remove 1:1 internal interfaces, preserve external seams.

## Current state (implemented)

Kept contracts:

- `app/domain/interfaces/retrieval_interface.py` (`IRetrievalRepository`, `Protocol`)
- `app/domain/interfaces/embedding_provider.py` (`IEmbeddingProvider`, `ABC`)

Removed contracts:

- `ITaxonomyManager`
- `IStructuredEngine`
- `IClusteringService`
- `ISummarizationService`
- previous inactive/low-value internal interfaces from the earlier pass

## Why these two remain

1. `IRetrievalRepository`
   - High fan-out and DB boundary.
   - Keeps retrieval broker/services decoupled from concrete storage wiring.

2. `IEmbeddingProvider`
   - Real provider swap in production (`JinaLocalProvider` vs `JinaCloudProvider`).
   - Useful seam for fallback behavior and provider-specific failure handling.

## Metrics after simplification

- Interface files in `app/domain/interfaces`: **2**
- Protocol contracts: **1**
- ABC contracts: **1**
- Contracts with active multi-implementation runtime value: **1** (`IEmbeddingProvider`)

## Working rule for new abstractions

Create a new interface only if at least one is true:

1. There are 2+ active implementations now.
2. It isolates an external dependency (DB/provider/network) with high churn risk.
3. It removes meaningful test setup cost in current tests.

Otherwise, prefer concrete classes and direct wiring.
