"""Aprendizaje supervisado de tasas con validación cronológica por partido.

Aprende dos multiplicadores de goles restantes con regularización Poisson.
No usa resultados de validación para ajustar parámetros ni reutiliza partidos
ya evaluados como una nueva validación. Conserva la distribución conjunta.
"""
import argparse
import hashlib
import json
import math
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from evaluar_modelo import puntuaciones, vector
from modelo_poisson import predecir
from registro_vivo import DEFAULT_DB, VERSION, abrir_db

MODEL_PATH = DEFAULT_DB.parent / "modelo_vivo.json"
STATE_PATH = DEFAULT_DB.parent / "aprendizaje_estado.json"
MIN_TRAIN, MIN_VALID = 70, 30
BASE = {"version": VERSION, "factores": {"local": 1.0, "visitante": 1.0}, "aprobado": False}


def leer_json(path, default):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else default.copy()
    except (OSError, ValueError):
        return default.copy()


def guardar_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def modelo_activo(path=MODEL_PATH):
    m = leer_json(path, BASE)
    try:
        if m.get("aprobado") is not True or not isinstance(m.get("version"), str):
            return BASE.copy()
        if not all(.6 <= float(m["factores"][e]) <= 1.6 for e in ("local", "visitante")):
            return BASE.copy()
        return m
    except (KeyError, TypeError, ValueError):
        return BASE.copy()


def fecha(valor):
    d = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError("Fecha sin zona horaria")
    return d


def cargar_ejemplos(path=DEFAULT_DB):
    with abrir_db(path) as db:
        filas = db.execute("SELECT p.fixture,p.minuto,p.recibido,p.datos,p.prediccion,f.recibido,f.local,f.visitante FROM pronosticos p JOIN finales f USING(fixture) ORDER BY p.recibido").fetchall()
    grupos = {}
    for fid, minuto, recibido, raw, payload, final_recibido, gl, gv in filas:
        try:
            ev, p = json.loads(raw), json.loads(payload)
            if ev.get("_fuente") != "365scores" or not 15 <= minuto <= 80:
                continue
            t, fin = fecha(recibido), fecha(final_recibido)
            if not t < fin or (fin - t).total_seconds() > 6 * 3600:
                continue
            m = ev["marcador"]
            observados = [float(m["local"]), float(m["visitante"])]
            objetivos = [float(gl), float(gv)]
            if any(not n.is_integer() or not 0 <= n <= 20 for n in observados + objetivos):
                continue
            restantes = [objetivos[i] - observados[i] for i in range(2)]
            if min(restantes) < 0:  # Correcciones de marcador incompatibles.
                continue
            bases = p.get("lambda_base") or {
                "local": float(p["goles_esperados_local"]) - observados[0],
                "visitante": float(p["goles_esperados_visitante"]) - observados[1],
            }
            lambdas = [float(bases[e]) for e in ("local", "visitante")]
            if not all(math.isfinite(v) and 0 < v <= 15 for v in lambdas):
                continue
            # Ventanas fijas: nunca más de cuatro ejemplos por partido.
            ventana = next((c for c in (20, 40, 60, 80) if c-5 <= minuto <= c), None)
            if ventana is None:
                continue
            g = grupos.setdefault(fid, {"fixture": fid, "final": fin, "primero": t, "ejemplos": {}})
            g["primero"] = min(g["primero"], t)
            e = {"minuto": minuto, "timestamp": t.isoformat(), "base": lambdas,
                 "restantes": restantes, "marcador": {k: int(m[k]) for k in ("local", "visitante")},
                 "y": 0 if gl > gv else 1 if gl == gv else 2}
            old = g["ejemplos"].get(ventana)
            if old is None or minuto > old["minuto"]:
                g["ejemplos"][ventana] = e
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    return sorted(grupos.values(), key=lambda g: (g["primero"], g["fixture"]))


def dividir(grupos, consumidos):
    nuevos = [g for g in grupos if g["fixture"] not in consumidos]
    if len(nuevos) < MIN_VALID:
        return [], []
    valid = nuevos[-MIN_VALID:]
    corte = min(g["primero"] for g in valid)
    ids = {g["fixture"] for g in valid}
    # El resultado de cada partido de entrenamiento debía ser conocido ANTES
    # de la primera observación utilizada en validación (purga de solapamientos).
    train = [g for g in grupos if g["fixture"] not in ids and g["final"] < corte]
    return train, valid


def ajustar(grupos):
    numeradores, denominadores = [20.0, 20.0], [20.0, 20.0]
    for g in grupos:
        peso = 1 / len(g["ejemplos"])
        for e in g["ejemplos"].values():
            for i in range(2):
                numeradores[i] += peso * e["restantes"][i]
                denominadores[i] += peso * e["base"][i]
    return {lado: max(.6, min(1.6, numeradores[i]/denominadores[i]))
            for i, lado in enumerate(("local", "visitante"))}


def evaluar(grupos, factores):
    por_partido = []
    for g in grupos:
        scores = []
        for e in g["ejemplos"].values():
            p = predecir(e["base"][0]*factores["local"], e["base"][1]*factores["visitante"], e["marcador"], muestras=0)
            scores.append(puntuaciones(vector(p), e["y"]))
        por_partido.append({k: sum(s[k] for s in scores)/len(scores) for k in scores[0]})
    return {k: sum(s[k] for s in por_partido)/len(por_partido) for k in por_partido[0]}


def supera(candidato, referencia):
    return candidato["log_loss"] < referencia["log_loss"] * .99 and candidato["brier"] <= referencia["brier"]


def entrenar(path=DEFAULT_DB, model_path=MODEL_PATH, state_path=STATE_PATH):
    owner = uuid.uuid4().hex
    with abrir_db(path) as db:
        db.execute("INSERT OR IGNORE INTO leases VALUES ('entrenar','',0)")
        changed = db.execute("UPDATE leases SET propietario=?,vence=? WHERE nombre='entrenar' AND vence<?", (owner, time.time()+900, time.time())).rowcount
    if not changed:
        return {"estado": "ocupado", "mensaje": "Ya hay un entrenamiento en curso"}
    try:
        return _entrenar(path, model_path, state_path)
    finally:
        with abrir_db(path) as db:
            db.execute("DELETE FROM leases WHERE nombre='entrenar' AND propietario=?", (owner,))


def _entrenar(path, model_path, state_path):
    grupos = cargar_ejemplos(path)
    previo = leer_json(state_path, {})
    activo = modelo_activo(model_path)
    consumidos = set(previo.get("validaciones_consumidas", [])) | set(activo.get("validacion_ids", []))
    train, valid = dividir(grupos, consumidos)
    reporte = {"actualizado": datetime.now(timezone.utc).isoformat(), "partidos_utiles": len(grupos),
               "minimo_entrenamiento": MIN_TRAIN, "minimo_validacion": MIN_VALID,
               "entrenamiento": len(train), "validacion": len(valid),
               "version_activa": activo["version"], "validaciones_consumidas": sorted(consumidos),
               "ultima_evaluacion": previo.get("ultima_evaluacion")}
    if len(train) < MIN_TRAIN or len(valid) < MIN_VALID:
        reporte.update(estado="recolectando", mensaje="Faltan partidos finalizados útiles y posteriores para evaluar un ajuste")
        guardar_json(state_path, reporte)
        return reporte
    factores = ajustar(train)
    candidato, base, champion = evaluar(valid, factores), evaluar(valid, BASE["factores"]), evaluar(valid, activo["factores"])
    aprobado = supera(candidato, base) and supera(candidato, champion)
    ids = [g["fixture"] for g in valid]
    reporte["validaciones_consumidas"] = sorted(consumidos | set(ids))
    decision = {"candidato": candidato, "base": base, "activo_anterior": champion,
                "factores": factores, "aprobado": aprobado, "validacion_ids": ids,
                "entrenamiento_ids": [g["fixture"] for g in train], "fecha": reporte["actualizado"]}
    digest = hashlib.sha256(json.dumps(decision, sort_keys=True).encode()).hexdigest()[:12]
    nuevo = {"version": f"live-fit-{digest}", "aprobado": aprobado, "factores": factores,
             "validacion_ids": ids, "evaluacion": decision, "anterior": activo["version"]}
    guardar_json(Path(model_path).parent / "modelos" / f"{nuevo['version']}.json", nuevo)
    # Persistimos qué datos ya se evaluaron incluso si el proceso se interrumpe.
    reporte.update(estado="promovido" if aprobado else "conservado", ultima_evaluacion=decision,
                   mensaje="Ajuste aprobado con mejora fuera del entrenamiento" if aprobado else "El candidato no mejoró: se conserva el modelo activo")
    guardar_json(state_path, reporte)
    if aprobado:
        guardar_json(model_path, nuevo)
        reporte["version_activa"] = nuevo["version"]
        guardar_json(state_path, reporte)
    return reporte


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    print(json.dumps(entrenar(args.db, args.db.parent / "modelo_vivo.json", args.db.parent / "aprendizaje_estado.json"), indent=2, ensure_ascii=False))
