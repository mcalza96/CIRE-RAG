# Q/A Orchestrator (Extraction Staging)

Esta carpeta aisla runtime/scripts del orquestador para preparar su extraccion a un repo independiente.

Estado actual:

- El servicio se ejecuta desde `orchestrator/runtime/orchestrator_main.py`.
- La logica Q/A vive en `orchestrator/runtime/qa_orchestrator/*`.
- El runtime sigue consumiendo componentes compartidos de `app/*` mientras se completa la extraccion.
- Esta carpeta contiene wrappers de arranque/CLI para desacoplar operacion desde la raiz.

Comandos:

```bash
./orchestrator/start_api.sh
./chat.sh
```

Variables relevantes:

- `ORCHESTRATOR_URL` (default `http://localhost:8001`)
- `RAG_ENGINE_URL` (default `http://localhost:8000`)
