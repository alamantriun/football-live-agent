"""Baseline de ataque/defensa por sede con regularización hacia la liga."""
import csv
from datetime import datetime
from modelo_poisson import predecir


def cargar(paths):
    partidos, vistos = [], set()
    for path in paths:
        with open(path, encoding="utf-8-sig", newline="") as archivo:
            for fila in csv.DictReader(archivo):
                if not fila.get("Date"):
                    continue
                fecha = None
                for formato in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
                    try:
                        fecha = datetime.strptime(fila["Date"], formato).date()
                        break
                    except ValueError:
                        pass
                if fecha is None:
                    raise ValueError(f"Fecha inválida: {fila['Date']}")
                local, visita = fila["HomeTeam"].strip(), fila["AwayTeam"].strip()
                gl, gv = int(fila["FTHG"]), int(fila["FTAG"])
                if not local or not visita or local == visita or min(gl, gv) < 0:
                    raise ValueError("Partido inválido")
                clave = (fecha, local, visita)
                if clave in vistos:
                    raise ValueError(f"Partido duplicado: {clave}")
                vistos.add(clave)
                partidos.append(dict(fecha=fecha, local=local, visita=visita, gl=gl, gv=gv))
    return sorted(partidos, key=lambda p: p["fecha"])


def estimar(historial, local, visita, fecha, regularizacion=10.0):
    """Sólo resultados anteriores al día objetivo; ventana de dos años.

    No se ajustan hiperparámetros en el conjunto de evaluación.
    Requiere datos de una misma competición y nombres consistentes.
    """
    pasados = [p for p in historial if 0 < (fecha - p["fecha"]).days <= 730]
    if len(pasados) < 100:
        raise ValueError("Se requieren 100 partidos históricos anteriores")
    ml = sum(p["gl"] for p in pasados) / len(pasados)
    mv = sum(p["gv"] for p in pasados) / len(pasados)
    ml, mv = max(ml, .01), max(mv, .01)
    h = [p for p in pasados if p["local"] == local]
    a = [p for p in pasados if p["visita"] == visita]
    def promedio(grupo, campo, prior):
        return (sum(p[campo] for p in grupo) + regularizacion * prior) / (len(grupo) + regularizacion)
    tasa_l = promedio(h, "gl", ml) * promedio(a, "gl", ml) / ml
    tasa_v = promedio(a, "gv", mv) * promedio(h, "gv", mv) / mv
    return tasa_l, tasa_v, {"partidos_liga": len(pasados), "partidos_local": len(h), "partidos_visita": len(a)}


def pronosticar(historial, local, visita, fecha):
    tl, tv, soporte = estimar(historial, local, visita, fecha)
    resultado = predecir(tl, tv)
    resultado.update(soporte=soporte, fuente="histórico por sede", fecha_corte=fecha.isoformat())
    return resultado
