"""
football_data_client.py — Cliente para football-data.org v4.

Plan gratuito: 10 peticiones / minuto.
Todas las llamadas pasan por el rate limiter compartido.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

logger = logging.getLogger("football_data")

API_BASE = "https://api.football-data.org/v4"
MAX_REQUESTS_PER_MINUTE = 10
RATE_WINDOW_SEC = 60.0

_key_cache: str | None = None


class RateLimiter:
    """Ventana deslizante: máximo N peticiones por minuto."""

    def __init__(self, max_calls: int = MAX_REQUESTS_PER_MINUTE, period: float = RATE_WINDOW_SEC):
        self.max_calls = max_calls
        self.period = period
        self._times: list[float] = []
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._times = [t for t in self._times if now - t < self.period]
            if len(self._times) >= self.max_calls:
                espera = self.period - (now - self._times[0]) + 0.1
                if espera > 0:
                    logger.warning(
                        "Límite API (%d/min) — esperando %.1fs",
                        self.max_calls, espera,
                    )
                    time.sleep(espera)
                    now = time.monotonic()
                    self._times = [t for t in self._times if now - t < self.period]
            self._times.append(time.monotonic())


_limiter = RateLimiter()


def cargar_api_key() -> str:
    global _key_cache
    if _key_cache:
        return _key_cache

    key = os.getenv("FOOTBALL_DATA_API_KEY") or os.getenv("API_FOOTBALL_KEY", "")
    if not key:
        key_file = Path(__file__).parent / "api_key.txt"
        if key_file.exists():
            key = key_file.read_text(encoding="utf-8").strip()

    if not key:
        raise ValueError(
            "API key no configurada. Crea api_key.txt o define FOOTBALL_DATA_API_KEY."
        )
    _key_cache = key
    return key


def api_get(endpoint: str, params: dict | None = None, unfold: bool = False) -> dict:
    """GET con rate limiting y cabecera X-Auth-Token."""
    _limiter.acquire()
    key = cargar_api_key()

    path = endpoint.lstrip("/")
    url = f"{API_BASE}/{path}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{query}"

    req = Request(url)
    req.add_header("X-Auth-Token", key)
    if unfold:
        for hdr in (
            "X-Unfold-Lineups",
            "X-Unfold-Goals",
            "X-Unfold-Bookings",
            "X-Unfold-Subs",
        ):
            req.add_header(hdr, "true")

    try:
        with urlopen(req, timeout=15) as resp:
            disponibles = resp.headers.get("X-Requests-Available")
            if disponibles is not None:
                logger.debug("Peticiones API restantes: %s", disponibles)
            return json.loads(resp.read().decode())
    except HTTPError as exc:
        if exc.code == 429:
            logger.error("Rate limit 429 — esperando 60s")
            time.sleep(RATE_WINDOW_SEC)
            return api_get(endpoint, params, unfold=unfold)
        raise


def listar_partidos_vivo() -> list[dict]:
    """Una petición: todos los partidos en juego."""
    data = api_get("matches", {"status": "LIVE,IN_PLAY,PAUSED"}, unfold=True)
    partidos = []
    for m in data.get("matches", []):
        home = m.get("homeTeam", {})
        away = m.get("awayTeam", {})
        score = m.get("score", {}).get("fullTime", {}) or {}
        partidos.append({
            "id": m.get("id"),
            "nombre": f"{home.get('name', '?')} vs {away.get('name', '?')}",
            "liga": m.get("competition", {}).get("name", "En vivo"),
            "marcador": f"{score.get('home', 0)}-{score.get('away', 0)}",
            "minuto": m.get("minute"),
            "hora": "",
            "estado": "En Vivo",
            "fixture_id": m.get("id"),
            "modo": "api",
        })
    return partidos


def obtener_partido(match_id: str | int) -> dict:
    """Una petición: partido completo (stats, alineaciones, goles si el plan lo permite)."""
    return api_get(f"matches/{match_id}", unfold=True)
