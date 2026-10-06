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
let matchEventSnapshot = { id: null, keys: new Set() };
let resizeTimer = null;
let refreshTimer = null;
const refreshIntervalSeconds = 20;
let countdownSeconds = refreshIntervalSeconds;
let countdownTimer = null;
let visualDecimalTimer = null;
let visualDecimalTick = 0;
let visualDecimalNodes = [];
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
const matrixTeams = document.querySelector("#matrix-teams");
const evidence = document.querySelector("#analytics-evidence");
const refreshCountdown = document.querySelector("#refresh-countdown");
const refreshCountdownRing = document.querySelector("#refresh-countdown-ring");
const refreshCountdownValue = document.querySelector("#refresh-countdown-value");
const refreshCountdownLabel = document.querySelector("#refresh-countdown-label");
const bttsVisual = document.querySelector("#btts-visual");
const bttsSection = document.querySelector("#btts-section");
const bttsRing = document.querySelector("#btts-ring");
const bttsRingValue = document.querySelector("#btts-ring-value");
const bttsYes = document.querySelector("#btts-yes");
const bttsNo = document.querySelector("#btts-no");
const matchEventsSection = document.querySelector("#match-events-section");
const matchEvents = document.querySelector("#match-events");
const probabilitySummary = document.querySelector("#probability-summary");
const activitySummary = document.querySelector("#activity-summary");

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

function clearVisualDecimals(group) {
  visualDecimalNodes = visualDecimalNodes.filter(entry => entry.group !== group);
}

function visualProbability(value, group) {
  const numeric = Number(value);
  const [integer, decimal] = numeric.toFixed(2).split(".");
  const wrapper = element("span", "probability-visual-value");
  const visual = element("span", "probability-visual");
  visual.setAttribute("aria-hidden", "true");
  const decimalNode = element("span", "probability-visual-decimal", `.${decimal}`);
  decimalNode.setAttribute("aria-hidden", "true");
  decimalNode.dataset.baseDecimal = decimal;
  decimalNode.dataset.visualIndex = String(visualDecimalNodes.length);
  visual.append(element("span", "probability-integer", integer), decimalNode, element("span", "probability-suffix", "%"));
  wrapper.append(visual, element("span", "sr-only", `${numeric}%`));
  visualDecimalNodes.push({ group, node: decimalNode });
  return wrapper;
}

function refreshVisualDecimals() {
  visualDecimalTick += 1;
  const offsets = [1, -1, 2, -2, 1, -1];
  for (const { node } of visualDecimalNodes) {
    const base = Number(node.dataset.baseDecimal);
    const offset = offsets[(visualDecimalTick + Number(node.dataset.visualIndex || 0)) % offsets.length];
    const next = Math.max(0, Math.min(99, base + offset));
    node.textContent = `.${String(next).padStart(2, "0")}`;
  }
}

function scheduleVisualDecimals() {
  window.clearInterval(visualDecimalTimer);
  visualDecimalTimer = window.setInterval(() => { if (!document.hidden) refreshVisualDecimals(); }, 900);
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
  image.addEventListener("error", () => { mark.textContent = initials(name); });
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
  const reading = element("strong", "", available ? undefined : "Dato no disponible");
  if (available) reading.append(visualProbability(value, "summary"));
  row.append(element("span", "", label), reading);
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
  clearVisualDecimals("summary");
  detail.replaceChildren();
  const top = element("div", "detail-topline");
  top.append(element("span", "", item.competition || "Lectura en vivo"), liveBadge(item), element("span", `quality ${item.data_status}`, item.data_status === "degraded" ? "Datos parciales" : `Datos ${item.data_status}`));
  const scoreline = element("div", "scoreline");
  const home = element("div", "club"); home.append(clubMark(item.home_name, item.home_logo_url), element("span", "", item.home_name));
  const scoreClass = detailChanged.score || detailChanged.minute ? `score score-${visualState} value-changed` : `score score-${visualState}`;
  const score = element("div", scoreClass);
  score.append(
    element("b", "score-value score-home", item.score_home ?? "—"),
    element("span", "score-separator", "—"),
    element("b", "score-value score-away", item.score_away ?? "—"),
    element("small", "", item.minute == null ? "Minuto no disponible" : `${item.minute}'`),
  );
  const away = element("div", "club away"); away.append(element("span", "", item.away_name), clubMark(item.away_name, item.away_logo_url));
  scoreline.append(home, score, away);
  const grid = element("div", "detail-grid probabilities-layout");
  const probabilities = element("section", "surface"); probabilities.append(element("h3", "probabilities-title", "Probabilidades 1X2"));
  const values = item.probabilities || {};
  probabilities.append(probabilityCard(item.home_name, values.home, detailChanged.probabilities.home), probabilityCard("Empate", values.draw, detailChanged.probabilities.draw), probabilityCard(item.away_name, values.away, detailChanged.probabilities.away));
  grid.append(probabilities); detail.append(top, scoreline, grid);
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
  bttsSection.hidden = true;
  bttsVisual.hidden = true;
  bttsVisual.setAttribute("aria-label", "¿Anotan ambos equipos?: dato no disponible.");
  bttsRing.style.setProperty("--yes", "0");
  bttsRingValue.textContent = "—";
  bttsYes.textContent = "Sí, ambos marcan —";
  bttsNo.textContent = "No, uno se queda sin marcar —";
  matchEvents.replaceChildren();
  matchEventsSection.hidden = true;
  clearVisualDecimals("scenario");
  clearVisualDecimals("matrix");
  for (const node of [nextGoal, marketProbabilities, goalsDistribution, scorelines, scoreMatrix]) node.replaceChildren();
  goalsDistribution.hidden = true; scorelines.hidden = true;
  goalsDistribution.parentElement.parentElement.hidden = true;
  scoreMatrix.hidden = true; scoreMatrix.parentElement.hidden = true;
  matrixTeams.replaceChildren(); matrixTeams.hidden = true;
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
  const bar = element("span", "analysis-bar"); bar.setAttribute("aria-hidden", "true");
  if (available) bar.style.setProperty("--percent", `${Math.max(0, Math.min(100, value))}%`);
  if (ordered) {
    const row = element("li", "", `${label}: `);
    row.append(available ? visualProbability(value, "scenario") : element("span", "", "Dato no disponible"));
    if (available) row.append(bar); target.append(row);
  } else {
    const term = element("dt", "", label); const valueNode = element("dd", "", available ? undefined : "Dato no disponible");
    if (available) valueNode.append(visualProbability(value, "scenario"));
    if (available) valueNode.append(bar); target.append(term, valueNode);
  }
}

function scenarioValue(value) {
  return Math.max(0, Math.min(100, Number(value)));
}

function leadingScenarioIndex(rows) {
  let leading = -1;
  let highest = -Infinity;
  rows.forEach((row, index) => {
    if (Number.isFinite(row.value) && row.value > highest) {
      highest = row.value;
      leading = index;
    }
  });
  return leading;
}

function renderNextGoalCards(target, values) {
  target.className = "analysis-rows scenario-cards scenario-cards-three";
  const leading = leadingScenarioIndex(values);
  values.forEach((entry, index) => {
    const available = Number.isFinite(entry.value);
    const value = available ? scenarioValue(entry.value) : 0;
    const card = element("div", `scenario-card${index === leading ? " is-leading" : ""}`);
    const orbit = element("span", "scenario-orbit");
    orbit.setAttribute("aria-hidden", "true");
    orbit.style.setProperty("--percent", `${value}%`);
    const valueNode = element("strong", "scenario-card-value");
    valueNode.append(available ? visualProbability(value, "scenario") : element("span", "", "—"));
    card.append(element("span", "scenario-card-label", entry.label), orbit, valueNode);
    if (index === leading) card.append(element("span", "scenario-card-leader", "Lectura líder"));
    target.append(card);
  });
}

function renderGoalLineMeter(target, values) {
  target.className = "analysis-rows goal-line-meter";
  const [over, under] = values;
  const overValue = Number.isFinite(over.value) ? scenarioValue(over.value) : 0;
  const track = element("div", "goal-line-track");
  track.setAttribute("aria-hidden", "true");
  track.style.setProperty("--over", `${overValue}%`);
  const line = element("span", "goal-line-number", "2.5");
  line.setAttribute("aria-hidden", "true");
  track.append(line);
  target.append(track);
  values.forEach((entry, index) => {
    const available = Number.isFinite(entry.value);
    const row = element("div", `goal-line-option ${index === 0 ? "is-over" : "is-under"}`);
    const valueNode = element("strong", "goal-line-value");
    valueNode.append(available ? visualProbability(scenarioValue(entry.value), "scenario") : element("span", "", "Dato no disponible"));
    row.append(element("span", "goal-line-label", entry.label), valueNode);
    target.append(row);
  });
}

function renderGoalHistogram(target, rows) {
  target.className = "analysis-rows goal-histogram";
  const leading = leadingScenarioIndex(rows.map(row => ({ value: row.probability })));
  rows.forEach((row, index) => {
    const available = Number.isFinite(row.probability);
    const value = available ? scenarioValue(row.probability) : 0;
    const column = element("div", `goal-column${index === leading ? " is-leading" : ""}`);
    const fill = element("span", "goal-column-fill");
    fill.setAttribute("aria-hidden", "true");
    fill.style.setProperty("--percent", `${value}%`);
    const valueNode = element("strong", "goal-column-value");
    valueNode.append(available ? visualProbability(value, "scenario") : element("span", "", "—"));
    column.append(element("span", "goal-column-label", row.label), valueNode, fill);
    target.append(column);
  });
}

function renderScoreRanking(target, rows) {
  target.className = "analysis-rows score-ranking";
  const leading = leadingScenarioIndex(rows.map(row => ({ value: row.probability })));
  rows.forEach((row, index) => {
    const available = Number.isFinite(row.probability);
    const value = available ? scenarioValue(row.probability) : 0;
    const item = element("li", `score-rank${index === leading ? " is-leading" : ""}`);
    item.style.setProperty("--percent", `${value}%`);
    const probability = element("strong", "score-rank-probability");
    probability.append(available ? visualProbability(value, "scenario") : element("span", "", "—"));
    item.append(element("span", "score-rank-number", String(index + 1)), element("span", "score-rank-score", `${row.home}—${row.away}`), probability);
    target.append(item);
  });
}

function probabilityLabel(value) {
  return `${Number(value).toFixed(2)}%`;
}

function renderBtts(yes, no, available) {
  bttsVisual.hidden = !available;
  bttsSection.hidden = !available;
  if (!available) {
    bttsVisual.setAttribute("aria-label", "¿Anotan ambos equipos?: dato no disponible.");
    return;
  }
  const yesValue = Math.max(0, Math.min(100, Number(yes)));
  const noValue = Math.max(0, Math.min(100, Number(no)));
  const yesLabel = probabilityLabel(yesValue);
  const noLabel = probabilityLabel(noValue);
  bttsRing.style.setProperty("--yes", String(yesValue));
  bttsRingValue.replaceChildren(visualProbability(yesValue, "scenario"));
  bttsYes.replaceChildren(element("span", "", "Sí, ambos marcan "), visualProbability(yesValue, "scenario"));
  bttsNo.replaceChildren(element("span", "", "No, uno se queda sin marcar "), visualProbability(noValue, "scenario"));
  bttsVisual.setAttribute("aria-label", `¿Anotan ambos equipos?: sí, ambos marcan ${yesLabel}; no, uno se queda sin marcar ${noLabel}.`);
}

function detectedShotUpdates(points) {
  const latest = { home: null, away: null };
  const updates = [];
  for (const point of Array.isArray(points) ? points : []) {
    const minute = point?.minute;
    if (!Number.isFinite(minute) || minute < 0) continue;
    for (const side of ["home", "away"]) {
      const total = point?.stats?.shots?.[side];
      if (!Number.isFinite(total) || total < 0) continue;
      const previous = latest[side];
      if (Number.isFinite(previous) && total > previous) {
        updates.push({ minute, added_time: null, side, kind: "shot", increment: total - previous, source: "detected" });
      }
      latest[side] = Number.isFinite(previous) ? Math.max(previous, total) : total;
    }
  }
  return updates;
}

function renderMatchEvents(item) {
  const eventTypes = {
    goal: { label: "Gol", icon: "⚽" },
    yellow_card: { label: "Tarjeta amarilla", icon: "🟨" },
    red_card: { label: "Tarjeta roja", icon: "🟥" },
    shot: { label: "Tiros detectados", icon: "◎" },
  };
  const incidents = (Array.isArray(item.events) ? item.events : [])
    .filter(event => eventTypes[event?.kind] && event.kind !== "shot")
    .map((event, sequence) => ({ ...event, source: "provider", sequence }));
  const shots = detectedShotUpdates(item.points).map((event, sequence) => ({ ...event, sequence }));
  const events = [...incidents, ...shots].sort((left, right) => (
    right.minute - left.minute
    || (right.added_time || 0) - (left.added_time || 0)
    || Number(left.source === "detected") - Number(right.source === "detected")
    || left.sequence - right.sequence
  ));
  matchEvents.replaceChildren();
  matchEventsSection.hidden = false;
  if (!events.length) {
    matchEventSnapshot = { id: item.public_id, keys: new Set() };
    matchEvents.append(element("li", "match-event-empty", "Sin eventos verificables publicados todavía."));
    return;
  }
  const currentKeys = new Set();
  for (const [index, event] of events.entries()) {
    const minute = `${event.minute}${Number.isInteger(event.added_time) && event.added_time > 0 ? `+${event.added_time}` : ""}'`;
    const team = event.side === "away" ? item.away_name : item.home_name;
    const type = eventTypes[event.kind];
    const copy = event.kind === "shot" ? `${type.label} +${event.increment} · ${team}` : `${type.label} · ${team}`;
    const shotWord = event.increment === 1 ? "tiro" : "tiros";
    const key = [event.source, event.kind, event.minute, event.added_time || 0, event.side, event.increment || 0].join(":");
    const isNew = matchEventSnapshot.id !== item.public_id || !matchEventSnapshot.keys.has(key);
    currentKeys.add(key);
    const row = element("li", `match-event match-event-${event.kind} match-event-${event.side}${isNew ? " match-event-new" : ""}`);
    row.setAttribute("data-side", event.side === "away" ? "away" : "home");
    row.style.setProperty("--event-index", String(index));
    row.setAttribute("aria-label", event.kind === "shot"
      ? `${minute}: aumento detectado de ${event.increment} ${shotWord} de ${team} entre actualizaciones.`
      : `${minute}: ${type.label.toLowerCase()} de ${team}.`);
    const pin = element("span", "match-event-pin");
    pin.setAttribute("aria-hidden", "true");
    pin.append(element("span", "match-event-marker"), element("span", "match-event-icon", type.icon));
    row.append(element("strong", "match-event-minute", minute), pin, element("span", "match-event-copy", copy));
    matchEvents.append(row);
  }
  matchEventSnapshot = { id: item.public_id, keys: currentKeys };
}

function renderMatrixTeams(item, available) {
  matrixTeams.replaceChildren();
  matrixTeams.hidden = !available;
  if (!available) return;
  matrixTeams.setAttribute("aria-label", `Local: ${item.home_name}. Visitante: ${item.away_name}.`);
  const team = (side, name, logoUrl) => {
    const node = element("div", `matrix-team ${side}`);
    node.append(element("span", "matrix-team-role", side === "home" ? "LOCAL" : "VISITANTE"), clubMark(name, logoUrl), element("strong", "", name));
    return node;
  };
  matrixTeams.append(team("home", item.home_name, item.home_logo_url), element("span", "matrix-team-versus", "vs"), team("away", item.away_name, item.away_logo_url));
}

function renderScoreMatrix(rows, matchId) {
  clearVisualDecimals("matrix");
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
      const cell = element("span", changed ? "score-matrix-cell matrix-cell-changed" : "score-matrix-cell", available ? undefined : "—");
      if (available) cell.append(visualProbability(probability, "matrix"));
      cell.setAttribute("role", "cell");
      cell.setAttribute("aria-label", `Local ${home}, visitante ${away}: ${available ? `${probability}%` : "dato no disponible"}`);
      if (available) cell.style.setProperty("--heat", String(Math.max(0.06, Math.min(0.9, probability / 35))));
      row.append(cell);
    }
    scoreMatrix.append(row);
  }
}

function renderScenarios(analytics, dataStatus, modelVersion, matchId, item) {
  const allowed = ["fresh", "degraded"].includes(dataStatus);
  clearVisualDecimals("scenario");
  for (const node of [nextGoal, marketProbabilities, goalsDistribution, scorelines, scoreMatrix]) node.replaceChildren();
  const nextAvailable = allowed && analytics.next_goal != null;
  const marketsAvailable = allowed && analytics.markets != null;
  const bttsAvailable = marketsAvailable && Number.isFinite(analytics.markets?.btts_yes) && Number.isFinite(analytics.markets?.btts_no);
  scenarioMarkets.hidden = !nextAvailable && !marketsAvailable;
  nextGoal.parentElement.hidden = !nextAvailable; marketProbabilities.parentElement.hidden = !marketsAvailable;
  scenarioMarkets.setAttribute("aria-label", `Escenarios experimentales del modelo ${modelVersion ?? "Dato no disponible"} · Datos ${dataStatus ?? "Dato no disponible"}`);
  if (nextAvailable) renderNextGoalCards(nextGoal, [["home", "Local"], ["none", "Sin más goles"], ["away", "Visitante"]].map(([key, label]) => ({ label, value: analytics.next_goal[key] })));
  renderBtts(analytics.markets?.btts_yes, analytics.markets?.btts_no, bttsAvailable);
  if (marketsAvailable) renderGoalLineMeter(marketProbabilities, [["over_2_5", "Más de 2,5 goles"], ["under_2_5", "Menos de 2,5 goles"]].map(([key, label]) => ({ label, value: analytics.markets[key] })));
  const totals = allowed && Array.isArray(analytics.total_goals) ? analytics.total_goals : [];
  const scores = allowed && Array.isArray(analytics.scorelines) ? analytics.scorelines : [];
  const matrix = allowed && Array.isArray(analytics.score_matrix) ? analytics.score_matrix : [];
  goalsDistribution.hidden = !totals.length; goalsDistribution.parentElement.hidden = !totals.length;
  scorelines.hidden = !scores.length; scorelines.parentElement.hidden = !scores.length;
  goalsDistribution.parentElement.parentElement.hidden = !totals.length && !scores.length;
  if (totals.length) renderGoalHistogram(goalsDistribution, totals);
  if (scores.length) renderScoreRanking(scorelines, scores);
  renderMatrixTeams(item, matrix.length > 0);
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
  probabilitySummary.textContent = probabilityTrend
    ? "Evolución de las probabilidades publicadas."
    : "Se necesitan al menos dos lecturas para mostrar una evolución.";
  const trends = [];
  for (const [key, label, colors] of [["activity", "Actividad observada", ["#dfff54", "#ffd94d"]], ["momentum", "Momentum", ["#ff9a85", "#9ecfff"]]]) {
    const values = Array.isArray(item.analytics?.[key]) ? item.analytics[key] : [];
    for (const [index, side] of ["home", "away"].entries()) {
      const samples = values.map(point => Number.isFinite(point[side]) ? point[side] : null);
      if (samples.filter(Number.isFinite).length >= 2) {
        trends.push({ label: `${label} ${side === "home" ? "local" : "visitante"}`, values: samples, color: colors[index] });
      }
    }
  }
  if (trends.length) drawLineChart(activityCanvas, trends, { min: 0, max: 100 });
  else clearChart(activityCanvas, "Dato no disponible: faltan observaciones para una tendencia.");
  activitySummary.textContent = trends.length
    ? "Actividad observada durante el partido."
    : "Aún no hay suficientes lecturas para mostrar actividad.";
}

function renderAnalytics(item, dataStatus, modelVersion) {
  const points = Array.isArray(item.points) ? item.points : [];
  const analytics = item.analytics || {};
  const historyState = matchVisualState(item);
  analyticsArea.dataset.liveState = historyState === "unknown" ? (detail.dataset.liveState || historyState) : historyState;
  chartState = { item: { ...item, points }, dataStatus, modelVersion };
  for (const canvas of [probabilityCanvas, activityCanvas]) { canvas.hidden = false; canvas.parentElement.hidden = false; }
  renderTeamStats(points.at(-1), item.public_id);
  renderMatchEvents(item);
  const scenarioMessage = renderScenarios(analytics, dataStatus, modelVersion, item.public_id, item);
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
loadLive(); schedulePolling(); scheduleCountdown(); scheduleVisualDecimals();
