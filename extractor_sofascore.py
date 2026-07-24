"""
extractor_sofascore.py — Extractor en vivo desde Sofascore.

Usa Playwright para acceder a la API interna de Sofascore:
  - /sport/football/events/live          → partidos en vivo
  - /event/{id}                          → marcador, minuto, estado
  - /event/{id}/statistics               → posesión, remates, córners…
  - /event/{id}/incidents                → cronología (goles, tarjetas)
  - /event/{id}/lineups                  → alineaciones

Variable de entorno: FIXTURE_ID = ID numérico del evento (ej. 12813019)
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timezone

logging.basicConfig(format="[%(asctime)s] [%(name)s] %(message)s")
logger = logging.getLogger("extractor_sofascore")
logger.setLevel(logging.INFO)

POLL_INTERVAL = 20.0
SOFASCORE_HOME = "https://www.sofascore.com/"
API_ORIGIN = "https://www.sofascore.com"

STAT_MAP = {
    "ball possession": "posesion",
    "possession": "posesion",
    "posesión de balón": "posesion",
    "total shots": "tiros",
    "shots on target": "tiros_puerta",
    "shots on goal": "tiros_puerta",
    "corner kicks": "saques_esquina",
    "corners": "saques_esquina",
    "yellow cards": "tarjetas_amarillas",
    "red cards": "tarjetas_rojas",
    "fouls": "faltas",
    "offsides": "fueras_juego",
    "goalkeeper saves": "paradas_portero",
    "expected goals": "xg",
    "big chances": "grandes_ocasiones",
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


def _slug_nombre(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip()


def _nombre_desde_href(href: str) -> str:
    slug = href.split("/football/match/")[-1].split("#")[0].split("/")[0]
    if not slug or "-" not in slug:
        return "Partido en vivo"
    partes = slug.split("-")
    mitad = max(1, len(partes) // 2)
    local = " ".join(partes[:mitad]).title()
    visit = " ".join(partes[mitad:]).title()
    return f"{local} vs {visit}"


def _http_get_json(path: str) -> dict | None:
    """Petición directa a la API (curl_cffi si está instalado)."""
    url = path if path.startswith("http") else f"{API_ORIGIN}{path}"
    headers = {
        "Accept": "application/json",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Origin": SOFASCORE_HOME.rstrip("/"),
        "Referer": SOFASCORE_HOME,
    }
    try:
        from curl_cffi import requests as cffi_requests

        r = cffi_requests.get(url, headers=headers, impersonate="chrome131", timeout=25)
        if r.status_code == 200:
            return r.json()
        logger.debug("curl_cffi %s → HTTP %s", path, r.status_code)
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("curl_cffi error: %s", exc)

    try:
        import urllib.request

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=25) as resp:
            if resp.status == 200:
                import json
                return json.loads(resp.read().decode())
    except Exception as exc:
        logger.debug("urllib error: %s", exc)
    return None


async def _crear_browser():
    from playwright.async_api import async_playwright

    headless = os.getenv("SOFASCORE_HEADLESS", "1") != "0"
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )
    context = await browser.new_context(
        locale="es-ES",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 1080},
        extra_http_headers={"Accept-Language": "es-ES,es;q=0.9,en;q=0.8"},
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    page = await context.new_page()
    return pw, browser, page


async def _asegurar_sesion(page) -> bool:
    """Abre Sofascore para obtener cookies válidas."""
    try:
        resp = await page.goto(SOFASCORE_HOME, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3000)
        if resp and resp.status >= 400:
            logger.warning("Sofascore respondió HTTP %s", resp.status)
        return True
    except Exception as exc:
        logger.error("No se pudo abrir Sofascore: %s", exc)
        return False


async def _api_get(page, path: str) -> dict | None:
    """Llama a la API de Sofascore (HTTP directo primero, luego navegador)."""
    data = _http_get_json(path)
    if data:
        return data

    result = await page.evaluate(
        """async (path) => {
            try {
                const url = path.startsWith('http') ? path : 'https://www.sofascore.com' + path;
                const r = await fetch(url, {
                    credentials: 'include',
                    headers: { 'Accept': 'application/json' }
                });
                if (!r.ok) return { __error: r.status };
                return await r.json();
            } catch (e) {
                return { __error: String(e) };
            }
        }""",
        path,
    )
    if not result or result.get("__error"):
        logger.debug("API %s → error %s", path, result.get("__error") if result else "null")
        return None
    return result


def _evento_desde_live(ev: dict) -> dict | None:
    """Convierte un evento del listado live a entrada del selector."""
    home = ev.get("homeTeam") or {}
    away = ev.get("awayTeam") or {}
    if not home.get("name") or not away.get("name"):
        return None

    hs = ev.get("homeScore") or {}
    as_ = ev.get("awayScore") or {}
    gl, gv = hs.get("current", 0) or 0, as_.get("current", 0) or 0
    t = ev.get("time") or {}
    minuto = t.get("current")

    return {
        "id": ev.get("id"),
        "nombre": f"{home.get('name')} vs {away.get('name')}",
        "liga": (ev.get("tournament") or {}).get("name", "Sofascore"),
        "marcador": f"{gl}-{gv}",
        "minuto": minuto,
        "hora": "",
        "estado": "En Vivo",
        "fixture_id": ev.get("id"),
        "modo": "sofascore",
    }


async def listar_partidos_vivo(page) -> list[dict]:
    """Lista partidos en vivo (HTTP directo, API en navegador o DOM)."""
    data = _http_get_json("/api/v1/sport/football/events/live")
    if data and data.get("events"):
        partidos = [_evento_desde_live(ev) for ev in data["events"]]
        return [p for p in partidos if p]

    if not await _asegurar_sesion(page):
        return []

    data = await _api_get(page, "/api/v1/sport/football/events/live")
    if data and data.get("events"):
        partidos = [_evento_desde_live(ev) for ev in data["events"]]
        return [p for p in partidos if p]

    return await _listar_vivo_dom(page)


async def _listar_vivo_dom(page) -> list[dict]:
    """Fallback: enlaces de partidos en la portada."""
    enlaces = await page.evaluate(
        """() => {
            return [...document.querySelectorAll('a[href*="/football/match/"]')].map(a => {
                const m = a.href.match(/#id:(\\d+)/);
                const parts = (a.innerText || '').trim().split('\\n').map(s => s.trim());
                return { id: m ? m[1] : null, href: a.href, parts };
            }).filter(x => x.id);
        }"""
    )
    partidos = []
    for link in enlaces:
        parts = link.get("parts") or []
        minuto_txt = parts[0] if parts else ""
        marcador = parts[1] if len(parts) > 1 else "—"
        es_vivo = bool(re.search(r"^\d+['′]?", minuto_txt)) or "'" in minuto_txt
        if not es_vivo and marcador in ("-", "—"):
            continue

        href = link.get("href", "")
        nombre = _nombre_desde_href(href)

        partidos.append({
            "id": int(link["id"]),
            "nombre": nombre,
            "liga": "Sofascore",
            "marcador": marcador.replace(" - ", "-"),
            "minuto": re.sub(r"[^\d]", "", minuto_txt) or None,
            "hora": "",
            "estado": "En Vivo",
            "fixture_id": int(link["id"]),
            "modo": "sofascore",
        })
    return partidos


def _parsear_estadisticas(data: dict | None) -> dict:
    stats = {
        "posesion": {"local": 50.0, "visitante": 50.0},
        "tiros": {"local": 0, "visitante": 0},
        "tiros_puerta": {"local": 0, "visitante": 0},
        "saques_esquina": {"local": 0, "visitante": 0},
        "tarjetas_amarillas": {"local": 0, "visitante": 0},
        "tarjetas_rojas": {"local": 0, "visitante": 0},
        "faltas": {"local": 0, "visitante": 0},
        "fueras_juego": {"local": 0, "visitante": 0},
        "paradas_portero": {"local": 0, "visitante": 0},
    }
    if not data:
        return stats

    bloques = data.get("statistics") or []
    items = []
    for bloque in bloques:
        if bloque.get("period") in (None, "ALL", "all", "1ST", "2ND"):
            for grupo in bloque.get("groups") or []:
                items.extend(grupo.get("statisticsItems") or [])

    for item in items:
        clave = STAT_MAP.get((item.get("name") or "").lower())
        if not clave or clave not in stats:
            continue
        h, a = _parse_num(item.get("home")), _parse_num(item.get("away"))
        if clave == "posesion":
            stats["posesion"]["local"], stats["posesion"]["visitante"] = h, a
        else:
            stats[clave]["local"], stats[clave]["visitante"] = int(h), int(a)

    return stats


def _parsear_cronologia(data: dict | None, home_id: int, away_id: int) -> list[dict]:
    if not data:
        return []
    eventos = []
    for inc in data.get("incidents") or []:
        tipo = (inc.get("incidentType") or "").lower()
        t = inc.get("time")
        extra = inc.get("addedTime")
        min_str = f"{t}+{extra}" if extra else str(t) if t is not None else "?"
        jugador = (inc.get("player") or inc.get("scorer") or {}).get("name", "")
        tid = (inc.get("team") or {}).get("id")
        equipo = "local" if tid == home_id else "visitante" if tid == away_id else "?"

        if tipo == "goal":
            eventos.append({"tipo": "gol", "jugador": jugador or "?", "minuto": min_str, "equipo": equipo})
        elif tipo == "card":
            card = (inc.get("incidentClass") or "yellow").lower()
            eventos.append({
                "tipo": "roja" if card == "red" else "amarilla",
                "jugador": jugador,
                "minuto": min_str,
                "equipo": equipo,
                "texto": f"{card.upper()} {jugador} ({min_str}')",
            })
        elif tipo in ("substitution", "subst"):
            pin = (inc.get("playerIn") or {}).get("name", "?")
            pout = (inc.get("playerOut") or {}).get("name", "?")
            eventos.append({
                "tipo": "sustitucion",
                "minuto": min_str,
                "texto": f"↔ {pout} → {pin} ({min_str}')",
            })
    return eventos


def _parsear_alineaciones(data: dict | None) -> dict:
    resultado = {
        "local": {"titulares": [], "suplentes": [], "entrenador": "", "formacion": ""},
        "visitante": {"titulares": [], "suplentes": [], "entrenador": "", "formacion": ""},
    }
    if not data:
        return resultado

    for lado, clave in (("local", "home"), ("visitante", "away")):
        team = data.get(clave) or {}
        resultado[lado]["formacion"] = team.get("formation", "")
        for p in team.get("players") or []:
            jug = p.get("player") or {}
            entrada = {
                "dorsal": p.get("shirtNumber") or jug.get("shirtNumber"),
                "nombre": jug.get("name", "?"),
                "posicion": p.get("position") or jug.get("position"),
            }
            if p.get("substitute"):
                resultado[lado]["suplentes"].append(entrada)
            else:
                resultado[lado]["titulares"].append(entrada)
    return resultado


def _combinar_evento(
    event_data: dict,
    stats_data: dict | None,
    incidents_data: dict | None,
    lineups_data: dict | None,
) -> dict:
    ev = event_data.get("event") or event_data
    home = ev.get("homeTeam") or {}
    away = ev.get("awayTeam") or {}
    home_id, away_id = home.get("id"), away.get("id")

    hs, as_ = ev.get("homeScore") or {}, ev.get("awayScore") or {}
    gl = hs.get("current", hs.get("display", 0)) or 0
    gv = as_.get("current", as_.get("display", 0)) or 0

    tiempo = ev.get("time") or {}
    minuto = tiempo.get("current")
    if minuto is None and (ev.get("status") or {}).get("description", "").lower().find("half") >= 0:
        minuto = 45

    stats = _parsear_estadisticas(stats_data)
    cronologia = _parsear_cronologia(incidents_data, home_id, away_id)
    alineaciones = _parsear_alineaciones(lineups_data)

    ataques_l = stats["tiros_puerta"]["local"] or stats["tiros"]["local"]
    ataques_v = stats["tiros_puerta"]["visitante"] or stats["tiros"]["visitante"]

    status = (ev.get("status") or {}).get("description") or (ev.get("status") or {}).get("type") or ""

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "minuto": float(minuto) if minuto is not None else None,
        "marcador": {"local": int(gl), "visitante": int(gv)},
        "posesion": stats["posesion"],
        "tiros": stats["tiros"],
        "tiros_puerta": stats["tiros_puerta"],
        "saques_esquina": stats["saques_esquina"],
        "tarjetas_amarillas": stats["tarjetas_amarillas"],
        "tarjetas_rojas": stats["tarjetas_rojas"],
        "faltas": stats["faltas"],
        "fueras_juego": stats["fueras_juego"],
        "paradas_portero": stats["paradas_portero"],
        "ataques_peligrosos": {"local": ataques_l, "visitante": ataques_v},
        "acciones": [f"{e['tipo']}_{e.get('jugador','')}" for e in cronologia[-8:]],
        "_cronologia": cronologia,
        "_alineaciones": alineaciones,
        "_equipos": {"local": home.get("name", "Local"), "visitante": away.get("name", "Visitante")},
        "_fixture_id": ev.get("id"),
        "_status": status,
        "_jugadores": [
            f"⚽ [green]{e['jugador']}[/green] ({e['minuto']}')"
            for e in cronologia if e.get("tipo") == "gol"
        ],
        "_fuente": "sofascore",
    }


class _CacheExtra:
    """Rota estadísticas / cronología / alineaciones para no hacer 4 peticiones cada ciclo."""

    def __init__(self):
        self.stats: dict | None = None
        self.incidents: dict | None = None
        self.lineups: dict | None = None
        self._tick = 0

    async def fetch_rotativo(self, page, event_id: str) -> tuple[dict | None, dict | None, dict | None]:
        self._tick += 1
        stats = incidents = lineups = None
        fase = self._tick % 3
        if fase == 0 or self.stats is None:
            self.stats = await _api_get(page, f"/api/v1/event/{event_id}/statistics")
        if fase == 1 or self.incidents is None:
            self.incidents = await _api_get(page, f"/api/v1/event/{event_id}/incidents")
        if fase == 2 or self.lineups is None:
            self.lineups = await _api_get(page, f"/api/v1/event/{event_id}/lineups")
        return self.stats, self.incidents, self.lineups


async def obtener_evento_sofascore(page, event_id: str, cache: _CacheExtra) -> dict:
    """Obtiene datos completos de un partido."""
    event_data = _http_get_json(f"/api/v1/event/{event_id}")
    stats = _http_get_json(f"/api/v1/event/{event_id}/statistics")
    incidents = _http_get_json(f"/api/v1/event/{event_id}/incidents")
    lineups = _http_get_json(f"/api/v1/event/{event_id}/lineups")

    if not event_data:
        if not await _asegurar_sesion(page):
            return _evento_nulo()
        event_data = await _api_get(page, f"/api/v1/event/{event_id}")
        if not event_data:
            return _evento_nulo()
        stats, incidents, lineups = await cache.fetch_rotativo(page, event_id)
    elif not stats or not incidents or not lineups:
        stats = stats or _http_get_json(f"/api/v1/event/{event_id}/statistics")
        incidents = incidents or _http_get_json(f"/api/v1/event/{event_id}/incidents")
        lineups = lineups or _http_get_json(f"/api/v1/event/{event_id}/lineups")
    evento = _combinar_evento(event_data, stats, incidents, lineups)

    eq = evento["_equipos"]
    logger.info(
        "Sofascore: %s %s-%s %s | min %s | pos %.0f-%.0f%% | remates %d-%d | eventos %d",
        eq["local"], evento["marcador"]["local"], evento["marcador"]["visitante"],
        eq["visitante"], evento.get("minuto"),
        evento["posesion"]["local"], evento["posesion"]["visitante"],
        evento["tiros"]["local"], evento["tiros"]["visitante"],
        len(evento["_cronologia"]),
    )
    return evento


async def iniciar(raw_queue: asyncio.Queue) -> None:
    event_id = os.getenv("FIXTURE_ID", "")
    if not event_id:
        logger.error("FIXTURE_ID no configurado.")
        return

    logger.info("Extractor Sofascore iniciado — evento %s", event_id)
    pw, browser, page = await _crear_browser()
    cache = _CacheExtra()
    try:
        while True:
            evento = await obtener_evento_sofascore(page, event_id, cache)
            if evento.get("minuto") is not None or evento.get("marcador"):
                await raw_queue.put(evento)

            status = (evento.get("_status") or "").lower()
            if status in ("finished", "ended", "ft", "aet"):
                logger.info("Partido finalizado en Sofascore.")

            await asyncio.sleep(POLL_INTERVAL)
    finally:
        await browser.close()
        await pw.stop()


async def iniciar_con_reconexion(raw_queue: asyncio.Queue) -> None:
    while True:
        try:
            await iniciar(raw_queue)
        except Exception as exc:
            logger.error("Error Sofascore: %s — reconectando en 5s", exc)
            await asyncio.sleep(5)


if __name__ == "__main__":
    import json
    import sys

    async def _demo():
        pw, browser, page = await _crear_browser()
        try:
            if len(sys.argv) > 1:
                eid = sys.argv[1]
            else:
                vivos = await listar_partidos_vivo(page)
                print("En vivo:", [p["nombre"] for p in vivos])
                eid = str(vivos[0]["fixture_id"]) if vivos else ""
            if eid:
                ev = await obtener_evento_sofascore(page, eid, _CacheExtra())
                print(json.dumps(ev, indent=2, ensure_ascii=False, default=str))
        finally:
            await browser.close()
            await pw.stop()

    asyncio.run(_demo())
