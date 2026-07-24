"""
extractor_vivo.py — Módulo de extracción de datos en vivo.

Único punto de contacto con la fuente de datos externa.
Usa Playwright en modo headless para capturar respuestas JSON
de una URL de apuestas deportivas, normaliza los datos al schema
GameEvent y los coloca en una asyncio.Queue para consumo downstream.

No realiza cálculos: solo captura y normaliza.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

logging.basicConfig(format="[%(asctime)s] [%(name)s] %(message)s")
logger = logging.getLogger("extractor_vivo")
logger.setLevel(logging.DEBUG)

# ─────────────────────────── CONSTANTES ───────────────────────────
POLL_INTERVAL = 2.0          # segundos entre capturas
MAX_REINTENTOS = 5
BACKOFF_BASE = 2.0           # segundos de espera base (exponencial)
BETTING_URL = os.getenv("BETTING_URL", "https://ejemplo.com/partido")

# ─────────────────────── SCHEMA GameEvent ─────────────────────────
GAME_EVENT_FIELDS = [
    "timestamp", "minuto", "marcador", "posesion",
    "tiros", "ataques_peligrosos", "acciones",
]


def _normalizar_evento(raw: dict) -> dict:
    """
    Mapea un JSON crudo al schema GameEvent.
    Si un campo no existe, lo asigna como None y loguea advertencia.
    """
    evento = {}
    for campo in GAME_EVENT_FIELDS:
        if campo in raw:
            evento[campo] = raw[campo]
        else:
            evento[campo] = None
            logger.warning("Campo faltante: %s", campo)

    # Asegurar timestamp si no viene
    if evento.get("timestamp") is None:
        evento["timestamp"] = datetime.now(timezone.utc).isoformat()

    return evento


async def iniciar(raw_queue: asyncio.Queue) -> None:
    """
    Función principal del extractor.

    1. Abre BETTING_URL con Playwright (headless).
    2. Registra handler page.on("response") que filtra solo JSON.
    3. Para cada respuesta JSON: parsea → normaliza → pone en raw_queue.
    4. Mantiene loop con POLL_INTERVAL entre iteraciones.
    """
    from playwright.async_api import async_playwright

    url = os.getenv("BETTING_URL", BETTING_URL)
    logger.info("Iniciando extractor para URL: %s", url)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        async def _on_response(response):
            content_type = response.headers.get("content-type", "")
            if "application/json" not in content_type:
                return
            try:
                body = await response.json()
                
                # Escribir el primer payload interesante de Betano para calibrarlo
                if isinstance(body, dict) and len(str(body)) > 100:
                    # Solo volcamos un archivo si no existe para no sobreescribir mil veces
                    if not os.path.exists("betano_dump.json"):
                        with open("betano_dump.json", "w", encoding="utf-8") as f:
                            json.dump(body, f, indent=2)
                        logger.info("¡EUREKA! Se capturó el JSON de Betano en 'betano_dump.json'. Pásame este archivo para calibrar el normalizador.")

                # Intento de normalización genérica (probablemente falle con Betano directo)
                evento = _normalizar_evento(body)
                if evento.get("minuto") is not None:
                    await raw_queue.put(evento)
                    logger.info("Evento capturado — minuto: %s", evento.get("minuto"))
            except Exception as exc:
                pass # Ignorar errores de parseo de JSONs irrelevantes

        page.on("response", _on_response)

        await page.goto(url, wait_until="domcontentloaded")
        logger.info("Página cargada en Playwright: %s", url)

        # Loop de polling: mantiene la página activa
        while True:
            await page.wait_for_timeout(int(POLL_INTERVAL * 1000))


async def iniciar_con_reconexion(raw_queue: asyncio.Queue) -> None:
    """
    Wrapper con backoff exponencial alrededor de iniciar().

    - Si Playwright lanza cualquier excepción, espera BACKOFF_BASE ** intento
      segundos y reintenta hasta MAX_REINTENTOS.
    - Al agotar intentos, emite un GameEvent nulo.
    """
    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            await iniciar(raw_queue)
            return  # Salida limpia (no debería llegar aquí normalmente)
        except Exception as exc:
            espera = BACKOFF_BASE ** intento
            logger.warning(
                "Reconectando... intento %d/%d (espera %.1fs) — %s",
                intento, MAX_REINTENTOS, espera, exc,
            )
            await asyncio.sleep(espera)

    # Se agotaron los reintentos
    logger.critical("Fuente inaccesible. Emitiendo evento nulo.")
    evento_nulo = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "minuto": None,
        "marcador": None,
        "posesion": None,
        "tiros": None,
        "ataques_peligrosos": None,
        "acciones": None,
    }
    await raw_queue.put(evento_nulo)


if __name__ == "__main__":
    async def _main():
        queue = asyncio.Queue()
        await iniciar_con_reconexion(queue)

    asyncio.run(_main())
