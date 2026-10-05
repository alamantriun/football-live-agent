# Analítica en vivo: publicación del 5 de octubre de 2026

## Versión publicada

- Código: `019f5b0`, rama `codex/football-live-product`, subida a GitHub.
- Vercel: `dpl_GwXqMGEKNJ6YyxGsDuUuZ485bC6r`, estado `READY`, destino `production`.
- Panel: https://football-live-agent.vercel.app/app
- Supabase: migraciones `publish_live_predictions` y `add_match_history_predictions` aplicadas.

## Verificación

- Python: 233 pruebas aprobadas; un aviso preexistente de deprecación de Starlette/httpx.
- Interfaz: 16 pruebas de comportamiento aprobadas, incluyendo cancelación, cambio de partido, reintento y fallo de actualización.
- Base de datos: 10 comprobaciones pgTAP aprobadas en una transacción revertida, sin dejar fixtures.
- Navegador de producción: FK Smederevo–Vozdovac mostró 90 observaciones, gráficas, estadísticas, actividad, momentum y escenarios. Los goles esperados ausentes se indicaron como no disponibles.
- La actualización automática renovó el minuto, las probabilidades y el historial. El cambio a Panamá–Nueva Zelanda reemplazó la lectura; los escenarios se ocultaron con datos antiguos.
- Escritorio: ancho de viewport 1280, ancho del documento 1265; sin desplazamiento horizontal. Sin errores o advertencias de consola observados.
- Registros de producción: consultas de fixture, snapshots y RPC con HTTP 200; recolector con escrituras correctas. No se observaron credenciales ni errores en las solicitudes revisadas.
- Prueba visual móvil pendiente: la capacidad de viewport aceptó 390×844 pero el navegador conservó 1280 px, incluso en una pestaña nueva. No se declara comprobación visual móvil. El CSS responsive y el redibujado están cubiertos por pruebas automatizadas.

## Límites y seguridad

El historial devuelve como máximo 90 observaciones del UUID público seleccionado. La función privada de predicciones sólo permite ejecución al backend con `service_role`; el cliente recibe campos explícitamente permitidos. No se incorporaron secretos al repositorio. Los datos ausentes no se convierten en ceros y la pérdida de frescura oculta los escenarios sin borrar el historial.

Actividad, momentum, próximo gol, totales y marcadores son escenarios experimentales. Esta publicación no demuestra precisión predictiva: la promoción de modelos debe mantener la evaluación cronológica y los comparadores existentes.

## Decisiones de implementación

1. Se conservó el aviso de dependencia preexistente: no afecta estas pruebas; el coste de una interpretación incorrecta sería una migración posterior de TestClient.
2. Push y producción se ejecutaron dentro de la autorización previa del propietario; el coste de revertir es restaurar la versión anterior de Vercel y revertir commits, sin forzar la rama.
3. La lectura interna utiliza estado y versión del modelo, pero sólo expone la versión segura; el coste de mantenimiento es ajustar relaciones si cambia el esquema.
4. Se sustituyó el límite global de predicciones por una RPC privada que prioriza el modelo activo por snapshot y usa la predicción más reciente como alternativa. El coste es mantener una función SQL adicional y su migración.
