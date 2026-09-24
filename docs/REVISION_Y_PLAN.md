# Football Live Agent: revisión, plan aplicado y evidencia
Fecha: 10 de septiembre de 2026. Base revisada: commit 42c3896.

## Dictamen

Sí existe fundamento para construir un predictor probabilístico moderadamente útil: modelar goles con Poisson es una aproximación establecida. Sin embargo, el proyecto original no había demostrado precisión. Monte Carlo no aprende por sí solo: replica los supuestos de entrada.

El nuevo modelo histórico obtuvo 195 aciertos 1X2 sobre 380 partidos (51,3%) y 49 marcadores exactos (12,9%). Es evidencia preliminar para prepartido en una temporada de una liga. No demuestra precisión en vivo, generalización a selecciones, ni rentabilidad. No se midió el antiguo sistema contra esos mismos partidos porque no hay snapshots históricos de sus entradas.

## Hallazgos del código original

- El simulador mezclaba tiros totales y tiros a puerta cuando estos últimos eran cero. Tampoco distinguía ausencias de ceros.
- El horizonte mínimo de un minuto permitía goles incluso al terminar el tiempo.
- Sólo 250 simulaciones producían variación numérica: en una probabilidad de 50%, el error estándar Monte Carlo es aproximadamente 3,16 puntos porcentuales. Aumentar iteraciones reduce este ruido, no corrige tasas equivocadas.
- Prepartido mezclaba 60% de un Poisson histórico con 40% de una simulación de tiros sintéticos. La tabla de marcadores no representaba las mismas probabilidades 1X2.
- H2H, forma y temporada reutilizaban la misma fuente con nombres diferentes; se fabricaban tiros, córneres, tarjetas y jugadores de respaldo.
- La selección global de filas H2H podía incorporar partidos contra otros rivales.
- El índice de presión dependía del número de consultas, tenía coeficientes arbitrarios y aparecía como probabilidad de gol o “gol inminente”.
- Había pruebas desactualizadas, una prueba de red que requería un fixture inexistente y scripts que se ejecutaban durante la recolección de tests.

## Plan aplicado

| Prioridad | Trabajo | Resultado |
|---|---|---|
| 1 | Unificar distribución de resultados | Nuevo núcleo Poisson analítico compartido por predicción en vivo y prepartido. Marcadores, 1X2, BTTS, totales e intervalos consistentes. |
| 1 | Corregir entradas y horizonte | Validación numérica, ausencia distinta de cero, exclusión de eventos futuros, tasas regularizadas y tiempo restante cero al final. |
| 1 | Evitar conclusiones engañosas | Presión etiquetada como índice; goles esperados diferenciados de xG por tiro; intervalo predictivo condicional; eliminación de datos sintéticos prepartido y proyecciones de jugadores sin respaldo. |
| 2 | Introducir modelo histórico evaluable | Ataque/defensa por sede, medias de liga, regularización de 10 partidos y ventana de 730 días. CLI utilizable sin navegador. |
| 2 | Evaluación retrospectiva | Corte diario estricto, log loss, Brier multiclase, acierto 1X2, marcador exacto, cobertura y calibración del favorito. Dos baselines. |
| 2 | Recuperar verificación automática | 24 pruebas locales, dependencias de desarrollo y workflow CI preparado. Sintaxis del JavaScript del dashboard comprobada. |
| 3 | Documentación y trazabilidad | Instrucciones de uso, fuentes de datos, hashes y resultados JSON guardados. |

El núcleo usa una cola Poisson truncada por debajo de 1e-12 y normalizada. La nube de puntos sigue siendo muestreada, pero los porcentajes publicados son analíticos. La última categoría del mapa de calor está identificada como “6+” o equivalente si el marcador actual requiere ampliarla.

El índice de presión ahora utiliza minutos del partido y deduplica snapshots del mismo minuto. Sigue siendo una heurística descriptiva, no una probabilidad calibrada.

El modelo en vivo usa priors genéricos de 1,5 y 1,2 goles por 90 minutos, 45 minutos de regularización y conversiones de 0,30 para tiros a puerta o 0,10 para tiros totales. Son supuestos explícitos, no parámetros entrenados. La interfaz suspende la predicción al llegar al horizonte si el partido no consta finalizado; MATCH_DURATION permite fijar un horizonte conocido de descuento.

## Evaluación real

Datos: Premier League 2023–24 y 2024–25, 760 partidos, entre 2023-08-11 y 2025-05-25. Fechas y goles válidos; el cargador rechaza duplicados. Fuente de descarga: datasets/football-datasets, que declara obtener los datos de Football-Data.co.uk. No se verificó cada resultado contra una segunda fuente.

Se usan los 380 partidos de 2023–24 como historia inicial y se pronostican los 380 de 2024–25. Para cada fecha se incorporan sólo resultados de días anteriores; nunca los de ese mismo día. Es evaluación con actualización cronológica, no entrenamiento congelado. No se ajustaron los hiperparámetros tras observar estos resultados.

| Método | Acierto 1X2 | Log loss ↓ | Brier ↓ |
|---|---:|---:|---:|
| Nuevo modelo por equipos y sede | 51,32% | 1,0042 | 0,6002 |
| Frecuencias históricas de la liga | 40,79% | 1,0823 | 0,6563 |
| Poisson con medias de liga | 40,79% | 1,0799 | 0,6548 |

El Brier publicado suma los errores cuadrados de las tres clases (rango 0–2). El log loss usa logaritmo natural.

Marcador exacto: 12,89%. Cobertura del intervalo predictivo nominal 95%: 98,95%, compatible con intervalos discretos conservadores; no mide incertidumbre de parámetros. En el grupo de favoritos pronosticados entre 60% y 70%, la probabilidad media fue 64,24% y la frecuencia observada 64,79% sobre 71 partidos. Los grupos superiores tienen pocos casos: 14 y 2. No hay base suficiente para garantizar aciertos altos.

No se comparó con cuotas de mercado. Superar dos baselines sencillos no equivale a superar a las casas de apuestas. Tampoco se calcularon intervalos de significación con dependencia entre partidos; los resultados son descriptivos.

## Uso reproducible

Carpeta: C:\Users\Visitante\Documents\Codex\football-live-agent-work

Desde esa carpeta, en PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe evaluar_modelo.py data\E0-2324.csv data\E0-2425.csv --desde 2024-08-01
.\.venv\Scripts\python.exe predecir_partido.py --csv data\E0-2324.csv data\E0-2425.csv --local Arsenal --visita Chelsea --fecha 2025-05-01
```

El último comando es un ejemplo retrospectivo: excluye resultados del 1 de mayo en adelante. Para pronósticos actuales hace falta actualizar los históricos. Usar siempre archivos de una sola competición y los nombres exactos del CSV. Equipos sin historia por sede regresan al promedio de liga; revisar los conteos de soporte.

En el selector existente se puede activar la fuente histórica con FOOTBALL_HISTORY (rutas separadas por punto y coma en Windows) y MATCH_DATE en formato YYYY-MM-DD. Sin esa configuración se utiliza H2H exploratorio y se abstiene si no verifica al menos cinco partidos. La nueva extracción H2H no se probó contra Flashscore en esta sesión.

Los datos descargados y las dependencias locales están ignorados por Git. La instalación normal en otra máquina es python -m venv .venv seguido de python -m pip install -r requirements-dev.txt dentro del entorno. Para scraping hace falta instalar Chromium de Playwright. En esta máquina se resolvió un problema de permisos de carpetas temporales usando dependencias aisladas en work/packages, enlazadas al entorno .venv; no se instalaron globalmente.

## Próxima fase: criterios antes de declarar precisión confiable

1. Evaluar sin reajustes sobre temporadas y ligas independientes. Si se comparan hiperparámetros, reservar validación y un test final distinto.
2. Recolectar snapshots reales con hora de captura, fuente, marcador, minuto y predicción; evaluar por separado minutos 15, 30, 45, 60 y 75 sin usar estadísticas finales como entrada.
3. Comparar Poisson independiente con Dixon-Coles ajustado, ponderación temporal y modelos que incorporen expulsiones y calidad de tiros; adoptar cambios sólo si mejoran fuera de muestra.
4. Normalizar identificadores de equipos/competiciones, revisar frescura de proveedores y añadir abstención por datos obsoletos o poco soporte.
5. Validar el flujo real del navegador y los extractores con sus fuentes actuales. La revisión completada es del código, cálculo, pipeline simulado y sintaxis JS, no una certificación del servicio vivo.

Estas tareas requieren más datos y pruebas prospectivas. No se presentan como ya completadas.

## Fuentes

- Dixon y Coles (1997), modelo de regresión Poisson y dinámica de equipos: https://www.research.lancs.ac.uk/portal/en/publications/modelling-association-football-scores-and-inefficiencies-in-the-football-betting-market(d16276a2-d6e0-483b-a708-1d29663f1992).html
- Wheatcroft, evaluación de pronósticos probabilísticos y comparación de reglas de puntuación: https://arxiv.org/abs/1908.08980
- Datos y procedencia declarada: https://github.com/datasets/football-datasets
- CSV 2023–24: https://github.com/datasets/football-datasets/blob/main/datasets/premier-league/season-2324.csv
- CSV 2024–25: https://github.com/datasets/football-datasets/blob/main/datasets/premier-league/season-2425.csv

Hashes SHA-256:
- E0-2324.csv: f64a0999d037a8d1bcd0346f5d7532bbc3d2ec3434f9a46591713ea23c47aa15
- E0-2425.csv: 386369240ce88153da2ba231c6f356003eca395845b754e216b46bed62f69076

