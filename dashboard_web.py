"""
dashboard_web.py — Dashboard web con gráficas en vivo.

Sirve una página HTML con Chart.js que se actualiza en tiempo real
vía Server-Sent Events (SSE). Expone también /api/data como JSON.

Uso: se inicia automáticamente desde main_agente.py en el puerto 8765.
Abre http://localhost:8765 en tu navegador.
"""

import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

logger = logging.getLogger("dashboard_web")

PUERTO = 8765
_get_estado: Callable[[], dict] | None = None
_estado_cache: dict = {}
_puerto_activo: int | None = None
_sesion_id: str = ""
_clientes_sse: list = []
_lock = threading.Lock()
_servidor: ThreadingHTTPServer | None = None
_hilo: threading.Thread | None = None
_revision_web: int = 0


def _serializar(obj):
    """Convierte objetos no-JSON a tipos serializables."""
    if hasattr(obj, "item"):
        return obj.item()
    if hasattr(obj, "tolist"):
        return obj.tolist()
    return str(obj)


def _calcular_deltas(valores: list) -> list:
    """Convierte totales acumulados en incrementos entre eventos consecutivos."""
    if not valores:
        return []
    deltas = [0]  # El primer valor no tiene incremento previo
    for i in range(1, len(valores)):
        deltas.append(max(0, valores[i] - valores[i - 1]))
    return deltas


def construir_serie_temporal(buffer: list) -> dict:
    """Extrae series de tiempo del buffer de eventos para las gráficas."""
    if not buffer:
        return {
            "minutos": [],
            "riesgo_local": [],
            "riesgo_visitante": [],
            "animo_local": [],
            "animo_visitante": [],
            "posesion_local": [],
            "posesion_visitante": [],
            "tiros_local": [],
            "tiros_visitante": [],
            "ataques_local": [],
            "ataques_visitante": [],
        }

    minutos, r_l, r_v, a_l, a_v, p_l, p_v, t_l, t_v, at_l, at_v = [], [], [], [], [], [], [], [], [], [], []

    for ev in buffer[-60:]:
        minutos.append(ev.get("minuto", 0))
        r_l.append(ev.get("riesgo_gol_local", 0) or 0)
        r_v.append(ev.get("riesgo_gol_visitante", 0) or 0)
        a_l.append(ev.get("animo_local", 0) or 0)
        a_v.append(ev.get("animo_visitante", 0) or 0)

        pos = ev.get("posesion", {}) or {}
        if isinstance(pos, dict) and pos.get("local") is not None:
            p_l.append(pos.get("local"))
            p_v.append(pos.get("visitante"))
        else:
            p_l.append(None)
            p_v.append(None)

        tiros = ev.get("tiros", {}) or {}
        t_l.append(tiros.get("local", 0) if isinstance(tiros, dict) else 0)
        t_v.append(tiros.get("visitante", 0) if isinstance(tiros, dict) else 0)

        ataques = ev.get("ataques_peligrosos", {}) or {}
        at_l.append(ataques.get("local", 0) if isinstance(ataques, dict) else 0)
        at_v.append(ataques.get("visitante", 0) if isinstance(ataques, dict) else 0)

    # Calcular deltas ANTES de inyectar el cero artificial inicial
    deltas_at_l = _calcular_deltas(at_l)
    deltas_at_v = _calcular_deltas(at_v)

    # Insertar punto base en minuto 0 para que las gráficas arranquen desde 0
    if minutos and minutos[0] > 0:
        minutos.insert(0, 0)
        r_l.insert(0, 0)
        r_v.insert(0, 0)
        a_l.insert(0, 0)
        a_v.insert(0, 0)
        p_l.insert(0, None)   # Sin dato real de posesión al inicio
        p_v.insert(0, None)
        t_l.insert(0, 0)
        t_v.insert(0, 0)
        deltas_at_l.insert(0, 0)
        deltas_at_v.insert(0, 0)

    return {
        "minutos": minutos,
        "riesgo_local": r_l,
        "riesgo_visitante": r_v,
        "animo_local": a_l,
        "animo_visitante": a_v,
        "posesion_local": p_l,
        "posesion_visitante": p_v,
        "tiros_local": t_l,
        "tiros_visitante": t_v,
        "ataques_local": deltas_at_l,
        "ataques_visitante": deltas_at_v,
    }


def empaquetar_estado(
    buffer: list,
    ultimo: dict,
    prediccion: dict,
    meta: dict,
    historial_mc: list | None = None,
) -> dict:
    """Construye el payload JSON completo para el dashboard."""
    eq = ultimo.get("_equipos", {}) or {}
    marcador = ultimo.get("marcador", {}) or {}

    return {
        "partido": meta.get("nombre", "Partido en vivo"),
        "modo": meta.get("modo", "google"),
        "equipos": {
            "local": eq.get("local", "Local"),
            "visitante": eq.get("visitante", "Visitante"),
        },
        "marcador": {
            "local": marcador.get("local", 0) if isinstance(marcador, dict) else 0,
            "visitante": marcador.get("visitante", 0) if isinstance(marcador, dict) else 0,
        },
        "minuto": ultimo.get("minuto"),
        "status": ultimo.get("_status", ""),
        "metricas": {
            "riesgo_gol_local": ultimo.get("riesgo_gol_local", 0),
            "riesgo_gol_visitante": ultimo.get("riesgo_gol_visitante", 0),
            "animo_local": ultimo.get("animo_local", 0),
            "animo_visitante": ultimo.get("animo_visitante", 0),
            "posesion_local": (ultimo.get("posesion") or {}).get("local", 50),
            "posesion_visitante": (ultimo.get("posesion") or {}).get("visitante", 50),
        },
        "prediccion": prediccion or {},
        "historial_mc": historial_mc or [],
        "series": construir_serie_temporal(buffer),
        "cronologia": ultimo.get("_cronologia", []),
        "jugadores": ultimo.get("_jugadores", [])[:5],
        "timestamp": ultimo.get("timestamp", ""),
        "servidor": meta.get("servidor", {}),
    }


def puerto_activo() -> int | None:
    return _puerto_activo


def sesion_id() -> str:
    return _sesion_id


def obtener_estado_servido() -> dict:
    """Estado cacheado (misma fuente que ve la terminal)."""
    if _estado_cache:
        return _estado_cache
    if _get_estado:
        return _get_estado()
    return {}


def publicar_estado(estado: dict) -> None:
    """Guarda el estado y lo empuja a clientes web conectados."""
    global _estado_cache, _revision_web
    _revision_web += 1
    estado = dict(estado)
    servidor = dict(estado.get("servidor") or {})
    servidor["revision"] = _revision_web
    servidor.setdefault("puerto", _puerto_activo)
    servidor.setdefault("sesion", _sesion_id)
    estado["servidor"] = servidor
    _estado_cache = estado
    notificar_clientes(estado)


def notificar_estado_actual() -> None:
    """Refresca cache desde callback y notifica clientes."""
    if _get_estado:
        publicar_estado(_get_estado())


def detectar_servidor_en_puerto(puerto: int) -> dict | None:
    """Devuelve /api/data de un puerto si responde, o None."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{puerto}/api/data",
            headers={"Cache-Control": "no-cache"},
        )
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


def buscar_instancias_activas(puerto_base: int = PUERTO, rango: int = 6) -> list[dict]:
    """Lista dashboards activos en puertos consecutivos."""
    instancias = []
    for puerto in range(puerto_base, puerto_base + rango):
        data = detectar_servidor_en_puerto(puerto)
        if data:
            srv = data.get("servidor") or {}
            instancias.append({
                "puerto": puerto,
                "sesion": srv.get("sesion", "?"),
                "pid": srv.get("pid"),
                "partido": data.get("partido", "?"),
                "minuto": data.get("minuto"),
            })
    return instancias


HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>⚽ Dashboard en Vivo — FIFA Agent</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
    background: #0d1117; color: #e6edf3;
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
  }
  header {
    background: linear-gradient(135deg, #0d1117 0%, #161b22 40%, #1a2332 100%);
    border-bottom: 1px solid #30363d;
    padding: 20px 24px;
    display: flex; justify-content: space-between; align-items: center;
    flex-wrap: wrap; gap: 12px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.3);
    position: sticky; top: 0; z-index: 10;
  }
  header h1 { font-size: 1.3rem; color: #58a6ff; }
  .score {
    font-size: 2.4rem; font-weight: 800; color: #f0c040;
    letter-spacing: 3px;
    text-shadow: 0 0 20px rgba(240,192,64,0.3);
  }
  .badge {
    background: #238636; color: #fff;
    padding: 4px 10px; border-radius: 12px;
    font-size: 0.75rem; font-weight: 600;
    animation: pulse 2s infinite;
  }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.6} }
  .minuto { color: #8b949e; font-size: 0.9rem; }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
    gap: 16px; padding: 16px 24px;
  }
  .card {
    background: #161b22; border: 1px solid #30363d;
    border-radius: 12px; padding: 16px;
    transition: transform 0.2s ease, border-color 0.3s ease, box-shadow 0.3s ease;
  }
  .card:hover {
    transform: translateY(-2px);
    border-color: #58a6ff44;
    box-shadow: 0 8px 24px rgba(0,0,0,0.25);
  }
  .card h2 {
    font-size: 0.85rem; color: #8b949e;
    text-transform: uppercase; letter-spacing: 1px;
    margin-bottom: 12px;
  }
  .card canvas { max-height: 220px; }
  .kpis {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 10px; padding: 0 24px 16px;
  }
  .kpi {
    background: #161b22; border: 1px solid #30363d;
    border-radius: 10px; padding: 14px 12px; text-align: center;
    transition: transform 0.2s ease, box-shadow 0.3s ease;
  }
  .kpi:hover { transform: translateY(-2px); box-shadow: 0 6px 16px rgba(0,0,0,0.2); }
  .kpi .val { font-size: 1.6rem; font-weight: 700; transition: text-shadow 0.3s ease; }
  .kpi .lbl { font-size: 0.7rem; color: #8b949e; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
  .kpi.local .val { color: #3fb950; }
  .kpi.visit .val { color: #f85149; }
  .kpi.neutral .val { color: #58a6ff; }
  .kpi.danger .val { color: #f85149; text-shadow: 0 0 12px rgba(248,81,73,0.5); animation: pulse 1.5s infinite; }
  .tips { padding: 0 24px 24px; }
  .tip {
    background: #1c2128; border-left: 3px solid #f0c040;
    padding: 10px 14px; margin-bottom: 8px;
    border-radius: 0 8px 8px 0; font-size: 0.85rem;
    transition: background 0.2s ease, transform 0.15s ease;
  }
  .tip:hover { background: #22272e; transform: translateX(4px); }
  .tip.alert { border-left-color: #f85149; background: #2d1b1b; }
  .waiting { color: #8b949e; text-align: center; padding: 40px; }
  .mc-section {
    padding: 0 24px 16px;
  }
  .mc-section > h2 {
    font-size: 0.95rem; color: #58a6ff;
    text-transform: uppercase; letter-spacing: 1px;
    margin-bottom: 12px;
  }
  .mc-grid {
    display: grid;
    grid-template-columns: 1.2fr 1fr 1fr;
    gap: 16px;
  }
  @media (max-width: 1100px) {
    .mc-grid { grid-template-columns: 1fr; }
  }
  .mc-meta {
    display: flex; flex-wrap: wrap; gap: 10px;
    margin-bottom: 10px; font-size: 0.8rem; color: #8b949e;
  }
  .mc-meta span {
    background: #21262d; border: 1px solid #30363d;
    padding: 4px 10px; border-radius: 999px;
  }
  .heatmap-wrap { overflow-x: auto; }
  .heatmap-table {
    border-collapse: collapse; width: 100%; min-width: 320px;
    font-size: 0.75rem;
  }
  .heatmap-table th, .heatmap-table td {
    border: 1px solid #30363d; text-align: center;
    padding: 6px 4px; min-width: 42px;
  }
  .heatmap-table th {
    background: #21262d; color: #8b949e; font-weight: 600;
  }
  .heatmap-table td { color: #e6edf3; font-weight: 700; transition: background 0.4s; }
  .heatmap-table .corner { background: #21262d; }
  .heatmap-table .current {
    outline: 2px solid #f0c040; outline-offset: -2px;
  }
  .timeline { padding: 0 24px 24px; display: grid; gap: 8px; }
  .tl-item { 
    background: #161b22; border: 1px solid #30363d; 
    padding: 10px 14px; border-radius: 8px; font-size: 0.85rem;
    display: flex; gap: 12px; align-items: center;
  }
  .tl-min { font-weight: bold; color: #8b949e; width: 40px; }
  .tl-icon { font-size: 1.2rem; }
  .mc-scatter { max-height: 280px !important; }
  .sync-bar {
    background: #1c2128; border-bottom: 1px solid #30363d;
    padding: 8px 24px; font-size: 0.8rem; color: #8b949e;
    display: flex; flex-wrap: wrap; gap: 16px; align-items: center;
  }
  .sync-bar.ok { border-left: 4px solid #3fb950; }
  .sync-bar.warn { border-left: 4px solid #f85149; background: #2d1b1b; color: #ffb4b4; }
  .sync-bar strong { color: #58a6ff; }
</style>
</head>
<body>
<div class="sync-bar ok" id="syncBar">Conectando con el agente...</div>
<header>
  <div>
    <h1 id="titulo">⚽ Cargando partido...</h1>
    <div class="minuto" id="minuto">—</div>
  </div>
  <div class="score" id="marcador">0 - 0</div>
  <div><span class="badge">● EN VIVO</span></div>
</header>

<div class="kpis" id="kpis"></div>

<section class="mc-section">
  <h2>🎲 Simulación Monte Carlo en Vivo</h2>
  <div class="mc-meta" id="mcMeta">
    <span>Esperando primera simulación...</span>
  </div>
  <div class="mc-grid">
    <div class="card">
      <h2>Mapa de calor — Marcadores finales simulados</h2>
      <div class="heatmap-wrap" id="heatmapMc">
        <div class="waiting">Recalibrando Monte Carlo...</div>
      </div>
    </div>
    <div class="card">
      <h2>Nube de simulaciones (250 iteraciones)</h2>
      <canvas id="chartMcScatter" class="mc-scatter"></canvas>
    </div>
    <div class="card">
      <h2>Evolución probabilidades 1X2</h2>
      <canvas id="chartMcEvol"></canvas>
    </div>
  </div>
</section>

<section class="mc-section">
  <h2>⏱️ Cronología Reciente del Partido</h2>
  <div class="timeline" id="cronologia">
    <div class="waiting">Esperando eventos...</div>
  </div>
</section>

<div class="grid">
  <div class="card"><h2>🔥 Riesgo de Gol en Vivo</h2><canvas id="chartRiesgo"></canvas></div>
  <div class="card"><h2>📊 Posesión (%)</h2><canvas id="chartPosesion"></canvas></div>
  <div class="card"><h2>💪 Ánimo / Momentum</h2><canvas id="chartAnimo"></canvas></div>
  <div class="card"><h2>🎯 Ataques Peligrosos (incremento reciente)</h2><canvas id="chartAtaques"></canvas></div>
  <div class="card"><h2>🏆 Probabilidades 1X2</h2><canvas id="chart1x2"></canvas></div>
  <div class="card"><h2>📈 Over/Under & BTTS</h2><canvas id="chartOver"></canvas></div>
  <div class="card"><h2>⚽ Próximo Gol</h2><canvas id="chartProxGol"></canvas></div>
  <div class="card"><h2>🎲 Marcadores Simulados (Monte Carlo)</h2><canvas id="chartMarcadores"></canvas></div>
  <div class="card"><h2>📉 Distribución Goles Totales</h2><canvas id="chartHist"></canvas></div>
</div>

<div class="tips" id="tips"></div>

<script>
const COLORS = {
  local: '#3fb950', visit: '#f85149', empate: '#8b949e',
  over: '#58a6ff', btts: '#d2a8ff', bg: '#161b22'
};

const chartDefaults = {
  responsive: true, maintainAspectRatio: true,
  animation: { duration: 400, easing: 'easeOutQuart' },
  plugins: { legend: { labels: { color: '#8b949e', font: { size: 11, family: 'Inter' } } } },
  scales: {
    x: { ticks: { color: '#8b949e', maxTicksLimit: 8, font: { family: 'Inter' } }, grid: { color: '#21262d' } },
    y: { ticks: { color: '#8b949e' }, grid: { color: '#21262d' }, beginAtZero: true }
  }
};

function makeChart(id, type, labels, datasets, extraOpts={}) {
  const ctx = document.getElementById(id);
  if (!ctx) return null;
  const opts = JSON.parse(JSON.stringify(chartDefaults));
  // Deep merge para scales y plugins (preserva beginAtZero y grid defaults)
  if (extraOpts.scales) {
    for (const axis in extraOpts.scales) {
      opts.scales[axis] = Object.assign({}, opts.scales[axis] || {}, extraOpts.scales[axis]);
    }
  }
  if (extraOpts.plugins) {
    for (const plug in extraOpts.plugins) {
      opts.plugins[plug] = Object.assign({}, opts.plugins[plug] || {}, extraOpts.plugins[plug]);
    }
  }
  const rest = Object.assign({}, extraOpts);
  delete rest.scales;
  delete rest.plugins;
  Object.assign(opts, rest);
  if (ctx._chart) {
    ctx._chart.data.labels = labels;
    ctx._chart.data.datasets = datasets;
    for (const axis in opts.scales) {
      ctx._chart.options.scales[axis] = opts.scales[axis];
    }
    ctx._chart.update('none');
    return ctx._chart;
  }
  ctx._chart = new Chart(ctx, { type, data: { labels, datasets }, options: opts });
  return ctx._chart;
}

function destroyChart(id) {
  const ctx = document.getElementById(id);
  if (ctx?._chart) { ctx._chart.destroy(); ctx._chart = null; }
}

function detJitter(i, axis) {
  return ((i * 7 + axis * 3) % 10 - 5) * 0.035;
}

function renderKPIs(d) {
  const p = d.prediccion || {};
  const m = d.metricas || {};
  const eq = d.equipos || {};
  const rl = (m.riesgo_gol_local||0), rv = (m.riesgo_gol_visitante||0);
  const kpis = [
    { lbl: `Riesgo ${eq.local||'Local'}`, val: rl.toFixed(1)+'%', cls: rl > 70 ? 'danger' : 'local' },
    { lbl: `Riesgo ${eq.visitante||'Visit.'}`, val: rv.toFixed(1)+'%', cls: rv > 70 ? 'danger' : 'visit' },
    { lbl: `Ánimo ${eq.local||'Local'}`, val: (m.animo_local||0).toFixed(1)+'%', cls: 'local' },
    { lbl: `Posesión ${eq.local||'Local'}`, val: (m.posesion_local||0)+'%', cls: 'local' },
    { lbl: `Victoria ${eq.local||'Local'}`, val: (p.prob_1x2_local||0).toFixed(1)+'%', cls: 'local' },
    { lbl: 'Empate', val: (p.prob_1x2_empate||0).toFixed(1)+'%', cls: 'neutral' },
    { lbl: `Victoria ${eq.visitante||'Visit.'}`, val: (p.prob_1x2_visitante||0).toFixed(1)+'%', cls: 'visit' },
    { lbl: 'Marcador Prob.', val: p.marcador_mas_probable||'—', cls: 'neutral' },
    { lbl: 'Over 2.5', val: (p.prob_over_2_5||0).toFixed(1)+'%', cls: 'neutral' },
    { lbl: 'BTTS', val: (p.prob_btts||0).toFixed(1)+'%', cls: 'neutral' },
    { lbl: `xG ${eq.local||'Local'}`, val: (p.goles_esperados_local||0).toFixed(2), cls: 'local' },
    { lbl: `xG ${eq.visitante||'Visit.'}`, val: (p.goles_esperados_visitante||0).toFixed(2), cls: 'visit' },
  ];
  document.getElementById('kpis').innerHTML = kpis.map(k =>
    `<div class="kpi ${k.cls}"><div class="val">${k.val}</div><div class="lbl">${k.lbl}</div></div>`
  ).join('');
}

function heatColor(prob, maxProb) {
  const t = maxProb > 0 ? prob / maxProb : 0;
  // Cold-to-hot gradient: dark → blue → green → yellow → red
  let r, g, b;
  if (t < 0.25) {
    r = Math.round(13 + t * 4 * 40); g = Math.round(17 + t * 4 * 80); b = Math.round(50 + t * 4 * 150);
  } else if (t < 0.5) {
    const s = (t - 0.25) * 4;
    r = Math.round(53 + s * 20); g = Math.round(97 + s * 110); b = Math.round(200 - s * 120);
  } else if (t < 0.75) {
    const s = (t - 0.5) * 4;
    r = Math.round(73 + s * 150); g = Math.round(207 - s * 40); b = Math.round(80 - s * 50);
  } else {
    const s = (t - 0.75) * 4;
    r = Math.round(223 + s * 32); g = Math.round(167 - s * 120); b = Math.round(30 - s * 20);
  }
  const a = 0.3 + t * 0.7;
  return `rgba(${r},${g},${b},${a})`;
}

function renderHeatmapMC(d) {
  const p = d.prediccion || {};
  const mc = p.matriz_marcadores || {};
  const probs = mc.probabilidades || [];
  const maxG = mc.max_goles ?? 6;
  const eq = d.equipos || {};
  const marc = d.marcador || {};
  const host = document.getElementById('heatmapMc');
  if (!host) return;

  if (!probs.length) {
    host.innerHTML = '<div class="waiting">Esperando simulación Monte Carlo...</div>';
    return;
  }

  let maxProb = 0;
  probs.forEach(row => row.forEach(v => { if (v > maxProb) maxProb = v; }));

  let html = '<table class="heatmap-table"><thead><tr><th class="corner">Local ↓ / Visit. →</th>';
  for (let v = 0; v <= maxG; v++) html += `<th>${v}</th>`;
  html += '</tr></thead><tbody>';

  for (let l = 0; l <= maxG; l++) {
    html += `<tr><th>${l}</th>`;
    for (let v = 0; v <= maxG; v++) {
      const prob = probs[l]?.[v] ?? 0;
      const isCurrent = marc.local === l && marc.visitante === v;
      html += `<td class="${isCurrent ? 'current' : ''}" style="background:${heatColor(prob, maxProb)}">${prob > 0 ? prob.toFixed(1)+'%' : '·'}</td>`;
    }
    html += '</tr>';
  }
  html += '</tbody></table>';
  html += `<div style="margin-top:8px;font-size:0.75rem;color:#8b949e">Eje filas: goles ${eq.local||'Local'} · Eje columnas: goles ${eq.visitante||'Visitante'} · Cuadrado dorado = marcador actual</div>`;
  host.innerHTML = html;
}

function renderMcMeta(d) {
  const p = d.prediccion || {};
  const host = document.getElementById('mcMeta');
  if (!host) return;
  if (!p.n_iteraciones) {
    host.innerHTML = '<span>Esperando primera simulación...</span>';
    return;
  }
  const ic = p.ic95_goles_totales || {};
  host.innerHTML = [
    `<span>${p.n_iteraciones} simulaciones</span>`,
    `<span>Marcador más probable: <strong style="color:#f0c040">${p.marcador_mas_probable||'—'}</strong></span>`,
    `<span>IC95 goles totales: ${ic.min ?? '—'} – ${ic.max ?? '—'}</span>`,
    `<span>Tiempo restante simulado: ${(p.tiempo_restante||0).toFixed(0)}'</span>`,
    `<span>xG final L/V: ${(p.goles_esperados_local||0).toFixed(2)} / ${(p.goles_esperados_visitante||0).toFixed(2)}</span>`,
  ].join('');
}

function renderMcScatter(d) {
  const p = d.prediccion || {};
  const sims = p.simulaciones || [];
  const eq = d.equipos || {};
  const marc = d.marcador || {};
  if (!sims.length) { destroyChart('chartMcScatter'); return; }

  makeChart('chartMcScatter', 'scatter', undefined, [
    {
      label: 'Simulaciones MC',
      data: sims.map((s, i) => ({ x: s.local + detJitter(i, 0), y: s.visitante + detJitter(i, 1) })),
      backgroundColor: 'rgba(88,166,255,0.35)',
      borderColor: 'rgba(88,166,255,0.15)',
      pointRadius: 4,
      pointHoverRadius: 6,
    },
    {
      label: 'Marcador actual',
      data: [{ x: marc.local ?? 0, y: marc.visitante ?? 0 }],
      backgroundColor: '#f0c040',
      borderColor: '#f0c040',
      pointRadius: 9,
      pointStyle: 'star',
    },
    {
      label: 'Más probable',
      data: (() => {
        const top = (p.top_marcadores || [])[0];
        if (!top?.marcador) return [];
        const [l, v] = top.marcador.split('-').map(Number);
        return [{ x: l, y: v }];
      })(),
      backgroundColor: '#3fb950',
      borderColor: '#3fb950',
      pointRadius: 8,
      pointStyle: 'rectRot',
    },
  ], {
    plugins: {
      legend: { labels: { color: '#8b949e', font: { size: 11 } } },
      tooltip: {
        callbacks: {
          label: (ctx) => ctx.datasetIndex === 0
            ? `Simulación ~ ${Math.round(ctx.raw.x)}-${Math.round(ctx.raw.y)}`
            : `${ctx.dataset.label}: ${Math.round(ctx.raw.x)}-${Math.round(ctx.raw.y)}`,
        },
      },
    },
    scales: {
      x: {
        title: { display: true, text: `Goles ${eq.local||'Local'}`, color: '#8b949e' },
        ticks: { color: '#8b949e', stepSize: 1 }, grid: { color: '#21262d' },
        min: -0.5, max: 6.5,
      },
      y: {
        title: { display: true, text: `Goles ${eq.visitante||'Visitante'}`, color: '#8b949e' },
        ticks: { color: '#8b949e', stepSize: 1 }, grid: { color: '#21262d' },
        min: -0.5, max: 6.5,
      },
    },
  });
}

function renderMcEvol(d) {
  const hist = d.historial_mc || [];
  const eq = d.equipos || {};
  if (!hist.length) { destroyChart('chartMcEvol'); return; }
  const mins = hist.map(h => Math.round(h.minuto ?? 0));
  makeChart('chartMcEvol', 'line', mins, [
    { label: eq.local||'Local', data: hist.map(h => h.prob_1x2_local), borderColor: COLORS.local, backgroundColor: COLORS.local+'33', fill: false, tension: 0.25 },
    { label: 'Empate', data: hist.map(h => h.prob_1x2_empate), borderColor: COLORS.empate, tension: 0.25 },
    { label: eq.visitante||'Visitante', data: hist.map(h => h.prob_1x2_visitante), borderColor: COLORS.visit, backgroundColor: COLORS.visit+'33', fill: false, tension: 0.25 },
  ], { scales: { y: { max: 100, title: { display: true, text: 'Probabilidad %', color: '#8b949e' } } } });
}

function renderTips(d) {
  const items = [];
  const p = d.prediccion || {};
  const m = d.metricas || {};
  const eq = d.equipos || {};
  if ((m.riesgo_gol_local||0) > 80) items.push({t: `⚡ EN VIVO: Gol inminente ${eq.local||'LOCAL'}`, a: true});
  if ((m.riesgo_gol_visitante||0) > 80) items.push({t: `⚡ EN VIVO: Gol inminente ${eq.visitante||'VISITANTE'}`, a: true});
  if ((p.prob_prox_gol_local||0) > 65) items.push({t: `🔥 Alta probabilidad de próximo gol ${eq.local||'LOCAL'}`});
  if ((p.prob_prox_gol_visitante||0) > 65) items.push({t: `🔥 Alta probabilidad de próximo gol ${eq.visitante||'VISITANTE'}`});
  if ((p.prob_over_2_5||0) > 75) items.push({t: '📈 Over 2.5 goles con valor alto'});
  if ((p.prob_btts||0) > 70) items.push({t: '⚽ Ambos equipos marcarán (BTTS) con alta probabilidad'});
  (d.jugadores||[]).forEach(j => items.push({t: '👤 ' + j}));
  if (!items.length) items.push({t: 'Esperando más datos en vivo...'});
  document.getElementById('tips').innerHTML = items.map(i => `<div class="tip${i.a?' alert':''}">${i.t}</div>`).join('');
}

function renderCronologia(d) {
  const cron = d.cronologia || [];
  if (!cron.length) {
    document.getElementById('cronologia').innerHTML = '<div class="waiting">Sin eventos registrados aún...</div>';
    return;
  }
  // Tomar los últimos 10 y revertir para mostrar el más nuevo arriba
  const recientes = cron.slice(-8).reverse();
  const html = recientes.map(c => {
    let icon = '•';
    if (c.tipo === 'gol') icon = '⚽';
    else if (c.tipo === 'amarilla') icon = '🟨';
    else if (c.tipo === 'roja') icon = '🟥';
    else if (c.tipo === 'sustitucion') icon = '↔';
    
    let txt = c.texto || '';
    if (c.tipo === 'gol') txt = `<strong style="color:#f0c040">GOL</strong> — ${c.jugador} <span style="color:#8b949e">(${c.equipo.toUpperCase()})</span>`;
    
    return `<div class="tl-item"><div class="tl-min">${c.minuto}'</div><div class="tl-icon">${icon}</div><div>${txt}</div></div>`;
  }).join('');
  document.getElementById('cronologia').innerHTML = html;
}

function renderSyncBar(d) {
  const bar = document.getElementById('syncBar');
  if (!bar) return;
  const srv = d.servidor || {};
  const rev = srv.revision ?? 0;
  const sesion = srv.sesion || '—';
  const puerto = window.location.port || '80';
  const puertoSrv = srv.puerto != null ? String(srv.puerto) : puerto;
  const syncOk = puerto === puertoSrv;
  const hora = srv.actualizado_en || d.timestamp || '—';
  bar.className = 'sync-bar ' + (syncOk ? 'ok' : 'warn');
  if (!syncOk) {
    bar.innerHTML = `⛔ <strong>Instancia incorrecta</strong> — Estás en puerto ${puerto}, `
      + `pero el agente activo está en <strong>${puertoSrv}</strong> (sesión ${sesion}). `
      + `Cierra esta pestaña y abre <strong>http://localhost:${puertoSrv}</strong>`;
    return;
  }
  bar.innerHTML = `✓ Sincronizado · sesión <strong>${sesion}</strong> · puerto <strong>${puertoSrv}</strong> · rev <strong>${rev}</strong> · actualizado <strong>${hora}</strong>`;
}

function updateDashboard(d) {
  renderSyncBar(d);
  const eq = d.equipos || {};
  document.getElementById('titulo').textContent = `⚽ ${eq.local} vs ${eq.visitante}`;
  const ml = d.marcador?.local ?? 0, mv = d.marcador?.visitante ?? 0;
  document.getElementById('marcador').textContent = `${ml} - ${mv}`;
  const min = d.minuto != null ? Math.round(d.minuto) + "'" : '—';
  document.getElementById('minuto').textContent = `${min}  ·  ${d.status || d.modo?.toUpperCase() || ''}`;

  renderKPIs(d);
  renderTips(d);
  renderHeatmapMC(d);
  renderMcMeta(d);
  renderMcScatter(d);
  renderMcEvol(d);
  renderCronologia(d);

  const s = d.series || {};
  const mins = s.minutos?.map(m => Math.round(m)) || [];

  makeChart('chartRiesgo', 'line', mins, [
    { label: eq.local||'Local', data: s.riesgo_local, borderColor: COLORS.local, backgroundColor: COLORS.local+'22', fill: true, tension: 0.4, pointRadius: 0, borderWidth: 2.5 },
    { label: eq.visitante||'Visitante', data: s.riesgo_visitante, borderColor: COLORS.visit, backgroundColor: COLORS.visit+'22', fill: true, tension: 0.4, pointRadius: 0, borderWidth: 2.5 },
  ], { scales: { y: { min: 0, max: 100, beginAtZero: true, title: { display: true, text: '%', color: '#8b949e' } } } });

  makeChart('chartPosesion', 'line', mins, [
    { label: eq.local||'Local', data: s.posesion_local, borderColor: COLORS.local, tension: 0.4, spanGaps: true, pointRadius: 0, borderWidth: 2.5 },
    { label: eq.visitante||'Visitante', data: s.posesion_visitante, borderColor: COLORS.visit, tension: 0.4, spanGaps: true, pointRadius: 0, borderWidth: 2.5 },
  ], { scales: { y: { min: 0, max: 100, title: { display: true, text: '%', color: '#8b949e' } } } });

  makeChart('chartAnimo', 'line', mins, [
    { label: 'Ánimo Local', data: s.animo_local, borderColor: COLORS.local, backgroundColor: COLORS.local+'22', fill: true, tension: 0.4, pointRadius: 0, borderWidth: 2.5 },
    { label: 'Ánimo Visit.', data: s.animo_visitante, borderColor: COLORS.visit, backgroundColor: COLORS.visit+'22', fill: true, tension: 0.4, pointRadius: 0, borderWidth: 2.5 },
  ], { scales: { y: { min: 0, max: 100, beginAtZero: true, title: { display: true, text: '%', color: '#8b949e' } } } });

  makeChart('chartAtaques', 'bar', mins.slice(-15), [
    { label: 'Ataques Local', data: (s.ataques_local||[]).slice(-15), backgroundColor: COLORS.local+'aa', borderRadius: 4 },
    { label: 'Ataques Visit.', data: (s.ataques_visitante||[]).slice(-15), backgroundColor: COLORS.visit+'aa', borderRadius: 4 },
  ]);

  const p = d.prediccion || {};
  makeChart('chart1x2', 'bar',
    [eq.local||'Local', 'Empate', eq.visitante||'Visitante'],
    [{ label: 'Probabilidad %', data: [p.prob_1x2_local||0, p.prob_1x2_empate||0, p.prob_1x2_visitante||0],
       backgroundColor: [COLORS.local+'cc', COLORS.empate+'cc', COLORS.visit+'cc'], borderColor: [COLORS.local, COLORS.empate, COLORS.visit], borderWidth: 2, borderRadius: 6 }],
    { scales: { y: { max: 100 } } }
  );

  const bttsLabel = (d.marcador?.local >= 1 && d.marcador?.visitante >= 1) ? 'BTTS (Cumplido)' : 'BTTS';
  makeChart('chartOver', 'bar',
    ['Over 1.5', 'Over 2.5', 'Over 3.5', bttsLabel],
    [{ label: '%', data: [p.prob_over_1_5||0, p.prob_over_2_5||0, p.prob_over_3_5||0, p.prob_btts||0],
       backgroundColor: [COLORS.over+'cc', COLORS.over+'aa', COLORS.over+'77', COLORS.btts+'cc'], borderColor: [COLORS.over, COLORS.over, COLORS.over, COLORS.btts], borderWidth: 2, borderRadius: 6 }],
    { scales: { y: { max: 100 } } }
  );

  const proxL = p.prob_proximo_gol_local ?? p.prob_prox_gol_local ?? 0;
  const proxV = p.prob_proximo_gol_visitante ?? p.prob_prox_gol_visitante ?? 0;
  const sinGol = p.prob_sin_mas_goles ?? Math.max(0, 100 - proxL - proxV);
  makeChart('chartProxGol', 'doughnut',
    [eq.local||'Local', eq.visitante||'Visitante', 'Sin más goles'],
    [{ data: [proxL, proxV, sinGol],
       backgroundColor: [COLORS.local+'dd', COLORS.visit+'dd', '#30363d'], borderColor: '#0d1117', borderWidth: 3, hoverOffset: 8 }],
    { cutout: '55%', plugins: { legend: { position: 'bottom' } } }
  );

  const top = p.top_marcadores || [];
  makeChart('chartMarcadores', 'bar',
    top.map(t => t.marcador),
    [{ label: 'Probabilidad %', data: top.map(t => t.prob),
       backgroundColor: COLORS.over+'88', borderColor: COLORS.over, borderWidth: 2, borderRadius: 4 }],
    { indexAxis: 'y', scales: { x: { max: 100 } } }
  );

  const hist = p.hist_goles_totales || {};
  const nIter = p.n_iteraciones || 250;
  const histLabels = Object.keys(hist).sort((a,b) => +a - +b);
  makeChart('chartHist', 'bar', histLabels,
    [{ label: '% simulaciones', data: histLabels.map(k => Math.round((hist[k] / nIter) * 1000) / 10),
       backgroundColor: COLORS.btts+'88', borderColor: COLORS.btts, borderWidth: 2, borderRadius: 6 }],
    { scales: { y: { max: 100, title: { display: true, text: '% de 250 sim.', color: '#8b949e' } } } }
  );
}

// Polling principal — más fiable que SSE entre hilos
let ultimaRevision = -1;

async function cargarEstado() {
  try {
    const resp = await fetch('/api/data', { cache: 'no-store' });
    if (!resp.ok) return;
    const d = await resp.json();
    const rev = (d.servidor || {}).revision ?? 0;
    if (rev === ultimaRevision && rev !== 0) return;
    ultimaRevision = rev;
    updateDashboard(d);
  } catch (err) {
    const bar = document.getElementById('syncBar');
    if (bar) {
      bar.className = 'sync-bar warn';
      bar.textContent = '⚠ Sin conexión con el agente — ¿está corriendo main_agente.py?';
    }
  }
}

function conectarSSE() {
  const es = new EventSource('/api/stream');
  es.onmessage = (e) => {
    try {
      const d = JSON.parse(e.data);
      const rev = (d.servidor || {}).revision ?? 0;
      if (rev === ultimaRevision && rev !== 0) return;
      ultimaRevision = rev;
      updateDashboard(d);
    } catch(err) { console.error('Error parseando SSE:', err); }
  };
  es.onerror = () => {
    es.close();
    setTimeout(conectarSSE, 5000);
  };
}

cargarEstado();
setInterval(cargarEstado, 1000);
conectarSSE();
</script>
</body>
</html>"""


class _DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Silenciar logs HTTP

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_DASHBOARD.encode())

        elif self.path == "/api/data":
            estado = obtener_estado_servido()
            payload = json.dumps(estado, default=_serializar).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors()
            self.end_headers()
            self.wfile.write(payload)

        elif self.path == "/api/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self._cors()
            self.end_headers()

            wfile = self.wfile
            with _lock:
                _clientes_sse.append(wfile)

            try:
                estado = obtener_estado_servido()
                if estado:
                    data = json.dumps(estado, default=_serializar)
                    wfile.write(f"data: {data}\n\n".encode())
                    wfile.flush()

                # Mantener la conexión abierta; si el handler termina, el socket se cierra
                # y el navegador deja de recibir actualizaciones en vivo.
                while True:
                    time.sleep(15)
                    wfile.write(b": heartbeat\n\n")
                    wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
                pass
            finally:
                with _lock:
                    if wfile in _clientes_sse:
                        _clientes_sse.remove(wfile)

        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()


def notificar_clientes(estado: dict) -> None:
    """Envía actualización SSE a todos los clientes conectados."""
    payload = f"data: {json.dumps(estado, default=_serializar)}\n\n".encode()
    muertos = []
    with _lock:
        for wfile in _clientes_sse:
            try:
                wfile.write(payload)
                wfile.flush()
            except Exception:
                muertos.append(wfile)
        for w in muertos:
            _clientes_sse.remove(w)


def iniciar(get_estado: Callable[[], dict], puerto: int = PUERTO, sesion: str = "") -> int:
    """Inicia el servidor web en un hilo daemon. Devuelve el puerto usado."""
    global _get_estado, _servidor, _hilo, _puerto_activo, _sesion_id, _estado_cache
    _get_estado = get_estado
    _sesion_id = sesion
    _estado_cache = {}

    puerto_real = _puerto_libre(puerto)
    _puerto_activo = puerto_real
    if puerto_real != puerto:
        logger.warning("Puerto %d ocupado, usando %d", puerto, puerto_real)

    ThreadingHTTPServer.allow_reuse_address = True
    _servidor = ThreadingHTTPServer(("0.0.0.0", puerto_real), _DashboardHandler)
    _hilo = threading.Thread(target=_servidor.serve_forever, daemon=True)
    _hilo.start()
    logger.info("Dashboard web iniciado en http://localhost:%d (sesión %s)", puerto_real, sesion)
    if _get_estado:
        publicar_estado(_get_estado())
    return puerto_real


def detener() -> None:
    global _servidor
    if _servidor:
        _servidor.shutdown()
        _servidor = None


def _puerto_libre(puerto: int, max_intentos: int = 10) -> int:
    """Busca un puerto TCP libre empezando por `puerto`."""
    for candidato in range(puerto, puerto + max_intentos):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", candidato))
                return candidato
            except OSError:
                continue
    raise OSError(
        f"No hay puertos libres entre {puerto} y {puerto + max_intentos - 1}. "
        "Cierra otras instancias del agente."
    )
