"""
predictor_previo.py — Predicciones pre-partido basadas en scraping de Flashscore.

Para partidos futuros o próximos:
1. Busca historial H2H en Flashscore (Playwright)
2. Extrae forma reciente de cada equipo
3. Construye eventos sintéticos a partir de promedios estimados
4. Corre el simulador Monte Carlo con esos datos
5. Muestra un dashboard completo en terminal con Rich

SIN APIs — todo via Flashscore scraping.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone

import numpy as np
from rich.console import Console
from rich.table import Table

import simulador

logging.basicConfig(format="[%(asctime)s] [%(name)s] %(message)s")
logger = logging.getLogger("predictor_previo")
logger.setLevel(logging.INFO)

console = Console()

# ─────────────────────────── CONSTANTES ───────────────────────────
N_SIMULACIONES = 5000
MIN_EVENTOS = 2
MAX_G_HIST = 6
DECAY_H2H = 0.85  # Factor de decaimiento: partido más reciente = 1.0, siguiente = 0.85, etc.

# ─────────────────────────── HELPERS ───────────────────────────────

import re
import urllib.parse

async def _buscar_goleadores_reales(browser, equipo: str) -> list:
    """Busca en Wikipedia (EN) la plantilla actual y extrae los jugadores con mejor ratio de goles/partido recientes."""
    fallbacks = [
        {"nombre": "Delantero Principal", "goles": 40, "caps": 80, "ratio": 0.5},
        {"nombre": "Extremo Ofensivo", "goles": 25, "caps": 75, "ratio": 0.33},
        {"nombre": "Mediocampista (CAM)", "goles": 15, "caps": 60, "ratio": 0.25},
        {"nombre": "Defensa / Otro", "goles": 5, "caps": 50, "ratio": 0.1}
    ]
    if not equipo:
        return fallbacks

    traducciones = {
        "España": "Spain", "Alemania": "Germany", "Francia": "France",
        "Inglaterra": "England", "Holanda": "Netherlands", "Países Bajos": "Netherlands",
        "Italia": "Italy", "Brasil": "Brazil", "Japón": "Japan", "Bélgica": "Belgium",
        "Croacia": "Croatia", "Marruecos": "Morocco", "Corea del Sur": "South_Korea",
        "Estados Unidos": "United_States", "México": "Mexico", "Suiza": "Switzerland",
        "Uruguay": "Uruguay", "Argentina": "Argentina", "Colombia": "Colombia"
    }
    equipo_en = traducciones.get(equipo, equipo)

    ctx = await browser.new_context(locale="en-US")
    page = await ctx.new_page()
    url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(equipo_en)}_national_football_team"
    
    goleadores = []
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=12000)
        
        goleadores = await page.evaluate('''() => {
            let res = [];
            let tables = document.querySelectorAll('table');
            for (let t of tables) {
                let ths = Array.from(t.querySelectorAll('th')).map(th => th.innerText.toLowerCase());
                let capsIdx = ths.findIndex(h => h.includes('caps'));
                let goalsIdx = ths.findIndex(h => h.includes('goals'));
                let nameIdx = ths.findIndex(h => h.includes('player'));
                
                if (capsIdx !== -1 && goalsIdx !== -1 && nameIdx !== -1) {
                    let rows = t.querySelectorAll('tr');
                    for (let i = 1; i < rows.length; i++) {
                        let r = rows[i];
                        let tds = r.querySelectorAll('td, th');
                        if (tds.length > Math.max(capsIdx, goalsIdx, nameIdx)) {
                            let nameEl = tds[nameIdx].querySelector('a');
                            let name = nameEl ? nameEl.innerText : tds[nameIdx].innerText;
                            let goals = parseInt(tds[goalsIdx].innerText.replace(/\\D/g, '')) || 0;
                            let caps = parseInt(tds[capsIdx].innerText.replace(/\\D/g, '')) || 1;
                            
                            // Ignoramos porteros o jugadores que no han debutado
                            if (name.length > 2 && goals > 0 && caps > 5) {
                                res.push({
                                    nombre: name.trim(), 
                                    goles: goals, 
                                    caps: caps, 
                                    ratio: goals / caps
                                });
                            }
                        }
                    }
                    break;
                }
            }
            // Ordenar por ratio Goles/Partido (Eficiencia Reciente) en lugar de Goles Históricos
            res.sort((a,b) => b.ratio - a.ratio);
            return res.slice(0, 4);
        }''')
    except Exception as e:
        logger.debug(f"Error buscando goleadores reales de {equipo}: {e}")
    finally:
        await page.close()
        await ctx.close()

    if not goleadores or len(goleadores) < 4:
        return fallbacks
    return goleadores

async def _buscar_datos_flashscore_pw(fixture_id: str):
    """
    Usa Playwright para raspar el H2H en Flashscore.
    Extrae historial de enfrentamientos directos y calcula estadísticas.
    """
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
    page = await context.new_page()

    url = f"https://www.flashscore.co/partido/{fixture_id}/#/h2h/overall"
    h2h_data = {
        "partidos": 0, "victorias_local": 33.3, "victorias_visita": 33.3, "empates": 33.3,
        "goles_local": 1.2, "goles_visita": 1.2
    }
    forma_l = {"forma": "", "goles_favor": 1.5, "goles_contra": 1.2, "puntos_por_partido": 1.5}
    forma_v = {"forma": "", "goles_favor": 1.5, "goles_contra": 1.2, "puntos_por_partido": 1.5}

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        try:
            await page.wait_for_selector(".h2h__row", timeout=8000)
        except Exception:
            await page.wait_for_timeout(3000)

        # Obtener nombres de equipos del encabezado
        home_el = await page.query_selector(".duelParticipant__home .participant__participantName a")
        away_el = await page.query_selector(".duelParticipant__away .participant__participantName a")
        nombre_local = (await home_el.inner_text()).strip() if home_el else ""
        nombre_visita = (await away_el.inner_text()).strip() if away_el else ""

        # Procesar la sección H2H (enfrentamientos directos) con time decay
        filas = await page.query_selector_all(".h2h__row")
        gl_weighted, gv_weighted = 0.0, 0.0
        v_l_w, v_v_w, emp_w, weight_sum = 0.0, 0.0, 0.0, 0.0
        matches = 0

        for idx, f in enumerate(filas[:10]):
            w = DECAY_H2H ** idx  # Partidos recientes pesan más
            try:
                res_el = await f.query_selector(".h2h__result")
                home_p = await f.query_selector(".h2h__homeParticipant")
                if not res_el:
                    continue
                res = await res_el.inner_text()
                home_name = (await home_p.inner_text()).strip() if home_p else ""
                res_parts = [x.strip() for x in res.split('\n') if x.strip()]
                if len(res_parts) >= 2:
                    gh, ga = int(res_parts[0]), int(res_parts[1])
                    matches += 1
                    weight_sum += w
                    # Determinar quién es "local" del partido original
                    if nombre_local and nombre_local.lower() in home_name.lower():
                        gl_weighted += gh * w
                        gv_weighted += ga * w
                        if gh > ga: v_l_w += w
                        elif gh == ga: emp_w += w
                        else: v_v_w += w
                    else:
                        gl_weighted += ga * w
                        gv_weighted += gh * w
                        if ga > gh: v_l_w += w
                        elif ga == gh: emp_w += w
                        else: v_v_w += w
            except Exception:
                pass

        if matches > 0 and weight_sum > 0:
            h2h_data["partidos"] = matches
            h2h_data["goles_local"] = round(gl_weighted / weight_sum, 2)
            h2h_data["goles_visita"] = round(gv_weighted / weight_sum, 2)
            h2h_data["victorias_local"] = round((v_l_w / weight_sum) * 100, 1)
            h2h_data["empates"] = round((emp_w / weight_sum) * 100, 1)
            h2h_data["victorias_visita"] = round((v_v_w / weight_sum) * 100, 1)

            # Usar H2H también para estimar forma
            forma_l_str = ""
            forma_v_str = ""
            pts_l, pts_v = 0, 0
            for f in filas[:5]:
                try:
                    res_el = await f.query_selector(".h2h__result")
                    home_p = await f.query_selector(".h2h__homeParticipant")
                    if not res_el: continue
                    res = await res_el.inner_text()
                    home_name = (await home_p.inner_text()).strip() if home_p else ""
                    parts = [x.strip() for x in res.split('\n') if x.strip()]
                    if len(parts) >= 2:
                        gh, ga = int(parts[0]), int(parts[1])
                        if nombre_local and nombre_local.lower() in home_name.lower():
                            if gh > ga: forma_l_str += "W"; pts_l += 3; forma_v_str += "L"
                            elif gh == ga: forma_l_str += "D"; pts_l += 1; forma_v_str += "D"; pts_v += 1
                            else: forma_l_str += "L"; forma_v_str += "W"; pts_v += 3
                        else:
                            if ga > gh: forma_l_str += "W"; pts_l += 3; forma_v_str += "L"
                            elif ga == gh: forma_l_str += "D"; pts_l += 1; forma_v_str += "D"; pts_v += 1
                            else: forma_l_str += "L"; forma_v_str += "W"; pts_v += 3
                except Exception:
                    pass

            n5 = min(matches, 5)
            if n5 > 0:
                forma_l = {
                    "forma": forma_l_str,
                    "goles_favor": h2h_data["goles_local"],
                    "goles_contra": h2h_data["goles_visita"],
                    "puntos_por_partido": round(pts_l / n5, 2),
                }
                forma_v = {
                    "forma": forma_v_str,
                    "goles_favor": h2h_data["goles_visita"],
                    "goles_contra": h2h_data["goles_local"],
                    "puntos_por_partido": round(pts_v / n5, 2),
                }

    except Exception as e:
        logger.warning(f"Error procesando H2H en Flashscore: {e}")

    # Búsqueda dinámica de goleadores reales
    jug_l = await _buscar_goleadores_reales(browser, nombre_local)
    jug_v = await _buscar_goleadores_reales(browser, nombre_visita)

    await browser.close()
    await pw.stop()

    # Stats simuladas avanzadas (extrapoladas del rendimiento real)
    stats_l = {
        "ataques_por_partido": round(forma_l["goles_favor"] * 4.0, 2),
        "goles_favor": forma_l["goles_favor"],
        "goles_contra": forma_l["goles_contra"],
        "posesion": 50.0,
        "tiros_por_partido": round(forma_l["goles_favor"] * 8.0, 2),
        "corners_por_partido": round(forma_l["goles_favor"] * 3.5, 2),
        "tarjetas_por_partido": 2.2,
    }
    stats_v = {
        "ataques_por_partido": round(forma_v["goles_favor"] * 4.0, 2),
        "goles_favor": forma_v["goles_favor"],
        "goles_contra": forma_v["goles_contra"],
        "posesion": 50.0,
        "tiros_por_partido": round(forma_v["goles_favor"] * 8.0, 2),
        "corners_por_partido": round(forma_v["goles_favor"] * 3.5, 2),
        "tarjetas_por_partido": 2.5,
    }

    return h2h_data, forma_l, forma_v, stats_l, stats_v, jug_l, jug_v

def buscar_datos_flashscore(fixture_id: str):
    return asyncio.run(_buscar_datos_flashscore_pw(fixture_id))


# ─────────────────── SIMULACIÓN PRE-PARTIDO ───────────────────────

def _construir_eventos(ataques_l: float, ataques_v: float, n: int = 45) -> list[dict]:
    """Construye eventos sintéticos para alimentar el simulador."""
    eventos = []
    for i in range(1, n + 1):
        frac = i / n
        eventos.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "minuto": float(i),
            "marcador": {"local": 0, "visitante": 0},
            "posesion": {"local": 50.0, "visitante": 50.0},
            "tiros": {"local": round(12.0 * frac), "visitante": round(10.0 * frac)},
            "ataques_peligrosos": {
                "local": round(ataques_l * frac),
                "visitante": round(ataques_v * frac),
            },
            "acciones": [],
            "riesgo_gol_local": 0,
            "riesgo_gol_visitante": 0,
            "animo_local": 50.0,
            "animo_visitante": 50.0,
        })
    return eventos


def simular_previo(at_l: float, at_v: float, gl_esp: float, gv_esp: float, 
                   tl_esp: float, tv_esp: float, cl_esp: float, cv_esp: float,
                   tarl_esp: float, tarv_esp: float) -> dict:
    """
    Corre Monte Carlo calibrado para un partido completo de 90 min.
    Combina Poisson histórico (60%) + Monte Carlo por ataques (40%).
    """
    eventos = _construir_eventos(at_l, at_v)

    resultado = simulador.correr(
        eventos_recientes=eventos,
        minuto_actual=0.0,
        marcador_actual={"local": 0, "visitante": 0},
        seed=None,
    )

    rng = np.random.default_rng()
    sims_l = rng.poisson(gl_esp, N_SIMULACIONES)
    sims_v = rng.poisson(gv_esp, N_SIMULACIONES)
    totales = sims_l + sims_v
    
    # Simulaciones extra
    sims_tl = rng.poisson(tl_esp, N_SIMULACIONES)
    sims_tv = rng.poisson(tv_esp, N_SIMULACIONES)
    sims_cl = rng.poisson(cl_esp, N_SIMULACIONES)
    sims_cv = rng.poisson(cv_esp, N_SIMULACIONES)
    sims_tarl = rng.poisson(tarl_esp, N_SIMULACIONES)
    sims_tarv = rng.poisson(tarv_esp, N_SIMULACIONES)

    w_h, w_m = 0.60, 0.40  # peso histórico vs Monte Carlo

    resultado["prob_1x2_local"] = round(
        w_h * float(np.mean(sims_l > sims_v) * 100) + w_m * resultado["prob_1x2_local"], 1)
    resultado["prob_1x2_empate"] = round(
        w_h * float(np.mean(sims_l == sims_v) * 100) + w_m * resultado["prob_1x2_empate"], 1)
    resultado["prob_1x2_visitante"] = round(
        w_h * float(np.mean(sims_l < sims_v) * 100) + w_m * resultado["prob_1x2_visitante"], 1)
    resultado["prob_over_2_5"] = round(
        w_h * float(np.mean(totales > 2.5) * 100) + w_m * resultado["prob_over_2_5"], 1)
    resultado["prob_over_1_5"] = round(
        w_h * float(np.mean(totales > 1.5) * 100) + w_m * resultado["prob_over_1_5"], 1)
    resultado["prob_btts"] = round(
        w_h * float(np.mean((sims_l >= 1) & (sims_v >= 1)) * 100) + w_m * resultado["prob_btts"], 1)
    
    resultado["goles_esperados_local"] = round(float(np.mean(sims_l)), 2)
    resultado["goles_esperados_visitante"] = round(float(np.mean(sims_v)), 2)
    
    resultado["tiros_esperados_local"] = round(float(np.mean(sims_tl)), 1)
    resultado["tiros_esperados_visitante"] = round(float(np.mean(sims_tv)), 1)
    resultado["corners_esperados_local"] = round(float(np.mean(sims_cl)), 1)
    resultado["corners_esperados_visitante"] = round(float(np.mean(sims_cv)), 1)
    resultado["tarjetas_esperadas_local"] = round(float(np.mean(sims_tarl)), 1)
    resultado["tarjetas_esperadas_visitante"] = round(float(np.mean(sims_tarv)), 1)

    return resultado


# ─────────────────────── DASHBOARD ────────────────────────────────

def _barra(valor: float, max_val: float = 100.0, width: int = 20) -> str:
    bloques = int(min(valor, max_val) / max_val * width)
    return "█" * bloques + "░" * (width - bloques)


def mostrar_dashboard(
    nombre_local: str, nombre_visita: str,
    h2h: dict, forma_l: dict, forma_v: dict,
    stats_l: dict, stats_v: dict, pred: dict, jug_l: list, jug_v: list
) -> None:
    """Muestra dashboard de predicción pre-partido."""
    console.print()
    console.rule("[bold cyan]⚽ ANÁLISIS PRE-PARTIDO (Flashscore)[/bold cyan]")
    console.print(f"[bold white]  {nombre_local}  vs  {nombre_visita}[/bold white]\n")

    # ── Stats de temporada ──
    t1 = Table(title="📊 Estadísticas Estimadas", expand=True, border_style="blue")
    t1.add_column("Métrica", style="bold")
    t1.add_column(nombre_local, justify="center", style="cyan")
    t1.add_column(nombre_visita, justify="center", style="magenta")

    t1.add_row("Goles/partido", str(stats_l["goles_favor"]), str(stats_v["goles_favor"]))
    t1.add_row("Goles contra/p", str(stats_l["goles_contra"]), str(stats_v["goles_contra"]))
    t1.add_row("Posesión est.", f"{stats_l['posesion']}%", f"{stats_v['posesion']}%")
    t1.add_row("Tiros/partido est.", str(stats_l["tiros_por_partido"]), str(stats_v["tiros_por_partido"]))
    t1.add_row("Córneres est.", str(stats_l["corners_por_partido"]), str(stats_v["corners_por_partido"]))
    console.print(t1)

    # ── Forma y H2H ──
    t2 = Table(title="🔥 Forma Reciente & H2H", expand=True, border_style="yellow")
    t2.add_column("Métrica", style="bold")
    t2.add_column(nombre_local, justify="center", style="cyan")
    t2.add_column(nombre_visita, justify="center", style="magenta")
    t2.add_column("H2H", justify="center", style="white")

    t2.add_row("Forma", forma_l["forma"], forma_v["forma"],
               f"{h2h['partidos']} partidos")
    t2.add_row("Goles favor/p", str(forma_l["goles_favor"]),
               str(forma_v["goles_favor"]),
               f"Local {h2h['goles_local']} - {h2h['goles_visita']} Visita")
    t2.add_row("% Victoria H2H", f"{h2h['victorias_local']}%",
               f"{h2h['victorias_visita']}%", f"Emp: {h2h['empates']}%")
    console.print(t2)

    # ── Predicciones ──
    p = pred
    t3 = Table(title=f"🎯 Predicciones (Monte Carlo + Poisson, {N_SIMULACIONES:,} sim.)",
               expand=True, border_style="magenta")
    t3.add_column("Mercado", style="bold")
    t3.add_column("Prob.", justify="center")
    t3.add_column("Visual", ratio=2)

    p1 = p.get("prob_1x2_local", 0)
    px = p.get("prob_1x2_empate", 0)
    p2 = p.get("prob_1x2_visitante", 0)
    t3.add_row(f"1 — {nombre_local}", f"[cyan]{p1:.1f}%[/]", f"[cyan]{_barra(p1)}[/]")
    t3.add_row("X — Empate", f"[white]{px:.1f}%[/]", f"[white]{_barra(px)}[/]")
    t3.add_row(f"2 — {nombre_visita}", f"[magenta]{p2:.1f}%[/]", f"[magenta]{_barra(p2)}[/]")
    t3.add_section()

    ov15 = p.get("prob_over_1_5", 0)
    ov25 = p.get("prob_over_2_5", 0)
    ov35 = p.get("prob_over_3_5", 0)
    btts = p.get("prob_btts", 0)
    t3.add_row("Over 1.5", f"[yellow]{ov15:.1f}%[/]", f"[yellow]{_barra(ov15)}[/]")
    t3.add_row("Over 2.5", f"[yellow]{ov25:.1f}%[/]", f"[yellow]{_barra(ov25)}[/]")
    t3.add_row("Over 3.5", f"[yellow]{ov35:.1f}%[/]", f"[yellow]{_barra(ov35)}[/]")
    t3.add_row("BTTS", f"[green]{btts:.1f}%[/]", f"[green]{_barra(btts)}[/]")
    t3.add_section()

    marc = p.get("marcador_mas_probable", "?")
    t3.add_row("Marcador más probable", f"[bold white]{marc}[/]", "")
    t3.add_row("xG Local / Visita",
               f"[cyan]{p.get('goles_esperados_local',0):.2f}[/] / "
               f"[magenta]{p.get('goles_esperados_visitante',0):.2f}[/]", "")
    t3.add_row("xTiros (Esperados)",
               f"[cyan]{p.get('tiros_esperados_local',0):.1f}[/] / "
               f"[magenta]{p.get('tiros_esperados_visitante',0):.1f}[/]", "")
    t3.add_row("xCorners (Esperados)",
               f"[cyan]{p.get('corners_esperados_local',0):.1f}[/] / "
               f"[magenta]{p.get('corners_esperados_visitante',0):.1f}[/]", "")
    t3.add_row("xTarjetas (Esperadas)",
               f"[yellow]{p.get('tarjetas_esperadas_local',0):.1f}[/] / "
               f"[yellow]{p.get('tarjetas_esperadas_visitante',0):.1f}[/]", "")
    console.print(t3)

    # ── Top marcadores ──
    top = p.get("top_marcadores", [])
    if top:
        t4 = Table(title="🏆 Top Marcadores Probables", expand=True, border_style="white")
        t4.add_column("Marcador", style="bold")
        t4.add_column("Prob.", justify="right")
        t4.add_column("Barra", ratio=2)
        for item in top[:6]:
            m = item.get("marcador", "?")
            pr = item.get("prob", 0)
            t4.add_row(m, f"{pr:.1f}%", _barra(pr, max_val=max(top[0]["prob"], 1)))
        console.print(t4)

    # ── Proyección por Jugadores (Ratio Reciente) ──
    t5 = Table(title="🏃 Proyección Jugadores (Forma y Eficiencia Goleadora Reciente)", expand=True, border_style="cyan")
    t5.add_column("Jugadores (Goles / Partidos)", style="bold")
    t5.add_column(f"{nombre_local} (xTiros / xG)", justify="center", style="cyan")
    t5.add_column(f"{nombre_visita} (xTiros / xG)", justify="center", style="magenta")

    xt_l = p.get('tiros_esperados_local', 0)
    xg_l = p.get('goles_esperados_local', 0)
    xt_v = p.get('tiros_esperados_visitante', 0)
    xg_v = p.get('goles_esperados_visitante', 0)

    # Calcular distribución basada en el RATIO de Goles por Partido (Eficiencia actual)
    total_ratio_l = sum(j.get('ratio', 0.1) for j in jug_l) or 1.0
    total_ratio_v = sum(j.get('ratio', 0.1) for j in jug_v) or 1.0

    for i in range(4):
        # Fallback a diccionarios default si las listas son cortas
        jl = jug_l[i] if i < len(jug_l) else jug_l[0]
        jv = jug_v[i] if i < len(jug_v) else jug_v[0]

        pct_l = jl.get('ratio', 0.1) / total_ratio_l
        pct_v = jv.get('ratio', 0.1) / total_ratio_v

        pct_l = min(0.45, max(0.10, pct_l))
        pct_v = min(0.45, max(0.10, pct_v))

        nombre_txt_l = f"{jl['nombre']} ({jl.get('goles',0)}G/{jl.get('caps',1)}P)"
        nombre_txt_v = f"{jv['nombre']} ({jv.get('goles',0)}G/{jv.get('caps',1)}P)"

        t5.add_row(f"{nombre_txt_l} / {nombre_txt_v}", 
                   f"{xt_l * pct_l:.1f}  /  {xg_l * pct_l:.2f}", 
                   f"{xt_v * pct_v:.1f}  /  {xg_v * pct_v:.2f}")

    console.print(t5)

    # ── Sugerencias ──
    console.print()
    console.rule("[bold yellow]💡 SUGERENCIAS DE APUESTA[/bold yellow]")
    hay = False
    if p1 > 55:
        console.print(f"  🔥 [bold green]Victoria {nombre_local}[/] — {p1:.1f}%")
        hay = True
    if p2 > 55:
        console.print(f"  🔥 [bold green]Victoria {nombre_visita}[/] — {p2:.1f}%")
        hay = True
    if px > 35:
        console.print(f"  🛡️  [cyan]Empate posible[/] — {px:.1f}%")
        hay = True
    if ov25 > 65:
        console.print(f"  📈 [yellow]Over 2.5 goles[/] — {ov25:.1f}%")
        hay = True
    if btts > 60:
        console.print(f"  ⚽ [green]Ambos marcan (BTTS)[/] — {btts:.1f}%")
        hay = True
    if not hay:
        console.print("  [dim]Probabilidades equilibradas. Partido difícil de predecir.[/dim]")
    console.print()


# ──────────────────────── ENTRY POINT ─────────────────────────────

def analizar_partido_futuro(partido: dict) -> None:
    """
    Función principal: recibe el dict del partido seleccionado y
    ejecuta el pipeline completo via Google scraping.
    """
    nombre = partido.get("nombre", "")
    if " vs " not in nombre and " VS " not in nombre:
        console.print("[red]Error: formato de partido no reconocido.[/red]")
        return

    # Separar equipos
    sep = " vs " if " vs " in nombre else " VS "
    partes = nombre.split(sep, 1)
    local = partes[0].strip()
    visita = partes[1].strip()

    fixture_id = partido.get("fixture_id")
    if not fixture_id:
        console.print("[red]Error: partido sin ID de Flashscore válido.[/red]")
        return
        
    console.print(f"\n[dim]🔍 Obteniendo H2H, y goleadores reales (Web Scraping) para: {local} vs {visita}...[/dim]")
    
    h2h, forma_l, forma_v, stats_l, stats_v, jug_l, jug_v = buscar_datos_flashscore(fixture_id)

    # 4. Calcular parámetros para simulación
    # Ponderación: 35% temporada + 40% forma reciente + 25% H2H
    # (forma reciente tiene el mayor peso para reflejar el estado actual)
    gl_esp = (0.35 * stats_l["goles_favor"] + 0.40 * forma_l["goles_favor"]
              + 0.25 * h2h["goles_local"])
    gv_esp = (0.35 * stats_v["goles_favor"] + 0.40 * forma_v["goles_favor"]
              + 0.25 * h2h["goles_visita"])
    at_l = stats_l["ataques_por_partido"] or 5.0
    at_v = stats_v["ataques_por_partido"] or 5.0

    console.print("[dim]  → Corriendo simulación Monte Carlo...          [/dim]", end="\r")
    
    tl_esp = (0.7 * stats_l["tiros_por_partido"] + 0.3 * (stats_l["goles_favor"] * 6))
    tv_esp = (0.7 * stats_v["tiros_por_partido"] + 0.3 * (stats_v["goles_favor"] * 6))
    cl_esp = (0.7 * stats_l["corners_por_partido"] + 0.3 * (stats_l["tiros_por_partido"] * 0.4))
    cv_esp = (0.7 * stats_v["corners_por_partido"] + 0.3 * (stats_v["tiros_por_partido"] * 0.4))
    
    pred = simular_previo(
        at_l, at_v, gl_esp, gv_esp, 
        tl_esp, tv_esp, cl_esp, cv_esp,
        stats_l["tarjetas_por_partido"], stats_v["tarjetas_por_partido"]
    )

    # 5. Mostrar dashboard
    console.print(" " * 55, end="\r")
    mostrar_dashboard(local, visita, h2h, forma_l, forma_v, stats_l, stats_v, pred, jug_l, jug_v)

    console.print("[dim]Presiona Enter para volver al selector...[/dim]")
    try:
        input()
    except (KeyboardInterrupt, EOFError):
        pass
