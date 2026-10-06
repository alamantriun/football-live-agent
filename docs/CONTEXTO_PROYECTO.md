# Contexto completo — Football Live Agent

**Actualizado:** 6 de octubre de 2026  
**Repositorio:** `alamantriun/football-live-agent`  
**Rama de trabajo:** `codex/football-live-product`  
**Último commit publicado:** `ed53898` (`feat: visualize live scenario markets`)  
**Producción:** <https://football-live-agent.vercel.app/>  
**Aplicación:** <https://football-live-agent.vercel.app/app>  
**Modelo y evidencia:** <https://football-live-agent.vercel.app/modelo>

Este documento sirve como punto único de traspaso para continuar el proyecto. Describe el producto, las decisiones tomadas, el estado de producción y lo pendiente. No contiene claves, tokens, URLs privadas ni datos de acceso.

## 1. Propósito del producto

Football Live Agent es una aplicación web pública para seguir partidos de fútbol y mostrar lecturas probabilísticas actualizadas a partir de datos en vivo. El producto busca ser comprensible para una persona que sigue un partido, pero no presenta las predicciones como garantía ni como recomendación de apuesta.

La experiencia pública se divide en tres páginas:

| Ruta | Función |
|---|---|
| `/` | Presentación comercial: qué mide la plataforma, cómo interpreta señales en vivo y qué límites tiene. |
| `/app` | Panel de partidos publicados, selección de encuentro, estadísticas en vivo, historial, escenarios y eventos. |
| `/modelo` | Estado del modelo, evidencia disponible y límites metodológicos. |

No se requiere cuenta para visitar el producto. El navegador sólo consume la API pública de lectura; no se conecta directamente con el proveedor deportivo ni con Supabase.

## 2. Qué muestra actualmente `/app`

Al seleccionar un partido publicado, el panel presenta:

- Partido, estado/minuto, marcador, competición y escudos de local y visitante.
- Selector en cuadrícula de partidos en seguimiento, con minuto o estado visible.
- Probabilidades 1X2: local, empate y visitante, redondeadas a dos decimales.
- Cuenta regresiva al lado de **Análisis en vivo**, que indica la próxima actualización programada.
- Matriz de marcadores posibles, etiquetada para distinguir local y visitante. La matriz se muestra primero dentro del análisis.
- Probabilidad de que ambos equipos marquen, con un visual circular accesible y situado inmediatamente debajo de la matriz.
- Historial de probabilidades 1X2, actividad y momentum descriptivo. Los valores faltantes se mantienen como faltantes; no se convierten en cero.
- Línea de tiempo con eventos verificables del proveedor: goles, tarjetas amarillas, tarjetas rojas y, cuando dos lecturas consecutivas permiten detectarlo, aumentos en el conteo de tiros. Un aumento de tiros se comunica como cambio detectado entre actualizaciones, no como un evento con minuto inventado.
- Escenarios de próximo gol, más/menos de 2,5 goles, distribución de goles y marcadores más probables.

### Diseño reciente de los escenarios

El commit actualmente en producción sustituyó las barras genéricas por cuatro visuales diferentes:

| Escenario | Visual |
|---|---|
| Próximo gol | Tres tarjetas con anillo de progreso y una lectura líder destacada. |
| Más/Menos 2,5 | Medidor dividido con el umbral 2.5 al centro. |
| Distribución de goles | Histograma vertical; el resultado más probable recibe énfasis visual. |
| Marcadores probables | Ranking con intensidad proporcional y prioridad visual para el primero. |

Los cambios visuales no modifican cálculos, probabilidades base ni contratos de la API. Los decimales que cambian en algunas vistas se identifican como capa decorativa; el valor publicado y leído por tecnologías asistivas permanece disponible.

## 3. Arquitectura técnica

```text
Proveedor deportivo (365Scores)
        │
        ▼
Trabajos privados: discover / collect / settle / train
        │  (autenticados e idempotentes)
        ▼
Supabase PostgreSQL
  - snapshots, partidos, predicciones, eventos, modelo, auditoría
        │
        ▼
FastAPI en Vercel (`api/index.py`)
  - API pública sanitizada de sólo lectura
        │
        ▼
Frontend estático (`public/`)
  - landing, app y página de modelo
```

### Backend

- `api/index.py`: entrada de Vercel que instancia FastAPI.
- `football_live/api.py`: rutas, validación, límites, manejo seguro de errores y autorización de trabajos internos.
- `football_live/provider.py`: adaptación al proveedor activo.
- `football_live/repository.py` y `football_live/supabase_gateway.py`: abstracción y acceso a Supabase.
- `football_live/prediction_service.py`: preparación de lecturas públicas y predicciones.
- `football_live/live_analytics.py`: analítica de las observaciones de un partido.
- `football_live/jobs.py`: ejecución de descubrimiento, recolección, liquidación y entrenamiento.
- `football_live/training.py`: evaluación cronológica y reglas de promoción de ajustes.

### Frontend

- `public/app/index.html`: estructura semántica del panel.
- `public/app/app.js`: consulta de API, selección de partido, actualización periódica, renderizado de visuales y controles de estados vacíos/frescos/obsoletos.
- `public/app/charts.js`: gráficas de evolución.
- `public/app/app.css`: apariencia responsiva, animaciones de actualización, efectos hover y preferencia de movimiento reducido.
- `public/index.html`: landing de producto.
- `public/modelo/`: página pública de modelo y evidencia.

### Base de datos

Las migraciones están en `supabase/migrations/`. Las principales crean el esquema de producto, snapshots/predicciones públicas, control de entrenamiento, programación de recolección, historial de partidos y eventos privados verificados.

Las operaciones que exponen información pública usan proyecciones explícitas. Los payloads crudos del proveedor, identificadores internos, secretos y funciones privadas no se entregan al navegador.

## 4. API pública y trabajos internos

### Lectura pública

| Método y ruta | Propósito |
|---|---|
| `GET /api/health` | Estado básico del servicio. |
| `GET /api/live` | Lista de partidos que pueden mostrarse públicamente. |
| `GET /api/matches/{public_id}` | Sobre, snapshots, escenarios, historial y eventos de un partido. |
| `GET /api/model/status` | Estado seguro del modelo activo y de la evidencia disponible. |

### Operación privada

| Ruta | Trabajo |
|---|---|
| `POST /api/jobs/discover` | Encuentra partidos aptos para seguimiento. |
| `POST /api/jobs/collect` | Recoge observaciones en vivo. |
| `POST /api/jobs/settle` | Concilia partidos terminados y resultados. |
| `POST /api/jobs/train` | Evalúa candidatos de ajuste y sólo promueve si cumplen las reglas. |

Cada trabajo interno exige `X-Cron-Secret` y `X-Idempotency-Key`. La base usa leases para impedir ejecuciones simultáneas incompatibles y conserva auditoría en `private.job_runs`.

## 5. Modelo y honestidad predictiva

### Lo que tiene fundamento

El núcleo de resultados utiliza una distribución Poisson consistente para 1X2, marcadores, totales, BTTS y próximo gol. El muestreo Monte Carlo se usa para visualización o simulación de la distribución, no para cambiar artificialmente la probabilidad publicada por una semilla aleatoria.

Existe una evaluación prepartido retrospectiva de Premier League 2024–25, alimentada con la temporada 2023–24 y actualizada de forma cronológica. El resultado histórico fue:

| Método | Acierto 1X2 | Log loss | Brier multiclase |
|---|---:|---:|---:|
| Modelo por equipos y sede | 51,32% | 1,0042 | 0,6002 |
| Frecuencias históricas | 40,79% | 1,0823 | 0,6563 |
| Poisson con medias de liga | 40,79% | 1,0799 | 0,6548 |

El marcador exacto acertó 12,89%. El detalle, fuentes, hashes y límites están en `docs/REVISION_Y_PLAN.md` y `docs/evaluacion_2024_25.json`.

### Lo que todavía no se puede afirmar

- La evaluación anterior es **prepartido** y de una sola liga/temporada de prueba; no valida predicción en vivo.
- No demuestra generalización a selecciones, otras ligas ni rentabilidad frente a cuotas de mercado.
- Actividad y momentum son índices descriptivos, no probabilidades calibradas de gol.
- Las tasas live conservan supuestos explícitos que deben calibrarse con snapshots reales acumulados.

Por esto, la interfaz debe conservar expresiones como **escenario experimental** y no prometer porcentaje de acierto en vivo hasta completar evaluación prospectiva reproducible.

## 6. Aprendizaje continuo y promoción de modelos

El proyecto está preparado para recolectar datos y refinar ajustes con controles, no para entrenar automáticamente sin evaluación.

1. `collect` guarda snapshots con marca temporal, minuto, marcador y métricas recibidas.
2. `settle` vincula predicciones con finales observados.
3. `train` forma una partición cronológica 70/30 y evalúa candidato, campeón y baseline.
4. Un candidato sólo se promueve si mejora log loss y Brier bajo las reglas de promoción.
5. Se persisten versión de modelo, hashes/IDs de validación, versión previa y el indicador `approved`.
6. El simulador sólo aplica factores con `approved=True`.

Los datos incompletos, eventos futuros, lecturas después del horizonte y valores nulos no deben convertirse en evidencia ficticia. En particular, un conteo de tiros a puerta igual a cero es una observación válida; `null` es ausencia de dato y no cero.

## 7. Datos en vivo y refresco

El proveedor activo es 365Scores, mediante el adaptador del proyecto. La aplicación se actualiza periódicamente y conserva la última lectura válida mientras llega la siguiente.

Reglas visibles importantes:

- Lecturas `fresh` y `degraded` pueden mostrar escenarios si contienen tasas válidas.
- Lecturas obsoletas o suspendidas ocultan escenarios que podrían ser engañosos, pero no inventan estadísticas para llenar huecos.
- El historial conserva huecos cuando falta una observación.
- La actualización de lista no puede sobrescribir la selección actual con una respuesta tardía; cada petición se aísla y se cancela de forma segura.
- El refresco manual reintenta el resumen correcto y el cambio de partido limpia la línea de tiempo anterior antes de mostrar la nueva.

## 8. Seguridad y secretos

Las credenciales deben permanecer únicamente en entornos server-side. No se deben poner en el repositorio, HTML, JavaScript público, capturas, issues, documentos ni logs.

Variables que deben existir en Vercel para Preview y Production:

```text
ENVIRONMENT
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
CRON_SECRET
ALLOWED_HOSTS
PUBLIC_ORIGIN
```

Principios aplicados:

- Ninguna variable privilegiada tiene prefijo público ni se envía al cliente.
- Las rutas de trabajos internos validan secreto con comparación segura e idempotencia.
- CSP, `nosniff` y `frame-ancestors 'none'` se mantienen en la configuración de despliegue.
- La política de Supabase separa tablas/funciones privadas de las proyecciones públicas.
- La rotación de secretos se realiza coordinadamente entre Vercel y Supabase Vault; nunca se pega el valor en una conversación o archivo versionado.

## 9. Producción y despliegue

La aplicación está en Vercel y la base está en Supabase. La última promoción se completó el 6 de octubre de 2026:

| Elemento | Estado |
|---|---|
| Fuente | Rama `codex/football-live-product`, commit `ed53898`. |
| Preview | Construcción correcta antes de promover. |
| Production | `Ready` en Vercel. |
| Dominio | `football-live-agent.vercel.app`. |
| Base de datos | Migraciones de producto, entrenamiento, predicciones, historial y eventos aplicadas. |

Flujo recomendado para un cambio futuro:

```powershell
python -m compileall football_live api
python -m pytest -q
node --test tests\e2e\public_flow_behavior.mjs
git diff --check
git add <archivos>
git commit -m "tipo: resumen"
git push origin codex/football-live-product
```

Después de que Vercel cree una Preview `Ready`, ejecutar:

```powershell
python scripts\smoke_preview.py --base-url https://URL-DE-LA-PREVIEW
```

Luego promover sólo esa Preview validada a Production y comprobar el estado terminal `Ready`. Para revertir una web defectuosa, promover el despliegue Vercel anterior; no ejecutar rollbacks destructivos de la base.

## 10. Pruebas y calidad actual

La publicación `ed53898` se verificó antes de subirla con:

```text
249 pruebas Python aprobadas
27 pruebas de comportamiento web aprobadas
```

La advertencia conocida procede de la deprecación de `httpx` con `starlette.testclient`; no es un fallo de las pruebas actuales, pero conviene planificar su actualización.

Cobertura relevante:

- Contratos de API, configuración, seguridad, repositorio y migraciones.
- Recolección, snapshots, escenarios y promoción controlada de ajustes.
- Estados de datos frescos, degradados, obsoletos, nulos y suspendidos.
- Selección de partido, respuestas tardías, abortos, timeouts y reintentos.
- Actualización de minuto, marcador, 1X2, cuenta regresiva y preservación de análisis legible.
- Matriz, BTTS, línea de tiempo, iconos de eventos, tiros detectados y escudos con fallback seguro.
- Nuevos visuales de escenarios y preservación de los valores publicados.

## 11. Mapa rápido de archivos

| Necesidad | Archivo o carpeta |
|---|---|
| API y autorización | `football_live/api.py` |
| Servicio de predicción | `football_live/prediction_service.py` |
| Distribución Poisson | `modelo_poisson.py` |
| Simulación/tasas live | `simulador.py` |
| Modelo histórico | `modelo_historico.py` |
| Evaluación cronológica | `evaluar_modelo.py` |
| Entrenamiento controlado | `football_live/training.py`, `aprendizaje.py` |
| Trabajos live | `football_live/jobs.py`, `recolector_aprendizaje.py` |
| Esquema Supabase | `supabase/migrations/` |
| Panel web | `public/app/` |
| Landing | `public/index.html` |
| Página de evidencia | `public/modelo/` |
| E2E de interfaz | `tests/e2e/public_flow_behavior.mjs` |
| Operación y reversión | `docs/operations.md` |
| Diseño de producto | `docs/superpowers/specs/2026-09-24-football-product-supabase-vercel-design.md` |

## 12. Siguientes prioridades recomendadas

1. **Acumular evidencia live real.** Mantener trabajos programados, verificar sus ejecuciones sin exponer payloads y confirmar que snapshots/finales se están guardando de forma continua.
2. **Evaluar por minuto.** Medir log loss, Brier, calibración y cobertura para franjas 15, 30, 45, 60 y 75, siempre con datos disponibles antes de ese instante.
3. **Comparar modelos con guardas temporales.** Probar Dixon-Coles, ponderación temporal, expulsiones y calidad de tiro sólo contra un holdout sin tocar.
4. **Mejorar cobertura de proveedor.** Normalizar clubes, selecciones y competiciones; manejar logos y datos ausentes de forma consistente.
5. **Observar producción.** Activar o revisar observabilidad, errores y tiempos de respuesta sin registrar secretos ni datos brutos innecesarios.
6. **Prueba visual móvil manual.** El CSS es responsivo y tiene cobertura automatizada, pero se recomienda verificar la interfaz con un viewport móvil real antes de una campaña pública.

## 13. Regla principal para futuras decisiones

Una interfaz atractiva no convierte un dato incierto en una predicción validada. Cualquier mejora de modelo debe pasar evaluación cronológica, compararse con baselines y respetar los mecanismos de abstención/frescura antes de ser promovida.

