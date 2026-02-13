# Q/A Orchestrator Subsystem

This package is the clean-architecture/DDD skeleton for `Q/A Orchestrator`.

Former module name: `mas_simple`.

## Layers

- `domain/`: core models and policies (pure logic)
- `application.py`: use-case orchestration (`HandleQuestionUseCase`)
- `ports.py`: dependency inversion contracts

## Scope

Current implementation is active in production paths used by `doc_chat_cli.py` and
ready to be reused by API adapters.

Execution flow (`HandleQuestionUseCase`):

1. classify intent (`literal_normativa`, `literal_lista`, `comparativa`, `explicativa`, `ambigua_scope`)
2. build retrieval plan (`chunk_k`, `fetch_k`, `summary_k`, literal evidence requirement)
3. detect scope ambiguity/conflict objectives and return clarification when needed
4. retrieve evidence via `RetrieverPort` (chunks + summaries)
5. generate draft answer via `AnswerGeneratorPort`
6. validate literal evidence and scope consistency via `ValidationPort`
7. return grounded answer, clarification request, or blocked answer when scope mismatch is hard

Boundary contract with RAG backend: `docs/qa-orchestrator-rag-boundary-contract.md`.
