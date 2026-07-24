#!/usr/bin/env python3
"""
buscar_partidos.py — Busca partidos en vivo y genera partidos.json.

Consulta API-Football para listar todos los partidos que están
jugándose en este momento, o los partidos del día, y permite
seleccionar uno para configurar el sistema.

Uso:
  API_FOOTBALL_KEY=tu_clave python3 buscar_partidos.py

Obtén tu clave gratis en: https://dashboard.api-football.com/register
(100 peticiones/día, no requiere tarjeta de crédito)
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

logging.basicConfig(format="[%(asctime)s] [%(name)s] %(message)s")
logger = logging.getLogger("buscar_partidos")
logger.setLevel(logging.INFO)

API_BASE_URL = "https://v3.football.api-sports.io"
PARTIDOS_FILE = "partidos.json"

console = Console()


def _api_request(endpoint: str, params: dict | None = None) -> dict:
    """Petición GET a API-Football."""
    key = os.getenv("API_FOOTBALL_KEY", "")
    if not key:
        console.print(
            "\n[bold red]ERROR: Necesitas configurar tu API key.[/bold red]\n"
        )
        console.print("[yellow]Pasos para obtener tu clave GRATIS:[/yellow]")
        console.print("  1. Ve a [cyan]https://dashboard.api-football.com/register[/cyan]")
        console.print("  2. Regístrate (no pide tarjeta de crédito)")
        console.print("  3. En el dashboard, copia tu API Key")
        console.print("  4. Ejecuta:")
        console.print(
            '     [green]API_FOOTBALL_KEY="tu_clave_aqui" python3 buscar_partidos.py[/green]\n'
        )
        sys.exit(1)

    url = f"{API_BASE_URL}/{endpoint}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{query}"

    req = Request(url)
    req.add_header("x-apisports-key", key)

    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def buscar_en_vivo() -> list[dict]:
    """Busca todos los partidos que se están jugando ahora."""
    console.print("\n[bold cyan]🔍 Buscando partidos EN VIVO...[/bold cyan]\n")

    data = _api_request("fixtures", {"live": "all"})
    partidos = []

    for fix in data.get("response", []):
        fixture = fix.get("fixture", {})
        teams = fix.get("teams", {})
        goals = fix.get("goals", {})
        league = fix.get("league", {})
        status = fixture.get("status", {})

        partidos.append({
            "id": fixture.get("id"),
            "nombre": f"{teams.get('home', {}).get('name', '?')} vs {teams.get('away', {}).get('name', '?')}",
            "liga": f"{league.get('name', '?')} ({league.get('country', '?')})",
            "marcador": f"{goals.get('home', 0)}-{goals.get('away', 0)}",
            "minuto": status.get("elapsed", 0),
            "estado": status.get("long", ""),
            "url": f"https://v3.football.api-sports.io/fixtures?id={fixture.get('id')}",
        })

    return partidos


def buscar_hoy() -> list[dict]:
    """Busca todos los partidos programados para hoy."""
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    console.print(f"\n[bold cyan]📅 Buscando partidos del día {hoy}...[/bold cyan]\n")

    data = _api_request("fixtures", {"date": hoy})
    partidos = []

    for fix in data.get("response", []):
        fixture = fix.get("fixture", {})
        teams = fix.get("teams", {})
        goals = fix.get("goals", {})
        league = fix.get("league", {})
        status = fixture.get("status", {})

        estado = status.get("long", "")
        hora = fixture.get("date", "")
        if hora:
            try:
                dt = datetime.fromisoformat(hora.replace("Z", "+00:00"))
                hora_local = dt.strftime("%H:%M")
            except ValueError:
                hora_local = hora
        else:
            hora_local = "?"

        partidos.append({
            "id": fixture.get("id"),
            "nombre": f"{teams.get('home', {}).get('name', '?')} vs {teams.get('away', {}).get('name', '?')}",
            "liga": f"{league.get('name', '?')} ({league.get('country', '?')})",
            "marcador": f"{goals.get('home', 0) or '-'}-{goals.get('away', 0) or '-'}",
            "hora": hora_local,
            "minuto": status.get("elapsed"),
            "estado": estado,
            "url": f"https://v3.football.api-sports.io/fixtures?id={fixture.get('id')}",
        })

    return partidos


def mostrar_y_seleccionar(partidos: list[dict]) -> dict | None:
    """Muestra la tabla de partidos y permite seleccionar uno."""
    if not partidos:
        console.print("[yellow]No se encontraron partidos.[/yellow]")
        return None

    table = Table(title="⚽ Partidos Encontrados", show_lines=True)
    table.add_column("#", style="bold yellow", width=4)
    table.add_column("Partido", style="bold white")
    table.add_column("Liga", style="dim")
    table.add_column("Estado", style="cyan")
    table.add_column("Marcador", style="bold green", justify="center")
    table.add_column("Min", justify="center")

    for i, p in enumerate(partidos, 1):
        min_str = str(p.get("minuto", p.get("hora", "?"))) or "—"
        estado = p.get("estado", "")

        # Color del estado
        if "progress" in estado.lower() or "half" in estado.lower():
            estado_style = "[green]" + estado + "[/green]"
        elif "finished" in estado.lower():
            estado_style = "[dim]" + estado + "[/dim]"
        elif "not started" in estado.lower():
            estado_style = "[yellow]" + estado + "[/yellow]"
        else:
            estado_style = estado

        table.add_row(
            str(i),
            p["nombre"],
            p["liga"],
            estado_style,
            p.get("marcador", "—"),
            min_str,
        )

    console.print(table)
    console.print()

    eleccion = Prompt.ask(
        f"[bold]Selecciona un partido [1-{len(partidos)}] o 'q' para salir[/bold]",
        default="1",
    )

    if eleccion.lower() == "q":
        return None

    try:
        idx = int(eleccion) - 1
        if 0 <= idx < len(partidos):
            return partidos[idx]
    except ValueError:
        pass

    console.print("[red]Selección inválida.[/red]")
    return None


def guardar_partido(partido: dict) -> None:
    """
    Guarda el partido seleccionado en partidos.json y configura
    las variables de entorno necesarias.
    """
    config = [
        {
            "id": partido["id"],
            "nombre": partido["nombre"],
            "liga": partido.get("liga", ""),
            "url": partido["url"],
            "fixture_id": str(partido["id"]),
        }
    ]

    # Leer partidos existentes y agregar si no está
    if os.path.exists(PARTIDOS_FILE):
        with open(PARTIDOS_FILE, "r", encoding="utf-8") as f:
            existentes = json.load(f)
        # Evitar duplicados
        ids_existentes = {p.get("id") for p in existentes}
        if partido["id"] not in ids_existentes:
            existentes.append(config[0])
            config = existentes
        else:
            config = existentes
    
    with open(PARTIDOS_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    console.print(f"\n[green]✓ Partido guardado en {PARTIDOS_FILE}[/green]")
    console.print(
        f"\n[bold]Para iniciar el análisis, ejecuta:[/bold]\n"
        f'  [cyan]API_FOOTBALL_KEY="tu_clave" '
        f'FIXTURE_ID="{partido["id"]}" '
        f"python3 main_agente.py[/cyan]\n"
    )


def main():
    """Flujo principal del buscador de partidos."""
    console.print(
        Panel(
            "[bold white]🔍 BUSCADOR DE PARTIDOS EN VIVO[/bold white]\n"
            "[dim]Conecta con API-Football para encontrar partidos reales[/dim]",
            border_style="cyan",
        )
    )

    opcion = Prompt.ask(
        "\n[bold]¿Qué deseas buscar?[/bold]\n"
        "  [yellow]1.[/yellow] Partidos EN VIVO ahora mismo\n"
        "  [yellow]2.[/yellow] Todos los partidos de hoy\n"
        "  [yellow]3.[/yellow] Ingresar un FIXTURE_ID manualmente\n",
        choices=["1", "2", "3"],
        default="1",
    )

    if opcion == "1":
        partidos = buscar_en_vivo()
        if not partidos:
            console.print(
                "\n[yellow]No hay partidos en vivo ahora. "
                "¿Quieres ver los de hoy?[/yellow]"
            )
            if Prompt.ask("Ver partidos de hoy", choices=["s", "n"], default="s") == "s":
                partidos = buscar_hoy()

    elif opcion == "2":
        partidos = buscar_hoy()

    elif opcion == "3":
        fix_id = Prompt.ask("[bold]Ingresa el FIXTURE_ID[/bold]")
        partido = {"id": int(fix_id), "nombre": f"Fixture #{fix_id}",
                    "url": f"https://v3.football.api-sports.io/fixtures?id={fix_id}"}
        guardar_partido(partido)
        return

    if opcion in ("1", "2"):
        partido = mostrar_y_seleccionar(partidos)
        if partido:
            guardar_partido(partido)


if __name__ == "__main__":
    main()
