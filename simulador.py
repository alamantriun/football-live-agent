"""Predicción en vivo con tasas regularizadas; coeficientes aún sin calibrar."""
import math
from modelo_poisson import numero, predecir
SEED = 42
MIN_EVENTOS_REQUERIDOS = 1
N_ITERACIONES = 250

def _campo(evento, nombre, equipo):
    anidado = evento.get(nombre)
    valor = evento.get(f"{nombre}_{equipo}", anidado.get(equipo)
                       if isinstance(anidado, dict) else None)
    return None if valor is None else numero(valor, nombre)

def correr(eventos_recientes, minuto_actual, marcador_actual, seed=SEED,
           *, duracion=90.0, prior_local=1.5, prior_visitante=1.2, ajuste=None):
    minuto = numero(minuto_actual, "minuto", 150)
    duracion = numero(duracion, "duración", 150)
    if not eventos_recientes:
        raise ValueError("Se requieren al menos 1 eventos")
    validos = [ev for ev in eventos_recientes if ev.get("minuto") is not None
               and 0 <= numero(ev["minuto"], "minuto", 150) <= minuto]
    if not validos:
        raise ValueError("No hay eventos válidos en el buffer")
    ev = max(enumerate(validos), key=lambda par: (float(par[1]["minuto"]), par[0]))[1]
    ref = float(ev["minuto"])
    restante = max(0.0, duracion - minuto)
    tasas, fuentes = [], []
    for equipo, prior in (("local", prior_local), ("visitante", prior_visitante)):
        prior = numero(prior, "prior", 15)
        xg = _campo(ev, "xg", equipo)
        puerta = _campo(ev, "tiros_puerta", equipo)
        tiros = _campo(ev, "tiros", equipo)
        if xg is not None:
            evidencia, fuente = xg, "xg_proveedor"
        elif puerta is not None:
            evidencia, fuente = puerta * .30, "tiros_puerta"
        elif tiros is not None:
            evidencia, fuente = tiros * .10, "tiros_totales"
        else:
            evidencia, fuente = None, "prior_sin_estadisticas"
        tasa = prior / 90
        if evidencia is not None and ref > 0:
            tasa = (tasa * 45 + evidencia) / (45 + ref)
        tasas.append(tasa)
        fuentes.append(fuente)
    bases = {"local": tasas[0]*restante, "visitante": tasas[1]*restante}
    ajuste = ajuste or {}
    factores = ajuste.get("factores", {}) if ajuste.get("aprobado") else {}
    multiplicadores = [numero(factores.get(e, 1), "factor aprendido", 1.6) for e in ("local", "visitante")]
    if min(multiplicadores) < .6:
        raise ValueError("Factor aprendido fuera del rango validado")
    tasas = [t * f for t, f in zip(tasas, multiplicadores)]
    resultado = predecir(tasas[0] * restante, tasas[1] * restante,
                         marcador_actual, seed, N_ITERACIONES)
    ventana = min(10.0, restante)
    resultado.update(tiempo_restante=restante, tasa_ataques_local=tasas[0],
                     lambda_base=bases,
                     factores_aplicados=dict(zip(("local", "visitante"), multiplicadores)),
                     modelo_version=ajuste.get("version", "live-poisson-2") if ajuste.get("aprobado") else "live-poisson-2",
                     tasa_ataques_visitante=tasas[1], fuentes_tasa=fuentes,
                     antiguedad_minutos=minuto - ref,
                     minuto_prediccion=minuto, timestamp_datos=ev.get("timestamp"),
                     marcador_observado=dict(marcador_actual),
                     ventana_gol_minutos=ventana,
                     prob_gol_10_min=-math.expm1(-sum(tasas) * ventana) * 100,
                     prob_gol_10_local=-math.expm1(-tasas[0] * ventana) * 100,
                     prob_gol_10_visitante=-math.expm1(-tasas[1] * ventana) * 100,
                     evidencia={"local": fuentes[0], "visitante": fuentes[1],
                                "peso_observado": ref / (45 + ref),
                                "prior_local": prior_local, "prior_visitante": prior_visitante},
                     advertencias=["Tasas en vivo sin calibración histórica",
                                   "Sin ajuste por expulsiones; xG sólo cuando lo informa el proveedor"])
    if any((_campo(ev, "tarjetas_rojas", equipo) or 0) > 0 for equipo in ("local", "visitante")):
        resultado["advertencias"].append("Hay expulsiones: el modelo no ajusta la inferioridad numérica")
    if ajuste.get("aprobado"):
        resultado["advertencias"][0] = "Tasas ajustadas con resultados locales; mejora validada en una muestra limitada"
    return resultado
