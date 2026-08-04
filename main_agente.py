"""
main_agente.py — Orquestador principal del sistema de análisis deportivo.

Coordina los 3 módulos (extractor_vivo, motor_metricas, simulador)
usando asyncio.TaskGroup y presenta un dashboard en terminal con Rich.
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone

try:
    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ModuleNotFoundError:
    print("\n" + "="*60)
    print("❌ ERROR: FALTAN LIBRERÍAS (rich / numpy)")
    print("="*60)
    print("Parece que hiciste clic en el botón de 'Play' de tu editor de código.")
    print("Ese botón usa un entorno (.venv) que está vacío y no tiene las librerías instaladas.")
    print("\nPOR FAVOR, IGNORA EL BOTÓN DE PLAY.")
    print("Copia y pega este comando exacto en la terminal de abajo y presiona Enter:")
    print("\n/usr/bin/python3 main_agente.py\n")
    print("="*60 + "\n")
    sys.exit(1)

import extractor_365scores
import motor_metricas
import simulador
import dashboard_web
import selector_visual
import predictor_previo

logging.basicConfig(format="[%(asctime)s] [%(name)s] %(message)s")
logger = logging.getLogger("main_agente")
logger.setLevel(logging.DEBUG)

# ─────────────────────────── ESTADO GLOBAL ────────────────────────
raw_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
metrics_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
buffer_q1: list = []
ultimo_metrics: dict = {}
prediccion_actual: dict = {}
historial_mc: list = []
cuarto_actual: int = 1
nombre_partido: str = "Local vs Visitante"
modo_extraccion: str = "365scores"
SESSION_ID: str = uuid.uuid4().hex[:8]

console = Console()

DASHBOARD_REFRESH = 1.5  # segundos entre actualizaciones del dashboard
PARTIDOS_FILE = "partidos.json"
WEB_PORT = int(os.getenv("WEB_PORT", "8765"))


def _obtener_estado_web() -> dict:
    """Snapshot del estado global para el dashboard web."""
    return dashboard_web.empaquetar_estado(
        buffer_q1, ultimo_metrics, prediccion_actual,
        {
            "nombre": nombre_partido,
            "modo": modo_extraccion,
            "servidor": {
                "sesion": SESSION_ID,
                "pid": os.getpid(),
                "puerto": dashboard_web.puerto_activo(),
                "actualizado_en": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
            },
        },
        historial_mc=historial_mc,
    )


def _sync_web() -> None:
    """Publica el mismo estado que ve la terminal al dashboard web."""
    dashboard_web.publicar_estado(_obtener_estado_web())


def _sparkline_text(vals: list[float], width: int = 24) -> str:
    """Mini gráfica ASCII para la terminal (escala relativa)."""
    if not vals:
        return " " * width
    chars = " ▂▃▄▅▆▇█"
    mn, mx = min(vals), max(vals)
    rango = mx - mn if mx != mn else 1.0
    muestra = vals[-width:]
    return "".join(chars[min(7, int((v - mn) / rango * 7))] for v in muestra)


def _sparkline_text_riesgo(vals: list[float], width: int = 24) -> str:
    """Mini gráfica ASCII para la terminal (escala absoluta 0-100 para riesgo)."""
    if not vals:
        return " " * width
    chars = " ▂▃▄▅▆▇█"
    muestra = vals[-width:]
    return "".join(chars[min(7, int(max(0, min(100, v)) / 100.0 * 7.99))] for v in muestra)


def _sparkline_valores(campo: str, subcampo: str | None = None, n: int = 20) -> list[float]:
    """Extrae últimos N valores de un campo del buffer para sparklines."""
    vals = []
    for ev in buffer_q1[-n:]:
        if subcampo:
            v = (ev.get(campo) or {}).get(subcampo, 0)
        else:
            v = ev.get(campo, 0)
        vals.append(float(v or 0))
    return vals if vals else [0.0]


# ─────────────────── SELECTOR DE PARTIDOS ─────────────────────────
def seleccionar_partido():
    """Selector visual — datos en vivo vía 365Scores."""
    global nombre_partido, modo_extraccion

    if os.getenv("FIXTURE_ID") and os.getenv("SKIP_SELECTOR"):
        nombre_partido = os.getenv("FIXTURE_ID_NAME", f"Partido 365Scores ID: {os.getenv('FIXTURE_ID')}")
        modo_extraccion = "365scores"
        console.print(f"\n[green]✓ 365Scores: {nombre_partido}[/green]\n")
        return

    while True:
        console.print("[dim]Cargando partidos (365Scores + Flashscore)...[/dim]")
        elegido = selector_visual.seleccionar_partido_visual()

        if elegido is None:
            console.print("\n[yellow]Cancelado por el usuario.[/yellow]")
            sys.exit(0)

        estado = str(elegido.get("estado", ""))
        es_futuro = (
            "📅" in estado
            or estado.upper() in ("NS", "NOT STARTED", "PROGRAMADO")
            or (":" in estado and "En Vivo" not in estado and "vivo" not in estado.lower())
        )

        if es_futuro and " vs " in elegido.get("nombre", "") and "Vivo" not in estado and "vivo" not in estado.lower():
            console.print(
                f"\n[bold yellow]📅 Partido futuro: {elegido.get('nombre')}[/bold yellow]"
            )
            predictor_previo.analizar_partido_futuro(elegido)
            continue

        selector_visual.aplicar_seleccion(elegido)
        selector_visual.guardar_seleccion(elegido)
        nombre_partido = elegido.get("nombre", "Partido seleccionado")
        modo_extraccion = elegido.get("modo", "365scores")

        console.print(f"\n[bold green]✓ Modo 365Scores: {nombre_partido}[/bold green]\n")
        break


# ──────────────────── GENERADOR DE DASHBOARD ──────────────────────
def generar_dashboard() -> Layout:
    """Genera el layout del dashboard con Rich."""
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=5),
        Layout(name="sparklines", size=5),
        Layout(name="body", size=16),
        Layout(name="footer", size=6),
    )
    layout["body"].split_row(
        Layout(name="metricas", ratio=1),
        Layout(name="prediccion", ratio=1),
        Layout(name="apuestas", ratio=1),
    )

    # Header — Marcador
    minuto = ultimo_metrics.get("minuto")
    minuto_str = f"{minuto:.0f}'" if isinstance(minuto, (int, float)) else "?"
    marcador = ultimo_metrics.get("marcador", {}) or {}
    m_local = marcador.get("local", 0) if isinstance(marcador, dict) else 0
    m_visit = marcador.get("visitante", 0) if isinstance(marcador, dict) else 0
    
    # Nombre real de los equipos si vienen de la API
    eq = ultimo_metrics.get("_equipos", {})
    eq_local = eq.get("local") if eq else None
    eq_visit = eq.get("visitante") if eq else None
    titulo_partido = f"{eq_local} vs {eq_visit}" if eq_local and eq_visit else nombre_partido

    estado_texto = ultimo_metrics.get("_status", "")
    
    # Manejo de Receso / Medio Tiempo
    es_receso = False
    texto_low = estado_texto.lower()
    if texto_low in ("half time", "halftime", "ht") or "descanso" in texto_low or "medio tiempo" in texto_low:
        es_receso = True
        minuto_str = "⏸️ MEDIO TIEMPO (Descanso de ~15 min)"
    elif estado_texto:
        minuto_str = f"{minuto_str} [{estado_texto}]"

    header_text = Text(justify="center")
    header_text.append(f"⚽ {titulo_partido}", style="bold white")
    header_text.append(f"   {m_local} - {m_visit}", style="bold yellow")
    header_text.append(f"\n{minuto_str}", style="bold cyan" if es_receso else "dim")

    layout["header"].update(
        Panel(header_text, title=f"[bold cyan]PARTIDO EN VIVO ({modo_extraccion.upper()})[/bold cyan]",
              border_style="cyan")
    )

    # Sparklines — tendencias en vivo
    spark_table = Table(show_header=True, expand=True, box=None, show_lines=False)
    spark_table.add_column("Tendencia", style="bold dim", width=18)
    spark_table.add_column("Gráfica", ratio=3)
    spark_table.add_column("Actual", justify="right", width=8)

    sl_rl = _sparkline_valores("riesgo_gol_local")
    sl_rv = _sparkline_valores("riesgo_gol_visitante")
    sl_pl = _sparkline_valores("posesion", "local")
    sl_tl = _sparkline_valores("tiros", "local")
    sl_at_l = _sparkline_valores("ataques_peligrosos", "local")

    spark_table.add_row(
        "[yellow]Riesgo Local[/yellow]",
        f"[bold red]{_sparkline_text_riesgo(sl_rl)}[/bold red]",
        f"{sl_rl[-1]:.0f}%",
    )
    spark_table.add_row(
        "[yellow]Riesgo Visit.[/yellow]",
        f"[bold red]{_sparkline_text_riesgo(sl_rv)}[/bold red]",
        f"{sl_rv[-1]:.0f}%",
    )
    spark_table.add_row(
        "[cyan]Posesión Local[/cyan]",
        f"[bold cyan]{_sparkline_text(sl_pl)}[/bold cyan]",
        f"{sl_pl[-1]:.0f}%",
    )
    spark_table.add_row(
        "[white]Tiros Local[/white]",
        f"[bold white]{_sparkline_text(sl_tl)}[/bold white]",
        f"{sl_tl[-1]:.0f}",
    )
    spark_table.add_row(
        "[white]Ataques Local[/white]",
        f"[bold green]{_sparkline_text(sl_at_l)}[/bold green]",
        f"{sl_at_l[-1]:.0f}",
    )

    layout["sparklines"].update(
        Panel(spark_table, title="[bold blue]📈 TENDENCIAS EN VIVO[/bold blue]",
              border_style="blue")
    )

    # Métricas en vivo
    met_table = Table(show_header=False, expand=True, box=None)
    met_table.add_column("Métrica", style="bold")
    met_table.add_column("Valor", justify="right")

    posesion = ultimo_metrics.get("posesion", {}) or {}
    pos_l = posesion.get("local", "-") if isinstance(posesion, dict) else "-"
    pos_v = posesion.get("visitante", "-") if isinstance(posesion, dict) else "-"

    rgl = ultimo_metrics.get("riesgo_gol_local", 0) or 0
    rgv = ultimo_metrics.get("riesgo_gol_visitante", 0) or 0
    al = ultimo_metrics.get("animo_local", 0) or 0
    av = ultimo_metrics.get("animo_visitante", 0) or 0

    rgl_style = "bold red" if rgl > 80 else "green"
    rgv_style = "bold red" if rgv > 80 else "green"

    pl_style = "bold cyan" if pos_l > pos_v else "white"
    pv_style = "bold cyan" if pos_v > pos_l else "white"
    
    al_style = "bold green" if al > 55 else "dim" if al < 45 else "white"
    av_style = "bold green" if av > 55 else "dim" if av < 45 else "white"

    tiros = ultimo_metrics.get("tiros", {}) or {}
    t_l = tiros.get("local", "-") if isinstance(tiros, dict) else "-"
    t_v = tiros.get("visitante", "-") if isinstance(tiros, dict) else "-"
    ataques = ultimo_metrics.get("ataques_peligrosos", {}) or {}
    at_l = ataques.get("local", "-") if isinstance(ataques, dict) else "-"
    at_v = ataques.get("visitante", "-") if isinstance(ataques, dict) else "-"

    met_table.add_row("[cyan]Posesión Local[/cyan]", f"[{pl_style}]{pos_l}%[/{pl_style}]")
    met_table.add_row("[cyan]Posesión Visitante[/cyan]", f"[{pv_style}]{pos_v}%[/{pv_style}]")
    met_table.add_row("[white]Tiros Local[/white]", str(t_l))
    met_table.add_row("[white]Tiros Visitante[/white]", str(t_v))
    met_table.add_row("[white]Ataques Local[/white]", str(at_l))
    met_table.add_row("[white]Ataques Visitante[/white]", str(at_v))

    t_p_l = ultimo_metrics.get("tiros_puerta", {}).get("local", "-")
    t_p_v = ultimo_metrics.get("tiros_puerta", {}).get("visitante", "-")
    f_l = ultimo_metrics.get("faltas", {}).get("local", "-")
    f_v = ultimo_metrics.get("faltas", {}).get("visitante", "-")
    t_a_l = ultimo_metrics.get("tarjetas_amarillas", {}).get("local", "-")
    t_a_v = ultimo_metrics.get("tarjetas_amarillas", {}).get("visitante", "-")
    t_r_l = ultimo_metrics.get("tarjetas_rojas", {}).get("local", "-")
    t_r_v = ultimo_metrics.get("tarjetas_rojas", {}).get("visitante", "-")
    s_e_l = ultimo_metrics.get("saques_esquina", {}).get("local", "-")
    s_e_v = ultimo_metrics.get("saques_esquina", {}).get("visitante", "-")
    
    met_table.add_row("[white]Tiros a puerta[/white]", f"{t_p_l} - {t_p_v}")
    met_table.add_row("[cyan]Córneres[/cyan]", f"{s_e_l} - {s_e_v}")
    met_table.add_row("[yellow]T. Amarillas[/yellow]", f"{t_a_l} - {t_a_v}")
    met_table.add_row("[red]T. Rojas[/red]", f"{t_r_l} - {t_r_v}")
    met_table.add_row("[white]Faltas[/white]", f"{f_l} - {f_v}")
    met_table.add_row("[yellow]Riesgo Gol Local[/yellow]", f"[{rgl_style}]{rgl:.1f}%[/{rgl_style}]")
    met_table.add_row("[yellow]Riesgo Gol Visitante[/yellow]", f"[{rgv_style}]{rgv:.1f}%[/{rgv_style}]")
    met_table.add_row("[magenta]Ánimo Local[/magenta]", f"[{al_style}]{al:.1f}%[/{al_style}]")
    met_table.add_row("[magenta]Ánimo Visitante[/magenta]", f"[{av_style}]{av:.1f}%[/{av_style}]")

    layout["metricas"].update(
        Panel(met_table, title="[bold green]MÉTRICAS EN VIVO[/bold green]",
              border_style="green")
    )

    # Predicción Continua (Resto del Partido)
    pred_table = Table(show_header=False, expand=True, box=None)
    pred_table.add_column("Métrica", style="bold")
    pred_table.add_column("Valor", justify="right")
    
    if prediccion_actual:
        pred_table.add_row("Victoria Local", f"{prediccion_actual.get('prob_1x2_local', 0):.1f}%")
        pred_table.add_row("Empate", f"{prediccion_actual.get('prob_1x2_empate', 0):.1f}%")
        pred_table.add_row("Victoria Visitante", f"{prediccion_actual.get('prob_1x2_visitante', 0):.1f}%")
        pred_table.add_row("Próx. gol Local", f"{prediccion_actual.get('prob_prox_gol_local', 0):.1f}%")
        pred_table.add_row("Próx. gol Visit.", f"{prediccion_actual.get('prob_prox_gol_visitante', 0):.1f}%")
        pred_table.add_row("Marcador Final", prediccion_actual.get("marcador_mas_probable", "-"))
        ic = prediccion_actual.get("ic95_goles_totales", {})
        pred_table.add_row("IC95 Goles", f"{ic.get('min', 0)} - {ic.get('max', 0)}")
        pred_table.add_row("Over 1.5", f"{prediccion_actual.get('prob_over_1_5', 0):.1f}%")
        pred_table.add_row("Over 2.5", f"{prediccion_actual.get('prob_over_2_5', 0):.1f}%")
        pred_table.add_row("Over 3.5", f"{prediccion_actual.get('prob_over_3_5', 0):.1f}%")
        
        btts_val = prediccion_actual.get('prob_btts', 0)
        if m_local >= 1 and m_visit >= 1:
            btts_str = "[green]✅ Ya ocurrió (100%)[/green]"
        else:
            btts_str = f"{btts_val:.1f}%"
        pred_table.add_row("BTTS", btts_str)
        pred_table.add_row(
            "Goles esp. L/V",
            f"{prediccion_actual.get('goles_esperados_local', 0):.1f} / "
            f"{prediccion_actual.get('goles_esperados_visitante', 0):.1f}",
        )
        pred_table.add_row(
            "Tasa ataques L/V",
            f"{prediccion_actual.get('tasa_ataques_local', 0):.2f} / "
            f"{prediccion_actual.get('tasa_ataques_visitante', 0):.2f}",
        )
    else:
        pred_table.add_row("Estado", "[dim]Recalibrando simulador en vivo...[/dim]")

    layout["prediccion"].update(
        Panel(pred_table, title="[bold magenta]PREDICCIÓN FINAL (Ligera)[/bold magenta]",
              border_style="magenta")
    )

    # Sugerencias de Apuestas
    apuestas_table = Table(show_header=False, expand=True, box=None)
    apuestas_table.add_column("Sugerencia", style="bold")

    if prediccion_actual:
        p_gol_l = prediccion_actual.get('prob_prox_gol_local', 0)
        p_gol_v = prediccion_actual.get('prob_prox_gol_visitante', 0)
        p_over_25 = prediccion_actual.get('prob_over_2_5', 0)
        p_over_35 = prediccion_actual.get('prob_over_3_5', 0)
        p_empate = prediccion_actual.get('prob_1x2_empate', 0)
        ic_min = prediccion_actual.get("ic95_goles_totales", {}).get("min", 0)

        sugerencias = 0
        if p_gol_l > 65:
            apuestas_table.add_row("🔥 [bold green]Próximo gol: LOCAL[/bold green]")
            sugerencias += 1
            
        if p_gol_v > 65:
            apuestas_table.add_row("🔥 [bold green]Próximo gol: VISITANTE[/bold green]")
            sugerencias += 1
            
        if p_over_25 > 75:
            apuestas_table.add_row("📈 [yellow]Más de 2.5 Goles en total (Valor Alto)[/yellow]")
            sugerencias += 1
        elif p_over_35 > 60:
            apuestas_table.add_row("🚀 [bold yellow]Más de 3.5 Goles en total (Riesgo/Recompensa)[/bold yellow]")
            sugerencias += 1

        if p_empate > 50:
            apuestas_table.add_row("🛡️ [cyan]Terminará en EMPATE (Posible Cobertura)[/cyan]")
            sugerencias += 1

        if sugerencias == 0:
            apuestas_table.add_row("[dim]Las cuotas en vivo están ajustadas. Evitar apostar ahora.[/dim]")
    else:
        apuestas_table.add_row("[dim]Generando proyecciones avanzadas...[/dim]")
        
    # Sugerencias extremas en vivo (sin esperar al simulador)
    if rgl > 80:
        apuestas_table.add_row("⚡ [bold red]EN VIVO: Gol inminente LOCAL[/bold red]")
    if rgv > 80:
        apuestas_table.add_row("⚡ [bold red]EN VIVO: Gol inminente VISITANTE[/bold red]")

    # Sugerencias específicas de jugadores
    jugadores_tips = ultimo_metrics.get("_jugadores", [])
    if jugadores_tips:
        apuestas_table.add_section()
        for tip in jugadores_tips[:3]:  # Mostrar solo los 3 más importantes para no saturar
            apuestas_table.add_row(tip)

    # Cronología de Google
    cronologia = ultimo_metrics.get("_cronologia", [])
    if cronologia:
        apuestas_table.add_section()
        apuestas_table.add_row("[bold cyan]📋 CRONOLOGÍA (365SCORES)[/bold cyan]")
        for ev in cronologia[:6]:
            if ev.get("tipo") == "gol":
                apuestas_table.add_row(f"  ⚽ {ev.get('jugador', '?')} ({ev.get('minuto', '?')}')")
            else:
                apuestas_table.add_row(f"  • {ev.get('texto', ev.get('tipo', ''))[:50]}")

    # Alineaciones resumidas
    alineaciones = ultimo_metrics.get("_alineaciones", {})
    tit_local = alineaciones.get("local", {}).get("titulares", [])
    tit_visit = alineaciones.get("visitante", {}).get("titulares", [])
    if tit_local or tit_visit:
        apuestas_table.add_section()
        apuestas_table.add_row("[bold cyan]👥 ALINEACIONES (365SCORES)[/bold cyan]")
        if tit_local:
            nombres = ", ".join(
                j.get("nombre", "?") for j in tit_local[:5]
            )
            apuestas_table.add_row(f"  Local: {nombres}...")
        if tit_visit:
            nombres = ", ".join(
                j.get("nombre", "?") for j in tit_visit[:5]
            )
            apuestas_table.add_row(f"  Visit.: {nombres}...")

    layout["apuestas"].update(
        Panel(apuestas_table, title="[bold yellow]💰 APUESTAS Y JUGADORES[/bold yellow]",
              border_style="yellow")
    )

    # Top marcadores más probables (modelo Monte Carlo)
    top_table = Table(show_header=True, expand=True, box=None)
    top_table.add_column("Marcador", style="bold")
    top_table.add_column("Prob.", justify="right")
    top_table.add_column("Distribución goles totales", ratio=2)

    if prediccion_actual:
        for item in prediccion_actual.get("top_marcadores", [])[:5]:
            top_table.add_row(item.get("marcador", "-"), f"{item.get('prob', 0):.1f}%", "")

        hist = prediccion_actual.get("hist_goles_totales", {})
        if hist:
            max_val = max(hist.values()) or 1
            barras = []
            for g in range(0, 7):
                cnt = hist.get(str(g), 0)
                porcentaje = (cnt / sum(hist.values())) * 100 if sum(hist.values()) > 0 else 0
                bloques = int(cnt / max_val * 8) if max_val else 0
                barra = ("█" * bloques).ljust(8, "░")
                barras.append(f"{g} {barra} {porcentaje:4.1f}%")
            top_table.add_row("", "", "  ".join(barras[:3]))
            top_table.add_row("", "", "  ".join(barras[3:6]))
    else:
        top_table.add_row("[dim]Esperando simulación...[/dim]", "", "")

    layout["footer"].update(
        Panel(top_table, title="[bold white]🎯 TOP MARCADORES Y DISTRIBUCIÓN (Modelo)[/bold white]",
              border_style="white")
    )

    return layout


# ──────────────────── TAREAS ASYNC ────────────────────────────────
async def tarea_recolector_metricas() -> None:
    """Lee metrics_queue y actualiza estado global."""
    global ultimo_metrics, buffer_q1
    while True:
        evento = await metrics_queue.get()
        ultimo_metrics = evento
        buffer_q1.append(evento)
        if len(buffer_q1) > 180:
            buffer_q1.pop(0)
        _sync_web()
        metrics_queue.task_done()


async def tarea_simulador() -> None:
    """Ejecuta el simulador continuamente pero SOLO cuando hay datos nuevos."""
    global prediccion_actual, buffer_q1, historial_mc
    ultimo_timestamp_simulado = None
    
    while True:
        await asyncio.sleep(1.0)
        
        # 1. Solo correr matemáticas si entró un paquete de datos nuevo
        timestamp_actual = ultimo_metrics.get("timestamp")
        if not timestamp_actual or timestamp_actual == ultimo_timestamp_simulado:
            continue
            
        ultimo_timestamp_simulado = timestamp_actual

        minuto = ultimo_metrics.get("minuto")
        if minuto is None:
            continue

        minuto = float(minuto)
        marcador = ultimo_metrics.get("marcador", {"local": 0, "visitante": 0})
        
        # No simular si estamos explícitamente en el receso
        estado_texto = ultimo_metrics.get("_status", "").lower()
        if estado_texto in ("half time", "halftime", "ht", "paused") or "descanso" in estado_texto or "medio tiempo" in estado_texto:
            _sync_web()
            continue

        if len(buffer_q1) >= simulador.MIN_EVENTOS_REQUERIDOS:
            try:
                eventos_recientes = buffer_q1[-20:]
                # 2. Desplazar el cálculo pesado a otro hilo (no congela la terminal)
                prediccion_actual = await asyncio.to_thread(
                    simulador.correr, eventos_recientes, minuto, marcador
                )
                historial_mc.append({
                    "minuto": minuto,
                    "prob_1x2_local": prediccion_actual.get("prob_1x2_local", 0),
                    "prob_1x2_empate": prediccion_actual.get("prob_1x2_empate", 0),
                    "prob_1x2_visitante": prediccion_actual.get("prob_1x2_visitante", 0),
                    "marcador_mas_probable": prediccion_actual.get("marcador_mas_probable", "—"),
                    "prob_over_2_5": prediccion_actual.get("prob_over_2_5", 0),
                })
                if len(historial_mc) > 60:
                    historial_mc.pop(0)
                _sync_web()
            except ValueError:
                pass
        else:
            _sync_web()


async def tarea_dashboard() -> None:
    """Actualiza el dashboard con Rich cada DASHBOARD_REFRESH segundos."""
    with Live(generar_dashboard(), refresh_per_second=1, console=console) as live:
        while True:
            await asyncio.sleep(DASHBOARD_REFRESH)

            # Alertas de riesgo crítico
            rgl = ultimo_metrics.get("riesgo_gol_local", 0) or 0
            rgv = ultimo_metrics.get("riesgo_gol_visitante", 0) or 0

            if rgl > 80:
                console.print(
                    f"[bold red]⚠️  ALERTA: RIESGO DE GOL CRÍTICO — LOCAL [{rgl:.1f}%][/bold red]"
                )
            if rgv > 80:
                console.print(
                    f"[bold red]⚠️  ALERTA: RIESGO DE GOL CRÍTICO — VISITANTE [{rgv:.1f}%][/bold red]"
                )

            live.update(generar_dashboard())
            _sync_web()


async def loop_principal() -> None:
    """Loop principal — datos en vivo vía 365Scores."""
    extractores = {
        "365scores": extractor_365scores.iniciar_con_reconexion,
        "sofascore": __import__("extractor_sofascore").iniciar_con_reconexion,
        "api": __import__("extractor_api").iniciar_con_reconexion,
    }
    iniciar_extractor = extractores.get(modo_extraccion, extractor_365scores.iniciar_con_reconexion)
    while True:
        tareas = []
        try:
            tareas.append(asyncio.create_task(
                iniciar_extractor(raw_queue), name="extractor"
            ))
            tareas.append(asyncio.create_task(motor_metricas.iniciar(raw_queue, metrics_queue), name="motor"))
            tareas.append(asyncio.create_task(tarea_recolector_metricas(), name="recolector"))
            tareas.append(asyncio.create_task(tarea_dashboard(), name="dashboard"))
            tareas.append(asyncio.create_task(tarea_simulador(), name="simulador"))

            resultados = await asyncio.gather(*tareas, return_exceptions=True)
            for r in resultados:
                if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                    logger.error("Tarea terminó con error: %s", r)
        except asyncio.CancelledError:
            logger.info("[SISTEMA] Detenido por el usuario.")
            for t in tareas:
                t.cancel()
            break
        except Exception as e:
            logger.error("Error inesperado: %s — reconectando en 3s", e)
            for t in tareas:
                t.cancel()
            await asyncio.sleep(3)


if __name__ == "__main__":
    console.print(
        "\n[bold cyan]╔══════════════════════════════════════════════╗[/bold cyan]"
    )
    console.print(
        "[bold cyan]║   ⚽ AGENTE DE ANÁLISIS DEPORTIVO EN VIVO   ║[/bold cyan]"
    )
    console.print(
        "[bold cyan]╚══════════════════════════════════════════════╝[/bold cyan]\n"
    )

    seleccionar_partido()

    instancias_previas = dashboard_web.buscar_instancias_activas(WEB_PORT)
    if instancias_previas:
        console.print("\n[bold yellow]⚠ Hay otros dashboards activos:[/bold yellow]")
        for inst in instancias_previas:
            console.print(
                f"  · puerto [cyan]{inst['puerto']}[/cyan] — sesión {inst['sesion']} — "
                f"{inst['partido']} (min {inst['minuto']})"
            )
        console.print(
            "[dim]Si abres el puerto equivocado verás datos viejos, no los de esta terminal.[/dim]\n"
        )

    puerto_web = dashboard_web.iniciar(_obtener_estado_web, puerto=WEB_PORT, sesion=SESSION_ID)
    _sync_web()

    console.print(f"[cyan]ID de sesión web:[/cyan] [bold]{SESSION_ID}[/bold]")
    console.print(
        f"\n[bold green]📊 ABRE ESTE DASHBOARD (vinculado a esta terminal):[/bold green]"
    )
    console.print(f"[bold green underline]http://localhost:{puerto_web}[/bold green underline]\n")

    if puerto_web != WEB_PORT:
        console.print(
            f"[bold red]⛔ NO uses http://localhost:{WEB_PORT} — "
            f"pertenece a otra instancia antigua[/bold red]"
        )
        console.print(
            f"[bold red]   Cierra la pestaña vieja y usa solo el enlace de arriba.[/bold red]\n"
        )
    elif instancias_previas:
        otros = [i for i in instancias_previas if i["puerto"] != puerto_web]
        if otros:
            console.print(
                "[bold red]⛔ Instancias antiguas siguen activas en otros puertos "
                "(ver lista arriba). Ciérralas con Ctrl+C en esas terminales "
                "o ejecuta: pkill -f main_agente.py[/bold red]\n"
            )

    try:
        asyncio.run(loop_principal())
    except KeyboardInterrupt:
        console.print(
            "\n[bold yellow][SISTEMA] Detenido correctamente. "
            "¡Hasta la próxima![/bold yellow]"
        )
