from datetime import datetime, timedelta, timezone
import pytest

from calidad_vivo import evaluar
from dashboard_web import empaquetar_estado, construir_serie_temporal, evaluar_calidad
from extractor_365scores import _parsear_estadisticas
from simulador import correr


def evento():
    return {"timestamp": datetime.now(timezone.utc).isoformat(), "minuto": 60,
            "marcador": {"local": 1, "visitante": 0},
            "tiros_puerta": {"local": 4, "visitante": 2}}


def test_cobertura_no_inventa_estadisticas_y_detecta_datos_viejos():
    ev = evento()
    ahora = datetime.fromisoformat(ev["timestamp"])
    assert evaluar(ev, ahora)["vigente"]
    assert not evaluar(ev, ahora + timedelta(seconds=61))["vigente"]
    assert evaluar(ev, ahora)["disponibles"] == ["tiros_puerta"]


def test_evaluar_calidad_suspende_minuto_requerido_ausente():
    ev = evento()
    ahora = datetime.fromisoformat(ev["timestamp"])
    ev["minuto"] = None

    calidad = evaluar_calidad(ev, ahora)

    assert calidad["vigente"] is False
    assert calidad["basicos_validos"] is False
    assert calidad.get("frescura") == "suspended"
    assert calidad["estado"] == "suspendido"


def test_evaluar_calidad_suspende_score_requerido_ausente():
    ev = evento()
    ahora = datetime.fromisoformat(ev["timestamp"])
    ev["marcador"] = {"local": 1}

    calidad = evaluar_calidad(ev, ahora)

    assert calidad["vigente"] is False
    assert calidad["basicos_validos"] is False
    assert calidad.get("frescura") == "suspended"
    assert calidad["estado"] == "suspendido"


def test_evaluar_calidad_mantiene_estadisticas_opcionales_ausentes_degradadas():
    ev = evento()
    ahora = datetime.fromisoformat(ev["timestamp"])

    calidad = evaluar_calidad(ev, ahora)

    assert calidad["basicos_validos"] is True
    assert calidad["vigente"] is True
    assert calidad.get("frescura") == "degraded"
    assert calidad["estado"] == "actualizado"


def test_no_se_publica_prediccion_vieja_o_previa_a_un_gol():
    ev = evento()
    pred = correr([ev], 60, ev["marcador"])
    ev["marcador"] = {"local": 2, "visitante": 0}
    d = empaquetar_estado([ev], ev, pred, {})
    assert d["prediccion"] == {}
    assert d["estado_prediccion"] == "recalculando"
    ev["timestamp"] = (datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat()
    d = empaquetar_estado([ev], ev, pred, {})
    assert d["estado_prediccion"] == "datos_retrasados"


def test_xg_decimal_y_cero_se_preservan():
    stats = _parsear_estadisticas({"statistics": [
        {"name": "expected goals", "competitorId": 1, "value": "1.37"},
        {"name": "goles esperados", "competitorId": 2, "value": "0"},
        {"name": "possession", "competitorId": 1, "value": "—"},
    ]}, 1, 2)
    assert stats["xg"] == {"local": 1.37, "visitante": 0}
    assert stats["posesion"]["local"] is None
    ev = evento() | {"xg": stats["xg"]}
    p = correr([ev], 60, ev["marcador"])
    assert p["fuentes_tasa"] == ["xg_proveedor", "xg_proveedor"]


def test_ventana_de_diez_minutos_respeta_el_tiempo_restante():
    ev = evento()
    p = correr([ev], 85, ev["marcador"])
    assert p["ventana_gol_minutos"] == 5
    assert p["prob_gol_10_min"] == pytest.approx(100 - p["prob_sin_mas_goles"])
    assert p["prob_gol_10_local"] <= p["prob_gol_10_min"]


def test_series_admiten_estadisticas_ausentes_sin_puntos_inventados():
    a = evento() | {"ataques_peligrosos": {"local": None, "visitante": None}}
    b = evento() | {"minuto": 61, "ataques_peligrosos": {"local": 2, "visitante": 1}}
    s = construir_serie_temporal([a, b])
    assert s["minutos"] == [60, 61]
    assert s["ataques_local"][1] is None


def test_cambio_de_partido_y_detencion_cancelan_el_productor_anterior(monkeypatch):
    import main_agente
    import dashboard_web
    from web_app import ControladorWeb
    monkeypatch.setattr(dashboard_web, "_puerto_activo", None)
    c = ControladorWeb(demo=True)
    try:
        c.seleccionar(c.listar("en_vivo")[0])
        c.seleccionar(c.listar("hoy")[0])
        # La siguiente transición espera que la tarea anterior quede cancelada.
        c.detener()
        assert c.estado()["control"]["fase"] == "inicio"
        assert main_agente.ultimo_metrics == {}
        assert c._tarea is None
    finally:
        c.cerrar()


def test_registro_no_cuenta_repeticiones_ni_predicciones_finales(tmp_path):
    from registro_vivo import registrar, evaluar_registro
    db = tmp_path / "partidos.sqlite3"
    ev = evento() | {"_fixture_id": "42", "_fuente": "365scores"}
    pred = correr([ev], 60, ev["marcador"])
    registrar(ev, pred, path=db)
    registrar(ev, pred, path=db)
    assert evaluar_registro(db)["n"] == 0
    ev.update(timestamp=(datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(), minuto=90)
    registrar(ev, correr([ev], 90, ev["marcador"]), finalizado=True, path=db)
    informe = evaluar_registro(db)
    assert informe["n"] == 1
    assert informe["modelo"]["brier"] > 0  # No se usó el 100% del resultado final.
    assert evaluar_registro(db, 30)["n"] == 0


def test_cliente_sse_lento_no_bloquea_actualizaciones(monkeypatch):
    import queue
    import dashboard_web
    mensajes = queue.Queue(maxsize=2)
    monkeypatch.setattr(dashboard_web, "_clientes_sse", [mensajes])
    for i in range(100):
        dashboard_web.notificar_clientes({"revision": i})
    assert mensajes.qsize() == 2
    assert b'98' in mensajes.get_nowait()
    assert b'99' in mensajes.get_nowait()


@pytest.mark.asyncio
async def test_proveedor_no_retrocede_el_reloj_del_analisis():
    import asyncio
    import motor_metricas
    entrada, salida = asyncio.Queue(), asyncio.Queue()
    for minuto in (65, 63, 66):
        await entrada.put(evento() | {"minuto": minuto})
    tarea = asyncio.create_task(motor_metricas.iniciar(entrada, salida))
    try:
        await asyncio.wait_for(entrada.join(), timeout=3)
        assert salida.qsize() == 2
        assert (await salida.get())["minuto"] == 65
        assert (await salida.get())["minuto"] == 66
    finally:
        tarea.cancel()
        await asyncio.gather(tarea, return_exceptions=True)
