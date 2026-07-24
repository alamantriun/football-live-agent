"""
extractor_google.py — Extractor de datos en vivo desde Google Search.

Extrae del widget deportivo de Google las tres secciones:
  - Cronología (goles, tarjetas, sustituciones)
  - Alineaciones (titulares y suplentes)
  - Estadísticas (posesión, remates, córners, etc.)

Variables de entorno:
  GOOGLE_MATCH_QUERY   — Partido, ej: "España vs Austria"
  GOOGLE_SEARCH_QUERY  — Búsqueda general si no hay partido fijo
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus

logging.basicConfig(format="[%(asctime)s] [%(name)s] %(message)s")
logger = logging.getLogger("extractor_google")
logger.setLevel(logging.INFO)

POLL_INTERVAL = 90.0
MAX_REINTENTOS_BUSQUEDA = 4
DEFAULT_SEARCH = "partidos de futbol en vivo hoy"
GOOGLE_BASE = "https://www.google.com/search"

STAT_MAP = {
    "remates": "tiros",
    "shots": "tiros",
    "remates al arco": "tiros_puerta",
    "shots on target": "tiros_puerta",
    "tiros a puerta": "tiros_puerta",
    "tiros de esquina": "saques_esquina",
    "corner kicks": "saques_esquina",
    "córners": "saques_esquina",
    "corners": "saques_esquina",
    "posesión": "posesion",
    "posesion": "posesion",
    "possession": "posesion",
    "ball possession": "posesion",
    "tarjetas amarillas": "tarjetas_amarillas",
    "yellow cards": "tarjetas_amarillas",
    "tarjetas rojas": "tarjetas_rojas",
    "red cards": "tarjetas_rojas",
    "faltas": "faltas",
    "fouls": "faltas",
    "fuera de juego": "fueras_juego",
    "offsides": "fueras_juego",
    "paradas": "paradas_portero",
    "saves": "paradas_portero",
}

LIVE_STATUS = ("en vivo", "live", "medio tiempo", "half time", "halftime", "descanso")
FINISHED_STATUS = ("finalizado", "full time", "full-time", "fin del partido", "aet", "pen")

_RE_TIMER = re.compile(r"^(\d{1,3}):(\d{2})$")
_RE_GOL_CRONO = re.compile(
    r"^([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'-]{1,30})\s+(\d+(?:\+\d+)?)\s*['′]?\s*$"
)
_RE_STAT_TAB = re.compile(r"^(.+?)\t(.+?)\t(.+)$")
_RE_STAT_INLINE = re.compile(r"^(\d+(?:\.\d+)?%?)\s+(.+?)\s+(\d+(?:\.\d+)?%?)$")


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


def _parse_numero(valor: str) -> float:
    limpio = (valor or "").strip().replace("%", "").replace(",", ".")
    try:
        return float(limpio)
    except ValueError:
        return 0.0


def _lineas(cuerpo: str) -> list[str]:
    return [l.strip() for l in cuerpo.split("\n") if l.strip()]


def _widget_presente(lineas: list[str]) -> bool:
    if not any(" vs " in l.lower() or " vs. " in l.lower() for l in lineas[:30]):
        return False
    return any(_RE_TIMER.match(l) for l in lineas[:35])


def _extraer_minuto(lineas: list[str], estado: str) -> float | None:
    for i, linea in enumerate(lineas[:40]):
        m = _RE_TIMER.match(linea)
        if m:
            return float(int(m.group(1)))
        if i > 0 and " vs" in lineas[i - 1].lower() and m:
            return float(int(m.group(1)))

    low = estado.lower()
    if any(s in low for s in ("medio tiempo", "half time", "halftime", "descanso")):
        return 45.0
    m = re.search(r"(\d+)(?:\+(\d+))?\s*['′]", estado)
    if m:
        return float(int(m.group(1)) + int(m.group(2) or 0))
    if any(s in low for s in FINISHED_STATUS):
        return 90.0
    if any(s in low for s in LIVE_STATUS):
        return None
    return None


def _extraer_marcador_y_equipos(lineas: list[str]) -> tuple[str, str, int, int, str]:
    local = visitante = ""
    goles_l = goles_v = 0
    estado = ""

    for i, linea in enumerate(lineas[:50]):
        if re.search(r"\s+vs\.?\s+", linea, re.IGNORECASE):
            partes = re.split(r"\s+vs\.?\s+", linea, maxsplit=1, flags=re.IGNORECASE)
            if len(partes) == 2:
                local, visitante = partes[0].strip(), partes[1].strip()
                for j in range(i + 1, min(i + 15, len(lineas))):
                    if lineas[j].isdigit() and j + 2 < len(lineas):
                        if lineas[j + 1] in ("-", "–") and lineas[j + 2].isdigit():
                            goles_l, goles_v = int(lineas[j]), int(lineas[j + 2])
                            break
                break

    for linea in lineas[:40]:
        low = linea.lower()
        if any(s in low for s in LIVE_STATUS + FINISHED_STATUS):
            estado = linea
            break
    if not estado:
        for linea in lineas[:40]:
            if linea.lower() in ("en vivo", "live"):
                estado = linea
                break

    return local, visitante, goles_l, goles_v, estado


_EXCLUIR_CRONO = (
    "copa", "mundial", "eliminatoria", "fifa", "grupo", "octavos",
    "comentarios", "resultados", "ver en", "youtube",
)


def _extraer_cronologia(lineas: list[str], local: str, visitante: str) -> list[dict]:
    """Parsea goles, tarjetas y sustituciones del panel principal / cronología."""
    eventos: list[dict] = []
    inicio = 0
    for i, linea in enumerate(lineas[:50]):
        if linea.isdigit() and i + 2 < len(lineas) and lineas[i + 1] in ("-", "–"):
            inicio = i + 3
            break

    for linea in lineas[inicio : inicio + 40]:
        limpia = linea.replace("\xa0", " ").strip()
        m_gol = _RE_GOL_CRONO.match(limpia)
        if m_gol:
            jugador = m_gol.group(1).strip()
            minuto = m_gol.group(2)
            if any(w in jugador.lower() for w in _EXCLUIR_CRONO):
                continue
            try:
                min_base = int(re.sub(r"\+\d+", "", minuto))
            except ValueError:
                continue
            if not 1 <= min_base <= 120:
                continue
            eventos.append({
                "tipo": "gol",
                "jugador": jugador,
                "minuto": minuto,
                "equipo": "local",
            })
            continue

        low = limpia.lower()
        if "tarjeta amarilla" in low or "yellow card" in low:
            eventos.append({"tipo": "amarilla", "texto": limpia})
        elif "tarjeta roja" in low or "red card" in low:
            eventos.append({"tipo": "roja", "texto": limpia})
        elif "sustitución" in low or "substitution" in low:
            eventos.append({"tipo": "sustitucion", "texto": limpia})

    return eventos


def _parse_estadisticas(cuerpo: str) -> dict[str, dict[str, float]]:
    stats: dict[str, dict[str, float]] = {}
    for linea in cuerpo.split("\n"):
        partes_tab = _RE_STAT_TAB.match(linea.strip())
        if partes_tab:
            local_raw, etiqueta, visit_raw = partes_tab.groups()
        else:
            m = _RE_STAT_INLINE.match(linea.strip())
            if not m:
                continue
            local_raw, etiqueta, visit_raw = m.groups()

        clave = STAT_MAP.get(etiqueta.strip().lower())
        if not clave:
            continue
        stats[clave] = {
            "local": _parse_numero(local_raw),
            "visitante": _parse_numero(visit_raw),
        }

    inicio = 0
    lineas = _lineas(cuerpo)
    for i, linea in enumerate(lineas):
        if any(k in linea.upper() for k in ("ESTADÍSTICAS", "TEAM STATS", "ESTADISTICAS")):
            inicio = i
            break
    stats_texto = "\n".join(lineas[inicio : inicio + 30])
    for linea in stats_texto.split("\n"):
        partes = [p.strip() for p in linea.split("\t") if p.strip()]
        if len(partes) != 3:
            m = _RE_STAT_INLINE.match(linea.strip())
            if m:
                partes = [m.group(1), m.group(2), m.group(3)]
            else:
                continue
        clave = STAT_MAP.get(partes[1].lower())
        if clave:
            stats[clave] = {
                "local": _parse_numero(partes[0]),
                "visitante": _parse_numero(partes[2]),
            }
    return stats


def _extraer_alineaciones(cuerpo: str) -> dict:
    """Parsea titulares y suplentes de la pestaña Alineaciones."""
    resultado = {
        "local": {"titulares": [], "suplentes": [], "entrenador": ""},
        "visitante": {"titulares": [], "suplentes": [], "entrenador": ""},
    }
    lineas = _lineas(cuerpo)
    seccion = None
    equipo_actual = "local"

    for linea in lineas:
        low = linea.lower()
        if "alineación inicial" in low or low == "titulares":
            seccion = "titulares"
            continue
        if low in ("suplentes", "banquillo"):
            seccion = "suplentes"
            continue
        if low in ("local", "visitante"):
            equipo_actual = low
            continue
        if "entrenador" in low or "manager" in low:
            m = re.search(r":\s*(.+)$", linea)
            if m:
                resultado[equipo_actual]["entrenador"] = m.group(1).strip()
            continue

        m_jug = re.match(r"^(\d{1,2})\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'-]+)$", linea)
        if m_jug and seccion:
            entrada = {"dorsal": int(m_jug.group(1)), "nombre": m_jug.group(2).strip()}
            resultado[equipo_actual][seccion].append(entrada)

    return resultado


def _stats_a_evento(
    stats: dict[str, dict[str, float]],
    local: str,
    visitante: str,
    goles_l: int,
    goles_v: int,
    estado: str,
    minuto: float | None,
    cronologia: list[dict],
    alineaciones: dict,
) -> dict:
    tiros = stats.get("tiros", {"local": 0.0, "visitante": 0.0})
    puerta = stats.get("tiros_puerta", tiros)
    pos = stats.get("posesion", {"local": 50.0, "visitante": 50.0})
    corners = stats.get("saques_esquina", {"local": 0.0, "visitante": 0.0})
    amarillas = stats.get("tarjetas_amarillas", {"local": 0.0, "visitante": 0.0})
    rojas = stats.get("tarjetas_rojas", {"local": 0.0, "visitante": 0.0})
    faltas = stats.get("faltas", {"local": 0.0, "visitante": 0.0})
    fueras = stats.get("fueras_juego", {"local": 0.0, "visitante": 0.0})
    paradas = stats.get("paradas_portero", {"local": 0.0, "visitante": 0.0})

    ataques_l = int(puerta["local"]) if puerta["local"] > 0 else int(tiros["local"])
    ataques_v = int(puerta["visitante"]) if puerta["visitante"] > 0 else int(tiros["visitante"])

    acciones = [f"goal_{e['jugador']}_{e['minuto']}" for e in cronologia if e.get("tipo") == "gol"]
    jugadores = [
        f"⚽ [green]{e['jugador']}[/green] ({e['minuto']}')"
        for e in cronologia if e.get("tipo") == "gol"
    ]

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "minuto": minuto,
        "marcador": {"local": goles_l, "visitante": goles_v},
        "posesion": {"local": pos["local"], "visitante": pos["visitante"]},
        "tiros": {"local": int(tiros["local"]), "visitante": int(tiros["visitante"])},
        "tiros_puerta": {"local": int(puerta["local"]), "visitante": int(puerta["visitante"])},
        "saques_esquina": {"local": int(corners["local"]), "visitante": int(corners["visitante"])},
        "tarjetas_amarillas": {"local": int(amarillas["local"]), "visitante": int(amarillas["visitante"])},
        "tarjetas_rojas": {"local": int(rojas["local"]), "visitante": int(rojas["visitante"])},
        "faltas": {"local": int(faltas["local"]), "visitante": int(faltas["visitante"])},
        "fueras_juego": {"local": int(fueras["local"]), "visitante": int(fueras["visitante"])},
        "paradas_portero": {"local": int(paradas["local"]), "visitante": int(paradas["visitante"])},
        "ataques_peligrosos": {"local": ataques_l, "visitante": ataques_v},
        "acciones": acciones,
        "_cronologia": cronologia,
        "_alineaciones": alineaciones,
        "_equipos": {"local": local or "Local", "visitante": visitante or "Visitante"},
        "_status": estado or "En vivo",
        "_jugadores": jugadores,
        "_fuente": "google",
    }


async def _crear_browser():
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )
    context = await browser.new_context(
        locale="es-MX",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 1080},
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    page = await context.new_page()
    return pw, browser, page


async def _buscar_url(page, query: str) -> list[str]:
    params = f"q={quote_plus(query)}&hl=es&gl=mx"
    for base in ("https://www.google.com.mx/search", "https://www.google.com/search"):
        await page.goto(f"{base}?{params}", wait_until="domcontentloaded", timeout=60000)
        for sel in (
            'button:has-text("Aceptar todo")',
            'button:has-text("Accept all")',
            "#L2AGLb",
        ):
            btn = page.locator(sel).first
            if await btn.count():
                try:
                    await btn.click(timeout=2000)
                    await page.wait_for_timeout(800)
                except Exception:
                    pass
        await page.wait_for_timeout(3000)
        lineas = _lineas(await page.inner_text("body"))
        if len(lineas) > 20:
            return lineas
    return _lineas(await page.inner_text("body"))


async def _abrir_pestaña(page, nombres: tuple[str, ...]) -> bool:
    for nombre in nombres:
        for exact in (True, False):
            loc = page.get_by_text(nombre, exact=exact)
            if await loc.count() > 0:
                try:
                    await loc.first.click(timeout=4000)
                    await page.wait_for_timeout(2000)
                    return True
                except Exception:
                    continue
    return False


async def _obtener_cuerpo_pestaña(page, query: str, nombres: tuple[str, ...]) -> str:
    await _buscar_url(page, query)
    if await _abrir_pestaña(page, nombres):
        return await page.inner_text("body")
    return ""


def _parsear_pagina_completa(
    cuerpo_crono: str,
    cuerpo_stats: str,
    cuerpo_lineups: str,
) -> dict | None:
    lineas = _lineas(cuerpo_crono)
    if not _widget_presente(lineas):
        return None

    local, visitante, goles_l, goles_v, estado = _extraer_marcador_y_equipos(lineas)
    minuto = _extraer_minuto(lineas, estado)
    cronologia = _extraer_cronologia(lineas, local, visitante)

    stats = _parse_estadisticas(cuerpo_stats or cuerpo_crono)
    alineaciones = _extraer_alineaciones(cuerpo_lineups)

    if minuto is None and goles_l + goles_v == 0 and not stats:
        return None

    return _stats_a_evento(
        stats, local, visitante, goles_l, goles_v, estado, minuto,
        cronologia, alineaciones,
    )


async def obtener_evento_google(page, match_query: str | None) -> dict:
    """Obtiene datos del partido desde las 3 secciones de Google."""
    query = match_query or os.getenv("GOOGLE_MATCH_QUERY", "")
    if not query:
        logger.error("GOOGLE_MATCH_QUERY no configurado.")
        return _evento_nulo()

    cuerpo_crono = ""
    for intento in range(MAX_REINTENTOS_BUSQUEDA):
        lineas = await _buscar_url(page, query)
        if _widget_presente(lineas):
            cuerpo_crono = "\n".join(lineas)
            break
        logger.warning("Widget de Google no visible (intento %d/%d)", intento + 1, MAX_REINTENTOS_BUSQUEDA)
        await asyncio.sleep(2)

    if not cuerpo_crono:
        logger.warning("No se pudo cargar el widget deportivo de Google para: %s", query)
        return _evento_nulo()

    cuerpo_stats = await _obtener_cuerpo_pestaña(page, query, ("ESTADÍSTICAS", "Estadísticas", "STATS"))
    cuerpo_lineups = await _obtener_cuerpo_pestaña(page, query, ("ALINEACIONES", "Alineaciones", "LINEUPS"))

    evento = _parsear_pagina_completa(cuerpo_crono, cuerpo_stats, cuerpo_lineups)
    if evento is None:
        logger.warning("No se pudieron parsear datos de Google.")
        return _evento_nulo()

    eq = evento.get("_equipos", {})
    logger.info(
        "Google: %s %s-%s %s | Min %s' | Pos %.0f-%.0f%% | Remates %s-%s | Cronología: %d eventos",
        eq.get("local"), evento["marcador"]["local"], evento["marcador"]["visitante"],
        eq.get("visitante"), evento.get("minuto"),
        evento["posesion"]["local"], evento["posesion"]["visitante"],
        evento["tiros"]["local"], evento["tiros"]["visitante"],
        len(evento.get("_cronologia", [])),
    )
    return evento


def _es_partido_terminado(estado: str) -> bool:
    return any(s in (estado or "").lower() for s in FINISHED_STATUS)


async def iniciar(raw_queue: asyncio.Queue) -> None:
    match_query = os.getenv("GOOGLE_MATCH_QUERY")
    if not match_query:
        logger.error("GOOGLE_MATCH_QUERY no está configurado.")
        return

    logger.info("Iniciando extractor Google: %s", match_query)
    pw, browser, page = await _crear_browser()
    try:
        while True:
            evento = await obtener_evento_google(page, match_query)
            if evento.get("minuto") is not None or evento.get("marcador"):
                await raw_queue.put(evento)

            if _es_partido_terminado(evento.get("_status", "")):
                logger.info("Partido finalizado en Google.")

            await asyncio.sleep(POLL_INTERVAL)
    finally:
        await browser.close()
        await pw.stop()


async def iniciar_con_reconexion(raw_queue: asyncio.Queue) -> None:
    while True:
        try:
            await iniciar(raw_queue)
        except Exception as e:
            logger.error("Error en Google Scraper: %s — reconectando en 5s", e)
            await asyncio.sleep(5)
