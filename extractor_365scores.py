"""
extractor_365scores.py — Extractor en vivo desde 365Scores.

API interna (webws.365scores.com):
  - /games/allscores/   → partidos del día (filtrar statusGroup 2/3 = en vivo)
  - /game/?gameId=      → marcador, minuto, eventos, alineaciones
  - /game/stats/?games= → posesión, remates, córners…

Variable de entorno: FIXTURE_ID = ID numérico del partido (ej. 4749273)
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logging.basicConfig(format="[%(asctime)s] [%(name)s] %(message)s")
logger = logging.getLogger("extractor_365scores")
logger.setLevel(logging.INFO)

POLL_INTERVAL = 20.0
API_BASE = "https://webws.365scores.com/web"
SCORES_HOME = "https://www.365scores.com/es"

DEFAULT_PARAMS = {
    "appTypeId": "5",
    "langId": "29",
    "timezoneName": "America/Mexico_City",
    "userCountryId": "29",
}

STAT_MAP = {
    "posesión": "posesion",
    "possession": "posesion",
    "total remates": "tiros",
    "total shots": "tiros",
    "remates al arco": "tiros_puerta",
    "shots on target": "tiros_puerta",
    "saques de esquina": "saques_esquina",
    "corner kicks": "saques_esquina",
    "tarjetas amarillas": "tarjetas_amarillas",
    "yellow cards": "tarjetas_amarillas",
    "tarjetas rojas": "tarjetas_rojas",
    "red cards": "tarjetas_rojas",
    "faltas": "faltas",
    "fouls": "faltas",
    "fueras de juego": "fueras_juego",
    "offsides": "fueras_juego",
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


def _parse_num(val) -> float:
    if val is None:
        return 0.0
    s = str(val).replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _api_url(path: str, **extra) -> str:
    params = {**DEFAULT_PARAMS, **extra}
    sep = "&" if "?" in path else "?"
    return f"{API_BASE}{path}{sep}{urlencode(params)}"


def _http_get_json(path_or_url: str, **params) -> dict | None:
    url = path_or_url if path_or_url.startswith("http") else _api_url(path_or_url, **params)
    headers = {
        "Accept": "application/json",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Origin": "https://www.365scores.com",
        "Referer": SCORES_HOME,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    try:
        from curl_cffi import requests as cffi_requests

        r = cffi_requests.get(url, headers=headers, impersonate="chrome131", timeout=25)
        if r.status_code == 200:
            return r.json()
        logger.debug("curl_cffi %s → HTTP %s", url, r.status_code)
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("curl_cffi error: %s", exc)

    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=25) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode())
    except Exception as exc:
        logger.debug("urllib error: %s", exc)
    return None


def _hoy_365() -> str:
    return datetime.now().strftime("%d/%m/%Y")


def _es_vivo(game: dict) -> bool:
    return game.get("statusGroup") in (2, 3)


def _minuto_desde_juego(game: dict) -> float | None:
    precise = game.get("preciseGameTime") or {}
    if isinstance(precise, dict) and precise.get("minutes") is not None:
        return float(precise["minutes"])
    if game.get("gameTime") is not None:
        return float(game["gameTime"])
    display = str(game.get("gameTimeDisplay") or "")
    m = re.search(r"(\d+)", display)
    return float(m.group(1)) if m else None


def _marcador_desde_juego(game: dict) -> tuple[int | None, int | None]:
    home = game.get("homeCompetitor") or {}
    away = game.get("awayCompetitor") or {}
    hs, as_ = home.get("score"), away.get("score")
    if hs is None or as_ is None or hs < 0 or as_ < 0:
        return None, None
    return int(_parse_num(hs)), int(_parse_num(as_))


def _evento_desde_listado(game: dict) -> dict | None:
    home = game.get("homeCompetitor") or {}
    away = game.get("awayCompetitor") or {}
    if not home.get("name") or not away.get("name"):
        return None

    gl, gv = _marcador_desde_juego(game)
    minuto = _minuto_desde_juego(game)
    status = game.get("statusText") or "En Vivo"
    marcador = f"{gl}-{gv}" if gl is not None and gv is not None else "—"

    return {
        "id": game.get("id"),
        "nombre": f"{home.get('name')} vs {away.get('name')}",
        "liga": game.get("competitionDisplayName") or game.get("stageName") or "365Scores",
        "marcador": marcador,
        "minuto": int(minuto) if minuto is not None else None,
        "hora": "",
        "estado": status if _es_vivo(game) else status,
        "fixture_id": game.get("id"),
        "modo": "365scores",
    }


def listar_partidos_vivo_sync() -> list[dict]:
    """Lista partidos en vivo desde 365Scores (sin Playwright)."""
    data = _http_get_json(
        "/games/allscores/",
        sports="1",
        startDate=_hoy_365(),
        endDate=_hoy_365(),
    )
    if not data:
        return []

    partidos = []
    for game in data.get("games") or []:
        if not _es_vivo(game):
            continue
        p = _evento_desde_listado(game)
        if p:
            partidos.append(p)
    return partidos


async def listar_partidos_vivo(page=None) -> list[dict]:
    """Lista partidos en vivo (page ignorado; API directa)."""
    return await asyncio.to_thread(listar_partidos_vivo_sync)


def _parsear_estadisticas(stats_data: dict | None, home_id: int, away_id: int) -> dict:
    stats = {
        "posesion": {"local": 50.0, "visitante": 50.0},
        "tiros": {"local": 0, "visitante": 0},
        "tiros_puerta": {"local": 0, "visitante": 0},
        "saques_esquina": {"local": 0, "visitante": 0},
        "tarjetas_amarillas": {"local": 0, "visitante": 0},
        "tarjetas_rojas": {"local": 0, "visitante": 0},
        "faltas": {"local": 0, "visitante": 0},
        "fueras_juego": {"local": 0, "visitante": 0},
    }
    if not stats_data:
        return stats

    acumulado: dict[str, dict[str, float]] = {}
    for item in stats_data.get("statistics") or []:
        nombre = (item.get("name") or item.get("categoryName") or "").lower().strip()
        clave = STAT_MAP.get(nombre)
        if not clave or clave not in stats:
            continue
        cid = item.get("competitorId")
        lado = "local" if cid == home_id else "visitante" if cid == away_id else None
        if not lado:
            continue
        acumulado.setdefault(clave, {})[lado] = _parse_num(item.get("value"))

    for clave, valores in acumulado.items():
        for lado, val in valores.items():
            if clave == "posesion":
                stats[clave][lado] = val
            else:
                stats[clave][lado] = int(val)
    return stats


def _mapa_jugadores(game: dict) -> dict[int, str]:
    return {m.get("id"): m.get("name", "?") for m in game.get("members") or [] if m.get("id")}


def _parsear_cronologia(game: dict, home_id: int, away_id: int) -> list[dict]:
    jugadores = _mapa_jugadores(game)
    eventos = []
    for ev in game.get("events") or []:
        tipo_info = ev.get("eventType") or {}
        tipo_nombre = (tipo_info.get("name") or "").lower()
        cid = ev.get("competitorId")
        equipo = "local" if cid == home_id else "visitante" if cid == away_id else "?"
        minuto = ev.get("gameTimeDisplay") or str(ev.get("gameTime", "?"))
        jugador = jugadores.get(ev.get("playerId"), "?")

        if "gol" in tipo_nombre:
            eventos.append({"tipo": "gol", "jugador": jugador, "minuto": minuto, "equipo": equipo})
        elif "tarjeta" in tipo_nombre:
            es_roja = "roja" in tipo_nombre
            eventos.append({
                "tipo": "roja" if es_roja else "amarilla",
                "jugador": jugador,
                "minuto": minuto,
                "equipo": equipo,
                "texto": f"{'ROJA' if es_roja else 'AMARILLA'} {jugador} ({minuto})",
            })
        elif "cambio" in tipo_nombre or "sustit" in tipo_nombre:
            eventos.append({
                "tipo": "sustitucion",
                "minuto": minuto,
                "texto": f"↔ Cambio ({minuto})",
            })
    return eventos


def _parsear_alineaciones(game: dict) -> dict:
    resultado = {
        "local": {"titulares": [], "suplentes": [], "entrenador": "", "formacion": ""},
        "visitante": {"titulares": [], "suplentes": [], "entrenador": "", "formacion": ""},
    }
    for lado, clave in (("local", "homeCompetitor"), ("visitante", "awayCompetitor")):
        team = game.get(clave) or {}
        lineups = team.get("lineups") or {}
        resultado[lado]["formacion"] = lineups.get("formation", "")
        for p in lineups.get("members") or []:
            entrada = {
                "dorsal": p.get("jerseyNumber"),
                "nombre": p.get("name", "?"),
                "posicion": (p.get("position") or {}).get("name"),
            }
            if (p.get("statusText") or "").lower().startswith("start"):
                resultado[lado]["titulares"].append(entrada)
            else:
                resultado[lado]["suplentes"].append(entrada)
    return resultado


def _combinar_evento(game_data: dict, stats_data: dict | None) -> dict:
    game = game_data.get("game") or game_data
    home = game.get("homeCompetitor") or {}
    away = game.get("awayCompetitor") or {}
    home_id, away_id = home.get("id"), away.get("id")

    gl, gv = _marcador_desde_juego(game)
    minuto = _minuto_desde_juego(game)
    status = game.get("statusText") or ""
    if game.get("statusGroup") == 3 and minuto is None:
        minuto = 45.0

    marcador = {"local": gl or 0, "visitante": gv or 0}
    if gl is None or gv is None:
        marcador = {"local": 0, "visitante": 0}

    stats = _parsear_estadisticas(stats_data, home_id, away_id)
    cronologia = _parsear_cronologia(game, home_id, away_id)
    alineaciones = _parsear_alineaciones(game)

    ataques_l = stats["tiros_puerta"]["local"] or stats["tiros"]["local"]
    ataques_v = stats["tiros_puerta"]["visitante"] or stats["tiros"]["visitante"]

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "minuto": minuto,
        "marcador": marcador,
        "posesion": stats["posesion"],
        "tiros": stats["tiros"],
        "tiros_puerta": stats["tiros_puerta"],
        "saques_esquina": stats["saques_esquina"],
        "tarjetas_amarillas": stats["tarjetas_amarillas"],
        "tarjetas_rojas": stats["tarjetas_rojas"],
        "faltas": stats["faltas"],
        "fueras_juego": stats["fueras_juego"],
        "ataques_peligrosos": {"local": ataques_l, "visitante": ataques_v},
        "acciones": [f"{e['tipo']}_{e.get('jugador', '')}" for e in cronologia[-8:]],
        "_cronologia": cronologia,
        "_alineaciones": alineaciones,
        "_equipos": {"local": home.get("name", "Local"), "visitante": away.get("name", "Visitante")},
        "_fixture_id": game.get("id"),
        "_status": status,
        "_jugadores": [
            f"⚽ [green]{e['jugador']}[/green] ({e['minuto']})"
            for e in cronologia if e.get("tipo") == "gol"
        ],
        "_fuente": "365scores",
    }


async def obtener_evento_365scores(game_id: str) -> dict:
    """Obtiene datos completos de un partido."""
    game_data = await asyncio.to_thread(_http_get_json, "/game/", gameId=game_id, topBookmaker="14")
    stats_data = await asyncio.to_thread(_http_get_json, "/game/stats/", games=game_id)

    if not game_data or not game_data.get("game"):
        return _evento_nulo()

    evento = _combinar_evento(game_data, stats_data)
    eq = evento["_equipos"]
    logger.info(
        "365Scores: %s %s-%s %s | min %s | pos %.0f-%.0f%% | remates %d-%d | eventos %d",
        eq["local"], evento["marcador"]["local"], evento["marcador"]["visitante"],
        eq["visitante"], evento.get("minuto"),
        evento["posesion"]["local"], evento["posesion"]["visitante"],
        evento["tiros"]["local"], evento["tiros"]["visitante"],
        len(evento["_cronologia"]),
    )
    return evento


async def iniciar(raw_queue: asyncio.Queue) -> None:
    game_id = os.getenv("FIXTURE_ID", "")
    if not game_id:
        logger.error("FIXTURE_ID no configurado.")
        return

    logger.info("Extractor 365Scores iniciado — partido %s", game_id)
    while True:
        evento = await obtener_evento_365scores(game_id)
        if evento.get("minuto") is not None or evento.get("marcador"):
            await raw_queue.put(evento)

        status = (evento.get("_status") or "").lower()
        if evento.get("marcador") and (
            "finalizado" in status or "finished" in status or "ft" in status
        ):
            logger.info("Partido finalizado en 365Scores.")

        await asyncio.sleep(POLL_INTERVAL)


async def iniciar_con_reconexion(raw_queue: asyncio.Queue) -> None:
    while True:
        try:
            await iniciar(raw_queue)
        except Exception as exc:
            logger.error("Error 365Scores: %s — reconectando en 5s", exc)
            await asyncio.sleep(5)


if __name__ == "__main__":
    import sys

    async def _demo():
        if len(sys.argv) > 1:
            gid = sys.argv[1]
        else:
            vivos = listar_partidos_vivo_sync()
            print("En vivo:", [p["nombre"] for p in vivos])
            gid = str(vivos[0]["fixture_id"]) if vivos else ""
        if gid:
            ev = await obtener_evento_365scores(gid)
            print(json.dumps(ev, indent=2, ensure_ascii=False, default=str))

    asyncio.run(_demo())
