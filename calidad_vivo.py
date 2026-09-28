"""Cobertura y vigencia de datos; no representa precisión del modelo."""
from datetime import datetime, timezone
from typing import Literal


Freshness = Literal["fresh", "degraded", "stale", "suspended"]


def classify_freshness(
    observed_at: datetime,
    now: datetime,
    completeness: Freshness | bool,
) -> Freshness:
    """Classify data freshness without deriving or changing match values."""
    if completeness == "suspended" or completeness is False:
        return "suspended"
    try:
        if observed_at.tzinfo is None or now.tzinfo is None:
            return "suspended"
        age_seconds = (now - observed_at).total_seconds()
    except (AttributeError, TypeError):
        return "suspended"
    if age_seconds < 0 or age_seconds > 120:
        return "suspended"
    if completeness == "stale" or age_seconds > 60:
        return "stale"
    return "fresh" if completeness in (True, "fresh") else "degraded"


def evaluar(evento, ahora=None):
    ahora = ahora or datetime.now(timezone.utc)
    try:
        recibido = datetime.fromisoformat(evento.get("timestamp", "").replace("Z", "+00:00"))
        edad = max(0, (ahora - recibido).total_seconds())
    except (ValueError, TypeError):
        edad = None
    campos = ("tiros", "tiros_puerta", "posesion", "xg", "saques_esquina", "tarjetas_rojas")
    disponibles = [campo for campo in campos if all(
        (evento.get(campo) or {}).get(equipo) is not None for equipo in ("local", "visitante"))]
    basicos = evento.get("minuto") is not None and all(
        (evento.get("marcador") or {}).get(equipo) is not None for equipo in ("local", "visitante"))
    completeness = "fresh" if basicos and len(disponibles) == len(campos) else "degraded"
    frescura = (
        classify_freshness(recibido, ahora, completeness)
        if edad is not None
        else "suspended"
    )
    vigente = frescura in ("fresh", "degraded")
    return {"edad_segundos": round(edad, 1) if edad is not None else None,
            "vigente": vigente, "basicos_validos": basicos,
            "estado": "sin_datos" if edad is None else "retrasado" if not vigente else "actualizado",
            "cobertura": round(100 * len(disponibles) / len(campos)),
            "disponibles": disponibles, "ausentes": [c for c in campos if c not in disponibles],
            "fuente": evento.get("_fuente", "Sin proveedor"),
            "nota": "Cobertura de estadísticas, no porcentaje de acierto"}
