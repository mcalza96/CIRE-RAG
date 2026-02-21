# Documentation Hub

Bienvenido a la documentación técnica de **CIRE-RAG**. Este directorio contiene guías sobre el funcionamiento interno y la operación del sistema.

## Guías Disponibles

- **[E2E Flow & Security](e2e.md)**: 
  - Explicación del sistema de búsqueda híbrido.
  - Detalles sobre el middleware de seguridad (LeakCanary).
  - Diagramas de secuencia de retrieval.

## Configuración y Entorno

El sistema se configura principalmente mediante variables de entorno en el archivo `.env.local` (basado en `.env.example`).

### Variables Críticas
- `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`: Conexión con la base de datos y persistencia.
- `RAG_SERVICE_SECRET`: Clave para autenticación S2S (Service-to-Service).
- `JINA_API_KEY`: Necesaria cuando `JINA_MODE=CLOUD` para embeddings y rerank.

## Desarrollo Local

Para ejecutar pruebas y validar cambios:
1. Asegurarse de tener los contenedores de Supabase activos.
2. Ejecutar `pytest tests/integration/test_rag_e2e_security.py` para validar la integridad del flujo de seguridad.

---
*Nota: Si necesitas documentación de un módulo específico que no aparece aquí, consulta el README en la raíz de `app/`.*
