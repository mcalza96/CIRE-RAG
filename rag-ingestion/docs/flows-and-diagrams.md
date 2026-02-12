# Flujos y Diagramas (Exactos del Servicio)

Este documento describe los flujos operativos de `rag-ingestion` usando diagramas Mermaid compatibles con GitHub.

## 1) Vista General del Sistema

```mermaid
flowchart LR
    C[Cliente API] --> A[FastAPI app/main.py]
    A --> R1[/api/v1/ingestion]
    A --> R2[/api/v1/knowledge]
    A --> R3[/api/v1/synthesis]
    A --> R4[/api/v1/curriculum (legacy path)]

    R1 --> U1[Use Cases de ingesta]
    R2 --> U2[UnifiedRetrievalEngine + TricameralOrchestrator]
    R3 --> U3[Workflow structured synthesis]
    R4 --> U3

    U1 --> S1[Servicios de parsing/chunking/embedding]
    U2 --> S2[Vector retrieval + GraphRAG + RAPTOR]

    S1 --> DB[(Supabase Postgres + pgvector)]
    S2 --> DB

    DB --> RT[Supabase Realtime]
    RT --> W[Worker run_worker.py]
    W --> P[ProcessDocumentWorkerUseCase]
    P --> DB
```

## 2) Flujo Exacto: Ingesta Manual (`POST /api/v1/ingestion/ingest`)

```mermaid
sequenceDiagram
    autonumber
    participant U as Usuario/Cliente
    participant API as FastAPI Router
    participant UC as ManualIngestionUseCase
    participant DB as Supabase
    participant BG as Background Task
    participant WR as Worker/Pipeline

    U->>API: POST /ingestion/ingest (archivo + metadata)
    API->>UC: validar request y crear documento fuente
    UC->>DB: insert source_documents (estado inicial)
    DB-->>UC: doc_id
    UC-->>API: documento registrado
    API->>BG: programar procesamiento async
    BG->>WR: ejecutar pipeline de documento
    WR->>DB: upsert chunks/visual_nodes/grafo/estado final
```

## 3) Flujo Exacto: Ingesta Institucional (`POST /api/v1/ingestion/institutional`)

```mermaid
sequenceDiagram
    autonumber
    participant S as Servicio Emisor
    participant API as FastAPI Router
    participant SEC as Validador de Secret
    participant UC as InstitutionalIngestionUseCase
    participant DB as Supabase
    participant WR as Worker

    S->>API: POST /ingestion/institutional + X-Service-Secret
    API->>SEC: validar header contra RAG_SERVICE_SECRET
    SEC-->>API: ok/unauthorized
    API->>UC: ejecutar caso de uso institucional
    UC->>DB: crear/actualizar source_documents y contexto institucional
    DB-->>UC: ids y estado
    UC-->>API: accepted/created
    API-->>S: respuesta de aceptacion
    WR->>DB: procesa documento y publica estado final
```

## 4) Flujo Exacto: Worker Realtime + Retry

```mermaid
flowchart TD
    A[run_worker.py] --> B[Suscripcion Realtime: source_documents]
    B --> C{Evento}
    C -->|INSERT| D[Procesar documento nuevo]
    C -->|UPDATE queued| E[Reintento de documento]

    D --> F[Lock por doc_id en memoria]
    E --> F
    F --> G[ProcessDocumentWorkerUseCase]
    G --> H[PDF parsing]
    H --> I[Chunking late/contextual]
    I --> J[Embeddings Jina]
    J --> K[Persistencia chunks + visual + grafo]
    K --> L[Actualizar estado en source_documents]
```

## 5) Flujo Exacto: Parsing y Chunking de PDF

```mermaid
flowchart TD
    P0[PDF input] --> P1{pymupdf4llm disponible?}
    P1 -->|Si| P2[extract_markdown_with_structure]
    P1 -->|No| P3[extract_text_with_page_map fallback]

    P2 --> P4[Detectar imagenes y crear visual_tasks]
    P3 --> P5[Texto plano + page_map]

    P4 --> C1[ChunkingService]
    P5 --> C1

    C1 --> C2{Late chunking Jina OK?}
    C2 -->|Si| C3[chunk_and_encode full document]
    C2 -->|No| C4[split por headings + fallback contextual]

    C3 --> C5[Attach heading_path + metadata]
    C4 --> C5
    C5 --> C6[Persistir content_chunks]
```

## 6) Flujo Exacto: Parsing Visual (Tablas/Figuras)

```mermaid
sequenceDiagram
    autonumber
    participant I as Ingestion Task
    participant VP as VisualDocumentParser
    participant CM as Cache middleware
    participant MF as ModelFactory
    participant VLM as Gemini/OpenAI Adapter
    participant HV as HEART Verifier
    participant DB as Supabase

    I->>VP: parse_image(image_path/image_bytes)
    VP->>CM: cached_extraction(image_hash, provider, model)
    alt cache hit
      CM-->>VP: VisualParseResult cached
    else cache miss
      VP->>MF: create_ingest_model()
      MF-->>VP: adapter concreto
      VP->>VLM: generate_structured_output(schema)
      VLM-->>VP: JSON estructurado
      opt HEART habilitado
        VP->>HV: verify(parse_result vs image)
        HV-->>VP: verified/unverified + discrepancias
      end
      VP->>CM: store cache row
      CM->>DB: upsert cache_visual_extractions
    end
    VP-->>I: structured_reconstruction + metadata
```

## 7) Flujo Exacto: RAPTOR (Hierarchical Summarization)

```mermaid
sequenceDiagram
    autonumber
    participant W as Worker
    participant RP as RaptorProcessor
    participant CS as ClusteringService (GMM)
    participant SA as SummarizationAgent (LLM)
    participant ES as EmbeddingService (Jina)
    participant DB as Supabase (Vector Store)

    W->>RP: build_tree(base_chunks, tenant_id)
    loop Recursive Levels (0 to max_depth)
        RP->>CS: cluster(embeddings)
        CS-->>RP: cluster_results (groups of chunk_ids)
        loop Per Cluster
            RP->>SA: summarize(cluster_texts)
            SA-->>RP: title + summary
            RP->>ES: embed_texts([summary])
            ES-->>RP: summary_embedding
            RP->>DB: save_summary_node(Level N)
        end
        Note over RP: Check convergence or max_depth
    end
    RP-->>W: RaptorTreeResult
```

## 8) Flujo Exacto: Retrieval (`POST /api/v1/knowledge/retrieve`)

```mermaid
sequenceDiagram
    autonumber
    participant U as Usuario
    participant API as Router knowledge
    participant ORQ as TricameralOrchestrator
    participant V as Vector Retrieval
    participant LG as Local Graph Search
    participant GG as Global Graph Search
    participant RP as RAPTOR summaries
    participant RR as GravityReranker
    participant DB as Supabase

    U->>API: query + tenant/scope
    API->>ORQ: orchestrate(intent)
    ORQ->>ORQ: classify mode (SPECIFIC/GENERAL/HYBRID)

    par fuentes segun modo
      ORQ->>V: retrieve_context(query_vector)
      V->>DB: rpc unified_search_context_v2
      DB-->>V: rows
      ORQ->>LG: local graph anchors + 1-hop
      ORQ->>GG: community summary search
      ORQ->>RP: search hierarchical summaries
    end

    ORQ->>RR: rerank by authority/task/role
    RR-->>ORQ: ranked results
    ORQ-->>API: contexto final grounded
    API-->>U: respuesta
```

## 8) Entidades Principales (Simplificado)

```mermaid
erDiagram
    SOURCE_DOCUMENTS ||--o{ CONTENT_CHUNKS : has
    SOURCE_DOCUMENTS ||--o{ VISUAL_NODES : has
    SOURCE_DOCUMENTS }o--|| TENANTS : belongs_to

    TENANTS ||--o{ KNOWLEDGE_ENTITIES : owns
    TENANTS ||--o{ KNOWLEDGE_RELATIONS : owns
    KNOWLEDGE_ENTITIES ||--o{ KNOWLEDGE_RELATIONS : source_or_target

    TENANTS ||--o{ REGULATORY_NODES : has
    TENANTS ||--o{ CURRICULUM_JOBS : runs
```

## 9) Referencias de Codigo

- API bootstrap: `rag-ingestion/app/main.py`
- Routers v1: `rag-ingestion/app/api/v1/api_router.py`
- Ingestion router: `rag-ingestion/app/api/v1/routers/ingestion.py`
- Knowledge router: `rag-ingestion/app/api/v1/routers/knowledge.py`
- Worker: `rag-ingestion/app/worker.py`
- PDF parser: `rag-ingestion/app/services/ingestion/pdf_parser.py`
- Chunking: `rag-ingestion/app/services/ingestion/chunking_service.py`
- Visual parser: `rag-ingestion/app/services/ingestion/visual_parser.py`
- Unified retrieval: `rag-ingestion/app/services/retrieval/engine.py`
- Tricameral orchestrator: `rag-ingestion/app/application/services/tricameral_orchestrator.py`
