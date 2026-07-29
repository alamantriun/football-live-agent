"""
simulador.py — Simulador Monte Carlo para predicciones de Cuarto 2.

Recibe una lista de MetricsEvents del Cuarto 1 y devuelve predicciones
probabilísticas para el Cuarto 2 usando simulación Monte Carlo con
distribuciones Poisson (ataques) y Binomial (conversión a goles).

Puro cálculo: sin I/O, sin async, sin red.
"""

import logging
from collections import Counter

import numpy as np

logging.basicConfig(format="[%(asctime)s] [%(name)s] %(message)s")
logger = logging.getLogger("simulador")
logger.setLevel(logging.DEBUG)

# ─────────────────────────── CONSTANTES ───────────────────────────
N_ITERACIONES = 250
DURACION_CUARTO = 22.5
MIN_CUARTO1 = 0.0
MAX_CUARTO1 = 22.5
P_GOL_POR_ATAQUE = 0.12
MIN_EVENTOS_REQUERIDOS = 2
TASA_MAX_POR_MINUTO = 0.12
P_GOL_POR_TIRO_PUERTA = 0.18
SEED = 42


def _extraer_campo(evento: dict, campo: str) -> float:
    """Extrae campo aplanado o anidado."""
    if campo in evento:
        val = evento[campo]
        return float(val) if val is not None else 0.0
    partes = campo.rsplit("_", 1)
    if len(partes) == 2:
        padre, hijo = partes
        if padre in evento and isinstance(evento[padre], dict):
            val = evento[padre].get(hijo)
            return float(val) if val is not None else 0.0
    return 0.0


def _matriz_marcadores(goles_l, goles_v, max_goles: int = 6) -> dict:
    """Construye mapa de calor de marcadores finales simulados."""
    matriz = [[0 for _ in range(max_goles + 1)] for _ in range(max_goles + 1)]
    for l, v in zip(goles_l, goles_v):
        li, vi = min(int(l), max_goles), min(int(v), max_goles)
        matriz[li][vi] += 1
    total = max(1, len(goles_l))
    return {
        "max_goles": max_goles,
        "conteos": matriz,
        "probabilidades": [[round(c / total * 100, 1) for c in fila] for fila in matriz],
        "total_simulaciones": int(total),
    }


def correr(eventos_recientes: list[dict], minuto_actual: float, marcador_actual: dict, seed: int | None = SEED) -> dict:
    """
    Ejecuta simulación Monte Carlo para predecir el resto del partido.

    Raises:
        ValueError: Si hay menos de MIN_EVENTOS_REQUERIDOS eventos.
    """
    if len(eventos_recientes) < MIN_EVENTOS_REQUERIDOS:
        raise ValueError(
            f"Se requieren al menos {MIN_EVENTOS_REQUERIDOS} eventos, "
            f"se recibieron {len(eventos_recientes)}"
        )

    # Filtrar eventos válidos del buffer
    filtrados = [
        ev for ev in eventos_recientes
        if ev.get("minuto") is not None
    ]

    if not filtrados:
        raise ValueError("No hay eventos válidos en el buffer")

    # Obtener eventos más recientes para calcular la tasa por minuto
    ultimo_evento = filtrados[-1]
    minuto_ref = max(15.0, float(ultimo_evento.get("minuto", minuto_actual) or minuto_actual))
    if minuto_ref <= 0:
        minuto_ref = max(15.0, float(minuto_actual))

    tiros_puerta_l = _extraer_campo(ultimo_evento, "tiros_puerta_local")
    tiros_puerta_v = _extraer_campo(ultimo_evento, "tiros_puerta_visitante")
    if tiros_puerta_l <= 0:
        tiros_puerta_l = _extraer_campo(ultimo_evento, "tiros_local")
    if tiros_puerta_v <= 0:
        tiros_puerta_v = _extraer_campo(ultimo_evento, "tiros_visitante")

    tasa_local = min(TASA_MAX_POR_MINUTO, tiros_puerta_l / minuto_ref)
    tasa_visitante = min(TASA_MAX_POR_MINUTO, tiros_puerta_v / minuto_ref)

    logger.info("Tasas — local: %.2f, visitante: %.2f", tasa_local, tasa_visitante)

    # Simulación del tiempo restante
    tiempo_restante = max(1.0, 90.0 - minuto_actual)
    rng = np.random.default_rng(seed)
    
    # Tiros a puerta esperados en el tiempo restante (Poisson)
    tiros_rest_l = rng.poisson(tasa_local * tiempo_restante, size=N_ITERACIONES)
    tiros_rest_v = rng.poisson(tasa_visitante * tiempo_restante, size=N_ITERACIONES)

    goles_nuevos_l = rng.binomial(tiros_rest_l, P_GOL_POR_TIRO_PUERTA)
    goles_nuevos_v = rng.binomial(tiros_rest_v, P_GOL_POR_TIRO_PUERTA)

    # P(al menos 1 gol en el resto) — independientes por equipo
    prob_gol_l = float(np.mean(goles_nuevos_l >= 1) * 100)
    prob_gol_v = float(np.mean(goles_nuevos_v >= 1) * 100)

    # Próximo gol — resultados mutuamente excluyentes (suman 100%)
    ninguno = (goles_nuevos_l == 0) & (goles_nuevos_v == 0)
    solo_l = (goles_nuevos_l >= 1) & (goles_nuevos_v == 0)
    solo_v = (goles_nuevos_v >= 1) & (goles_nuevos_l == 0)
    ambos = (goles_nuevos_l >= 1) & (goles_nuevos_v >= 1)
    total_goles = goles_nuevos_l + goles_nuevos_v
    frac_local = np.where(ambos, goles_nuevos_l / np.maximum(total_goles, 1), 0.0)
    frac_visit = np.where(ambos, goles_nuevos_v / np.maximum(total_goles, 1), 0.0)
    prob_proximo_local = float(np.mean(solo_l) * 100 + np.mean(frac_local) * 100)
    prob_proximo_visit = float(np.mean(solo_v) * 100 + np.mean(frac_visit) * 100)
    prob_sin_mas_goles = float(np.mean(ninguno) * 100)

    # Resultados FINALES de partido (sumando el marcador actual)
    goles_actuales_l = marcador_actual.get("local", 0)
    goles_actuales_v = marcador_actual.get("visitante", 0)
    
    goles_finales_l = goles_nuevos_l + goles_actuales_l
    goles_finales_v = goles_nuevos_v + goles_actuales_v

    # Probabilidades de resultado 1X2 (Gana Local, Empate, Gana Visita)
    prob_gana_local = float(np.mean(goles_finales_l > goles_finales_v) * 100)
    prob_empate = float(np.mean(goles_finales_l == goles_finales_v) * 100)
    prob_gana_visitante = float(np.mean(goles_finales_l < goles_finales_v) * 100)

    # Over/Under
    goles_totales_finales = goles_finales_l + goles_finales_v
    prob_over_2_5 = float(np.mean(goles_totales_finales > 2.5) * 100)
    prob_over_3_5 = float(np.mean(goles_totales_finales > 3.5) * 100)

    marcadores = list(zip(goles_finales_l.tolist(), goles_finales_v.tolist()))
    contador_marcadores = Counter(marcadores)
    moda = contador_marcadores.most_common(1)[0][0]

    ic_min = int(np.percentile(goles_totales_finales, 2.5))
    ic_max = int(np.percentile(goles_totales_finales, 97.5))

    # Top 8 marcadores más probables para gráficas
    top_marcadores = [
        {"marcador": f"{m[0]}-{m[1]}", "prob": round(c / N_ITERACIONES * 100, 1)}
        for m, c in contador_marcadores.most_common(8)
    ]

    # Histograma de goles totales (0-8)
    hist_goles = {}
    for g in range(0, 9):
        hist_goles[str(g)] = int(np.sum(goles_totales_finales == g))

    return {
        "prob_prox_gol_local": prob_gol_l,
        "prob_prox_gol_visitante": prob_gol_v,
        "prob_proximo_gol_local": round(prob_proximo_local, 1),
        "prob_proximo_gol_visitante": round(prob_proximo_visit, 1),
        "prob_sin_mas_goles": round(prob_sin_mas_goles, 1),
        "prob_1x2_local": prob_gana_local,
        "prob_1x2_empate": prob_empate,
        "prob_1x2_visitante": prob_gana_visitante,
        "prob_over_2_5": prob_over_2_5,
        "prob_over_3_5": prob_over_3_5,
        "prob_over_1_5": float(np.mean(goles_totales_finales > 1.5) * 100),
        "prob_btts": float(np.mean((goles_finales_l >= 1) & (goles_finales_v >= 1)) * 100),
        "marcador_mas_probable": f"{moda[0]}-{moda[1]}",
        "ic95_goles_totales": {"min": ic_min, "max": ic_max},
        "tiempo_restante": tiempo_restante,
        "tasa_ataques_local": round(tasa_local, 3),
        "tasa_ataques_visitante": round(tasa_visitante, 3),
        "top_marcadores": top_marcadores,
        "hist_goles_totales": hist_goles,
        "goles_esperados_local": round(float(np.mean(goles_finales_l)), 2),
        "goles_esperados_visitante": round(float(np.mean(goles_finales_v)), 2),
        "simulaciones": [
            {"local": int(l), "visitante": int(v)}
            for l, v in zip(goles_finales_l.tolist(), goles_finales_v.tolist())
        ],
        "matriz_marcadores": _matriz_marcadores(goles_finales_l, goles_finales_v),
        "n_iteraciones": N_ITERACIONES,
    }


if __name__ == "__main__":
    import json
    eventos = [
        {"minuto": i * 0.5, "ataques_peligrosos_local": 4,
         "ataques_peligrosos_visitante": 1}
        for i in range(45)
    ]
    print(json.dumps(correr(eventos, 22.0, {"local": 0, "visitante": 0}), indent=2))
