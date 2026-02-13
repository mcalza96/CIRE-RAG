# Migration Note: `mas_simple` -> `qa_orchestrator`

Date: 2026-02-12

## Summary

The internal module name was renamed from `app/mas_simple` to
`app/qa_orchestrator` to avoid confusion with MAS (Multi-Agent Systems).

This is a naming and positioning change. The architectural role remains the same:
Q/A orchestration on top of the RAG backend.

## What Changed

- Package path rename:
  - `app/mas_simple/*` -> `app/qa_orchestrator/*`
- Import updates across codebase and tests:
  - `app.mas_simple...` -> `app.qa_orchestrator...`
- Test file rename:
  - `tests/unit/test_mas_simple_policies.py` -> `tests/unit/test_qa_orchestrator_policies.py`
  - `tests/unit/test_mas_simple_use_case.py` -> `tests/unit/test_qa_orchestrator_use_case.py`
- Documentation updates:
  - Integration notes updated in
    `docs/qa-rag-integration-notes.md`

## PR Description Snippet (copy/paste)

```
## Migration note

Renamed `app/mas_simple` to `app/qa_orchestrator`.

- Why: `MAS` was being interpreted as "Multi-Agent System"; this module is a
  Q/A orchestration layer, not a MAS runtime.
- Scope: package paths, imports, tests, and docs were updated.
- Risk: low-to-medium (mainly import-path breakage for downstream consumers).
- Mitigation: unit tests for orchestrator pass; docs and integration notes updated.
```

## Release Notes Snippet (copy/paste)

```
### Changed

- Renamed internal module `app/mas_simple` to `app/qa_orchestrator`.
- Updated docs and integration notes naming to "Q/A Orchestrator".

### Upgrade Notes

- Replace imports from `app.mas_simple` to `app.qa_orchestrator`.
- If you run targeted tests, use:
  - `tests/unit/test_qa_orchestrator_policies.py`
  - `tests/unit/test_qa_orchestrator_use_case.py`
```

## Consumer Action Checklist

- Search and replace imports:
  - `from app.mas_simple...` -> `from app.qa_orchestrator...`
- Update any internal docs or scripts referencing `mas_simple`.
- Run unit tests for orchestrator after migration.
