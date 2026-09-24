"""Pruebas del servidor y del formato consumido por la interfaz web."""

import json
from urllib.request import Request, urlopen

import dashboard_web


def test_serie_prepartido_admite_minuto_nulo():
    serie = dashboard_web.construir_serie_temporal([
        {"minuto": None, "posesion": {"local": 50, "visitante": 50}}
    ])
    assert serie["minutos"] == [0]


def test_servidor_web_expone_pagina_y_controles():
    seleccionado = []
    dashboard_web.configurar_controles(
        listar=lambda categoria: [{"nombre": "Local vs Visitante", "categoria": categoria}],
        seleccionar=lambda partido: seleccionado.append(partido) or {"mensaje": "ok"},
        detener=lambda: {"mensaje": "detenido"},
    )
    puerto = dashboard_web.iniciar(
        lambda: {"control": {"fase": "inicio"}}, puerto=19876, sesion="test"
    )
    base = f"http://127.0.0.1:{puerto}"
    try:
        with urlopen(base + "/", timeout=3) as respuesta:
            assert b"Football Live Agent" in respuesta.read()

        with urlopen(base + "/api/partidos?categoria=hoy", timeout=3) as respuesta:
            listado = json.load(respuesta)
        assert listado["ok"] is True
        assert listado["partidos"][0]["categoria"] == "hoy"

        cuerpo = json.dumps({"nombre": "Local vs Visitante", "fixture_id": "123"}).encode()
        peticion = Request(
            base + "/api/seleccionar",
            data=cuerpo,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(peticion, timeout=3) as respuesta:
            resultado = json.load(respuesta)
        assert resultado == {"ok": True, "mensaje": "ok"}
        assert seleccionado[0]["fixture_id"] == "123"
    finally:
        dashboard_web.detener()
