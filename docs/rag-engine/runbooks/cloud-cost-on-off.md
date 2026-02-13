# Runbook: ON/OFF de Servicios Cloud (Costo Bajo Demanda)

Objetivo: operar `cire-rag` en modo pruebas pagando solo cuando usas API/worker.

## Principios

- El costo principal de PaaS viene de computo reservado/activo por servicio.
- API y worker deben gestionarse por separado.
- En pruebas, el worker debe estar apagado por defecto.
- Evitar borrar proyectos para ahorrar costo; preferir pausar/suspender/escalar.

## Configuracion recomendada

- API: Docker target `api_image`.
- Worker: Docker target `worker_cloud_image`.
- Variables comunes: `JINA_MODE=CLOUD`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`.
- API: `UVICORN_WORKERS=1`, `UVICORN_RELOAD=false`.

## Flujo operativo (pruebas)

1. Encender API.
2. Validar `GET /health`.
3. Encender worker solo al procesar ingestas.
4. Ejecutar prueba (ingesta/retrieval).
5. Apagar worker al terminar.
6. Si no seguiras probando, apagar API.

## Checklist de encendido

- API en estado activo.
- Endpoint `/health` responde 200.
- Variables criticas cargadas (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `JINA_API_KEY`).
- Worker activo solo si hay jobs pendientes.

## Checklist de apagado

- `job_queue` sin trabajos criticos en `processing`.
- Worker suspendido/escalado a 0.
- API suspendida/escalada a 0 si no hay pruebas activas.
- Confirmar que no quedan despliegues temporales innecesarios.

## Notas por plataforma

- Railway/Render: usar controles de pausa/suspend/replicas del servicio segun plan.
- No asumir autosuspend/scale-to-zero sin validarlo en el plan actual.
- Mantener budget alerts habilitados en la plataforma.

## Riesgos comunes

- Dejar worker encendido 24/7 sin jobs.
- Usar imagen pesada (`worker_image`) en cloud cuando `worker_cloud_image` es suficiente.
- Activar `--reload` en produccion/pruebas cloud.

## Verificacion semanal (5 minutos)

- Revisar uptime de API/worker.
- Revisar consumo RAM promedio por servicio.
- Revisar costo acumulado mensual.
- Ajustar recursos (RAM/CPU) segun uso real.
