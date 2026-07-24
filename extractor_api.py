"""
extractor_api.py — Extractor en vivo via football-data.org API v4.

Una sola petición GET /matches/{id} devuelve:
  - Marcador, minuto, estado
  - Estadísticas (posesión, remates, córners, tarjetas…)
  - Cronología (goals, bookings, substitutions)
  - Alineaciones (lineup + bench por equipo)

Límite: 10 peticiones/minuto (gestionado en football_data_client).
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

import football_data_client as fdc

logging.basicConfig(format="[%(asctime)s] [%(name)s] %(message)s")
logger = logging.getLogger("extractor_api")
logger.setLevel(logging.INFO)

# 7s entre polls → ~8-9 req/min dejando margen para el selector
POLL_INTERVAL = 7.0
MAX_REINTENTOS = 5
BACKOFF_BASE = 2.0

_STATUS_VIVO = {"IN_PLAY", "PAUSED", "LIVE"}
_STATUS_FIN = {"FINISHED", "AWARDED", "CANCELLED", "POSTPONED", "SUSPENDED"}


def _jugador_a_dict(p: dict) -> dict:
    return {
        "dorsal": p.get("shirtNumber"),
        "nombre": p.get("name", "?"),
        "posicion": p.get("position"),
    }


def _parsear_alineaciones(match: dict) -> dict:
    resultado = {
        "local": {"titulares": [], "suplentes": [], "entrenador": "", "formacion": ""},
        "visitante": {"titulares": [], "suplentes": [], "entrenador": "", "formacion": ""},
    }
    for lado, clave in (("local", "homeTeam"), ("visitante", "awayTeam")):
        team = match.get(clave, {}) or {}
        coach = team.get("coach") or {}
        resultado[lado]["entrenador"] = coach.get("name", "")
        resultado[lado]["formacion"] = team.get("formation", "")
        resultado[lado]["titulares"] = [_jugador_a_dict(p) for p in team.get("lineup", [])]
        resultado[lado]["suplentes"] = [_jugador_a_dict(p) for p in team.get("bench", [])]
    return resultado


def _parsear_cronologia(match: dict, home_id: int, away_id: int) -> list[dict]:
    eventos: list[dict] = []
    for g in match.get("goals", []) or []:
        tid = (g.get("team") or {}).get("id")
        equipo = "local" if tid == home_id else "visitante" if tid == away_id else "?"
        minuto = g.get("minute")
        extra = g.get("injuryTime")
        min_str = f"{minuto}+{extra}" if extra else str(minuto)
        scorer = (g.get("scorer") or {}).get("name", "?")
        eventos.append({
            "tipo": "gol",
            "jugador": scorer,
            "minuto": min_str,
            "equipo": equipo,
            "detalle": g.get("type", "REGULAR"),
        })
    for b in match.get("bookings", []) or []:
        tid = (b.get("team") or {}).get("id")
        equipo = "local" if tid == home_id else "visitante"
        jugador = (b.get("player") or {}).get("name", "?")
        card = b.get("card", "YELLOW")
        eventos.append({
            "tipo": "roja" if card == "RED" else "amarilla",
            "jugador": jugador,
            "minuto": str(b.get("minute", "?")),
            "equipo": equipo,
            "texto": f"{card} {jugador} ({b.get('minute')}')",
        })
    for s in match.get("substitutions", []) or []:
        player_out = (s.get("playerOut") or {}).get("name", "?")
        player_in = (s.get("playerIn") or {}).get("name", "?")
        eventos.append({
            "tipo": "sustitucion",
            "minuto": str(s.get("minute", "?")),
            "texto": f"↔ {player_out} → {player_in} ({s.get('minute')}')",
        })
    return eventos


def _stat(team: dict, campo: str) -> int | float:
    stats = team.get("statistics") or {}
    val = stats.get(campo, 0)
    return val if val is not None else 0


def _estimar_minuto(match: dict) -> float | None:
    """Estima el minuto si la API no lo envía (plan gratuito)."""
    if match.get("minute") is not None:
        return float(match["minute"])

    status = match.get("status", "")
    if status == "PAUSED":
        return 45.0
    if status not in _STATUS_VIVO:
        return None

    utc = match.get("utcDate")
    if not utc:
        return None
    try:
        inicio = datetime.fromisoformat(utc.replace("Z", "+00:00"))
        transcurrido = (datetime.now(timezone.utc) - inicio).total_seconds() / 60.0
        # Descanso ~15 min entre mitades
        if transcurrido > 50:
            transcurrido -= 15.0
        return min(95.0, max(1.0, transcurrido))
    except (ValueError, TypeError):
        return None


def match_a_evento(match: dict) -> dict:
    """Convierte respuesta /matches/{id} al schema GameEvent del agente."""
    home = match.get("homeTeam", {}) or {}
    away = match.get("awayTeam", {}) or {}
    score = match.get("score", {}) or {}
    ft = score.get("fullTime", {}) or {}

    home_id = home.get("id")
    away_id = away.get("id")
    cronologia = _parsear_cronologia(match, home_id, away_id)
    alineaciones = _parsear_alineaciones(match)

    gl = ft.get("home", 0) or 0
    gv = ft.get("away", 0) or 0
    minuto_f = _estimar_minuto(match)

    jugadores = [
        f"⚽ [green]{e['jugador']}[/green] ({e['minuto']}')"
        for e in cronologia if e.get("tipo") == "gol"
    ]

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "minuto": minuto_f,
        "marcador": {"local": gl, "visitante": gv},
        "posesion": {
            "local": float(_stat(home, "ball_possession") or 50),
            "visitante": float(_stat(away, "ball_possession") or 50),
        },
        "tiros": {
            "local": int(_stat(home, "shots")),
            "visitante": int(_stat(away, "shots")),
        },
        "tiros_puerta": {
            "local": int(_stat(home, "shots_on_goal")),
            "visitante": int(_stat(away, "shots_on_goal")),
        },
        "saques_esquina": {
            "local": int(_stat(home, "corner_kicks")),
            "visitante": int(_stat(away, "corner_kicks")),
        },
        "tarjetas_amarillas": {
            "local": int(_stat(home, "yellow_cards")),
            "visitante": int(_stat(away, "yellow_cards")),
        },
        "tarjetas_rojas": {
            "local": int(_stat(home, "red_cards")),
            "visitante": int(_stat(away, "red_cards")),
        },
        "faltas": {
            "local": int(_stat(home, "fouls")),
            "visitante": int(_stat(away, "fouls")),
        },
        "fueras_juego": {
            "local": int(_stat(home, "offsides")),
            "visitante": int(_stat(away, "offsides")),
        },
        "paradas_portero": {
            "local": int(_stat(home, "saves")),
            "visitante": int(_stat(away, "saves")),
        },
        "ataques_peligrosos": {
            "local": int(_stat(home, "shots_on_goal") or _stat(home, "shots")),
            "visitante": int(_stat(away, "shots_on_goal") or _stat(away, "shots")),
        },
        "acciones": [
            f"{e.get('tipo')}_{e.get('jugador', '')}_{e.get('minuto', '')}"
            for e in cronologia[-8:]
        ],
        "_cronologia": cronologia,
        "_alineaciones": alineaciones,
        "_equipos": {
            "local": home.get("name", "Local"),
            "visitante": away.get("name", "Visitante"),
        },
        "_fixture_id": match.get("id"),
        "_status": match.get("status", ""),
        "_jugadores": jugadores,
        "_fuente": "football-data.org",
    }


def _evento_nulo() -> dict:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "minuto": None,
        "marcador": None,
        "posesion": None,
        "tiros": None,
        "ataques_peligrosos": None,
        "acciones": None,
        "_cronologia": [],
        "_alineaciones": {"local": {}, "visitante": {}},
        "_jugadores": [],
    }


def obtener_evento(fixture_id: str) -> dict:
    """Obtiene y normaliza un partido (1 petición API)."""
    try:
        match = fdc.obtener_partido(fixture_id)
    except Exception as exc:
        logger.error("Error API football-data: %s", exc)
        return _evento_nulo()

    if not match.get("id"):
        logger.warning("Sin datos para partido %s", fixture_id)
        return _evento_nulo()

    evento = match_a_evento(match)
    eq = evento["_equipos"]
    logger.info(
        "API: %s %s-%s %s | min %s | pos %.0f-%.0f%% | remates %d-%d | eventos %d",
        eq["local"], evento["marcador"]["local"], evento["marcador"]["visitante"],
        eq["visitante"], evento.get("minuto"),
        evento["posesion"]["local"], evento["posesion"]["visitante"],
        evento["tiros"]["local"], evento["tiros"]["visitante"],
        len(evento["_cronologia"]),
    )
    return evento


async def iniciar(raw_queue: asyncio.Queue) -> None:
    fixture_id = os.getenv("FIXTURE_ID", "")
    if not fixture_id:
        raise ValueError("FIXTURE_ID no configurado.")

    logger.info(
        "Extractor API iniciado — id=%s, intervalo=%.0fs (máx %d req/min)",
        fixture_id, POLL_INTERVAL, fdc.MAX_REQUESTS_PER_MINUTE,
    )

    while True:
        try:
            evento = await asyncio.to_thread(obtener_evento, fixture_id)
            if evento.get("minuto") is not None or evento.get("marcador"):
                await raw_queue.put(evento)

            if evento.get("_status", "") in _STATUS_FIN:
                logger.info("Partido finalizado (%s).", evento["_status"])

        except Exception as exc:
            logger.error("Error en extractor API: %s", exc)

        await asyncio.sleep(POLL_INTERVAL)


async def iniciar_con_reconexion(raw_queue: asyncio.Queue) -> None:
    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            await iniciar(raw_queue)
            return
        except Exception as exc:
            espera = BACKOFF_BASE ** intento
            logger.warning("Reconectando API (%d/%d) en %.1fs — %s", intento, MAX_REINTENTOS, espera, exc)
            await asyncio.sleep(espera)

    logger.critical("API inaccesible.")
    await raw_queue.put(_evento_nulo())


if __name__ == "__main__":
    import json
    import sys

    fid = os.getenv("FIXTURE_ID", "")
    if not fid and len(sys.argv) > 1:
        fid = sys.argv[1]
    if not fid:
        print("Uso: FIXTURE_ID=537420 python3 extractor_api.py")
        sys.exit(1)
    print(json.dumps(obtener_evento(fid), indent=2, ensure_ascii=False, default=str))
