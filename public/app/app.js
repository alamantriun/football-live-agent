import { drawLineChart, clearChart } from "./charts.js";

const list = document.querySelector("#match-list");
const detail = document.querySelector("#match-detail");
const status = document.querySelector("#data-status");
const refresh = document.querySelector("#refresh");
const favoritesKey = "football-live-favorites";
const states = ["loading", "empty", "fresh", "degraded", "stale", "suspended", "error"];
const allowedLogoHosts = new Set(["imagecache.365scores.com", "img.365scores.com"]);
const featuredClubLogos = new Map([
  ["arsenal", "https://imagecache.365scores.com/image/upload/f_png,w_160,h_160,c_limit,q_auto:eco,dpr_2,d_Competitors:default1.png/v20/Competitors/104"],
  ["arsenal fc", "https://imagecache.365scores.com/image/upload/f_png,w_160,h_160,c_limit,q_auto:eco,dpr_2,d_Competitors:default1.png/v20/Competitors/104"],
  ["chelsea", "https://imagecache.365scores.com/image/upload/f_png,w_160,h_160,c_limit,q_auto:eco,dpr_2,d_Competitors:default1.png/v7/Competitors/106"],
  ["chelsea fc", "https://imagecache.365scores.com/image/upload/f_png,w_160,h_160,c_limit,q_auto:eco,dpr_2,d_Competitors:default1.png/v7/Competitors/106"],
  ["panamá", "https://imagecache.365scores.com/image/upload/f_png,w_160,h_160,c_limit,q_auto:eco,dpr_2,d_Competitors:default1.png/Competitors/5414"],
  ["panama", "https://imagecache.365scores.com/image/upload/f_png,w_160,h_160,c_limit,q_auto:eco,dpr_2,d_Competitors:default1.png/Competitors/5414"],
  ["nueva zelanda", "https://imagecache.365scores.com/image/upload/f_png,w_160,h_160,c_limit,q_auto:eco,dpr_2,d_Competitors:default1.png/Competitors/2391"],
  ["new zealand", "https://imagecache.365scores.com/image/upload/f_png,w_160,h_160,c_limit,q_auto:eco,dpr_2,d_Competitors:default1.png/Competitors/2391"],
]);
const publicIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
let liveItems = [];
let selectedId = null;
let listController = null;
let detailController = null;
let historyController = null;
let selectionVersion = 0;
let loadedSummary = null;
let chartState = null;
let detailSnapshot = null;
let statsSnapshot = null;
let matrixSnapshot = null;
let resizeTimer = null;
let refreshTimer = null;
const refreshIntervalSeconds = 20;
let countdownSeconds = refreshIntervalSeconds;
let countdownTimer = null;
const analyticsArea = document.querySelector(".analytics");
const analyticsStatus = document.querySelector("#analytics-status");
const analyticsRetry = document.querySelector("#analytics-retry");
const probabilityCanvas = document.querySelector("#probability-history");
const activityCanvas = document.querySelector("#activity-chart");
const teamStats = document.querySelector("#team-stats");
const scenarioMarkets = document.querySelector("#scenario-markets");
const nextGoal = document.querySelector("#next-goal");
const marketProbabilities = document.querySelector("#market-probabilities");
const goalsDistribution = document.querySelector("#goals-distribution");
const scorelines = document.querySelector("#scorelines");
const scoreMatrix = document.querySelector("#score-matrix");
const evidence = document.querySelector("#analytics-evidence");
const refreshCountdown = document.querySelector("#refresh-countdown");
const refreshCountdownRing = document.querySelector("#refresh-countdown-ring");
const refreshCountdownValue = document.querySelector("#refresh-countdown-value");
const refreshCountdownLabel = document.querySelector("#refresh-countdown-label");
const bttsVisual = document.querySelector("#btts-visual");
const bttsRing = document.querySelector("#btts-ring");
const bttsRingValue = document.querySelector("#btts-ring-value");
const bttsYes = document.querySelector("#btts-yes");
const bttsNo = document.querySelector("#btts-no");

function setStatus(message, state) {
  status.textContent = message;
  status.dataset.state = states.includes(state) ? state : "error";
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function initials(name) {
  return String(name || "?").trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "?";
}

function safeLogo(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && allowedLogoHosts.has(parsed.hostname) ? parsed.href : null;
  } catch { return null; }
}

function normalizedMatchStatus(item) {
  return String(item.status || "").trim().toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
}

function matchVisualState(item) {
  const status = normalizedMatchStatus(item);
  if (/(half|descanso|medio tiempo|intervalo)/.test(status)) return "halftime";
  if (/(finished|final|full time|ended|finalizado)/.test(status)) return "finished";
  if (/(scheduled|not started|upcoming|proximo|programado)/.test(status)) return "scheduled";
  const minute = Number(item.minute);
  if (status === "live" || (Number.isFinite(minute) && item.minute !== null && item.minute !== "")) return "live";
  return "unknown";
}

function matchStateLabel(item) {
  return { live: "EN VIVO", halftime: "DESCANSO", finished: "FINALIZADO", scheduled: "PRÓXIMO", unknown: "SEÑAL" }[matchVisualState(item)];
}

function liveBadge(item) {
  const state = matchVisualState(item);
  const badge = element("span", `live-badge live-badge-${state}`);
  badge.append(element("i", "live-dot"), element("span", "", matchStateLabel(item)));
  return badge;
}

function matchTimeLabel(item) {
  const status = normalizedMatchStatus(item);
  if (/(half|descanso|medio tiempo|intervalo)/.test(status)) return "Descanso";
  if (/(finished|final|full time|ended|finalizado)/.test(status)) return "Finalizado";
  if (/(scheduled|not started|upcoming|proximo|programado)/.test(status)) return "Próximo";
  const minute = Number(item.minute);
  if (Number.isFinite(minute) && item.minute !== null && item.minute !== "") return `${minute}'`;
  if (status) return "En vivo";
  return "Minuto no disponible";
}

function clubMark(name, logoUrl) {
  const mark = element("span", "club-mark", initials(name));
  const featured = featuredClubLogos.get(String(name || "").trim().toLowerCase());
  const source = safeLogo(logoUrl) || safeLogo(featured);
  if (!source) return mark;
  const image = document.createElement("img");
  image.src = source;
  image.alt = "";
  image.loading = "lazy";
  image.width = 48;
  image.height = 48;
  image.addEventListener("error", () => { image.remove(); mark.textContent = initials(name); });
  mark.textContent = "";
  mark.append(image);
  return mark;
}

async function fetchWithTimeout(path, requestController) {
  let timeout;
  let onAbort;
  try {
    return await Promise.race([
      fetch(path, { signal: requestController.signal, headers: { Accept: "application/json" } })
        .then((response) => response.ok ? response.json() : Promise.reject(new Error("request failed"))),
      new Promise((_, reject) => {
        timeout = window.setTimeout(() => {
          reject(new Error("timeout"));
          requestController.abort();
        }, 8000);
      }),
      new Promise((_, reject) => {
        onAbort = () => reject(new DOMException("Aborted", "AbortError"));
        requestController.signal.addEventListener("abort", onAbort, { once: true });
        if (requestController.signal.aborted) onAbort();
      }),
    ]);
  } finally {
    window.clearTimeout(timeout);
    requestController.signal.removeEventListener("abort", onAbort);
  }
}

function loadFavorites() {
  try {
    const stored = JSON.parse(localStorage.getItem(favoritesKey) || "[]");
    return Array.isArray(stored) ? stored.filter((id) => typeof id === "string" && publicIdPattern.test(id)).slice(0, 20) : [];
  } catch { return []; }
}

function renderMatchList(items) {
  list.replaceChildren();
  if (!items.length) { list.append(element("p", "muted", "No hay partidos publicados en este momento.")); return; }
  const favorites = new Set(loadFavorites());
  [...items].sort((a, b) => Number(favorites.has(b.public_id)) - Number(favorites.has(a.public_id))).forEach((item) => {
    const visualState = matchVisualState(item);
    const button = element("button", `match-button match-${visualState}`);
    button.type = "button";
    button.dataset.liveState = visualState;
    button.setAttribute("aria-pressed", String(item.public_id === selectedId));
    const timeLabel = matchTimeLabel(item);
    button.setAttribute("aria-label", `${item.home_name} contra ${item.away_name}, ${timeLabel}, marcador ${item.score_home ?? "—"} a ${item.score_away ?? "—"}`);
    const copy = element("span", "match-copy");
    const clubs = element("span", "match-clubs");
    const home = element("span", "match-list-club");
    home.append(clubMark(item.home_name, item.home_logo_url), element("span", "", item.home_name));
    const away = element("span", "match-list-club away");
    away.append(clubMark(item.away_name, item.away_logo_url), element("span", "", item.away_name));
    clubs.append(home, element("span", "versus", "vs"), away);
    const matchMeta = element("span", "match-meta");
    matchMeta.append(element("small", `match-time match-time-${visualState}`, timeLabel), element("small", "", item.competition || "Competición no disponible"));
    copy.append(clubs, matchMeta);
    button.append(copy, element("span", "match-list-score", `${item.score_home ?? "—"} · ${item.score_away ?? "—"}`));
    button.addEventListener("click", () => loadMatch(item.public_id));
    list.append(button);
  });
}

function probabilityCard(label, value, changed = false) {
  const row = element("div", changed ? "probability value-changed" : "probability");
  const available = Number.isFinite(value);
  row.append(element("span", "", label), element("strong", "", available ? `${Number(value).toFixed(2)}%` : "Dato no disponible"));
  if (available) { const bar = element("i"); bar.style.setProperty("--percent", `${Math.max(0, Math.min(100, Number(value)))}%`); row.append(bar); }
  return row;
}

function renderDetail(item) {
  detail.setAttribute("aria-busy", "false");
  const visualState = matchVisualState(item);
  const nextDetailSnapshot = {
    id: item.public_id,
    scoreHome: item.score_home,
    scoreAway: item.score_away,
    minute: item.minute,
    probabilities: { ...(item.probabilities || {}) },
  };
  const previousDetail = detailSnapshot?.id === nextDetailSnapshot.id ? detailSnapshot : null;
  const detailChanged = {
    score: Boolean(previousDetail && (previousDetail.scoreHome !== nextDetailSnapshot.scoreHome || previousDetail.scoreAway !== nextDetailSnapshot.scoreAway)),
    minute: Boolean(previousDetail && previousDetail.minute !== nextDetailSnapshot.minute),
    probabilities: Object.fromEntries(["home", "draw", "away"].map(key => [key, Boolean(previousDetail && previousDetail.probabilities[key] !== nextDetailSnapshot.probabilities[key])])),
  };
  detailSnapshot = nextDetailSnapshot;
  detail.dataset.liveState = visualState;
  analyticsArea.dataset.liveState = visualState;
  detail.replaceChildren();
  const top = element("div", "detail-topline");
  top.append(element("span", "", item.competition || "Lectura en vivo"), liveBadge(item), element("span", `quality ${item.data_status}`, item.data_status === "degraded" ? "Datos parciales" : `Datos ${item.data_status}`));
  const scoreline = element("div", "scoreline");
  const home = element("div", "club"); home.append(clubMark(item.home_name, item.home_logo_url), element("span", "", item.home_name));
  const scoreClass = detailChanged.score || detailChanged.minute ? `score score-${visualState} value-changed` : `score score-${visualState}`;
  const score = element("div", scoreClass, `${item.score_home ?? "—"}—${item.score_away ?? "—"}`); score.append(element("small", "", item.minute == null ? "Minuto no disponible" : `${item.minute}'`));
  const away = element("div", "club away"); away.append(element("span", "", item.away_name), clubMark(item.away_name, item.away_logo_url));
  scoreline.append(home, score, away);
  const grid = element("div", "detail-grid");
  const probabilities = element("section", "surface"); probabilities.append(element("h3", "", "Probabilidades 1X2"));
  const values = item.probabilities || {};
  probabilities.append(probabilityCard(item.home_name, values.home, detailChanged.probabilities.home), probabilityCard("Empate", values.draw, detailChanged.probabilities.draw), probabilityCard(item.away_name, values.away, detailChanged.probabilities.away));
  const context = element("section", "surface"); context.append(element("h3", "", "Contexto de la lectura"));
  const warnings = Array.isArray(item.explanation?.warnings) ? item.explanation.warnings : [];
  context.append(element("p", "signal-summary", warnings[0] || "Dato no disponible: la explicación aún no fue publicada."));
  const timeline = element("ul", "timeline");
  timeline.append(element("li", "", `Calidad: ${item.data_status || "Dato no disponible"}`), element("li", "", `Modelo: ${item.model_version || "Dato no disponible"}`), element("li", "", `Observado: ${item.provider_observed_at || "Dato no disponible"}`));
  context.append(timeline); grid.append(probabilities, context); detail.append(top, scoreline, grid);
}

async function loadMatch(id, options = {}) {
  if (!publicIdPattern.test(id)) return;
  const preserveAnalysis = options.preserveAnalysis === true && selectedId === id;
  // Invalidate history before waiting for the newly selected summary.
  if (historyController) historyController.abort();
  historyController = null;
  if (detailController) detailController.abort();
  const requestController = new AbortController();
  detailController = requestController;
  const version = ++selectionVersion;
  selectedId = id;
  if (!preserveAnalysis) {
    detailSnapshot = null;
    statsSnapshot = null;
    matrixSnapshot = null;
    resetAnalytics();
  }
  renderAnalyticsState("loading", "Cargando historial del partido…");
  renderMatchList(liveItems);
  detail.setAttribute("aria-busy", "true"); setStatus("Actualizando la lectura del partido…", "loading");
  try {
    const response = await fetchWithTimeout("/api/matches/" + encodeURIComponent(id), requestController);
    if (version !== selectionVersion || requestController.signal.aborted) return;
    if (response.item?.public_id !== id) throw new Error("mismatched match");
    const item = { ...response.item, data_status: response.data_status ?? response.item.data_status, model_version: response.model_version ?? response.item.model_version };
    renderDetail(item);
    loadedSummary = { id, version, item };
    setStatus(item.data_status === "degraded" ? "Datos parciales: la lectura conserva señales ausentes." : `Lectura actualizada · ${matchStateLabel(item)}.`, item.data_status);
    detailController = null;
    await loadHistory(id, version);
  } catch (error) {
    if (version !== selectionVersion || (error.name === "AbortError")) return;
    // The old summary/history remain historical readings, not evidence that
    // the selected match is still fresh after its current summary failed.
    if (loadedSummary) renderDetail({ ...loadedSummary.item, data_status: "error" });
    if (chartState?.item.public_id === id) renderAnalytics(chartState.item, null, chartState.modelVersion);
    detail.setAttribute("aria-busy", "false");
    setStatus("No pudimos actualizar el partido. Puedes intentarlo de nuevo.", "error");
    renderAnalyticsState("error", "No pudimos actualizar la lectura: el estado actual no está confirmado y los escenarios están ocultos. Puedes reintentar; el historial disponible conserva su calidad histórica.");
  } finally { if (detailController === requestController) detailController = null; }
}

async function loadLive() {
  if (listController) listController.abort();
  const requestController = new AbortController();
  listController = requestController;
  refresh.dataset.refreshing = "true";
  if (!selectedId) setStatus("Cargando partidos disponibles…", "loading");
  try {
    const response = await fetchWithTimeout("/api/live?limit=24", requestController);
    if (listController !== requestController || requestController.signal.aborted) return;
    liveItems = Array.isArray(response.items) ? response.items.filter((item) => publicIdPattern.test(item.public_id || "")) : [];
    renderMatchList(liveItems);
    if (!selectedId) {
      if (!liveItems.length) setStatus("No hay partidos publicados en este momento.", "empty");
      else setStatus("Partidos actualizados. Selecciona uno para leer su contexto.", response.data_status || "fresh");
    }
  } catch (error) {
    if (listController !== requestController || error.name === "AbortError") return;
    list.replaceChildren(element("p", "muted", "No pudimos cargar los partidos."));
    if (!selectedId) setStatus("Servicio no disponible temporalmente.", "error");
  } finally { if (listController === requestController) { listController = null; refresh.dataset.refreshing = "false"; } }
}

function renderAnalyticsState(state, message) {
  analyticsArea.hidden = false;
  analyticsStatus.hidden = false;
  analyticsStatus.dataset.state = state;
  analyticsStatus.textContent = message;
  analyticsArea.setAttribute("aria-busy", String(state === "loading"));
  analyticsRetry.hidden = state !== "error";
}

function resetAnalytics() {
  chartState = null;
  window.clearTimeout(resizeTimer);
  for (const canvas of [probabilityCanvas, activityCanvas]) {
    clearChart(canvas); canvas.hidden = true; canvas.parentElement.hidden = true;
  }
  teamStats.replaceChildren(); teamStats.hidden = true; teamStats.parentElement.hidden = true;
  scenarioMarkets.hidden = true;
  bttsVisual.hidden = true;
  bttsVisual.setAttribute("aria-label", "Ambos equipos marcan: dato no disponible.");
  bttsRing.style.setProperty("--yes", "0");
  bttsRingValue.textContent = "—";
  bttsYes.textContent = "Sí —";
  bttsNo.textContent = "No —";
  for (const node of [nextGoal, marketProbabilities, goalsDistribution, scorelines, scoreMatrix]) node.replaceChildren();
  goalsDistribution.hidden = true; scorelines.hidden = true;
  goalsDistribution.parentElement.parentElement.hidden = true;
  scoreMatrix.hidden = true; scoreMatrix.parentElement.hidden = true;
  evidence.replaceChildren(); evidence.hidden = true;
}

async function loadHistory(id, version = selectionVersion) {
  if (!publicIdPattern.test(id) || selectedId !== id || version !== selectionVersion || loadedSummary?.id !== id || loadedSummary.version !== version) return;
  if (historyController) historyController.abort();
  const requestController = new AbortController();
  historyController = requestController;
  renderAnalyticsState("loading", "Cargando historial del partido…");
  try {
    const response = await fetchWithTimeout("/api/matches/" + encodeURIComponent(id) + "/history?limit=90", requestController);
    if (version !== selectionVersion || historyController !== requestController || requestController.signal.aborted) return;
    if (response.item?.public_id !== id) throw new Error("mismatched history");
    // Current age and model belong to the envelope, not to historical points.
    renderAnalytics(response.item, response.data_status, response.model_version);
  } catch (error) {
    if (version !== selectionVersion || historyController !== requestController || error.name === "AbortError") return;
    // An unsuccessful refresh must not keep old simulations looking current.
    resetAnalytics();
    renderAnalyticsState("error", "No pudimos cargar el historial a tiempo. Puedes reintentar el análisis.");
  } finally { if (historyController === requestController) historyController = null; }
}

const statPairs = [
  ["possession", "Posesión", "%"], ["shots", "Tiros", ""], ["shots_on_target", "Tiros a puerta", ""],
  ["corners", "Córners", ""], ["yellow_cards", "Tarjetas amarillas", ""],
  ["red_cards", "Tarjetas rojas", ""], ["expected_goals", "Goles esperados del proveedor", ""],
];

function renderTeamStats(point, matchId) {
  teamStats.replaceChildren(); teamStats.hidden = false; teamStats.parentElement.hidden = false;
  const nextStats = statPairs.map(([key]) => ({ key, home: point?.stats?.[key]?.home, away: point?.stats?.[key]?.away }));
  const previousStats = statsSnapshot?.id === matchId ? statsSnapshot.values : null;
  statsSnapshot = { id: matchId, values: nextStats };
  for (const [key, label, unit] of statPairs) {
    const pair = point?.stats?.[key] || {};
    const previous = previousStats?.find(value => value.key === key);
    const changed = Boolean(previous && (previous.home !== pair.home || previous.away !== pair.away));
    const row = element("div", changed ? "stat-changed" : ""); row.append(element("dt", "", label));
    const values = element("dd", "stat-pair");
    const total = Number.isFinite(pair.home) && Number.isFinite(pair.away) ? pair.home + pair.away : null;
    for (const side of ["home", "away"]) {
      const value = pair[side]; const available = Number.isFinite(value);
      const cell = element("span", "stat-value");
      cell.append(element("span", "", `${side === "home" ? "Local" : "Visitante"}: ${available ? `${value}${unit}` : "— · Dato no disponible"}`));
      if (available && total !== null) {
        const bar = element("span", `stat-bar ${side}`); bar.setAttribute("aria-hidden", "true");
        bar.style.setProperty("--percent", `${total > 0 ? Math.max(0, Math.min(100, value / total * 100)) : 0}%`); cell.append(bar);
      }
      values.append(cell);
    }
    row.append(values); teamStats.append(row);
  }
}

function percentageRow(target, label, value, ordered = false) {
  const available = Number.isFinite(value);
  const copy = available ? `${value}%` : "Dato no disponible";
  const bar = element("span", "analysis-bar"); bar.setAttribute("aria-hidden", "true");
  if (available) bar.style.setProperty("--percent", `${Math.max(0, Math.min(100, value))}%`);
  if (ordered) {
    const row = element("li", "", `${label}: ${copy}`); if (available) row.append(bar); target.append(row);
  } else {
    const term = element("dt", "", label); const valueNode = element("dd", "", copy);
    if (available) valueNode.append(bar); target.append(term, valueNode);
  }
}

function probabilityLabel(value) {
  return `${Number(value).toFixed(2)}%`;
}

function renderBtts(yes, no, available) {
  bttsVisual.hidden = !available;
  if (!available) {
    bttsVisual.setAttribute("aria-label", "Ambos equipos marcan: dato no disponible.");
    return;
  }
  const yesValue = Math.max(0, Math.min(100, Number(yes)));
  const noValue = Math.max(0, Math.min(100, Number(no)));
  const yesLabel = probabilityLabel(yesValue);
  const noLabel = probabilityLabel(noValue);
  bttsRing.style.setProperty("--yes", String(yesValue));
  bttsRingValue.textContent = yesLabel;
  bttsYes.textContent = `Sí ${yesLabel}`;
  bttsNo.textContent = `No ${noLabel}`;
  bttsVisual.setAttribute("aria-label", `Ambos equipos marcan: sí ${yesLabel}, no ${noLabel}.`);
}

function renderScoreMatrix(rows, matchId) {
  scoreMatrix.replaceChildren();
  scoreMatrix.hidden = !rows.length;
  scoreMatrix.parentElement.hidden = !rows.length;
  if (!rows.length) return;
  const labels = ["0", "1", "2", "3", "4", "5", "6+"];
  const values = new Map(rows.map((row) => [`${row.home}:${row.away}`, row.probability]));
  const previousValues = matrixSnapshot?.id === matchId ? matrixSnapshot.values : null;
  matrixSnapshot = { id: matchId, values };
  const header = element("div", "score-matrix-row score-matrix-head");
  header.setAttribute("role", "row");
  const corner = element("span", "score-matrix-axis", "Local ↓ · Visitante →");
  corner.setAttribute("role", "columnheader"); header.append(corner);
  for (const away of labels) {
    const label = element("span", "", away); label.setAttribute("role", "columnheader"); header.append(label);
  }
  scoreMatrix.append(header);
  for (const home of labels) {
    const row = element("div", "score-matrix-row"); row.setAttribute("role", "row");
    const rowLabel = element("span", "score-matrix-axis", home); rowLabel.setAttribute("role", "rowheader"); row.append(rowLabel);
    for (const away of labels) {
      const probability = values.get(`${home}:${away}`);
      const available = Number.isFinite(probability);
      const key = `${home}:${away}`;
      const changed = Boolean(previousValues?.has(key) && previousValues.get(key) !== probability);
      const cell = element("span", changed ? "score-matrix-cell matrix-cell-changed" : "score-matrix-cell", available ? `${probability}%` : "—");
      cell.setAttribute("role", "cell");
      cell.setAttribute("aria-label", `Local ${home}, visitante ${away}: ${available ? `${probability}%` : "dato no disponible"}`);
      if (available) cell.style.setProperty("--heat", String(Math.max(0.06, Math.min(0.9, probability / 35))));
      row.append(cell);
    }
    scoreMatrix.append(row);
  }
}

function renderScenarios(analytics, dataStatus, modelVersion, matchId) {
  const allowed = ["fresh", "degraded"].includes(dataStatus);
  for (const node of [nextGoal, marketProbabilities, goalsDistribution, scorelines, scoreMatrix]) node.replaceChildren();
  const nextAvailable = allowed && analytics.next_goal != null;
  const marketsAvailable = allowed && analytics.markets != null;
  const bttsAvailable = marketsAvailable && Number.isFinite(analytics.markets?.btts_yes) && Number.isFinite(analytics.markets?.btts_no);
  scenarioMarkets.hidden = !nextAvailable && !marketsAvailable;
  nextGoal.parentElement.hidden = !nextAvailable; marketProbabilities.parentElement.hidden = !marketsAvailable;
  scenarioMarkets.setAttribute("aria-label", `Escenarios experimentales del modelo ${modelVersion ?? "Dato no disponible"} · Datos ${dataStatus ?? "Dato no disponible"}`);
  if (nextAvailable) for (const [key, label] of [["home", "Local"], ["none", "Sin más goles"], ["away", "Visitante"]]) percentageRow(nextGoal, label, analytics.next_goal[key]);
  renderBtts(analytics.markets?.btts_yes, analytics.markets?.btts_no, bttsAvailable);
  if (marketsAvailable) for (const [key, label] of [["over_2_5", "Más de 2,5 goles"], ["under_2_5", "Menos de 2,5 goles"]]) percentageRow(marketProbabilities, label, analytics.markets[key]);
  const totals = allowed && Array.isArray(analytics.total_goals) ? analytics.total_goals : [];
  const scores = allowed && Array.isArray(analytics.scorelines) ? analytics.scorelines : [];
  const matrix = allowed && Array.isArray(analytics.score_matrix) ? analytics.score_matrix : [];
  goalsDistribution.hidden = !totals.length; goalsDistribution.parentElement.hidden = !totals.length;
  scorelines.hidden = !scores.length; scorelines.parentElement.hidden = !scores.length;
  goalsDistribution.parentElement.parentElement.hidden = !totals.length && !scores.length;
  for (const row of totals) percentageRow(goalsDistribution, row.label, row.probability);
  for (const row of scores) percentageRow(scorelines, `${row.home}—${row.away}`, row.probability, true);
  renderScoreMatrix(matrix, matchId);
  if (!allowed) return `Datos ${dataStatus ?? "no disponibles"}: los escenarios están ocultos por la antigüedad o suspensión de la lectura.`;
  if (!nextAvailable || !marketsAvailable || !totals.length || !scores.length || !matrix.length) return "Dato no disponible: faltan tasas ajustadas válidas para algunos escenarios del modelo.";
  return "Escenarios experimentales del modelo; no representan evidencia de precisión validada.";
}

function renderCharts() {
  if (!chartState) return;
  const { item } = chartState; const points = item.points;
  const series = [["home", item.home_name, "#dfff54"], ["draw", "Empate", "#ffd94d"], ["away", item.away_name, "#ff9a85"]]
    .map(([key, label, color]) => ({ label, color, values: points.map(point => Number.isFinite(point.probabilities?.[key]) ? point.probabilities[key] : null) }));
  const probabilityTrend = series.some(row => row.values.filter(Number.isFinite).length >= 2);
  if (probabilityTrend) drawLineChart(probabilityCanvas, series, { min: 0, max: 100, labels: points.map(point => point.minute == null ? point.provider_observed_at : `${point.minute}'`) });
  else clearChart(probabilityCanvas, "Dato no disponible: se necesitan al menos dos observaciones válidas.");
  const latest = points.at(-1)?.probabilities;
  document.querySelector("#probability-summary").textContent = `${points.length} observaciones. ${probabilityTrend ? "Local, empate y visitante (%); los valores ausentes dejan huecos en la serie." : "Dato no disponible: una observación muestra estadísticas actuales, pero no basta para formar una tendencia."} Última lectura: ${["home", "draw", "away"].map(key => `${key === "home" ? "Local" : key === "draw" ? "Empate" : "Visitante"} ${Number.isFinite(latest?.[key]) ? `${latest[key]}%` : "Dato no disponible"}`).join(" · ")}`;
  const trends = [];
  const descriptions = [];
  for (const [key, label, colors] of [["activity", "Actividad observada", ["#dfff54", "#ffd94d"]], ["momentum", "Momentum", ["#ff9a85", "#9ecfff"]]]) {
    const values = Array.isArray(item.analytics?.[key]) ? item.analytics[key] : [];
    for (const [index, side] of ["home", "away"].entries()) {
      const samples = values.map(point => Number.isFinite(point[side]) ? point[side] : null);
      if (samples.filter(Number.isFinite).length >= 2) {
        trends.push({ label: `${label} ${side === "home" ? "local" : "visitante"}`, values: samples, color: colors[index] });
        const latestValue = samples.at(-1);
        descriptions.push(`${label} ${side === "home" ? "local" : "visitante"}: ${Number.isFinite(latestValue) ? latestValue : "Dato no disponible"}`);
      } else descriptions.push(`${label} ${side === "home" ? "local" : "visitante"}: Dato no disponible`);
    }
  }
  if (trends.length) drawLineChart(activityCanvas, trends, { min: 0, max: 100 });
  else clearChart(activityCanvas, "Dato no disponible: faltan observaciones para una tendencia.");
  document.querySelector("#activity-summary").textContent = `Índice experimental (0–100); no representa una probabilidad validada. ${descriptions.join(" · ")}. Los valores ausentes dejan huecos; momentum necesita cuatro puntos válidos de actividad.`;
}

function renderAnalytics(item, dataStatus, modelVersion) {
  const points = Array.isArray(item.points) ? item.points : [];
  const analytics = item.analytics || {};
  const historyState = matchVisualState(item);
  analyticsArea.dataset.liveState = historyState === "unknown" ? (detail.dataset.liveState || historyState) : historyState;
  chartState = { item: { ...item, points }, dataStatus, modelVersion };
  for (const canvas of [probabilityCanvas, activityCanvas]) { canvas.hidden = false; canvas.parentElement.hidden = false; }
  renderTeamStats(points.at(-1), item.public_id);
  const scenarioMessage = renderScenarios(analytics, dataStatus, modelVersion, item.public_id);
  evidence.hidden = false;
  const coverage = analytics.coverage;
  evidence.replaceChildren(element("p", "", `Cobertura: ${coverage && Number.isFinite(coverage.percent) ? `${coverage.available}/${coverage.total} (${coverage.percent}%)` : "Dato no disponible"} · Estado actual: ${dataStatus ?? "Dato no disponible"} · Calidad histórica: ${points.at(-1)?.quality ?? "Dato no disponible"} · Modelo: ${modelVersion ?? "Dato no disponible"} · Observado: ${points.at(-1)?.provider_observed_at ?? "Dato no disponible"}`));
  renderAnalyticsState(points.length ? dataStatus : "empty", `${points.length ? "Historial actualizado." : "No hay observaciones publicadas."} ${scenarioMessage}`);
  renderCharts();
}

function refreshData() {
  const id = selectedId;
  const version = selectionVersion;
  resetRefreshCountdown();
  loadLive();
  // Capture selection now, never after an asynchronous list response. Polls do not
  // supersede requests already serving a newer selection or a manual retry.
  if (id && id === selectedId && version === selectionVersion && !detailController && !historyController) {
    loadMatch(id, { preserveAnalysis: true });
  }
}
function schedulePolling() { window.clearInterval(refreshTimer); refreshTimer = window.setInterval(() => { if (!document.hidden) refreshData(); }, 20000); }
function renderRefreshCountdown() {
  const progress = Math.max(0, Math.min(1, countdownSeconds / refreshIntervalSeconds));
  refreshCountdownRing.style.setProperty("--progress", `${progress * 360}deg`);
  refreshCountdownValue.textContent = String(countdownSeconds);
  refreshCountdownLabel.textContent = `en ${countdownSeconds} s`;
  refreshCountdown.setAttribute("aria-label", `Próxima actualización de predicciones en ${countdownSeconds} segundos.`);
}
function resetRefreshCountdown() { countdownSeconds = refreshIntervalSeconds; renderRefreshCountdown(); }
function scheduleCountdown() {
  window.clearInterval(countdownTimer);
  resetRefreshCountdown();
  countdownTimer = window.setInterval(() => {
    if (document.hidden) return;
    countdownSeconds = Math.max(0, countdownSeconds - 1);
    renderRefreshCountdown();
  }, 1000);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshData(); });
window.addEventListener("resize", () => { window.clearTimeout(resizeTimer); resizeTimer = window.setTimeout(renderCharts, 150); });
analyticsRetry.addEventListener("click", () => {
  if (!selectedId) return;
  if (loadedSummary?.id !== selectedId || loadedSummary.version !== selectionVersion) {
    loadMatch(selectedId, { preserveAnalysis: true });
  } else loadHistory(selectedId);
});
refresh.addEventListener("click", () => { resetRefreshCountdown(); loadLive(); if (selectedId) loadMatch(selectedId); });
loadLive(); schedulePolling(); scheduleCountdown();
