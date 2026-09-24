import math
from datetime import date, timedelta
import pytest
from modelo_poisson import predecir
from simulador import correr
from modelo_historico import estimar
from evaluar_modelo import puntuaciones


def test_probabilidades_coherentes():
    p = predecir(1.5, 1.2)
    assert sum(p[f"prob_1x2_{k}"] for k in ("local", "empate", "visitante")) == pytest.approx(100)
    assert sum(p[k] for k in ("prob_proximo_gol_local", "prob_proximo_gol_visitante", "prob_sin_mas_goles")) == pytest.approx(100)
    assert p["prob_btts"] == pytest.approx(100 * (1-math.exp(-1.5)) * (1-math.exp(-1.2)))
    assert p["prob_over_1_5"] >= p["prob_over_2_5"] >= p["prob_over_3_5"]
    assert sum(map(sum, p["matriz_marcadores"]["probabilidades"])) == pytest.approx(100)
    assert p["top_marcadores"][0]["marcador"] == p["marcador_mas_probable"]
    assert sum(p["hist_goles_totales"].values()) == p["n_iteraciones"]


def test_probabilidades_no_dependen_de_semilla():
    a, b = predecir(2, 1, seed=1), predecir(2, 1, seed=99)
    for k in a:
        if k.startswith("prob_"):
            assert a[k] == b[k]


@pytest.mark.parametrize("valor", [-1, float("nan"), float("inf"), 100])
def test_tasas_invalidas(valor):
    with pytest.raises(ValueError):
        predecir(valor, 1)


def test_fin_partido_y_descuento_explicito():
    ev = [{"minuto": 90}]
    p = correr(ev, 90, {"local": 2, "visitante": 1})
    assert p["prob_1x2_local"] == 100
    assert p["prob_sin_mas_goles"] == 100
    assert p["tiempo_restante"] == 0
    assert correr(ev, 90, {"local": 2, "visitante": 1}, duracion=95)["prob_sin_mas_goles"] < 100


def test_cero_tiros_puerta_no_es_dato_ausente():
    ev = {"minuto": 30, "tiros": {"local": 15, "visitante": 0}, "tiros_puerta": {"local": 0, "visitante": 0}}
    a = correr([ev], 30, {"local": 0, "visitante": 0})
    del ev["tiros_puerta"]
    b = correr([ev], 30, {"local": 0, "visitante": 0})
    assert a["goles_esperados_local"] < b["goles_esperados_local"]


def test_ignora_datos_futuros_y_orden():
    a, b = {"minuto": 20}, {"minuto": 90, "tiros_local": 100}
    assert correr([a, b], 20, {"local": 0, "visitante": 0}) == correr([a], 20, {"local": 0, "visitante": 0})


def test_historico_sin_fuga_del_dia_o_futuro():
    corte = date(2024, 8, 1)
    hist = [dict(fecha=corte-timedelta(days=i+1), local="A", visita="B", gl=2, gv=1) for i in range(110)]
    esperado = estimar(hist, "A", "B", corte)
    hist += [dict(fecha=corte+timedelta(days=i), local="A", visita="B", gl=99, gv=0) for i in range(3)]
    assert estimar(hist, "A", "B", corte) == esperado


def test_score_penaliza_error_confiado():
    assert puntuaciones([.99, .005, .005], 1)["log_loss"] > puntuaciones([1/3]*3, 1)["log_loss"]


def test_presion_independiente_del_polling():
    import pandas as pd
    from motor_metricas import calcular_riesgo_gol_dinamico
    filas = [dict(minuto=20, tiros_local=2, ataques_peligrosos_local=2), dict(minuto=25, tiros_local=5, ataques_peligrosos_local=5)]
    assert calcular_riesgo_gol_dinamico(pd.DataFrame(filas), True) == calcular_riesgo_gol_dinamico(pd.DataFrame([filas[0]]*30+[filas[1]]), True)


def test_extractor_distingue_ausencia_de_cero():
    from extractor_365scores import _parsear_estadisticas
    assert _parsear_estadisticas(None, 1, 2)["tiros_puerta"]["local"] is None
    stats = {"statistics": [{"name": "shots on target", "competitorId": 1, "value": "0"}]}
    assert _parsear_estadisticas(stats, 1, 2)["tiros_puerta"]["local"] == 0


def test_previo_una_sola_distribucion():
    from predictor_previo import simular_previo
    p = simular_previo(0, 0, 2, 1, 0, 0, 0, 0, 0, 0)
    q = predecir(2, 1, muestras=5000)
    assert p == q
