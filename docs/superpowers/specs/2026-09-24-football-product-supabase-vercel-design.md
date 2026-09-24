# Football Live AI: diseño de producto, aprendizaje y despliegue

Fecha: 2026-09-24

Estado: aprobado en conversación; pendiente de revisión final del documento

## 1. Objetivo

Convertir `football-live-agent` en un producto web público que explique y muestre predicciones probabilísticas mientras avanza un partido. La aplicación debe recolectar observaciones reales, conservar predicciones y resultados, evaluar candidatos cronológicamente y mejorar parámetros de forma controlada. Vercel alojará la web y las funciones; Supabase será la fuente de estado durable.

El producto no prometerá resultados ni beneficios de apuestas. Diferenciará siempre entre una estimación probabilística, una validación prepartido y evidencia live todavía insuficiente.

## 2. Alcance

Incluido:

- Portada pública de presentación sin cuenta.
- Dashboard de partidos y predicciones en vivo.
- Adaptador para el proveedor actual de datos, 365Scores.
- Persistencia de partidos, observaciones, predicciones, resultados y versiones del modelo en Supabase.
- Recolección periódica y trabajos de entrenamiento programados.
- Promoción automática y reversible de parámetros sólo cuando superen los criterios aprobados.
- Seguridad de secretos, RLS, cabeceras web, validación de entradas y rutas internas protegidas.
- Despliegue Preview en Vercel, verificación y posterior promoción a producción.

No incluido en esta fase:

- Registro, inicio de sesión o perfiles de usuario.
- Pagos, suscripciones o recomendaciones personalizadas.
- Promesas de precisión live sin una muestra continua suficiente.
- Reescritura automática de código por parte del modelo.
- Dependencia contractual de un proveedor no oficial; el adaptador debe permitir reemplazarlo.

## 3. Experiencia de producto aprobada

### 3.1 Rutas

- `/`: portada del producto.
- `/app`: explorador y dashboard de partidos.
- `/modelo`: estado del modelo, metodología, muestra y métricas de validación.

### 3.2 Portada

La primera sección contiene únicamente el mensaje principal, llamados a la acción y una composición de jugadores anónimos con uniformes blancos. La predicción Arsenal-Chelsea del prototipo se mueve a una segunda sección independiente titulada “La lectura cambia con cada minuto del partido”.

La sección de predicción debe explicar:

- Probabilidad de victoria local, empate y victoria visitante.
- Probabilidad del próximo equipo en marcar.
- Probabilidad de gol en los próximos diez minutos.
- Más/menos goles y ambos equipos anotan.
- Presión o ritmo reciente, frescura de datos y versión del modelo.

La interfaz usará una composición oscura editorial, verde lima para énfasis, `Barlow Condensed` para titulares y `Barlow` para cuerpos. Se evitarán microtextos y exceso de tarjetas: cuerpo principal de 17–19 px, auxiliares de al menos 14 px, espacios amplios y pocos contenedores. Los hovers comunicarán interactividad; `prefers-reduced-motion` desactivará movimiento no esencial.

La imagen generada de jugadores se integrará como PNG local y optimizado. Los escudos reales procederán de los datos del proveedor con fallback a iniciales; no se dependerá de hotlinks de Wikipedia en producción.

### 3.3 Transparencia

Cada lectura pública mostrará:

- Hora de actualización.
- Estado `vigente`, `degradado`, `desactualizado` o `suspendido`.
- Versión del modelo.
- Explicación breve de las señales usadas.
- Aviso claro de que una probabilidad no garantiza un resultado.

## 4. Arquitectura

### 4.1 Componentes

1. Vercel sirve HTML, CSS, JavaScript y activos estáticos.
2. Funciones Python stateless exponen la API pública y los trabajos internos.
3. Un adaptador de proveedor encapsula 365Scores y normaliza su respuesta.
4. El motor existente de Poisson produce una distribución única para 1X2, marcador, goles y próximo gol.
5. Supabase Postgres conserva todo el estado durable.
6. Supabase Cron invoca rutas internas protegidas para recolección, cierre de partidos y entrenamiento.

El diseño no conservará procesos, hilos o SQLite como fuente de verdad dentro de Vercel. Las funciones serverless son efímeras; leases, caché durable, progreso y versiones viven en Postgres.

### 4.2 Flujo principal

1. El trabajo de descubrimiento obtiene partidos disponibles del proveedor.
2. Los partidos se normalizan y se insertan o actualizan de forma idempotente.
3. El trabajo live captura como máximo una observación por minuto y partido.
4. El motor genera una predicción asociada al snapshot y a una versión inmutable del modelo.
5. La API pública consulta únicamente el último snapshot válido y sus predicciones almacenadas.
6. Al finalizar el encuentro, un trabajo registra el resultado oficial.
7. El entrenador crea un candidato con partidos finalizados y lo evalúa antes de cualquier promoción.

## 5. Modelo de datos

### 5.1 Esquema privado

`private.fixtures`

- `id uuid primary key`
- `provider`, `provider_fixture_id` con índice único compuesto
- liga, equipos, logos, inicio programado, estado y marcador final
- `discovered_at`, `updated_at`, `finished_at`

`private.live_snapshots`

- `id uuid primary key`
- `fixture_id` referencia a fixtures
- minuto, marcador y estadísticas normalizadas
- `provider_observed_at`, `collected_at`, calidad y datos crudos sanitizados
- índice único por fixture y minuto/instante normalizado

`private.predictions`

- `id uuid primary key`
- `fixture_id`, `snapshot_id`, `model_version_id`
- lambdas base y ajustadas
- probabilidades 1X2, mercados derivados, próximo gol y ventana de diez minutos
- `created_at`, estado de calidad y explicación estructurada
- índice único por snapshot y versión del modelo

`private.outcomes`

- resultado final confirmado y hora de confirmación
- indicador de fuente y revisión

`private.model_versions`

- versión, estado `candidate|active|rejected|retired`
- parámetros/calibración JSON, hash, versión de código y modelo anterior
- tamaños de train/validación, Brier, log loss y fecha de activación

`private.training_runs`

- rango temporal, IDs consumidos, métricas, decisión, motivo y errores sanitizados

`private.job_runs`

- trabajo, clave idempotente, lease, inicio, fin, estado, contadores y request ID

### 5.2 Superficie pública

- `public.live_matches`: proyección mínima de partidos, frescura y última predicción.
- `public.model_status`: versión activa, muestra, métricas permitidas y última ejecución.

Las vistas usarán `security_invoker = true`. RLS estará activa en todo objeto expuesto y `anon` tendrá únicamente `SELECT` sobre las proyecciones aprobadas. El navegador consumirá preferentemente la API del mismo origen; nunca recibirá `service_role`.

## 6. Contrato de API

### 6.1 Lectura pública

- `GET /api/live`: partidos live y recientes, con paginación limitada.
- `GET /api/matches/{public_id}`: snapshots, predicción actual y evolución resumida.
- `GET /api/model/status`: estado público del modelo.
- `GET /api/health`: disponibilidad básica sin secretos ni detalles internos.

Las respuestas incluyen `request_id`, `generated_at`, `data_status` y `model_version`. Se usarán UUID públicos y límites explícitos de tamaño.

### 6.2 Trabajos internos

- `POST /api/jobs/discover`
- `POST /api/jobs/collect`
- `POST /api/jobs/settle`
- `POST /api/jobs/train`

Estas rutas sólo aceptan solicitudes con secreto de Cron validado en tiempo constante. Cada ejecución exige una clave idempotente y obtiene un lease en Postgres. Una repetición devuelve el resultado previo o continúa de forma segura.

### 6.3 Errores

Formato:

```json
{
  "ok": false,
  "error": {
    "code": "provider_unavailable",
    "message": "Los datos en vivo no están disponibles temporalmente.",
    "request_id": "uuid"
  }
}
```

No se exponen stack traces, SQL, URLs internas, claves ni respuestas crudas del proveedor.

## 7. Recolección y aprendizaje

### 7.1 Recolección

- Descubrimiento de partidos cada dos minutos.
- Captura de partidos activos cada minuto, en lotes acotados.
- Reconciliación de resultados cada cinco minutos.
- Backoff con jitter ante `429`, timeouts o errores transitorios.
- No guardar observaciones posteriores al final ni fabricar estadísticas faltantes como cero.
- Un adaptador único transforma la respuesta del proveedor al esquema canónico.

### 7.2 Entrenamiento controlado

- El aprendizaje modifica parámetros y calibración; no modifica código.
- Mínimo 70 observaciones/partidos para entrenamiento y 30 posteriores para validación.
- División estrictamente cronológica; ninguna observación futura entra al entrenamiento.
- El candidato sólo se activa si mejora simultáneamente Brier score y log loss frente al modelo activo.
- Toda predicción conserva la versión que la produjo.
- La activación es transaccional y registra modelo anterior para rollback.
- Si faltan datos, falla una métrica o el candidato no supera ambos criterios, queda rechazado y el activo no cambia.

La evidencia histórica prepartido se mostrará como antecedente, no como prueba de rendimiento live. El producto no publicará una afirmación de precisión live hasta completar una muestra continua y auditada.

## 8. Seguridad

### 8.1 Secretos

- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `CRON_SECRET` y cualquier credencial del proveedor viven sólo en entornos server-side.
- No se usarán prefijos públicos para claves privilegiadas.
- Se incluirá `.env.example` con marcadores, nunca valores reales.
- Supabase Cron obtendrá el secreto mediante Vault.
- Los logs eliminarán cabeceras de autorización, claves, cookies y cuerpos crudos sensibles.

### 8.2 Base de datos

- Separación entre esquema privado y proyecciones públicas.
- RLS habilitada y grants mínimos.
- Sin funciones `SECURITY DEFINER` en `public`.
- Si una función privilegiada fuera imprescindible, vivirá en esquema privado, revocará `EXECUTE` de `PUBLIC` y validará explícitamente al invocador.
- Índices para fixture, instante, versión y estados de trabajo.
- Advisors de Supabase ejecutados antes de cerrar la migración.

### 8.3 Web y API

- CSP por cabecera, sin `unsafe-eval` y sin handlers inline.
- `frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, política de referrer y permissions policy mínima.
- Datos del proveedor se insertan con `textContent` y creación explícita de nodos; no mediante HTML sin sanitizar.
- CORS limitado al origen del producto.
- Validación allowlist de rutas, IDs, filtros y URLs de imágenes.
- Caché corta para lecturas públicas, paginación y límites de respuesta.
- Protección de rutas internas con secreto independiente y comparación constante.
- Dependencias fijadas y lockfiles/versiones reproducibles.

## 9. Comportamiento degradado

- Proveedor caído: conservar último snapshot, mostrar antigüedad y no inventar una actualización.
- Snapshot incompleto: marcar `degradado` y excluir señales ausentes del ajuste.
- Snapshot demasiado antiguo: marcar `suspendido` y ocultar recomendaciones derivadas.
- Supabase no disponible: devolver `503` con request ID; la UI mantiene el último estado local sólo como desactualizado.
- Entrenamiento fallido: registrar ejecución, conservar modelo activo y reintentar en la próxima ventana.
- Candidato peor: rechazar con métricas y motivo.
- Resultado en disputa: no usarlo para aprendizaje hasta confirmación.

## 10. Pruebas

### 10.1 Modelo

- Suma y límites de probabilidades.
- Consistencia entre 1X2, matriz de marcador y mercados derivados.
- Tratamiento de minuto, marcador, cero tiros y datos ausentes.
- División cronológica, ausencia de leakage y gate doble Brier/log loss.
- Rollback y carga exclusiva de modelos `active`/`approved`.

### 10.2 API y datos

- Contratos JSON y códigos de estado.
- Adaptador 365Scores con fixtures grabados, timeouts y respuestas parciales.
- Idempotencia y leases de trabajos.
- Restricciones, índices, RLS y grants con pruebas SQL.
- Verificación de que `anon` no puede escribir ni leer tablas privadas.

### 10.3 Interfaz

- Flujo portada → `/app` → partido → detalle del modelo.
- Estados vigente, degradado, vacío, error y suspendido.
- Escritorio y móvil sin scroll horizontal.
- Navegación por teclado, foco visible, contraste y reduced motion.
- Hovers como mejora visual, nunca como único medio de información.

### 10.4 Seguridad y despliegue

- Búsqueda de secretos y sinks peligrosos antes de commit.
- Cabeceras CSP y seguridad verificadas en Preview.
- Smoke tests contra API y páginas desplegadas.
- Confirmación de que Cron no se activa antes de que migración y Preview estén verdes.

## 11. Despliegue y rollback

1. Crear proyecto Supabase y aplicar migraciones versionadas.
2. Configurar secretos sin pegarlos en chat ni almacenarlos en el repositorio.
3. Desplegar Preview en Vercel.
4. Ejecutar smoke tests, pruebas responsive, RLS y un trabajo manual idempotente.
5. Promover a producción sólo después de la verificación.
6. Activar Cron y observar las primeras ejecuciones.
7. Mantener rollback de despliegue, migraciones aditivas y modelo anterior.

Si falla la versión web, se revierte el deployment. Si falla un candidato, se reactiva el modelo anterior sin redeploy. Las migraciones destructivas quedan fuera de esta fase.

## 12. Criterios de aceptación

- La portada aprobada aparece primero y conduce al dashboard.
- La tarjeta de predicción es una sección separada bajo el hero.
- Todos los partidos mostrados provienen del proveedor o están marcados explícitamente como demo.
- El navegador no contiene secretos ni llama directamente al proveedor.
- Las predicciones y snapshots sobreviven reinicios y despliegues.
- Los datos desactualizados se señalan y pueden suspender la predicción.
- Un candidato no se promueve sin 70 train, 30 validación y mejora simultánea de Brier y log loss.
- Las vistas públicas son sólo lectura y las tablas privadas no son accesibles por `anon`.
- Pruebas unitarias, integración, RLS y flujo web pasan en Preview.
- Vercel termina en estado Ready antes de anunciar el sitio como desplegado.

## 13. Riesgos abiertos

- El endpoint actual de 365Scores puede cambiar o imponer límites; el adaptador y el estado degradado reducen, pero no eliminan, este riesgo.
- Los escudos y datos pueden tener condiciones de uso propias; antes de comercializar se debe revisar la licencia del proveedor.
- El rendimiento live sólo podrá estimarse de forma responsable después de recolectar suficientes partidos continuos y sin sesgo de selección.
- La cadencia por minuto debe ajustarse a límites reales de Vercel, Supabase y proveedor tras observar consumo y latencia.

