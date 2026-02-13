# Flujos y Diagramas (Exactos del Servicio)

Este documento describe los flujos operativos de `rag-engine` usando diagramas Mermaid compatibles con GitHub.

## 1) Vista General del Sistema

```mermaid
flowchart LR
    C[Cliente API] --> A[FastAPI app/main.py]
    A --> R1[/api/v1/ingestion]
    A --> R2[/api/v1/knowledge]

    R1 --> U1[Use Cases de ingesta]
    R2 --> U2[AtomicRetrievalEngine + QueryDecomposer]

    U1 --> S1[Servicios de parsing/chunking/embedding]
    U2 --> S2[Vector/FTS RRF + Multi-hop Graph]

    S1 --> DB[(Supabase Postgres + pgvector)]
    S2 --> DB

    DB --> JQ[(job_queue)]
    JQ --> W[Worker run_worker.py (pull)]
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
    participant JQ as job_queue (DB)
    participant WR as Worker/Pipeline (pull)

    U->>API: POST /ingestion/ingest (archivo + metadata)
    API->>UC: validar request y crear documento fuente
    UC->>DB: insert source_documents (estado queued)
    DB-->>UC: doc_id
    UC-->>API: accepted + queue_depth/eta
    DB->>JQ: encolar ingest_document (trigger/RPC DB)
    WR->>JQ: fetch_next_job(ingest_document)
    JQ-->>WR: source_document_id
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
    participant JQ as job_queue (DB)
    participant WR as Worker (pull)

    S->>API: POST /ingestion/institutional + X-Service-Secret
    API->>SEC: validar header contra RAG_SERVICE_SECRET
    SEC-->>API: ok/unauthorized
    API->>UC: ejecutar caso de uso institucional
    UC->>DB: upsert source_documents + strategy_override
    DB->>JQ: encolar ingest_document
    DB-->>UC: ids + estado queued
    UC-->>API: accepted + queue_depth/eta
    API-->>S: respuesta de aceptacion
    WR->>JQ: fetch_next_job(ingest_document)
    WR->>DB: procesa documento y publica estado final
```

## 4) Flujo Exacto: Worker Pull Model + Retry

```mermaid
flowchart TD
    A[run_worker.py] --> B[Pollers async]
    B --> C[RPC fetch_next_job(ingest_document)]
    C --> D{Hay job?}
    D -->|No| E[Sleep poll interval]
    D -->|Si| F[Cargar source_document]
    F --> G[Lock por doc_id en memoria]
    G --> H[Semaphore global y por tenant]
    H --> I[ProcessDocumentWorkerUseCase]
    I --> J[Pipeline: parse/chunk/embed/persist]
    J --> K[Opcional: Visual Anchors + RAPTOR + Graph]
    K --> L[Actualizar status y job_queue]
    L --> C
```

## 5) Flujo Exacto: Parsing y Chunking de PDF

```mermaid
flowchart TD
    P0[PDF input] --> P1{DocumentStructureRouter}
    P1 -->|Text| P2[PdfParser: Markdown extraction]
    P1 -->|Visual| P3[VisualDocumentParser: VLM Task]

    P2 --> C1[ChunkingService: Late Chunking Jina v3]
    P3 --> C2[Visual Anchor: Structured JSON]

    C1 --> C3[Persistir content_chunks + heading_path]
    C2 --> C4[Persistir visual_nodes + crops]
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

## 8) Flujo Exacto: Retrieval (Atomic Engine)

```mermaid
sequenceDiagram
    autonumber
    participant U as Usuario
    participant API as Router knowledge
    participant QP as QueryDecomposer
    participant ORQ as AtomicRetrievalEngine
    participant V as Vector/FTS (RRF)
    participant G as Multi-hop Graph
    participant DB as Supabase

    U->>API: query + scope
    API->>QP: decompose(query)
    QP-->>API: QueryPlan (is_multihop=true/false)

    API->>ORQ: retrieve_context_from_plan(plan)
    
    par Fuentes Atómicas
      ORQ->>V: search_vectors + search_fts
      V->>DB: rpc search_vectors_only/search_fts_only
      ORQ->>G: search_multi_hop_context
      G->>DB: graph hop navigation
    end

    ORQ->>ORQ: Fusion RRF + Dedupe + Rerank
    ORQ-->>API: contexto final grounded
    API-->>U: respuesta
```

## 9) Flujo Exacto: Consumo de Retrieval por Cliente Externo

```mermaid
sequenceDiagram
    autonumber
    participant C as Cliente
    participant APP as Aplicacion Consumidora
    participant RAG as Retrieval API
    participant KB as RetrievalTools/Knowledge stack

    C->>APP: Query + tenant/collection
    APP->>RAG: POST /knowledge/retrieve
    RAG->>KB: Retrieval con filtros y fusion hibrida
    KB-->>RAG: evidencia C#/R#
    RAG-->>APP: Contexto grounded + citas
    APP-->>C: Respuesta final propia
```

## 10) Flujo Operativo 5 Etapas (Ingestion->Proceso->Pregunta->Analisis->Respuesta)

```mermaid
flowchart LR
    A[Ingestion API] --> B[source_documents queued]
    B --> C[Proceso Worker pull job_queue]
    C --> D[Knowledge base actualizada]
    D --> E[Pregunta cliente API/CLI]
    E --> F[Analisis aplicacion cliente]
    F --> G{Validacion scope/evidencia}
    G -->|OK| H[Respuesta grounded C#/R#]
    G -->|No OK| I[Aclaracion o bloqueo]
```

## 11) Entidades Principales (Simplificado)

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

## 12) Referencias de Codigo

- API bootstrap: `app/main.py`
- Routers v1: `app/api/v1/api_router.py`
- Ingestion router: `app/api/v1/routers/ingestion.py`
- Knowledge router: `app/api/v1/routers/knowledge.py`
- Worker: `app/worker.py`
- PDF parser: `app/services/ingestion/pdf_parser.py`
- Chunking: `app/services/ingestion/chunking_service.py`
- Visual parser: `app/services/ingestion/visual_parser.py`
- Atomic retrieval: `app/services/retrieval/atomic_engine.py`
- Query decomposition: `app/application/services/query_decomposer.py`
- Retrieval router: `app/application/services/retrieval_router.py`
