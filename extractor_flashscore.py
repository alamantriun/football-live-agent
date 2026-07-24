"""
extractor_flashscore.py — Extractor de datos en vivo desde Flashscore.

Usa Playwright para consultar el partido seleccionado y extraer
las estadísticas (posesión, remates, esquinas, etc.)
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timezone

logging.basicConfig(format="[%(asctime)s] [%(name)s] %(message)s")
logger = logging.getLogger("extractor_flashscore")
logger.setLevel(logging.INFO)

POLL_INTERVAL = 30.0  # cada 30 segundos
FLASHSCORE_BASE = "https://www.flashscore.co/partido"

def _parsear_marcador(page_text: str) -> tuple[int, int]:
    nums = re.findall(r"\d+", page_text or "")
    if len(nums) >= 2:
        return int(nums[0]), int(nums[1])
    return 0, 0


def _minuto_desde_estado(estado: str) -> float:
    low = estado.lower().strip()
    m = re.search(r"(\d+)", low)
    if m:
        return float(m.group(1))
    if any(x in low for x in ("descanso", "mitad", "half time", "halftime", "ht", "mt")):
        return 45.0
    if any(x in low for x in ("finalizado", "finished", "fin")):
        return 90.0
    return 0.0


def _aplicar_stat(name: str, h_val: float, a_val: float, stats: dict) -> None:
    if "posesión" in name or "possession" in name:
        stats["posesion"]["local"] = h_val
        stats["posesion"]["visitante"] = a_val
    elif "remates" in name or "tiros" in name or "shots" in name:
        if "arco" in name or "puerta" in name or "on goal" in name:
            stats["tiros_puerta"]["local"] = int(h_val)
            stats["tiros_puerta"]["visitante"] = int(a_val)
        else:
            stats["tiros"]["local"] = int(h_val)
            stats["tiros"]["visitante"] = int(a_val)
    elif "ataques peligrosos" in name or "dangerous attacks" in name:
        stats["ataques_peligrosos"]["local"] = int(h_val)
        stats["ataques_peligrosos"]["visitante"] = int(a_val)
    elif "ataques" in name or "attacks" in name:
        stats["ataques"]["local"] = int(h_val)
        stats["ataques"]["visitante"] = int(a_val)
    elif "faltas" in name or "fouls" in name:
        stats["faltas"]["local"] = int(h_val)
        stats["faltas"]["visitante"] = int(a_val)
    elif "amarillas" in name or "yellow cards" in name:
        stats["tarjetas_amarillas"]["local"] = int(h_val)
        stats["tarjetas_amarillas"]["visitante"] = int(a_val)
    elif "rojas" in name or "red cards" in name:
        stats["tarjetas_rojas"]["local"] = int(h_val)
        stats["tarjetas_rojas"]["visitante"] = int(a_val)
    elif "esquina" in name or "corner kicks" in name or "córneres" in name:
        stats["saques_esquina"]["local"] = int(h_val)
        stats["saques_esquina"]["visitante"] = int(a_val)
    elif "fueras" in name or "offsides" in name:
        stats["fueras_juego"]["local"] = int(h_val)
        stats["fueras_juego"]["visitante"] = int(a_val)
    elif "paradas" in name or "saves" in name or "salvadas" in name:
        stats["paradas_portero"]["local"] = int(h_val)
        stats["paradas_portero"]["visitante"] = int(a_val)
    elif "libres" in name or "free kicks" in name:
        stats["tiros_libres"]["local"] = int(h_val)
        stats["tiros_libres"]["visitante"] = int(a_val)


def _to_float(val_str: str) -> float:
    val_str = (val_str or "").replace("%", "").strip()
    if not val_str:
        return 0.0
    try:
        return float(val_str)
    except ValueError:
        return 0.0


def _evento_nulo() -> dict:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "minuto": None,
        "marcador": None,
        "posesion": None,
        "tiros": None,
        "ataques_peligrosos": None,
        "acciones": None,
        "_jugadores": [],
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

async def obtener_evento_flashscore(page, fixture_id: str) -> dict:
    if not fixture_id:
        return _evento_nulo()

    url = f"{FLASHSCORE_BASE}/{fixture_id}/#/estadisticas-del-partido/estadisticas-del-partido"
    
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        try:
            await page.wait_for_selector(".detailScore__wrapper", timeout=8000)
        except Exception:
            await page.wait_for_timeout(3000)
        else:
            await page.wait_for_timeout(1500)
    except Exception as e:
        logger.warning(f"Error cargando flashscore: {e}")
        return _evento_nulo()
        
    try:
        minuto_el = await page.query_selector(".detailScore__status")
        home_el = await page.query_selector(".duelParticipant__home .participant__participantName")
        away_el = await page.query_selector(".duelParticipant__away .participant__participantName")
        score_wrapper = await page.query_selector(".detailScore__wrapper")
        
        minuto_text = await minuto_el.inner_text() if minuto_el else ""
        local = await home_el.inner_text() if home_el else "Local"
        visitante = await away_el.inner_text() if away_el else "Visitante"
        
        if score_wrapper:
            gl, gv = _parsear_marcador(await score_wrapper.inner_text())
        else:
            gl, gv = 0, 0
            
        estado = minuto_text.replace("\n", " ").strip()
        minuto_val = _minuto_desde_estado(estado)
            
        stats = {
            "posesion": {"local": 50.0, "visitante": 50.0},
            "tiros": {"local": 0, "visitante": 0},
            "tiros_puerta": {"local": 0, "visitante": 0},
            "ataques_peligrosos": {"local": 0, "visitante": 0},
            "ataques": {"local": 0, "visitante": 0},
            "faltas": {"local": 0, "visitante": 0},
            "tarjetas_amarillas": {"local": 0, "visitante": 0},
            "tarjetas_rojas": {"local": 0, "visitante": 0},
            "saques_esquina": {"local": 0, "visitante": 0},
            "fueras_juego": {"local": 0, "visitante": 0},
            "paradas_portero": {"local": 0, "visitante": 0},
            "tiros_libres": {"local": 0, "visitante": 0},
        }
        
        cats = await page.query_selector_all(".stat__category")
        for cat in cats:
            name_el = await cat.query_selector(".stat__categoryName")
            if not name_el:
                continue
            name = (await name_el.inner_text()).lower()
            home_val_el = await cat.query_selector(".stat__homeValue")
            away_val_el = await cat.query_selector(".stat__awayValue")
            if not home_val_el or not away_val_el:
                continue
            h_val = _to_float(await home_val_el.inner_text())
            a_val = _to_float(await away_val_el.inner_text())
            _aplicar_stat(name, h_val, a_val, stats)

        if not cats:
            for row in await page.query_selector_all(".wcl-category_Ydwqh"):
                partes = [p.strip() for p in (await row.inner_text()).split("\n") if p.strip()]
                if len(partes) < 3:
                    continue
                h_val = _to_float(partes[0])
                name = partes[1].lower()
                a_val = _to_float(partes[2])
                _aplicar_stat(name, h_val, a_val, stats)
                
        # Si no hay ataques peligrosos, estimarlos a partir de tiros y corners
        if stats["ataques_peligrosos"]["local"] == 0:
            stats["ataques_peligrosos"]["local"] = stats["tiros"]["local"] * 3
            stats["ataques_peligrosos"]["visitante"] = stats["tiros"]["visitante"] * 3
            
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "minuto": minuto_val if minuto_val > 0 else None,
            "marcador": {"local": gl, "visitante": gv},
            "posesion": stats["posesion"],
            "tiros": stats["tiros"],
            "tiros_puerta": stats["tiros_puerta"],
            "ataques_peligrosos": stats["ataques_peligrosos"],
            "ataques": stats["ataques"],
            "faltas": stats["faltas"],
            "tarjetas_amarillas": stats["tarjetas_amarillas"],
            "tarjetas_rojas": stats["tarjetas_rojas"],
            "saques_esquina": stats["saques_esquina"],
            "fueras_juego": stats["fueras_juego"],
            "paradas_portero": stats["paradas_portero"],
            "tiros_libres": stats["tiros_libres"],
            "acciones": [],
            "_equipos": {"local": local, "visitante": visitante},
            "_status": estado,
            "_jugadores": [],
        }
    except Exception as e:
        logger.warning(f"Error procesando flashscore: {e}")
        return _evento_nulo()

async def iniciar(raw_queue: asyncio.Queue) -> None:
    fixture_id = os.getenv("FIXTURE_ID")
    if not fixture_id:
        logger.error("FIXTURE_ID no está configurado.")
        return

    logger.info(f"Iniciando extractor Flashscore (ID: {fixture_id})")
    pw, browser, page = await _crear_browser()
    try:
        while True:
            evento = await obtener_evento_flashscore(page, fixture_id)
            if evento.get("minuto") is not None or evento.get("marcador"):
                await raw_queue.put(evento)
                logger.info(f"Flashscore: Min {evento['minuto']}' | Pos {evento['posesion']['local']}-{evento['posesion']['visitante']}% | Remates {evento['tiros']['local']}-{evento['tiros']['visitante']}")

            if "finalizado" in evento.get("_status", "").lower():
                logger.info("Partido finalizado en Flashscore.")

            await asyncio.sleep(POLL_INTERVAL)
    finally:
        await browser.close()
        await pw.stop()

async def iniciar_con_reconexion(raw_queue: asyncio.Queue) -> None:
    while True:
        try:
            await iniciar(raw_queue)
        except Exception as e:
            logger.error(f"Error en Flashscore Scraper: {e} — reconectando en 5s")
            await asyncio.sleep(5)
