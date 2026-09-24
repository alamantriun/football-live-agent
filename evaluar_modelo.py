"""Evaluación walk-forward diaria. Ej.: python evaluar_modelo.py data/*.csv."""
import argparse
import glob
import hashlib
import json
import math
from datetime import date
from modelo_historico import cargar, estimar
from modelo_poisson import predecir


def vector(pred):
    return [pred[f"prob_1x2_{k}"] / 100 for k in ("local", "empate", "visitante")]


def puntuaciones(p, y):
    return {"log_loss": -math.log(max(p[y], 1e-15)),
            "brier": sum((v - int(i == y)) ** 2 for i, v in enumerate(p)),
            "acierto_1x2": float(max(range(3), key=lambda i: p[i]) == y)}


def evaluar(partidos, desde):
    registros = {k: [] for k in ("modelo", "frecuencias", "poisson_liga")}
    exactos, cobertura, calibracion, diferencias = [], [], [], []
    for partido in partidos:
        if partido["fecha"] < desde:
            continue
        pasado = [p for p in partidos if 0 < (partido["fecha"] - p["fecha"]).days <= 730]
        tl, tv, _ = estimar(pasado, partido["local"], partido["visita"], partido["fecha"])
        pred = predecir(tl, tv, muestras=0)
        actual = 0 if partido["gl"] > partido["gv"] else 1 if partido["gl"] == partido["gv"] else 2
        conteos = [1., 1., 1.]
        for p in pasado:
            conteos[0 if p["gl"] > p["gv"] else 1 if p["gl"] == p["gv"] else 2] += 1
        liga = predecir(sum(p["gl"] for p in pasado) / len(pasado), sum(p["gv"] for p in pasado) / len(pasado), muestras=0)
        vectores = {"modelo": vector(pred), "frecuencias": [c / sum(conteos) for c in conteos], "poisson_liga": vector(liga)}
        for nombre, v in vectores.items():
            registros[nombre].append(puntuaciones(v, actual))
        diferencias.append(registros["modelo"][-1]["log_loss"] - registros["frecuencias"][-1]["log_loss"])
        exactos.append(pred["marcador_mas_probable"] == f"{partido['gl']}-{partido['gv']}")
        ic = pred["ic95_goles_totales"]
        cobertura.append(ic["min"] <= partido["gl"] + partido["gv"] <= ic["max"])
        favorito = max(range(3), key=lambda i: vectores["modelo"][i])
        calibracion.append((vectores["modelo"][favorito], favorito == actual))
    n = len(exactos)
    if not n:
        raise ValueError("Sin partidos en evaluación")
    medias = {k: {m: sum(r[m] for r in filas) / n for m in filas[0]} for k, filas in registros.items()}
    bins = []
    for i in range(10):
        grupo = [(p, y) for p, y in calibracion if i / 10 <= p < (i + 1) / 10 or i == 9 and p == 1]
        if grupo:
            bins.append({"desde": i / 10, "n": len(grupo), "prob_media": sum(p for p, y in grupo) / len(grupo), "frecuencia": sum(y for p, y in grupo) / len(grupo)})
    return {"n": n, "desde": desde.isoformat(), "metricas": medias,
            "acierto_marcador_exacto": sum(exactos) / n,
            "cobertura_intervalo_condicional_95": sum(cobertura) / n,
            "calibracion_favorito": bins,
            "delta_log_loss_vs_frecuencias": sum(diferencias) / n,
            "nota": "Evaluación prepartido retrospectiva; no valida el modelo en vivo. Menor log_loss y Brier es mejor. Sin selección de hiperparámetros sobre estos resultados."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="+")
    parser.add_argument("--desde", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    paths = sorted({p for patron in args.csv for p in glob.glob(patron)})
    if not paths:
        parser.error("No se encontraron CSV")
    informe = evaluar(cargar(paths), args.desde)
    informe["fuentes_sha256"] = {}
    for path in paths:
        with open(path, "rb") as f:
            informe["fuentes_sha256"][path] = hashlib.sha256(f.read()).hexdigest()
    print(json.dumps(informe, indent=2, ensure_ascii=False))
