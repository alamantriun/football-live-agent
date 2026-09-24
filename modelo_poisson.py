"""Poisson independiente. Intervalos condicionales a tasas, no xG por tiro."""
import math
import random
from collections import Counter

def numero(valor, nombre, maximo=1000):
    valor = float(valor)
    if not math.isfinite(valor) or not 0 <= valor <= maximo:
        raise ValueError(f"{nombre} fuera de rango")
    return valor

def pmf(tasa):
    tasa = numero(tasa, "tasa", 30)
    valores = [math.exp(-tasa)]
    while sum(valores) < 1 - 1e-12:
        valores.append(valores[-1] * tasa / len(valores))
    total = sum(valores)
    return [p / total for p in valores]

def predecir(local, visitante, marcador=None, seed=42, muestras=250):
    """Probabilidades analíticas; muestreo solamente para visualización."""
    local, visitante = numero(local, "local", 30), numero(visitante, "visitante", 30)
    pl, pv = pmf(local), pmf(visitante)
    marcador = marcador or {"local": 0, "visitante": 0}
    gl, gv = (numero(marcador[k], k, 100) for k in ("local", "visitante"))
    if gl != int(gl) or gv != int(gv):
        raise ValueError("El marcador debe contener enteros")
    gl, gv = int(gl), int(gv)
    celdas = [(i + gl, j + gv, a * b) for i, a in enumerate(pl) for j, b in enumerate(pv)]
    prob = lambda condicion: 100 * sum(p for i, j, p in celdas if condicion(i, j))
    top = sorted(celdas, key=lambda c: (-c[2], c[0], c[1]))[:8]
    hist = {}
    for i, j, p in celdas:
        hist[i + j] = hist.get(i + j, 0) + p
    def cuantil(q):
        acumulado = 0
        for g, p in sorted(hist.items()):
            acumulado += p
            if acumulado >= q:
                return g
        return max(hist)
    rng = random.Random(seed)
    sl = rng.choices(range(len(pl)), weights=pl, k=muestras)
    sv = rng.choices(range(len(pv)), weights=pv, k=muestras)
    hist_muestras = Counter(i + gl + j + gv for i, j in zip(sl, sv))
    limite = max(6, gl + 1, gv + 1)
    matriz = [[0.0] * (limite + 1) for _ in range(limite + 1)]
    conteos = [[0] * (limite + 1) for _ in range(limite + 1)]
    for i, j, p in celdas:
        matriz[min(i, limite)][min(j, limite)] += p * 100
    for i, j in zip(sl, sv):
        conteos[min(i + gl, limite)][min(j + gv, limite)] += 1
    total = local + visitante
    alguno = -math.expm1(-total)
    return {
        "prob_1x2_local": prob(lambda i, j: i > j),
        "prob_1x2_empate": prob(lambda i, j: i == j),
        "prob_1x2_visitante": prob(lambda i, j: i < j),
        **{f"prob_over_{n}_5": prob(lambda i, j: i + j > n + .5) for n in (1, 2, 3)},
        "prob_btts": prob(lambda i, j: i > 0 and j > 0),
        "prob_prox_gol_local": -math.expm1(-local) * 100,
        "prob_prox_gol_visitante": -math.expm1(-visitante) * 100,
        "prob_proximo_gol_local": alguno * local / total * 100 if total else 0.0,
        "prob_proximo_gol_visitante": alguno * visitante / total * 100 if total else 0.0,
        "prob_sin_mas_goles": math.exp(-total) * 100,
        "marcador_mas_probable": f"{top[0][0]}-{top[0][1]}",
        "top_marcadores": [{"marcador": f"{i}-{j}", "prob": p * 100} for i, j, p in top],
        "ic95_goles_totales": {"min": cuantil(.025), "max": cuantil(.975)},
        "tipo_intervalo": "predictivo_condicional_95",
        "goles_esperados_local": gl + local,
        "goles_esperados_visitante": gv + visitante,
        "hist_goles_totales": {str(g): hist_muestras[g] for g in range(max(hist_muestras, default=0) + 1)},
        "prob_goles_totales": {str(g): p * 100 for g, p in sorted(hist.items())},
        "simulaciones": [{"local": i + gl, "visitante": j + gv} for i, j in zip(sl, sv)],
        "matriz_marcadores": {"max_goles": limite, "ultima_categoria": f"{limite}+", "conteos": conteos, "probabilidades": matriz, "total_simulaciones": muestras},
        "n_iteraciones": muestras,
        "metodo": "Poisson independiente analítico",
        "validacion": "experimental; precisión no certificada",
    }
