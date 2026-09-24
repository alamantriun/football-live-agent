"""Prepartido: histórico verificable o H2H exploratorio con abstención."""
import asyncio
import os
from datetime import date, datetime
from rich.console import Console
from modelo_poisson import predecir

console = Console()
N_SIMULACIONES = 5000

def simular_previo(at_l, at_v, gl_esp, gv_esp, tl_esp, tv_esp,
                   cl_esp, cv_esp, tarl_esp, tarv_esp):
    """Firma compatible; no inventa estadísticas auxiliares."""
    return predecir(gl_esp, gv_esp, seed=42, muestras=N_SIMULACIONES)


async def predecir_h2h(fixture_id):
    """Devuelve una predicción web o se abstiene si el soporte es insuficiente."""
    filas = await _buscar_h2h(fixture_id)
    if len(filas) < 5:
        raise ValueError("Se requieren al menos 5 enfrentamientos directos verificados")
    gl = (sum(p[1] for p in filas) + 10 * 1.5) / (len(filas) + 10)
    gv = (sum(p[2] for p in filas) + 10 * 1.2) / (len(filas) + 10)
    resultado = predecir(gl, gv, muestras=N_SIMULACIONES)
    resultado["soporte"] = {"partidos_h2h": len(filas)}
    resultado["fuente"] = "H2H Flashscore"
    return resultado


async def _buscar_h2h(fixture_id):
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page(locale="es-MX")
            await page.goto(f"https://www.flashscore.co/partido/{fixture_id}/#/h2h/overall",
                            wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_selector(".h2h__row", timeout=8000)
            local = (await page.locator(".duelParticipant__home .participant__participantName a").inner_text()).strip()
            visita = (await page.locator(".duelParticipant__away .participant__participantName a").inner_text()).strip()
            partidos, vistos = [], set()
            for fila in await page.locator(".h2h__row").all():
                try:
                    h = (await fila.locator(".h2h__homeParticipant").inner_text()).strip()
                    a = (await fila.locator(".h2h__awayParticipant").inner_text()).strip()
                    if {h.casefold(), a.casefold()} != {local.casefold(), visita.casefold()}:
                        continue
                    fecha_txt = (await fila.locator(".h2h__date").inner_text()).strip()
                    fecha = datetime.strptime(fecha_txt, "%d.%m.%y").date()
                    if fecha >= date.today() or (fecha, h, a) in vistos:
                        continue
                    goles = (await fila.locator(".h2h__result").inner_text()).split()
                    if len(goles) != 2:
                        continue
                    gh, ga = map(int, goles)
                    if min(gh, ga) < 0:
                        continue
                    vistos.add((fecha, h, a))
                    gl, gv = (gh, ga) if h.casefold() == local.casefold() else (ga, gh)
                    partidos.append((fecha, gl, gv))
                except (ValueError, TypeError):
                    continue
            return sorted(partidos, reverse=True)[:10]
        finally:
            await browser.close()


def analizar_partido_futuro(partido):
    nombre = partido.get("nombre", "")
    separador = " vs " if " vs " in nombre else " VS "
    if separador not in nombre:
        console.print("[yellow]No se pudieron identificar los equipos.[/yellow]")
        return
    local, visita = (v.strip() for v in nombre.split(separador, 1))
    historico = os.getenv("FOOTBALL_HISTORY")
    try:
        if historico:
            from modelo_historico import cargar, pronosticar
            fecha_txt = partido.get("fecha") or os.getenv("MATCH_DATE")
            if not fecha_txt:
                console.print("[yellow]Indique MATCH_DATE (YYYY-MM-DD) o use predecir_partido.py para fijar el corte histórico.[/yellow]")
                return
            pred = pronosticar(cargar(historico.split(os.pathsep)), local, visita, date.fromisoformat(fecha_txt))
            console.print(f"Histórico por sede: {pred['soporte']}")
        else:
            fixture = partido.get("fixture_id")
            if not fixture:
                console.print("[yellow]Sin identificador de Flashscore.[/yellow]")
                return
            pred = asyncio.run(predecir_h2h(fixture))
            console.print(f"H2H exploratorio: {pred['soporte']['partidos_h2h']} partidos; sin validación retrospectiva.")
    except Exception as exc:
        console.print(f"[yellow]No se emite predicción: {type(exc).__name__}. Verifique datos, nombres y conexión.[/yellow]")
        return
    console.rule(f"{local} vs {visita}")
    console.print("Modelo experimental; las probabilidades no garantizan el resultado.")
    for etiqueta, campo in (("Local", "prob_1x2_local"), ("Empate", "prob_1x2_empate"),
                            ("Visitante", "prob_1x2_visitante"), ("Over 2.5", "prob_over_2_5"),
                            ("Ambos marcan", "prob_btts")):
        console.print(f"{etiqueta}: {pred[campo]:.1f}%")
    console.print(f"Marcador modal: {pred['marcador_mas_probable']}")
    console.print(f"Goles esperados: {pred['goles_esperados_local']:.2f} / {pred['goles_esperados_visitante']:.2f}")
    console.print("Presiona Enter para volver al selector.")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass
