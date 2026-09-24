"""Recolecta partidos y recupera finales mientras el proceso está abierto.

Uso independiente: python recolector_aprendizaje.py
No requiere navegador abierto. La web lo inicia automáticamente salvo demo.
"""
import argparse
import asyncio
import logging
import time
import uuid

import aprendizaje
import extractor_365scores as proveedor
import simulador
from registro_vivo import DEFAULT_DB, abrir_db, registrar

logger = logging.getLogger(__name__)
FINALES = {"ft", "finished", "ended", "finalizado", "full time"}


class Recolector:
    def __init__(self, db=DEFAULT_DB, max_partidos=4, intervalo=60):
        self.db = db
        self.model_path = db.parent / "modelo_vivo.json"
        self.state_path = db.parent / "aprendizaje_estado.json"
        self.max_partidos = max_partidos
        self.intervalo = intervalo
        self.owner = uuid.uuid4().hex
        self.ultimo_entrenamiento = 0
        self.ultimo_ciclo = None
        self.error = None
        self.activo = False

    def reservar(self):
        with abrir_db(self.db) as db:
            db.execute("INSERT OR IGNORE INTO leases VALUES ('recolector','',0)")
            return bool(db.execute("UPDATE leases SET propietario=?,vence=? WHERE nombre='recolector' AND (vence<? OR propietario=?)", (self.owner, time.time()+900, time.time(), self.owner)).rowcount)

    def liberar(self):
        with abrir_db(self.db) as db:
            db.execute("DELETE FROM leases WHERE nombre='recolector' AND propietario=?", (self.owner,))

    def pendientes(self):
        with abrir_db(self.db) as db:
            # Recupera también los partidos vistos en versiones anteriores.
            db.execute("INSERT OR IGNORE INTO seguimiento(fixture,nombre,creado) SELECT fixture,fixture,? FROM pronosticos WHERE fixture NOT IN (SELECT fixture FROM finales) GROUP BY fixture", (time.time(),))
            db.execute("UPDATE seguimiento SET estado='sin_final' WHERE estado='pendiente' AND creado<?", (time.time()-86400,))
            return db.execute("SELECT fixture,nombre FROM seguimiento WHERE estado='pendiente' ORDER BY consultado ASC,creado ASC LIMIT ?", (self.max_partidos,)).fetchall()

    async def observar(self, fixture):
        # Fecha de consulta antes de red: rota pendientes incluso cuando el proveedor falla.
        with abrir_db(self.db) as db:
            db.execute("UPDATE seguimiento SET consultado=? WHERE fixture=?", (time.time(), fixture))
            max_min = db.execute("SELECT MAX(minuto) FROM pronosticos WHERE fixture=?", (fixture,)).fetchone()[0]
        ev = await proveedor.obtener_evento_365scores(fixture)
        m = ev.get("marcador") or {}
        if ev.get("_fuente") != "365scores" or any(m.get(k) is None for k in ("local", "visitante")):
            raise ValueError("El proveedor no devolvió un marcador válido")
        status = (ev.get("_status") or "").strip().lower()
        if status in FINALES:
            # No mezclamos marcadores posteriores a prórroga con horizontes de 90 minutos.
            if ev.get("minuto") is not None and float(ev["minuto"]) > 105:
                with abrir_db(self.db) as db:
                    db.execute("UPDATE seguimiento SET estado='fuera_de_horizonte' WHERE fixture=?", (fixture,))
                return
            await asyncio.to_thread(registrar, ev, {}, True, self.db)
        elif ev.get("minuto") is not None and 0 <= float(ev["minuto"]) < 90:
            if max_min is not None and float(ev["minuto"]) < max_min:
                return
            pred = simulador.correr([ev], ev["minuto"], m, ajuste=aprendizaje.modelo_activo(self.model_path))
            await asyncio.to_thread(registrar, ev, pred, False, self.db)

    async def ciclo(self):
        if not self.reservar():
            self.activo = False
            return
        self.activo, self.error = True, None
        pendientes = self.pendientes()
        if len(pendientes) < self.max_partidos:
            vivos = await asyncio.to_thread(proveedor.listar_partidos_vivo_sync)
            with abrir_db(self.db) as db:
                conocidos = {r[0] for r in db.execute("SELECT fixture FROM seguimiento")}
                nuevos = [p for p in vivos if str(p["fixture_id"]) not in conocidos and 0 <= (p.get("minuto") or 0) < 80]
                # Prioriza inicios para observar varias fases completas del encuentro.
                nuevos.sort(key=lambda p: p.get("minuto") or 0)
                for p in nuevos[:self.max_partidos-len(pendientes)]:
                    db.execute("INSERT OR IGNORE INTO seguimiento(fixture,nombre,creado) VALUES (?,?,?)", (str(p["fixture_id"]), p["nombre"], time.time()))
            pendientes = self.pendientes()
        sem = asyncio.Semaphore(2)
        async def observar_limitado(fid):
            async with sem:
                try:
                    await self.observar(fid)
                except Exception as exc:
                    self.error = "Algunas consultas fallaron; se reintentarán"
                    logger.warning("Recolección %s: %s", fid, exc)
        await asyncio.gather(*(observar_limitado(fid) for fid, _ in pendientes))
        if time.monotonic() - self.ultimo_entrenamiento >= 900 or not self.ultimo_entrenamiento:
            await asyncio.to_thread(aprendizaje.entrenar, self.db, self.model_path, self.state_path)
            self.ultimo_entrenamiento = time.monotonic()
        self.ultimo_ciclo = time.time()

    async def ejecutar(self):
        try:
            while True:
                try:
                    await self.ciclo()
                except Exception:
                    self.error = "No se completó el ciclo; se reintentará automáticamente"
                    logger.exception("Error en ciclo de aprendizaje")
                await asyncio.sleep(self.intervalo)
        finally:
            self.activo = False
            self.liberar()

    def estado(self):
        with abrir_db(self.db) as db:
            muestras, partidos = db.execute("SELECT COUNT(*),COUNT(DISTINCT fixture) FROM pronosticos").fetchone()
            finales = db.execute("SELECT COUNT(*) FROM finales").fetchone()[0]
            pendientes = db.execute("SELECT COUNT(*) FROM seguimiento WHERE estado='pendiente'").fetchone()[0]
            lease = db.execute("SELECT vence FROM leases WHERE nombre='recolector'").fetchone()
        reporte = aprendizaje.leer_json(self.state_path, {})
        modelo = aprendizaje.modelo_activo(self.model_path)
        return {"habilitado": True, "recolectando": bool(lease and lease[0] > time.time()),
                "muestras": muestras, "partidos": partidos, "finalizados": finales, "pendientes": pendientes,
                "partidos_utiles": reporte.get("partidos_utiles", 0), "minimo": aprendizaje.MIN_TRAIN + aprendizaje.MIN_VALID,
                "modelo": modelo["version"], "ajuste_activo": modelo["aprobado"],
                "estado": reporte.get("estado", "recolectando"), "mensaje": reporte.get("mensaje", "Reuniendo observaciones en vivo y resultados finales"),
                "ultima_evaluacion": reporte.get("ultima_evaluacion"),
                "ultimo_ciclo": self.ultimo_ciclo, "error": self.error}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--una-vez", action="store_true", help="Realiza un ciclo y termina")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    recolector = Recolector()
    try:
        asyncio.run(recolector.ciclo() if args.una_vez else recolector.ejecutar())
    except KeyboardInterrupt:
        pass
    finally:
        recolector.liberar()
