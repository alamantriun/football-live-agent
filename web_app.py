"""Aplicación web completa: selector, control del análisis y dashboard."""
import argparse
import asyncio
import logging
import os
import threading
import time
import uuid
import webbrowser
from copy import deepcopy
from datetime import datetime, timezone

import dashboard_web
import extractor_365scores
import main_agente
import predictor_previo
import selector_visual
import simulador
from motor_metricas import MotorMetricas
from modelo_poisson import predecir

logger = logging.getLogger("web_app")


class ControladorWeb:
    def __init__(self, demo=False):
        self.demo = demo
        self._lock = threading.RLock()
        self._control = {
            "fase": "inicio",
            "mensaje": "Selecciona un partido para comenzar",
            "error": None,
            "partido": None,
        }
        self._cache = {}
        self._tarea = None
        self._transicion = asyncio.Lock()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._ejecutar_loop, daemon=True)
        self._thread.start()

    def _ejecutar_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _set_control(self, **cambios):
        with self._lock:
            self._control.update(cambios)
        if dashboard_web.puerto_activo():
            dashboard_web.publicar_estado(self.estado())

    def estado(self):
        base = main_agente._obtener_estado_web()
        with self._lock:
            base["control"] = deepcopy(self._control)
        if base["control"]["fase"] == "en_vivo" and base["timestamp"]:
            base["control"]["mensaje"] = "Datos en vivo actualizados" if base["calidad"]["vigente"] else "Esperando actualización del proveedor"
        return base

    @staticmethod
    def _demo_partidos(categoria):
        ejemplos = {
            "en_vivo": [{"id": "demo-live", "fixture_id": "demo-live", "nombre": "Atlético Central vs Deportivo Norte", "liga": "Partido de demostración", "estado": "En Vivo", "minuto": 63, "marcador": "1-1", "modo": "demo", "categoria": "en_vivo"}],
            "hoy": [{"id": "demo-today", "fixture_id": "demo-today", "nombre": "Unión Verde vs Real Bahía", "liga": "Partido de demostración", "estado": "20:30", "hora": "20:30", "marcador": "—", "modo": "demo", "categoria": "hoy"}],
            "proximos": [{"id": "demo-next", "fixture_id": "demo-next", "nombre": "Ciudad FC vs Racing del Valle", "liga": "Partido de demostración", "estado": "📅 12.09. 18:00", "marcador": "—", "modo": "demo", "categoria": "proximos"}],
            "guardados": [],
        }
        return ejemplos.get(categoria, sum(ejemplos.values(), []))

    def listar(self, categoria="en_vivo"):
        if categoria not in {"en_vivo", "hoy", "proximos", "guardados", "todos"}:
            raise ValueError("Categoría no válida")
        if self.demo:
            return self._demo_partidos(categoria)
        ahora = time.monotonic()
        with self._lock:
            cache = self._cache.get(categoria)
            if cache and ahora - cache[0] < 45:
                return deepcopy(cache[1])
        if categoria == "en_vivo":
            partidos = extractor_365scores.listar_partidos_vivo_sync()
        elif categoria == "guardados":
            partidos = selector_visual.cargar_guardados()
        elif categoria == "hoy":
            partidos = extractor_365scores.listar_partidos_hoy_sync()
        elif categoria == "proximos":
            partidos = extractor_365scores.listar_partidos_proximos_sync()
        else:
            partidos = self.listar("en_vivo") + self.listar("guardados")
        for partido in partidos:
            partido["categoria"] = categoria
        with self._lock:
            self._cache[categoria] = (ahora, deepcopy(partidos))
        return partidos

    @staticmethod
    def _validar_partido(data):
        nombre = str(data.get("nombre", "")).strip()
        fixture = str(data.get("fixture_id") or data.get("id") or "").strip()
        if not 3 <= len(nombre) <= 180 or " vs " not in nombre.lower():
            raise ValueError("Nombre de partido inválido")
        if not fixture or len(fixture) > 80 or not all(c.isalnum() or c in "-_" for c in fixture):
            raise ValueError("Identificador de partido inválido")
        if data.get("modo", "365scores") not in {"365scores", "flashscore", "demo"}:
            raise ValueError("Proveedor no soportado")
        partido = {k: data.get(k) for k in ("id", "fixture_id", "nombre", "liga", "estado", "hora", "marcador", "modo", "categoria", "fecha")}
        import re
        partido["nombre"] = " vs ".join(re.split(r"\s+vs\s+", nombre, maxsplit=1, flags=re.I))
        partido["fixture_id"] = fixture
        return partido

    def seleccionar(self, data):
        partido = self._validar_partido(data)
        asyncio.run_coroutine_threadsafe(self._seleccionar(partido), self._loop).result(timeout=10)
        return {"mensaje": "Análisis iniciado", "partido": partido}

    async def _parar_tarea(self):
        if self._tarea:
            self._tarea.cancel()
            await asyncio.gather(self._tarea, return_exceptions=True)
            self._tarea = None

    async def _seleccionar(self, partido):
        async with self._transicion:
            await self._parar_tarea()
            self._iniciar_partido(partido)

    def _iniciar_partido(self, partido):
        main_agente.reiniciar_estado()
        main_agente.nombre_partido = partido["nombre"]
        main_agente.modo_extraccion = partido.get("modo") or "365scores"
        self._set_control(fase="cargando", mensaje="Preparando análisis…", error=None, partido=partido)
        if self.demo or partido.get("modo") == "demo":
            self._tarea = asyncio.create_task(self._demo_en_movimiento(partido))
        elif partido.get("modo") == "365scores":
            self._tarea = asyncio.create_task(self._resolver_partido(partido))
        elif (
            partido.get("modo") == "flashscore"
            or partido.get("categoria") in {"hoy", "proximos"}
            or "📅" in str(partido.get("estado"))
        ):
            self._tarea = asyncio.create_task(self._analizar_prepartido(partido))
        else:
            os.environ["FIXTURE_ID"] = str(partido["fixture_id"] or partido["id"])
            self._tarea = asyncio.create_task(self._analizar_en_vivo(partido))

    async def _resolver_partido(self, partido):
        """Revalida favoritos/programados antes de escoger el flujo."""
        try:
            data = await asyncio.to_thread(extractor_365scores._http_get_json, "/game/", gameId=partido["fixture_id"])
            game = (data or {}).get("game")
            if not game:
                raise ValueError("El proveedor no respondió con el partido solicitado")
            if game.get("statusGroup") == 2:
                await self._analizar_prepartido(partido)
            else:
                os.environ["FIXTURE_ID"] = partido["fixture_id"]
                await self._analizar_en_vivo(partido)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._set_control(fase="error", mensaje="No se pudo consultar el partido", error=str(exc))

    async def _demo_en_movimiento(self, partido):
        self._activar_demo(partido)
        if partido.get("categoria") != "en_vivo":
            main_agente.buffer_q1 = []
            main_agente.ultimo_metrics = {k: v for k, v in main_agente.ultimo_metrics.items()
                                         if k in {"timestamp", "minuto", "marcador", "_status", "_equipos"}}
            self._set_control(fase="demo", mensaje="Demostración prepartido")
            return
        motor = MotorMetricas()
        minuto = 48
        while True:
            ev = deepcopy(main_agente.ultimo_metrics)
            ev.update(timestamp=datetime.now(timezone.utc).isoformat(), minuto=minuto,
                      _fuente="Demostración sintética",
                      tiros={"local": 8 + (minuto - 48)//3, "visitante": 6 + (minuto - 48)//5},
                      tiros_puerta={"local": 3 + (minuto - 48)//8, "visitante": 2 + (minuto - 48)//12},
                      saques_esquina={"local": 5, "visitante": 3},
                      tarjetas_rojas={"local": 0, "visitante": 0})
            ev["ataques_peligrosos"] = dict(ev["tiros_puerta"])
            motor.agregar_evento(ev)
            main_agente.ultimo_metrics = motor.calcular_metricas(ev)
            main_agente.buffer_q1.append(deepcopy(main_agente.ultimo_metrics))
            main_agente.buffer_q1 = main_agente.buffer_q1[-180:]
            pred = simulador.correr([ev], minuto, ev["marcador"])
            pred["advertencias"].insert(0, "Demostración: datos sintéticos, reloj acelerado")
            main_agente.prediccion_actual = pred
            main_agente.historial_mc.append({"minuto": minuto, **{k: v for k, v in pred.items() if k.startswith("prob_1x2")}})
            main_agente.historial_mc = main_agente.historial_mc[-180:]
            self._set_control(fase="demo", mensaje="Demo · reloj acelerado")
            await asyncio.sleep(4)
            minuto += 1
            if minuto > 85:
                minuto = 48
                motor = MotorMetricas()
                main_agente.buffer_q1 = []
                main_agente.historial_mc = []

    def _activar_demo(self, partido):
        local, visita = [x.strip() for x in partido["nombre"].split(" vs ", 1)]
        es_vivo = partido.get("categoria") == "en_vivo"
        main_agente.ultimo_metrics = {
            "timestamp": datetime.now(timezone.utc).isoformat(), "minuto": 63 if es_vivo else None,
            "marcador": {"local": 1, "visitante": 1} if es_vivo else {"local": 0, "visitante": 0},
            "_status": "En Vivo · demostración" if es_vivo else "Prepartido · demostración",
            "_equipos": {"local": local, "visitante": visita},
            "posesion": {"local": 54, "visitante": 46},
            "tiros": {"local": 12, "visitante": 8},
            "ataques_peligrosos": {"local": 7, "visitante": 4},
            "riesgo_gol_local": 68, "riesgo_gol_visitante": 43,
            "animo_local": 61, "animo_visitante": 39,
            "_cronologia": [
                {"tipo": "gol", "jugador": "Jugador local", "minuto": "31", "equipo": "local"},
                {"tipo": "gol", "jugador": "Jugador visitante", "minuto": "44", "equipo": "visitante"},
            ] if es_vivo else [],
            "_fuente": "Demostración sintética",
        }
        main_agente.buffer_q1 = []
        if not es_vivo:
            main_agente.ultimo_metrics = {k: v for k, v in main_agente.ultimo_metrics.items()
                                         if k in {"timestamp", "minuto", "marcador", "_status", "_equipos", "_fuente"}}
        main_agente.prediccion_actual = predecir(.72, .38, {"local": 1, "visitante": 1}) if es_vivo else predecir(1.62, 1.18)
        main_agente.prediccion_actual.update(tiempo_restante=27 if es_vivo else 90, advertencias=["Datos de demostración"])
        self._set_control(fase="demo", mensaje="Demostración activa", error=None, partido=partido)

    async def _analizar_en_vivo(self, partido):
        self._set_control(fase="en_vivo", mensaje="Conectando con datos en vivo…", error=None)
        try:
            historico = os.getenv("FOOTBALL_HISTORY")
            if historico and partido.get("fecha"):
                from modelo_historico import cargar, estimar
                from datetime import date
                try:
                    local, visita = partido["nombre"].split(" vs ", 1)
                    def estimacion():
                        return estimar(cargar(historico.split(os.pathsep)), local, visita, date.fromisoformat(partido["fecha"]))
                    tl, tv, soporte = await asyncio.to_thread(estimacion)
                    if min(soporte["partidos_local"], soporte["partidos_visita"]) >= 5:
                        main_agente.prior_partido = {"local": tl, "visitante": tv, "soporte": soporte, "fuente": "Histórico por sede"}
                except (ValueError, OSError, KeyError):
                    logger.warning("Histórico incompatible: se usa prior general")
            await main_agente.loop_principal(mostrar_terminal=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Fallo en análisis en vivo")
            self._set_control(fase="error", mensaje="No se pudo mantener el análisis", error=str(exc))

    async def _analizar_prepartido(self, partido):
        self._set_control(fase="prepartido", mensaje="Calculando predicción prepartido…", error=None)
        try:
            local, visita = [x.strip() for x in partido["nombre"].split(" vs ", 1)]
            historico = os.getenv("FOOTBALL_HISTORY")
            if historico and partido.get("fecha"):
                from datetime import date
                from modelo_historico import cargar, pronosticar
                pred = await asyncio.to_thread(
                    pronosticar, cargar(historico.split(os.pathsep)), local, visita,
                    date.fromisoformat(partido["fecha"]),
                )
            elif partido.get("modo") == "flashscore":
                pred = await predictor_previo.predecir_h2h(str(partido["fixture_id"] or partido["id"]))
            else:
                pred = predecir(1.5, 1.2, muestras=5000)
                pred["fuente"] = "Prior general sin historial de equipos"
                pred["advertencias"] = [
                    "Sin histórico compatible para estos equipos",
                    "Predicción base: no diferencia la fuerza de los equipos",
                ]
            main_agente.ultimo_metrics = {
                "timestamp": datetime.now(timezone.utc).isoformat(), "minuto": None,
                "marcador": {"local": 0, "visitante": 0}, "_status": "Prepartido",
                "_equipos": {"local": local, "visitante": visita},
                "posesion": {"local": 50, "visitante": 50},
            }
            main_agente.prediccion_actual = pred
            self._set_control(fase="prepartido_listo", mensaje="Predicción prepartido lista", error=None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._set_control(fase="error", mensaje="No se emite predicción prepartido", error=str(exc))

    def detener(self):
        asyncio.run_coroutine_threadsafe(self._detener(), self._loop).result(timeout=10)
        return {"mensaje": "Análisis detenido"}

    async def _detener(self):
        async with self._transicion:
            await self._parar_tarea()
            main_agente.reiniciar_estado()
            self._set_control(fase="inicio", mensaje="Análisis detenido. Selecciona otro partido.", error=None, partido=None)

    def cerrar(self):
        self.detener()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._loop.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.getenv("WEB_PORT", "8765")))
    parser.add_argument("--demo", action="store_true", help="Usa partidos locales de demostración")
    parser.add_argument("--open", action="store_true", help="Abre el navegador al iniciar")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(name)s] %(message)s")
    controlador = ControladorWeb(demo=args.demo)
    dashboard_web.configurar_controles(listar=controlador.listar, seleccionar=controlador.seleccionar, detener=controlador.detener)
    puerto = dashboard_web.iniciar(controlador.estado, puerto=args.port, sesion=uuid.uuid4().hex[:8])
    dashboard_web.publicar_estado(controlador.estado())
    url = f"http://127.0.0.1:{puerto}"
    print(f"Football Live Agent web: {url}")
    if args.open:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        controlador.cerrar()
        dashboard_web.detener()


if __name__ == "__main__":
    main()
