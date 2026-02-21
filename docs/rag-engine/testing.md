# Testing

## Estructura de pruebas

- `tests/unit/`: pruebas unitarias puras y rapidas.
- `tests/integration/`: integracion de componentes del servicio.
- `tests/stress/`: escenarios de carga y robustez.
- `tests/evaluation/`: benchmark y metrica de calidad de respuestas.

## Comandos recomendados

Desde la raiz del repo:

```bash
venv/bin/pytest tests/unit -q
venv/bin/pytest tests/integration -q
venv/bin/pytest tests/stress -q
```

Calidad estatica:

```bash
ruff check app/schemas app/domain/schemas app/domain/models/source_entity.py app/services/retrieval/atomic_engine.py app/services/ingestion/visual_parser.py
mypy --config-file mypy.ini -m app.domain.schemas.ingestion_schemas -m app.core.config.model_config -m app.services.retrieval.atomic_engine -m app.services.ingestion.visual_parser
```

Evaluacion:

```bash
venv/bin/python tests/evaluation/run_benchmark.py
```

Baseline mantenible (CI/local rapido):

```bash
venv/bin/pytest tests/unit tests/integration tests/tools -q
```

## Convenciones

- Nombre de archivo: `test_<componente>.py`.
- Un test debe validar una sola expectativa principal.
- Mocks solo en bordes externos (DB, red, proveedores LLM).
- Reutilizar fixtures en `tests/fixtures` cuando aplique.

## Politica de calidad sugerida

- PR de bugfix: al menos unit + integration de la zona tocada.
- PR de retrieval/quality: incluir corrida de `tests/evaluation`.
- PR de performance: incluir evidencia en `tests/stress`.
