"""
selector_visual.py — Selector de partidos con flechas en terminal.

Busca partidos via Flashscore (scraping con Playwright).
NO usa APIs. Navega con ↑/↓, confirma con Enter, actualiza con R, sale con Q.
"""

import curses
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

PARTIDOS_FILE = "partidos.json"
CATEGORIAS = ("EN VIVO", "HOY", "PRÓXIMOS", "GUARDADOS")
DIAS_FUTUROS = 7


# ───────────────── SCRAPING FLASHSCORE ──────────────────

import asyncio

URLS_FLASHSCORE = (
    # Mundial
    "https://www.flashscore.co/futbol/mundial/campeonato-del-mundo/",
    "https://www.flashscore.co/futbol/mundial/campeonato-del-mundo/partidos/",
    # Champions League
    "https://www.flashscore.co/futbol/europa/champions-league/",
    "https://www.flashscore.co/futbol/europa/champions-league/partidos/",
    # Copa América
    "https://www.flashscore.co/futbol/sudamerica/copa-america/",
    "https://www.flashscore.co/futbol/sudamerica/copa-america/partidos/",
    # Eurocopa
    "https://www.flashscore.co/futbol/europa/eurocopa/",
    "https://www.flashscore.co/futbol/europa/eurocopa/partidos/",
    # Premier League
    "https://www.flashscore.co/futbol/inglaterra/premier-league/",
    "https://www.flashscore.co/futbol/inglaterra/premier-league/partidos/",
    # La Liga
    "https://www.flashscore.co/futbol/espana/laliga/",
    "https://www.flashscore.co/futbol/espana/laliga/partidos/",
    # Serie A
    "https://www.flashscore.co/futbol/italia/serie-a/",
    "https://www.flashscore.co/futbol/italia/serie-a/partidos/",
    # Bundesliga
    "https://www.flashscore.co/futbol/alemania/bundesliga/",
    "https://www.flashscore.co/futbol/alemania/bundesliga/partidos/",
    # Ligue 1
    "https://www.flashscore.co/futbol/francia/ligue-1/",
    "https://www.flashscore.co/futbol/francia/ligue-1/partidos/",
)

_LIGA_MAP = {
    "campeonato-del-mundo": "Mundial 2026",
    "champions-league": "Champions League",
    "copa-america": "Copa América",
    "eurocopa": "Eurocopa",
    "premier-league": "Premier League",
    "laliga": "La Liga",
    "serie-a": "Serie A",
    "bundesliga": "Bundesliga",
    "ligue-1": "Ligue 1",
}


def _liga_desde_url(url: str) -> str:
    """Detecta el nombre de la liga desde la URL de Flashscore."""
    for key, nombre in _LIGA_MAP.items():
        if key in url:
            return nombre
    return "Flashscore"


def _dentro_de_rango_dias(estado: str, dias: int = DIAS_FUTUROS) -> bool:
    """Verifica si un partido futuro está dentro del rango de días permitido."""
    match = re.search(r'(\d{1,2})\.(\d{1,2})\.', estado)
    if not match:
        return True  # Sin fecha explícita → permitir (puede ser hoy/en vivo)
    dia, mes = int(match.group(1)), int(match.group(2))
    ahora = datetime.now(timezone.utc)
    try:
        fecha = datetime(ahora.year, mes, dia, tzinfo=timezone.utc)
        if fecha < ahora - timedelta(days=1):
            fecha = datetime(ahora.year + 1, mes, dia, tzinfo=timezone.utc)
        return 0 <= (fecha - ahora).days <= dias
    except ValueError:
        return True

_RE_MINUTO_VIVO = re.compile(r"^\d{1,3}(\+\d{1,2})?'?$")


def _es_estado_vivo(estado: str) -> bool:
    """Detecta minuto en juego: 45', 90+2, 45+4, En vivo, Descanso, etc."""
    low = estado.lower().strip()
    if any(x in low for x in ("vivo", "live", "descanso", "mitad", "half time", "halftime")):
        return True
    if low in ("ht", "mt"):
        return True
    return bool(_RE_MINUTO_VIVO.match(low))


def _es_estado_finalizado(estado: str) -> bool:
    low = estado.lower().strip()
    return low in ("fin", "finalizado", "finished", "ft", "aet", "pen") or "finalizado" in low


def _es_estado_hoy(estado: str) -> bool:
    """Horario de hoy sin haber empezado, ej: 14:00 o 21:00."""
    return "." not in estado and ":" in estado and not _es_estado_vivo(estado)


async def _parsear_partido_element(el, liga_default: str = "Mundial 2026") -> dict | None:
    """Extrae un partido desde un nodo .event__match de Flashscore."""
    id_attr = await el.get_attribute("id")
    match_id = id_attr.split("_")[-1] if id_attr else None
    if not match_id:
        return None

    time_el = await el.query_selector(".event__time")
    home_el = await el.query_selector(".event__participant--home")
    away_el = await el.query_selector(".event__participant--away")
    score_home_el = await el.query_selector(".event__score--home")
    score_away_el = await el.query_selector(".event__score--away")
    stage_el = await el.query_selector(".event__stage")

    if home_el and away_el:
        local = (await home_el.inner_text()).strip()
        visita = (await away_el.inner_text()).strip()
        estado_hora = (await time_el.inner_text()).strip() if time_el else ""
        if stage_el:
            stage = (await stage_el.inner_text()).strip()
            if stage:
                estado_hora = stage if not estado_hora else estado_hora
        sh = (await score_home_el.inner_text()).strip() if score_home_el else ""
        sa = (await score_away_el.inner_text()).strip() if score_away_el else ""
    else:
        texto = await el.inner_text()
        lineas = [l.strip() for l in texto.split("\n") if l.strip()]
        if len(lineas) < 3:
            return None
        estado_hora = lineas[0]
        local = lineas[1]
        visita = lineas[2]
        sh, sa = "", ""
        if len(lineas) >= 5 and lineas[3].isdigit() and lineas[4].isdigit():
            sh, sa = lineas[3], lineas[4]

    estado_hora = estado_hora.replace("\n", " ").strip()
    marcador = f"{sh}-{sa}" if sh.isdigit() and sa.isdigit() else "—"
    es_vivo = _es_estado_vivo(estado_hora)
    es_hoy = _es_estado_hoy(estado_hora)
    es_finalizado = _es_estado_finalizado(estado_hora)

    estado_limpio = estado_hora
    if "📅" not in estado_limpio and not es_hoy and not es_vivo and not es_finalizado:
        estado_limpio = f"📅 {estado_hora}"

    minuto = None
    if es_vivo:
        m = re.search(r"(\d+)", estado_hora)
        minuto = int(m.group(1)) if m else None
        if minuto is None and any(x in estado_hora.lower() for x in ("descanso", "mitad", "half")):
            minuto = 45

    return {
        "id": match_id,
        "nombre": f"{local} vs {visita}",
        "liga": liga_default,
        "marcador": marcador,
        "hora": estado_hora if es_hoy else "",
        "minuto": minuto,
        "estado": "En Vivo" if es_vivo else estado_limpio,
        "fixture_id": match_id,
        "modo": "flashscore",
        "_es_vivo": es_vivo,
        "_es_hoy": es_hoy,
        "_es_finalizado": es_finalizado,
    }


def _clasificar_partido(p: dict, en_vivo: list, hoy_lista: list, proximos: list) -> None:
    es_vivo = p.pop("_es_vivo", False)
    es_hoy = p.pop("_es_hoy", False)
    es_finalizado = p.pop("_es_finalizado", False)
    if es_vivo:
        en_vivo.append(p)
    elif es_hoy or es_finalizado:
        hoy_lista.append(p)
    elif _dentro_de_rango_dias(p.get("estado", "")):
        proximos.append(p)
    # Fuera del rango de días → descartado silenciosamente


async def _cargar_datos_flashscore_pw():
    """Lanza Playwright y saca los partidos de Flashscore."""
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
    await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    page = await context.new_page()

    en_vivo, hoy_lista, proximos = [], [], []
    vistos: set[str] = set()

    try:
        for url in URLS_FLASHSCORE:
            liga = _liga_desde_url(url)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                try:
                    await page.wait_for_selector(".event__match", timeout=8000)
                except Exception:
                    await page.wait_for_timeout(3000)

                elementos = await page.query_selector_all(".event__match")
                for el in elementos:
                    p = await _parsear_partido_element(el, liga_default=liga)
                    if not p or p["id"] in vistos:
                        continue
                    vistos.add(p["id"])
                    _clasificar_partido(p, en_vivo, hoy_lista, proximos)
            except Exception as e:
                print(f"Error cargando {url}: {e}")

    except Exception as e:
        print(f"Error cargando Flashscore: {e}")

    await browser.close()
    await pw.stop()

    return en_vivo, hoy_lista, proximos

def cargar_datos_flashscore():
    """Función síncrona que envuelve el loop asyncio."""
    return asyncio.run(_cargar_datos_flashscore_pw())





def cargar_guardados() -> list[dict]:
    if not os.path.exists(PARTIDOS_FILE):
        return []
    try:
        with open(PARTIDOS_FILE, encoding="utf-8") as f:
            datos = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    partidos = []
    for p in datos:
        partidos.append({
            "id": p.get("id"),
            "nombre": p.get("nombre", "Partido"),
            "liga": p.get("liga", "Guardado"),
            "marcador": p.get("marcador", "—"),
            "minuto": p.get("minuto"),
            "hora": p.get("hora", "—"),
            "estado": p.get("estado", "Guardado"),
            "fixture_id": p.get("fixture_id") or p.get("id"),
            "modo": p.get("modo", "flashscore"),
        })
    return partidos


def _opcion_auto_flashscore() -> dict:
    return {
        "id": None,
        "nombre": "Auto-detectar partido en vivo (Flashscore)",
        "liga": "Búsqueda automática en Flashscore",
        "marcador": "—",
        "estado": "FLASHSCORE",
        "modo": "flashscore",
        "fixture_id": None,
    }


def _formatear_linea(p: dict, ancho: int) -> str:
    nombre = p.get("nombre", "?")[: max(20, ancho - 28)]
    liga = (p.get("liga") or "")[:18]
    marc = p.get("marcador", "—")
    extra = p.get("minuto")
    if extra is not None:
        info = f"{marc} {extra}'"
    else:
        info = f"{marc} {p.get('hora', '')}"
    return f"{nombre:<{max(20, ancho - 28)}} {liga:<18} {info:>10}"


def _dibujar_selector(
    stdscr,
    partidos: list[dict],
    indice: int,
    categoria: int,
    mensaje: str,
) -> None:
    stdscr.clear()
    alto, ancho = stdscr.getmaxyx()
    if alto < 10 or ancho < 50:
        stdscr.addstr(0, 0, "Terminal muy pequeña. Amplía la ventana.")
        stdscr.refresh()
        return

    titulo = " AGENTE DE ANALISIS DEPORTIVO — SELECTOR DE PARTIDOS "
    stdscr.attron(curses.A_BOLD)
    stdscr.addstr(0, max(0, (ancho - len(titulo)) // 2), titulo[: ancho - 1])
    stdscr.attroff(curses.A_BOLD)

    tabs = "  ".join(
        f"[{cat}]" if i == categoria else f" {cat} "
        for i, cat in enumerate(CATEGORIAS)
    )
    stdscr.addstr(2, max(0, (ancho - len(tabs)) // 2), tabs[: ancho - 1])

    stdscr.addstr(4, 2, "PARTIDO".ljust(max(20, ancho - 28)))
    stdscr.addstr(4, max(22, ancho - 26), "LIGA".ljust(18))
    stdscr.addstr(4, ancho - 12, "ESTADO", curses.A_DIM)

    fila_inicio = 6
    visible = alto - fila_inicio - 4
    scroll = max(0, indice - visible + 1)
    ventana = partidos[scroll : scroll + visible]

    for i, partido in enumerate(ventana):
        fila = fila_inicio + i
        real_idx = scroll + i
        linea = _formatear_linea(partido, ancho)
        if real_idx == indice:
            stdscr.attron(curses.A_REVERSE)
            stdscr.addstr(fila, 1, " " + linea[: ancho - 3])
            stdscr.attroff(curses.A_REVERSE)
        else:
            stdscr.addstr(fila, 1, " " + linea[: ancho - 3])

    if not partidos:
        stdscr.addstr(fila_inicio, 2, "No hay partidos en esta categoría.")

    ayuda = "↑↓ Navegar   Enter Seleccionar   Tab Cambiar lista   R Actualizar   Q Salir"
    stdscr.addstr(alto - 2, max(0, (ancho - len(ayuda)) // 2), ayuda[: ancho - 1], curses.A_DIM)

    if mensaje:
        stdscr.addstr(alto - 3, 2, mensaje[: ancho - 4], curses.A_BOLD)


def _loop_curses(
    stdscr,
    listas: dict[int, list[dict]],
    categoria_inicial: int = 0,
) -> dict | None:
    curses.curs_set(0)
    stdscr.keypad(True)
    curses.start_color()
    curses.use_default_colors()

    categoria = categoria_inicial
    mensaje = ""
    indice = 0

    while True:
        partidos = listas.get(categoria, [])
        idx_guardados = len(CATEGORIAS) - 1
        if not partidos and categoria != idx_guardados:
            if categoria == 0:
                partidos = [{
                    "id": None,
                    "nombre": "⚠ No hay partidos en vivo ahora",
                    "liga": "Presiona Tab → PRÓXIMOS",
                    "marcador": "—",
                    "estado": "SIN VIVO",
                    "modo": "flashscore",
                    "fixture_id": None,
                }]
            else:
                partidos = [_opcion_auto_flashscore()]
        elif categoria == idx_guardados and not partidos:
            partidos = [_opcion_auto_flashscore()]

        indice = min(indice, max(0, len(partidos) - 1))
        _dibujar_selector(stdscr, partidos, indice, categoria, mensaje)
        mensaje = ""
        stdscr.refresh()

        try:
            tecla = stdscr.getch()
        except curses.error:
            continue

        if tecla in (curses.KEY_UP, ord("k")):
            indice = max(0, indice - 1)
        elif tecla in (curses.KEY_DOWN, ord("j")):
            indice = min(len(partidos) - 1, indice + 1)
        elif tecla == 9:  # Tab
            categoria = (categoria + 1) % len(CATEGORIAS)
            indice = 0
        elif tecla in (10, 13) or tecla == getattr(curses, "KEY_ENTER", -1):
            return partidos[indice]
        elif tecla in (ord("r"), ord("R")):
            mensaje = "Buscando partidos..."
            _dibujar_selector(stdscr, partidos, indice, categoria, mensaje)
            stdscr.refresh()

            if categoria == 0:
                en_vivo = cargar_datos_365scores()
                listas[0] = en_vivo
                mensaje = "✓ EN VIVO actualizado (365Scores)."
            else:
                en_vivo_fs, hoy_lista, proximos = cargar_datos_flashscore()
                listas[1] = hoy_lista
                listas[2] = proximos
                mensaje = "✓ Lista actualizada desde Flashscore."
            listas[3] = cargar_guardados()
            indice = 0
        elif tecla in (ord("q"), ord("Q"), 27):
            return None


def cargar_datos_365scores() -> list[dict]:
    """Partidos en vivo desde 365Scores (API webws.365scores.com)."""
    try:
        import extractor_365scores as e365

        partidos = e365.listar_partidos_vivo_sync()
        if not partidos:
            print("[365Scores sin partidos en vivo — probando API football-data.org como respaldo]")
            return cargar_datos_api()
        return partidos
    except Exception as e:
        print(f"Error cargando 365Scores: {e}")
        return cargar_datos_api()


def cargar_datos_sofascore() -> list[dict]:
    """Alias de compatibilidad → 365Scores."""
    return cargar_datos_365scores()


def cargar_datos_api() -> list[dict]:
    """Partidos en vivo desde football-data.org (1 petición API)."""
    try:
        import football_data_client as fdc
        return fdc.listar_partidos_vivo()
    except Exception as e:
        print(f"Error cargando API: {e}")
        return []


def seleccionar_partido_visual() -> dict | None:
    """
    Muestra el selector con flechas y devuelve el partido elegido.
    EN VIVO: 365Scores | HOY/PRÓXIMOS: Flashscore.
    """
    print("\n[Cargando partidos en vivo desde 365Scores...]\n")
    en_vivo = cargar_datos_365scores()
    print("[Buscando partidos de hoy/próximos en Flashscore...]\n")
    _, hoy, proximos = cargar_datos_flashscore()
    guardados = cargar_guardados()

    # Auto-seleccionar pestaña con contenido
    categoria_inicial = 0
    if not en_vivo:
        if hoy:
            categoria_inicial = 1
        elif proximos:
            categoria_inicial = 2
        elif guardados:
            categoria_inicial = 3

    listas = {
        0: en_vivo,
        1: hoy,
        2: proximos,
        3: guardados,
    }

    if not any(listas.values()):
        listas[0] = [_opcion_auto_flashscore()]

    try:
        return curses.wrapper(_loop_curses, listas, categoria_inicial)
    except curses.error:
        print("Error: no se pudo iniciar la interfaz visual (curses).")
        return _opcion_auto_flashscore()


def aplicar_seleccion(partido: dict) -> None:
    """Configura variables de entorno según el partido elegido."""
    os.environ.pop("GOOGLE_MATCH_QUERY", None)
    fid = partido.get("fixture_id") or partido.get("id")
    if fid and "Auto-detectar" not in partido.get("nombre", ""):
        os.environ["FIXTURE_ID"] = str(fid)
    modo = partido.get("modo", "365scores")
    if modo == "api":
        try:
            import football_data_client as fdc
            os.environ["FOOTBALL_DATA_API_KEY"] = fdc.cargar_api_key()
        except ValueError:
            pass


def guardar_seleccion(partido: dict) -> None:
    """Persiste el partido elegido en partidos.json."""
    nombre = partido.get("nombre", "")
    if not nombre or "Auto-detectar" in nombre or "No hay partidos" in nombre:
        return

    entrada = {
        "id": partido.get("id") or partido.get("fixture_id") or hash(nombre) % 100000,
        "nombre": nombre,
        "liga": partido.get("liga", ""),
        "marcador": partido.get("marcador"),
        "estado": partido.get("estado"),
        "hora": partido.get("hora", ""),
        "fixture_id": partido.get("fixture_id"),
        "modo": partido.get("modo", "365scores"),
    }

    existentes = []
    if os.path.exists(PARTIDOS_FILE):
        try:
            with open(PARTIDOS_FILE, encoding="utf-8") as f:
                existentes = json.load(f)
        except (json.JSONDecodeError, OSError):
            existentes = []

    nombres = {p.get("nombre") for p in existentes}
    if entrada["nombre"] not in nombres:
        existentes.append(entrada)

    with open(PARTIDOS_FILE, "w", encoding="utf-8") as f:
        json.dump(existentes, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    elegido = seleccionar_partido_visual()
    if elegido:
        aplicar_seleccion(elegido)
        guardar_seleccion(elegido)
        print(f"\nSeleccionado: {elegido.get('nombre')}")
    else:
        print("\nCancelado.")
        sys.exit(1)
