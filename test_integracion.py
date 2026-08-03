"""
test_integracion.py — Test de integración del sistema completo.

Mockea extractor_vivo.iniciar() con un generador que emite 360 MetricsEvents
acelerando el flujo para verificar el pipeline completo.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import main_agente
import motor_metricas
import simulador


def _generar_evento(minuto: float, at_local: int = 4, at_visit: int = 1) -> dict:
    """Genera un GameEvent de prueba."""
    return {
        "timestamp": "2025-06-15T22:00:00Z",
        "minuto": minuto,
        "marcador": {"local": 0, "visitante": 0},
        "posesion": {"local": 60.0, "visitante": 40.0},
        "tiros": {"local": 3, "visitante": 1},
        "ataques_peligrosos": {"local": at_local, "visitante": at_visit},
        "acciones": [],
    }


async def _mock_extractor(raw_queue: asyncio.Queue) -> None:
    """
    Mock del extractor que emite 360 eventos:
    - 180 para minutos 0-22.4 (Cuarto 1)
    - 180 para minutos 22.5-44.9 (Cuarto 2)
    Sin delay (test acelerado).
    """
    # Cuarto 1: minutos 0.0 a 22.375 (step ≈ 0.125)
    for i in range(180):
        minuto = (i / 180) * 22.5
        evento = _generar_evento(minuto, at_local=4, at_visit=1)
        await raw_queue.put(evento)

    # Cuarto 2: minutos 22.5 a 44.875
    for i in range(180):
        minuto = 22.5 + (i / 180) * 22.5
        evento = _generar_evento(minuto, at_local=3, at_visit=2)
        await raw_queue.put(evento)


@pytest.mark.asyncio
async def test_flujo_completo():
    """
    Corre el pipeline completo con extractor mockeado.
    Verifica:
    - Sin excepciones no capturadas
    - simulador.correr fue llamado al menos 1 vez
    - metrics_queue fue drenada completamente
    """
    # Reset estado global
    main_agente.raw_queue = asyncio.Queue(maxsize=100)
    main_agente.metrics_queue = asyncio.Queue(maxsize=100)
    main_agente.buffer_q1 = []
    main_agente.ultimo_metrics = {}
    main_agente.prediccion_actual = {}
    main_agente.cuarto_actual = 1

    raw_q = main_agente.raw_queue
    met_q = main_agente.metrics_queue

    simulador_mock = MagicMock(wraps=simulador.correr)

    async def run_pipeline():
        # Extractor mockeado
        extractor_task = asyncio.create_task(_mock_extractor(raw_q))

        # Motor de métricas real
        motor_task = asyncio.create_task(
            motor_metricas.iniciar(raw_q, met_q)
        )

        # Recolector de métricas
        recolector_task = asyncio.create_task(
            main_agente.tarea_recolector_metricas()
        )

        # Simulador con monitoreo
        async def tarea_sim_mock():
            """Versión simplificada del simulador para test."""
            ultimo_min = -1.0
            while True:
                await asyncio.sleep(0.1)
                minuto = main_agente.ultimo_metrics.get("minuto")
                if minuto is None:
                    continue
                minuto = float(minuto)
                if (minuto >= 22.5 and ultimo_min < 22.5
                        and len(main_agente.buffer_q1) >= simulador.MIN_EVENTOS_REQUERIDOS):
                    marcador = main_agente.ultimo_metrics.get(
                        "marcador", {"local": 0, "visitante": 0}
                    )
                    main_agente.prediccion_actual = simulador_mock(
                        main_agente.buffer_q1, minuto, marcador
                    )
                    main_agente.buffer_q1 = []
                    main_agente.cuarto_actual += 1
                ultimo_min = minuto

        sim_task = asyncio.create_task(tarea_sim_mock())

        # Esperar a que el extractor termine de emitir
        await extractor_task

        # Dar tiempo al pipeline para procesar
        await asyncio.sleep(2.0)

        # Cancelar tareas de loop infinito
        motor_task.cancel()
        recolector_task.cancel()
        sim_task.cancel()

        for t in [motor_task, recolector_task, sim_task]:
            try:
                await t
            except asyncio.CancelledError:
                pass

    # Ejecutar con timeout
    await asyncio.wait_for(run_pipeline(), timeout=15.0)

    # Verificaciones
    assert simulador_mock.call_count >= 1, (
        "simulador.correr debería haberse llamado al menos 1 vez"
    )

    assert met_q.qsize() == 0, (
        f"metrics_queue debería estar vacía, tiene {met_q.qsize()} items"
    )


@pytest.mark.asyncio
async def test_motor_procesa_todos_los_eventos():
    """Verifica que el motor procesa eventos sin perder ninguno."""
    rq = asyncio.Queue()
    mq = asyncio.Queue()

    n_eventos = 50
    for i in range(n_eventos):
        await rq.put(_generar_evento(minuto=i * 0.5))

    task = asyncio.create_task(motor_metricas.iniciar(rq, mq))

    # Esperar procesamiento
    await asyncio.sleep(2.0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert mq.qsize() == n_eventos, (
        f"Se esperaban {n_eventos} MetricsEvents, se obtuvieron {mq.qsize()}"
    )
