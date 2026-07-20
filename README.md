# ⚽ Football Live Agent — Análisis Deportivo en Tiempo Real

<div align="center">

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Pandas](https://img.shields.io/badge/Pandas-Data_Engine-150458?style=for-the-badge&logo=pandas&logoColor=white)
![NumPy](https://img.shields.io/badge/NumPy-Monte_Carlo-013243?style=for-the-badge&logo=numpy&logoColor=white)
![Asyncio](https://img.shields.io/badge/Asyncio-Concurrent-2496ED?style=for-the-badge&logo=python&logoColor=white)
![Chart.js](https://img.shields.io/badge/Chart.js-Dashboards-FF6384?style=for-the-badge&logo=chartdotjs&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

**Sistema multi-agente que extrae datos de partidos de fútbol en vivo, calcula métricas propias (riesgo de gol, momentum) y predice resultados finales con simulación Monte Carlo.**

[Características](#-características) · [Arquitectura](#-arquitectura) · [Instalación](#-instalación) · [Uso](#-uso) · [Stack Técnico](#-stack-técnico)

</div>

---

## 🎯 Características

- 📡 **Extracción multi-fuente en tiempo real** — 365Scores, Sofascore, Flashscore (web scraping + API REST)
- 📊 **Dashboard terminal interactivo** — Métricas en vivo, sparklines ASCII, alertas de riesgo con `Rich`
- 🌐 **Dashboard web con gráficas** — Chart.js + Server-Sent Events (SSE), actualización en tiempo real
- 🎲 **Simulación Monte Carlo** (250 iteraciones) — Predicción de marcador final, probabilidades 1X2, Over/Under
- 📈 **Métricas propias** — Riesgo de Gol y Ánimo/Momentum calculados con ventanas deslizantes en Pandas
- 🔮 **Predictor pre-partido** — H2H scraping + Poisson + Monte Carlo (5,000 simulaciones) para partidos futuros
- ⚡ **Arquitectura async** — Pipeline producer/consumer con `asyncio.Queue`, cero bloqueos
- 🖥️ **Selector visual de partidos** — Interfaz `curses` con navegación por flechas y categorías (En Vivo, Hoy, Próximos)

## 🏗️ Arquitectura

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────────┐
│   Extractores   │     │    Motor     │     │   Simulador     │
│                 │     │  Métricas    │     │  Monte Carlo    │
│ · 365Scores API │────▶│              │────▶│                 │
│ · Flashscore    │     │ · Pandas DF  │     │ · Poisson       │
│ · Sofascore     │ raw │ · Riesgo Gol │ met │ · Binomial      │
│ · Football-data │ queue│ · Momentum  │rics │ · Top marcadores│
│   (.org) API    │     │ · Ventana    │queue│ · Over/Under    │
└─────────────────┘     │   deslizante │     │ · IC 95%        │
                        └──────────────┘     └────────┬────────┘
                                                      │
                              ┌────────────────────────┼──────────────┐
                              │                        │              │
                    ┌─────────▼─────────┐   ┌─────────▼─────────┐    │
                    │  Dashboard Web    │   │ Dashboard Terminal │    │
                    │                   │   │                   │    │
                    │ · Chart.js        │   │ · Rich Layout     │    │
                    │ · SSE stream      │   │ · Sparklines      │    │
                    │ · Mapa de calor   │   │ · Alertas color   │    │
                    │ · Nube MC scatter │   │ · Sugerencias     │    │
                    │ · Evolución 1X2   │   │ · Top marcadores  │    │
                    └───────────────────┘   └───────────────────┘    │
                                                                     │
                                            ┌────────────────────────┘
                                            │
                                  ┌─────────▼─────────┐
                                  │ Predictor Previo  │
                                  │                   │
                                  │ · H2H Flashscore  │
                                  │ · Forma reciente  │
                                  │ · Wikipedia stats  │
                                  │ · 5,000 sim. MC   │
                                  └───────────────────┘
```

### Pipeline de datos

1. **Extractores** hacen polling cada ~20s a APIs/sitios deportivos y producen `GameEvent` (dict con marcador, minuto, tiros, posesión, etc.)
2. **Motor de Métricas** consume eventos crudos, los aplana en un `DataFrame` de Pandas, y calcula métricas derivadas con ventana deslizante (180 eventos)
3. **Simulador Monte Carlo** toma los últimos 20 eventos enriquecidos, estima tasas de tiros/goles por minuto, y corre 250 simulaciones Poisson+Binomial del tiempo restante
4. **Dashboards** renderizan el estado global en paralelo: terminal (Rich) y web (HTTP + SSE embebido)

## 📦 Módulos

| Módulo | Líneas | Descripción |
|---|---|---|
| `main_agente.py` | 642 | Orquestador principal — asyncio loop, dashboard terminal Rich |
| `dashboard_web.py` | 908 | Servidor HTTP embebido + SSE + HTML/Chart.js inline |
| `simulador.py` | 202 | Monte Carlo: Poisson + Binomial, matriz de marcadores, IC95% |
| `motor_metricas.py` | 237 | Pandas DataFrame buffer, riesgo de gol, ánimo dinámico |
| `predictor_previo.py` | 585 | Pre-match: H2H scraping, Wikipedia stats, 5K simulaciones |
| `selector_visual.py` | 555 | Interfaz curses: partidos en vivo / hoy / próximos |
| `extractor_365scores.py` | 407 | API 365Scores: marcador, stats, cronología, alineaciones |
| `extractor_sofascore.py` | ~500 | API Sofascore con curl_cffi (anti-bot bypass) |
| `extractor_flashscore.py` | ~300 | Playwright headless scraping |
| `extractor_google.py` | ~500 | Google search scraping como fallback |
| `football_data_client.py` | ~120 | football-data.org REST API client |

## 🚀 Instalación

```bash
# 1. Clonar el repositorio
git clone https://github.com/TU_USUARIO/football-live-agent.git
cd football-live-agent

# 2. Crear entorno virtual
python3 -m venv .venv
source .venv/bin/activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Instalar navegador para Playwright (necesario para scraping)
python -m playwright install chromium

# 5. (Opcional) Configurar API key de football-data.org
echo "TU_API_KEY" > api_key.txt
```

### Requisitos

- **Python 3.11+**
- **Dependencias**: Rich, NumPy, Pandas, Playwright, curl_cffi
- **Sistema**: Linux / macOS / WSL (terminal con soporte curses)

## 🎮 Uso

### Modo en vivo (partido actual)

```bash
python main_agente.py
```

1. Se abre el **selector visual** con partidos en vivo (365Scores) y próximos (Flashscore)
2. Navega con ↑↓, selecciona con Enter
3. El **dashboard terminal** se actualiza cada 3 segundos
4. Abre `http://localhost:8765` para el **dashboard web** con gráficas interactivas

### Modo pre-partido (predicción)

Selecciona un partido futuro en el selector → se ejecuta automáticamente el **predictor previo**:
- Scraping H2H de Flashscore
- Búsqueda de goleadores en Wikipedia
- 5,000 simulaciones Monte Carlo
- Dashboard completo con sugerencias

## 🛠️ Stack Técnico

| Categoría | Tecnologías |
|---|---|
| **Lenguaje** | Python 3.11+ |
| **Async** | `asyncio`, `asyncio.Queue`, `asyncio.to_thread` |
| **Data** | Pandas (DataFrame buffer), NumPy (Monte Carlo) |
| **Scraping** | Playwright (headless Chromium), curl_cffi (TLS fingerprint) |
| **Terminal UI** | Rich (Layout, Table, Sparklines, Live) |
| **Web** | HTTP server stdlib + SSE, Chart.js 4.x |
| **Estadística** | Distribución Poisson, Binomial, Intervalos de confianza 95% |
| **APIs** | 365Scores (REST), football-data.org (REST) |

## 📊 Métricas que calcula

### En vivo
- **Riesgo de Gol** — % probabilidad de gol basado en tasa de tiros/ataques peligrosos por minuto
- **Ánimo / Momentum** — Quién está dominando los ataques en la ventana reciente
- **Sparklines** — Mini gráficas ASCII de tendencia para cada métrica

### Predicción (Monte Carlo)
- **1X2** — Probabilidades de Victoria Local / Empate / Victoria Visitante
- **Over/Under** — 1.5, 2.5, 3.5 goles
- **BTTS** — Ambos marcan
- **Marcador más probable** — Moda de 250 simulaciones
- **IC 95%** — Intervalo de confianza para goles totales
- **Mapa de calor** — Matriz de probabilidades de marcadores finales
- **xG** — Goles esperados por equipo

## 📁 Estructura del proyecto

```
.
├── main_agente.py           # Orquestador principal + dashboard terminal
├── dashboard_web.py         # Servidor web embebido + Chart.js
├── simulador.py             # Monte Carlo (Poisson + Binomial)
├── motor_metricas.py        # Pandas engine: riesgo de gol, momentum
├── predictor_previo.py      # Predicciones pre-partido (H2H + MC)
├── selector_visual.py       # Selector curses con categorías
├── extractor_365scores.py   # API 365Scores
├── extractor_sofascore.py   # API Sofascore
├── extractor_flashscore.py  # Playwright scraping
├── extractor_google.py      # Google scraping (fallback)
├── extractor_vivo.py        # Extractor genérico en vivo
├── extractor_api.py         # football-data.org wrapper
├── football_data_client.py  # REST client para football-data.org
├── buscar_partidos.py       # Utilidad de búsqueda
├── test_integracion.py      # Tests de integración
├── test_extractor.py        # Tests de extractores
├── test_motor.py            # Tests del motor de métricas
├── test_motor2.py           # Tests adicionales del motor
├── test_simulador.py        # Tests del simulador Monte Carlo
├── requirements.txt         # Dependencias Python
└── LICENSE                  # MIT License
```

## 📄 Licencia

Este proyecto está bajo la licencia [MIT](LICENSE).
