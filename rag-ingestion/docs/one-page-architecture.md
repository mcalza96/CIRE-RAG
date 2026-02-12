# One-Page Architecture (Executive)

Quick visual overview for GitHub readers.

## 1) End-to-End RAG Flow

```mermaid
flowchart LR
    subgraph Ingestion
      A[PDF/Docs Input] --> B[Parser: text + visual assets]
      B --> C[Chunking: late + contextual fallback]
      C --> D[Embeddings: Jina v3]
      D --> E[(Supabase: chunks, visual nodes, graph)]
    end

    subgraph Retrieval
      Q[User Query] --> R[Tricameral Router]
      R --> V[Vector Search]
      R --> G[GraphRAG Local/Global]
      R --> H[RAPTOR Summaries]
      V --> X[Gravity Reranking]
      G --> X
      H --> X
      X --> Y[Grounded Context]
      Y --> Z[Answer]
    end

    E --> V
    E --> G
    E --> H
```

## 2) Ingestion Topology (Visual Anchor Pattern)

```mermaid
flowchart TD
    In[Document PDF] --> P{Deep Parser}
    P -->|Text| C[Contextual Chunking]
    P -->|Images| V[Visual Anchor Task]
    
    C --> J[Jina v3 Embedding]
    V --> VLM[Gemini 2.5 Flash]
    
    VLM --> S[Structured JSON]
    VLM --> CR[Image Crop]
    
    S --> DB[(Supabase)]
    CR --> ST[Supabase Storage]
    J --> DB
```

## 3) Runtime Topology

```mermaid
flowchart TD
    U[Clients / Integrations] --> API[FastAPI Service]
    API --> DB[(Supabase Postgres + pgvector)]
    DB --> RT[Supabase Realtime]
    RT --> W[Async Worker]
    W --> DB

    API --> K[Knowledge API]
    API --> I[Ingestion API]
    API --> C[Curriculum API]

    K --> O[Tricameral Orchestrator]
    O --> RR[Gravity Reranker]
    O --> DB
```

## Value in one line

Multimodal ingestion + hybrid retrieval + authority-aware ranking, delivered as an API-first open-source RAG backend.
