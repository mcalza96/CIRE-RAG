# Ticket: Migrar SDK Gemini a `google.genai`

- ID: TECHDEBT-2026-02-GEMINI-SDK
- Prioridad: Alta
- Estado: Open
- Owner sugerido: Backend IA / Ingestion

## Contexto

Actualmente el proveedor Gemini usa `google.generativeai`, que ya esta deprecado.
En logs de produccion/local aparece warning de fin de soporte.

Ruta afectada principal:

- `rag-ingestion/app/core/models/providers/gemini.py`

## Objetivo

Migrar la integracion a `google.genai` sin romper:

- parseo visual estructurado,
- contratos de salida del adapter,
- reintentos/fallback actuales.

## Alcance

1. Reemplazar cliente SDK e inicializacion.
2. Adaptar llamadas de generacion y parseo de respuesta.
3. Mantener compatibilidad con provider-agnostic adapters.
4. Actualizar dependencias y docs de configuracion.

## Criterios de aceptacion

- No warnings deprecados de `google.generativeai` en ingesta.
- `pytest tests/unit tests/integration tests/tools -q` pasa.
- Flujo visual de ingestion mantiene fallback y no bloquea pipeline.
- Respuestas estructuradas de Gemini respetan el esquema actual.

## Riesgos

- Cambios en shape de respuesta del SDK nuevo.
- Cambios en manejo de errores/finish reasons.

## Plan corto

1. Implementar adapter paralelo (`google.genai`) detras de feature flag.
2. Ejecutar smoke en ingesta PDF con tablas/figuras.
3. Hacer switch por defecto y retirar codigo legacy.
