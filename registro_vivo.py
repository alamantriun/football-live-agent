"""Registro local auditable de pronósticos emitidos y finales observados."""
import argparse
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from evaluar_modelo import puntuaciones, vector

DEFAULT_DB = Path(__file__).with_name("data") / "predicciones_vivo.sqlite3"
VERSION = "live-poisson-2"


@contextmanager
def abrir_db(path=DEFAULT_DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS pronosticos (fixture TEXT, minuto INTEGER, version TEXT, recibido TEXT, datos TEXT, prediccion TEXT, PRIMARY KEY(fixture,minuto,version))")
        db.execute("CREATE TABLE IF NOT EXISTS finales (fixture TEXT PRIMARY KEY, recibido TEXT, local INTEGER, visitante INTEGER)")
        db.execute("CREATE TABLE IF NOT EXISTS seguimiento (fixture TEXT PRIMARY KEY, nombre TEXT, creado REAL, consultado REAL DEFAULT 0, estado TEXT DEFAULT 'pendiente')")
        db.execute("CREATE TABLE IF NOT EXISTS leases (nombre TEXT PRIMARY KEY, propietario TEXT, vence REAL)")
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def registrar(evento, prediccion, finalizado=False, path=DEFAULT_DB):
    if not evento.get("_fixture_id") or evento.get("_fuente") != "365scores":
        return
    fixture = str(evento["_fixture_id"])
    with abrir_db(path) as db:
        eq = evento.get("_equipos") or {}
        db.execute("INSERT OR IGNORE INTO seguimiento(fixture,nombre,creado) VALUES (?,?,?)", (fixture, f"{eq.get('local', '')} vs {eq.get('visitante', '')}", time.time()))
        if finalizado:
            m = evento["marcador"]
            db.execute("INSERT OR REPLACE INTO finales VALUES (?,?,?,?)", (fixture, evento["timestamp"], m["local"], m["visitante"]))
            db.execute("UPDATE seguimiento SET estado='finalizado',consultado=? WHERE fixture=?", (time.time(), fixture))
        elif prediccion and prediccion.get("tiempo_restante", 0) > 0:
            # Sólo la primera predicción de cada minuto; polling más frecuente no añade peso.
            db.execute("INSERT OR IGNORE INTO pronosticos VALUES (?,?,?,?,?,?)", (
                fixture, int(evento["minuto"]), prediccion.get("modelo_version", VERSION), evento["timestamp"],
                json.dumps(evento, ensure_ascii=False), json.dumps(prediccion, ensure_ascii=False)))


def evaluar_registro(path=DEFAULT_DB, minuto=60):
    """Una observación por partido, emitida hasta el minuto de corte."""
    path = Path(path)
    if not path.is_file():
        return {"n": 0, "nota": "Aún no hay un registro de partidos en vivo"}
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
        filas = db.execute("SELECT p.fixture,p.minuto,p.recibido,p.prediccion,f.recibido,f.local,f.visitante FROM pronosticos p JOIN finales f USING(fixture) WHERE p.minuto<=? AND p.minuto>=? AND p.version=? ORDER BY p.minuto DESC", (minuto, max(0, minuto - 5), VERSION)).fetchall()
    vistos, medidas, baseline = set(), [], []
    for fixture, _, recibido, payload, final_recibido, gl, gv in filas:
        if fixture in vistos or recibido >= final_recibido:
            continue
        vistos.add(fixture)
        pred = json.loads(payload)
        y = 0 if gl > gv else 1 if gl == gv else 2
        medidas.append(puntuaciones(vector(pred), y))
        # Comparador uniforme; no representa cuotas de mercado ni un modelo fuerte.
        baseline.append(puntuaciones([1/3]*3, y))
    def medias(filas):
        return {k: sum(f[k] for f in filas)/len(filas) for k in filas[0]} if filas else {}
    return {"n": len(medidas), "minuto_corte": minuto, "version": VERSION,
            "modelo": medias(medidas), "uniforme": medias(baseline),
            "nota": "Una predicción por partido en los 5 minutos previos al corte; sólo finales observados posteriormente. Muestra de partidos seleccionados, no representativa de todas las ligas. Menor Brier/log loss es mejor."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--minuto", type=int, default=60, choices=range(0, 151), metavar="0..150")
    args = parser.parse_args()
    print(json.dumps(evaluar_registro(args.db, args.minuto), indent=2, ensure_ascii=False))
