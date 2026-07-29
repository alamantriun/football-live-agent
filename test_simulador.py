"""
test_simulador.py — Tests para simulador.py

Verifica la simulación Monte Carlo con datos de dominio local,
rendimiento, y manejo de datos insuficientes.
"""

import time

import pytest

from simulador import MIN_EVENTOS_REQUERIDOS, SEED, correr

MINUTO_REF = 22.0
MARCADOR_REF = {"local": 0, "visitante": 0}


# ─────────────────────── FIXTURES ─────────────────────────────────
@pytest.fixture
def datos_dominio_local() -> list[dict]:
    """
    Genera 45 MetricsEvents (cada 0.5 minutos, minutos 0.0 a 22.0).
    ataques_peligrosos_local=2 → tasa media ≈ 2/22 ataques/min
    ataques_peligrosos_visitante=0 → tasa visitante = 0
    Esto crea dominio local claro.
    """
    eventos = []
    for i in range(45):
        eventos.append({
            "timestamp": f"2025-06-15T22:{i:02d}:00Z",
            "minuto": i * 0.5,
            "marcador_local": 0,
            "marcador_visitante": 0,
            "posesion_local": 65.0,
            "posesion_visitante": 35.0,
            "tiros_local": 3,
            "tiros_visitante": 0,
            "ataques_peligrosos_local": (i + 1) * 2,
            "ataques_peligrosos_visitante": 0,
            "acciones": [],
            "riesgo_gol_local": 45.0,
            "riesgo_gol_visitante": 0.0,
            "animo_local": 100.0,
            "animo_visitante": 0.0,
        })
    return eventos


def _correr(eventos: list[dict], minuto: float = MINUTO_REF, marcador: dict | None = None):
    return correr(eventos, minuto, marcador or MARCADOR_REF, seed=SEED)


# ─────── TEST PROBABILIDADES CON DOMINIO LOCAL ───────────────────
def test_probabilidades_con_dominio_local(datos_dominio_local):
    """
    Con tasa_local>0 y tasa_visitante=0:
    - prob_prox_gol_local debe ser > 70%
    - prob_prox_gol_visitante debe ser < 40%
    """
    resultado = _correr(datos_dominio_local)

    assert resultado["prob_prox_gol_local"] > 70.0, (
        f"prob_prox_gol_local={resultado['prob_prox_gol_local']} debería ser > 70%"
    )
    assert resultado["prob_prox_gol_visitante"] < 40.0, (
        f"prob_prox_gol_visitante={resultado['prob_prox_gol_visitante']} debería ser < 40%"
    )


def test_resultado_tiene_todos_los_campos(datos_dominio_local):
    """Verifica que el resultado contiene todos los campos requeridos."""
    resultado = _correr(datos_dominio_local)

    campos_requeridos = [
        "prob_prox_gol_local",
        "prob_prox_gol_visitante",
        "prob_1x2_local",
        "prob_1x2_empate",
        "prob_1x2_visitante",
        "marcador_mas_probable",
        "ic95_goles_totales",
        "top_marcadores",
        "hist_goles_totales",
    ]
    for campo in campos_requeridos:
        assert campo in resultado, f"Campo faltante: {campo}"

    ic = resultado["ic95_goles_totales"]
    assert "min" in ic
    assert "max" in ic
    assert ic["min"] <= ic["max"]


def test_marcador_formato(datos_dominio_local):
    """Verifica que el marcador más probable tiene formato 'X-Y'."""
    resultado = _correr(datos_dominio_local)
    marcador = resultado["marcador_mas_probable"]
    partes = marcador.split("-")
    assert len(partes) == 2
    assert all(p.strip().isdigit() for p in partes)


# ─────────────── TEST RENDIMIENTO ─────────────────────────────────
def test_rendimiento(datos_dominio_local):
    """La simulación debe completarse en menos de 3 segundos."""
    inicio = time.perf_counter()
    _correr(datos_dominio_local)
    duracion = time.perf_counter() - inicio

    assert duracion < 3.0, (
        f"La simulación tardó {duracion:.2f}s, debería ser < 3.0s"
    )


# ─────────────── TEST ERROR DATOS INSUFICIENTES ──────────────────
def test_error_datos_insuficientes():
    """Lista vacía debe lanzar ValueError."""
    with pytest.raises(ValueError, match="Se requieren al menos"):
        correr([], MINUTO_REF, MARCADOR_REF)


def test_error_eventos_sin_minuto():
    """Eventos sin minuto válido deben lanzar ValueError."""
    invalidos = [{"ataques_peligrosos_local": 1, "ataques_peligrosos_visitante": 1}]
    with pytest.raises(ValueError, match="No hay eventos válidos"):
        correr(invalidos, MINUTO_REF, MARCADOR_REF)


def test_minimo_un_evento_valido():
    """Con MIN_EVENTOS_REQUERIDOS=1, un evento válido es suficiente."""
    uno = [{
        "minuto": 10.0,
        "ataques_peligrosos_local": 2,
        "ataques_peligrosos_visitante": 0,
    }]
    resultado = correr(uno, 10.0, MARCADOR_REF, seed=SEED)
    assert "prob_prox_gol_local" in resultado


# ─────────────── TEST REPRODUCIBILIDAD ────────────────────────────
def test_reproducibilidad(datos_dominio_local):
    """Misma semilla → mismo resultado."""
    r1 = correr(datos_dominio_local, MINUTO_REF, MARCADOR_REF, seed=42)
    r2 = correr(datos_dominio_local, MINUTO_REF, MARCADOR_REF, seed=42)
    assert r1 == r2
