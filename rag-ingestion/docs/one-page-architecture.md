# One-Page Architecture (Executive)

Quick visual overview for GitHub readers.

## 1) End-to-End RAG Flow

```mermaid
flowchart LR
    subgraph Ingestion
      A[PDF/Docs Input] --> B[Structure Router: Text vs Visual]
      B --> C[PdfParser: Markdown + Table JSON]
      C --> D[Late Chunking: Jina v3]
      D --> E[(Supabase: chunks, visual nodes, graph)]
    end

    subgraph Retrieval
      Q[User Query] --> QP[Query Decomposer]
      QP --> AE[Atomic Retrieval Engine]
      AE --> V[Vector + FTS Fusion]
      AE --> G[Multi-hop Graph Nav]
      V --> X[Gravity Reranking]
      G --> X
      X --> Y[Grounded Context]
      Y --> Z[Answer]
    end

    E --> V
    E --> G
```

## 2) Ingestion Topology (Visual Anchor Pattern)

```mermaid
flowchart TD
    In[Document PDF] --> Router{Structure Router}
    Router -->|Text| P[PdfParser: Markdown]
    Router -->|Complex| V[Visual Anchor Task]
    
    P --> J[Jina v3 Embedding]
    V --> VLM[Gemini 2.5 Flash Lite]
    
    VLM --> S[Structured JSON]
    VLM --> CR[Image Crop]
    
    S --> DB[(Supabase)]
    CR --> ST[Supabase Storage]
    J --> DB
```

## 3) Runtime Topology

```mermaid
flowchart TD
    U[Clients / Integrations] --> QAO[Q/A Orchestrator]
    QAO --> AE[Atomic Retrieval Engine]
    AE --> DB[(Supabase Postgres + pgvector)]
    DB --> JQ[(job_queue)]
    JQ --> W[Async Worker (pull)]
    W --> DB

    QAO --> K[Knowledge API]
    QAO --> I[Ingestion API]

    AE --> QP[Query Decomposer]
    AE --> RR[Gravity Reranker]
```

## 4) Boundary Note

- `Q/A Orchestrator` (`app/qa_orchestrator`) decide intencion, plan y validacion.
- RAG backend (`AtomicRetrievalEngine`) ejecuta retrieval atómico sobre múltiples capas (Vector, FTS, Graph).
- Contrato vigente: `docs/qa-orchestrator-rag-boundary-contract.md`.

## Value in one line

Multimodal ingestion + multi-hop atomic retrieval + authority-aware ranking, delivered as an API-first open-source RAG backend.
