# Operación en producción

## Límites de la afirmación de rendimiento

La evaluación prepartido histórica es un antecedente, no una validación del modelo en vivo. La página `/modelo` conserva el estado **Evidencia live todavía en recolección** hasta que haya partidos y resultados confirmados suficientes. Ninguna operación debe describir una precisión live como certificada antes de una evaluación cronológica reproducible.

## Despliegue seguro

1. Ejecutar `python -m compileall football_live api` y `python -m pytest -q`.
2. Revisar que `vercel.json` conserve CSP, `nosniff`, `frame-ancestors 'none'` y las rutas públicas.
3. Configurar solamente en Vercel, para Preview y Production: `ENVIRONMENT`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `CRON_SECRET`, `ALLOWED_HOSTS`, `PUBLIC_ORIGIN`.
4. Desplegar Preview y ejecutar `python scripts/smoke_preview.py --base-url <preview-url>`.
5. Verificar `/`, `/app`, `/modelo`, `/api/health`, `/api/live` y `/api/model/status`; un estado de modelo `503 unavailable` es válido mientras no exista un modelo activo.
6. Promover únicamente una Preview verificada y esperar el estado terminal `Ready`.

Nunca registrar ni copiar en documentos, issues, capturas o Git la clave de servicio de Supabase ni `CRON_SECRET`.

## Trabajos programados

Los trabajos internos son `discover`, `collect`, `settle` y `train`. Cada llamada exige `X-Cron-Secret` y `X-Idempotency-Key`; la base de datos conserva el lease y la auditoría de `private.job_runs`.

La programación de Supabase sólo se habilita después de que la URL final de Production y el secreto se hayan guardado en Vault. Las frecuencias previstas son: descubrimiento cada dos minutos, recolección cada minuto, liquidación cada cinco minutos y entrenamiento diario. Si la observación inicial falla, pausar los schedules antes de reintentar.

## Observación y reversión

- Revisar resultados de trabajos sin imprimir payloads del proveedor ni cabeceras.
- Repetir una misma clave de idempotencia debe devolver un resultado duplicado sin duplicar snapshots.
- La proyección pública no puede incluir IDs internos ni payload proveedor sin sanitizar.
- Si un despliegue falla, promover el despliegue Vercel anterior; no ejecutar un rollback destructivo de base de datos.
- Si un modelo activo debe retirarse, promover el modelo previamente validado mediante la operación transaccional de base de datos, no mediante redeploy de código.

## Rotación

Para rotar `CRON_SECRET`, generar un nuevo valor en el gestor de secretos, actualizar Vercel y Supabase Vault de forma coordinada y validar una llamada interna autorizada antes de retirar el valor anterior. Para la clave de servicio de Supabase, crear/revocar según el panel de Supabase, sustituirla sólo en Vercel y volver a desplegar. En ambos casos, los valores nunca deben pasar por el cliente web.
