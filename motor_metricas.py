"""
motor_metricas.py — Motor de cálculo de métricas en tiempo real.

Lee eventos crudos de raw_queue, calcula métricas derivadas
(riesgo de gol, ánimo) usando un DataFrame de Pandas como buffer
en memoria, y escribe MetricsEvents enriquecidos en metrics_queue.

No toca archivos ni red. Todo en memoria.
"""

import asyncio
import logging

import pandas as pd

logging.basicConfig(format="[%(asctime)s] [%(name)s] %(message)s")
logger = logging.getLogger("motor_metricas")
logger.setLevel(logging.DEBUG)

# ─────────────────────────── CONSTANTES ───────────────────────────
VENTANA_EVENTOS = 180        # máximo de filas en el DataFrame (≈3 min a 1 evt/s)
POLL_INTERVAL_REF = 7.0     # intervalo del extractor API (segundos)
MAX_RATE_ESPERADO = 0.25     # calibración de presión constante por minuto
PROB_CONV_ATAQUE = 0.75      # umbral para bonus de ánimo
BONUS_ANIMO = 1.20           # multiplicador al superar el umbral


def _aplanar_evento(evento: dict) -> dict:
    """
    Aplana los campos anidados del GameEvent para insertarlos en un DataFrame.
    Ejemplo: marcador.local → marcador_local
    """
    plano = {}
    for key, value in evento.items():
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                plano[f"{key}_{sub_key}"] = sub_value
        else:
            plano[key] = value
    return plano


def calcular_deltas_recientes(df_buffer: pd.DataFrame, campo: str) -> float:
    """
    Calcula cuánto ha aumentado un valor acumulativo en la ventana reciente.
    """
    if df_buffer.empty or campo not in df_buffer.columns:
        return 0.0
    
    val_reciente = df_buffer[campo].iloc[-1]
    val_antiguo = df_buffer[campo].iloc[0]
    return max(0.0, float(val_reciente - val_antiguo))


def calcular_riesgo_gol_dinamico(df_buffer: pd.DataFrame, es_local: bool) -> float:
    """
    Calcula el riesgo de gol basándose puramente en la actividad reciente.
    """
    sufijo = "local" if es_local else "visitante"
    
    delta_tiros = calcular_deltas_recientes(df_buffer, f"tiros_{sufijo}")
    delta_ataques = calcular_deltas_recientes(df_buffer, f"ataques_peligrosos_{sufijo}")
    
    # Tiempo transcurrido en el buffer (basado en polling real, no en el reloj del partido)
    minutos_ventana = max(1.0, len(df_buffer) * (POLL_INTERVAL_REF / 60.0))

    tasa_tiros = delta_tiros / minutos_ventana
    tasa_ataques = delta_ataques / minutos_ventana
    
    # Si acaba de arrancar y no hay historial (menos de 2 min reales)
    if minutos_ventana < 2.0 and not df_buffer.empty and "minuto" in df_buffer.columns:
        min_total = max(1.0, df_buffer["minuto"].iloc[-1])
        tasa_tiros = df_buffer[f"tiros_{sufijo}"].iloc[-1] / min_total
        tasa_ataques = df_buffer[f"ataques_peligrosos_{sufijo}"].iloc[-1] / min_total

    componente = (tasa_tiros * 0.40) + (tasa_ataques * 0.60)
    
    # Un equipo muy agresivo podría lograr 0.3 a 0.5 acciones por minuto
    MAX_RATE_ESPERADO = 0.35 
    return min(100.0, max(0.0, (componente / MAX_RATE_ESPERADO) * 100))


def calcular_animo_dinamico(df_buffer: pd.DataFrame) -> tuple[float, float]:
    """
    Calcula el ánimo basado en quién está dominando los ataques recientemente.
    """
    delta_loc = calcular_deltas_recientes(df_buffer, "ataques_peligrosos_local")
    delta_vis = calcular_deltas_recientes(df_buffer, "ataques_peligrosos_visitante")
    
    # Fallback histórico si la ventana es muy corta
    if not df_buffer.empty and df_buffer["minuto"].iloc[-1] - df_buffer["minuto"].iloc[0] < 2.0:
        delta_loc = df_buffer["ataques_peligrosos_local"].iloc[-1]
        delta_vis = df_buffer["ataques_peligrosos_visitante"].iloc[-1]

    acc_total = delta_loc + delta_vis

    if acc_total == 0:
        ratio = 0.5
    else:
        ratio = delta_loc / acc_total

    animo_local = ratio * 100
    if ratio > PROB_CONV_ATAQUE:
        animo_local = min(100.0, animo_local * BONUS_ANIMO)

    animo_visitante = 100.0 - animo_local
    return animo_local, animo_visitante


class MotorMetricas:
    """Encapsula el estado del motor de métricas (df_buffer)."""

    def __init__(self):
        self.df_buffer = pd.DataFrame()

    def agregar_evento(self, evento: dict) -> None:
        """Agrega un evento al buffer y recorta a VENTANA_EVENTOS filas."""
        plano = _aplanar_evento(evento)
        nueva_fila = pd.DataFrame([plano])
        self.df_buffer = pd.concat(
            [self.df_buffer, nueva_fila], ignore_index=True
        )
        if len(self.df_buffer) > VENTANA_EVENTOS:
            self.df_buffer = self.df_buffer.tail(VENTANA_EVENTOS).reset_index(
                drop=True
            )

    def calcular_metricas(self, evento: dict) -> dict:
        """
        Calcula métricas a partir del evento actual y el buffer histórico.
        """
        plano = _aplanar_evento(evento)

        riesgo_local = calcular_riesgo_gol_dinamico(self.df_buffer, es_local=True)
        riesgo_visitante = calcular_riesgo_gol_dinamico(self.df_buffer, es_local=False)

        animo_local, animo_visitante = calcular_animo_dinamico(self.df_buffer)

        metrics_event = dict(evento)
        metrics_event["riesgo_gol_local"] = riesgo_local
        metrics_event["riesgo_gol_visitante"] = riesgo_visitante
        metrics_event["animo_local"] = animo_local
        metrics_event["animo_visitante"] = animo_visitante

        return metrics_event


# ─────────────── Instancia global del motor ───────────────────────
_motor = MotorMetricas()


async def iniciar(raw_queue: asyncio.Queue, metrics_queue: asyncio.Queue) -> None:
    """
    Función principal del motor de métricas.

    Loop infinito:
      1. Lee evento de raw_queue
      2. Valida campos (salta si hay None)
      3. Agrega al buffer
      4. Calcula métricas
      5. Pone MetricsEvent en metrics_queue
    """
    global _motor
    _motor = MotorMetricas()

    logger.info("Motor de métricas iniciado.")

    while True:
        evento = await raw_queue.get()

        # Verificar campos None críticos
        campos_numericos = ["minuto", "tiros", "ataques_peligrosos"]
        tiene_nulos = False
        for campo in campos_numericos:
            val = evento.get(campo)
            if val is None:
                tiene_nulos = True
                break
            if isinstance(val, dict):
                for sub_val in val.values():
                    if sub_val is None:
                        tiene_nulos = True
                        break

        if tiene_nulos:
            logger.warning(
                "Evento con campos None detectado — saltando: %s",
                evento,
            )
            raw_queue.task_done()
            continue

        # Agregar al buffer y calcular métricas
        _motor.agregar_evento(evento)
        metrics_event = _motor.calcular_metricas(evento)

        await metrics_queue.put(metrics_event)
        logger.info(
            "MetricsEvent emitido — minuto: %.1f | riesgo_local: %.1f%% | ánimo_local: %.1f%%",
            metrics_event.get("minuto", 0),
            metrics_event.get("riesgo_gol_local", 0),
            metrics_event.get("animo_local", 0),
        )

        raw_queue.task_done()


if __name__ == "__main__":
    import json

    async def _demo():
        rq = asyncio.Queue()
        mq = asyncio.Queue()

        # Inyectar un evento de ejemplo
        ejemplo = {
            "timestamp": "2025-06-15T22:31:00Z",
            "minuto": 22.5,
            "marcador": {"local": 1, "visitante": 0},
            "posesion": {"local": 62.0, "visitante": 38.0},
            "tiros": {"local": 8, "visitante": 2},
            "ataques_peligrosos": {"local": 12, "visitante": 3},
            "acciones": ["corner_local", "falta_visitante"],
        }
        await rq.put(ejemplo)

        # Ejecutar motor por un breve instante
        task = asyncio.create_task(iniciar(rq, mq))
        await asyncio.sleep(0.5)
        task.cancel()

        if not mq.empty():
            result = await mq.get()
            print(json.dumps(result, indent=2, default=str))

    asyncio.run(_demo())
