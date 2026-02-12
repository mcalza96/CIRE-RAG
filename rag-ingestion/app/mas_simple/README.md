# MAS Simple Subsystem

This package is the clean-architecture/DDD skeleton for `MAS Simple`.

## Layers

- `domain/`: core models and policies (pure logic)
- `application.py`: use-case orchestration (`HandleQuestionUseCase`)
- `ports.py`: dependency inversion contracts

## Scope

This initial phase introduces structure and intent/retrieval planning logic.
Adapters for retrieval/LLM/validation should implement the ports and replace
inline orchestration currently living in `doc_chat_cli.py`.
