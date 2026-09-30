const list = document.querySelector("#match-list");
const detail = document.querySelector("#match-detail");
const status = document.querySelector("#data-status");
const refresh = document.querySelector("#refresh");
const favoritesKey = "football-live-favorites";
const states = ["loading", "empty", "fresh", "degraded", "stale", "suspended", "error"];
const allowedLogoHosts = new Set(["imagecache.365scores.com", "img.365scores.com"]);
const publicIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
let liveItems = [];
let selectedId = null;
let controller = null;
let refreshTimer = null;

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

function clubMark(name, logoUrl) {
  const mark = element("span", "club-mark", initials(name));
  const source = safeLogo(logoUrl);
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

function fetchWithTimeout(path) {
  if (controller) controller.abort();
  controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 8000);
  return fetch(path, { signal: controller.signal, headers: { Accept: "application/json" } })
    .then((response) => response.ok ? response.json() : Promise.reject(new Error("request failed")))
    .finally(() => window.clearTimeout(timeout));
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
    const button = element("button", "match-button");
    button.type = "button";
    button.setAttribute("aria-pressed", String(item.public_id === selectedId));
    button.setAttribute("aria-label", `${item.home_name} contra ${item.away_name}`);
    const copy = element("span", "", `${item.home_name} vs ${item.away_name}`);
    copy.append(element("small", "", item.competition || "Competición no disponible"));
    button.append(copy, element("span", "", `${item.score_home ?? "—"} · ${item.score_away ?? "—"}`));
    button.addEventListener("click", () => loadMatch(item.public_id));
    list.append(button);
  });
}

function probabilityCard(label, value) {
  const row = element("div", "probability");
  const available = Number.isFinite(Number(value));
  row.append(element("span", "", label), element("strong", "", available ? `${Number(value).toFixed(0)}%` : "Dato no disponible"));
  if (available) { const bar = element("i"); bar.style.setProperty("--percent", `${Math.max(0, Math.min(100, Number(value)))}%`); row.append(bar); }
  return row;
}

function renderDetail(item) {
  detail.setAttribute("aria-busy", "false");
  detail.replaceChildren();
  const top = element("div", "detail-topline");
  top.append(element("span", "", item.competition || "Lectura en vivo"), element("span", `quality ${item.data_status}`, item.data_status === "degraded" ? "Datos parciales" : `Datos ${item.data_status}`));
  const scoreline = element("div", "scoreline");
  const home = element("div", "club"); home.append(clubMark(item.home_name, item.home_logo_url), element("span", "", item.home_name));
  const score = element("div", "score", `${item.score_home ?? "—"}—${item.score_away ?? "—"}`); score.append(element("small", "", item.minute == null ? "Minuto no disponible" : `${item.minute}'`));
  const away = element("div", "club away"); away.append(element("span", "", item.away_name), clubMark(item.away_name, item.away_logo_url));
  scoreline.append(home, score, away);
  const grid = element("div", "detail-grid");
  const probabilities = element("section", "surface"); probabilities.append(element("h3", "", "Probabilidades 1X2"));
  const values = item.probabilities || {};
  probabilities.append(probabilityCard(item.home_name, values.home), probabilityCard("Empate", values.draw), probabilityCard(item.away_name, values.away));
  const context = element("section", "surface"); context.append(element("h3", "", "Contexto de la lectura"));
  const warnings = Array.isArray(item.explanation?.warnings) ? item.explanation.warnings : [];
  context.append(element("p", "signal-summary", warnings[0] || "Dato no disponible: la explicación aún no fue publicada."));
  const timeline = element("ul", "timeline");
  timeline.append(element("li", "", `Calidad: ${item.data_status || "Dato no disponible"}`), element("li", "", `Modelo: ${item.model_version || "Dato no disponible"}`), element("li", "", `Observado: ${item.provider_observed_at || "Dato no disponible"}`));
  context.append(timeline); grid.append(probabilities, context); detail.append(top, scoreline, grid);
}

async function loadMatch(id) {
  if (!publicIdPattern.test(id)) return;
  selectedId = id; detail.setAttribute("aria-busy", "true"); setStatus("Actualizando la lectura del partido…", "loading");
  try { const response = await fetchWithTimeout("/api/matches/" + encodeURIComponent(id)); const item = response.item; renderDetail(item); setStatus(item.data_status === "degraded" ? "Datos parciales: la lectura conserva señales ausentes." : "Lectura actualizada.", item.data_status); renderMatchList(liveItems); }
  catch { detail.setAttribute("aria-busy", "false"); setStatus("No pudimos actualizar el partido. Puedes intentarlo de nuevo.", "error"); }
}

async function loadLive() {
  setStatus("Cargando partidos disponibles…", "loading");
  try { const response = await fetchWithTimeout("/api/live?limit=24"); liveItems = Array.isArray(response.items) ? response.items.filter((item) => publicIdPattern.test(item.public_id || "")) : []; renderMatchList(liveItems); if (!liveItems.length) setStatus("No hay partidos publicados en este momento.", "empty"); else setStatus("Partidos actualizados. Selecciona uno para leer su contexto.", response.data_status || "fresh"); }
  catch { list.replaceChildren(element("p", "muted", "No pudimos cargar los partidos.")); setStatus("Servicio no disponible temporalmente.", "error"); }
}

function schedulePolling() { window.clearInterval(refreshTimer); refreshTimer = window.setInterval(() => { if (!document.hidden) loadLive(); }, 20000); }
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadLive(); });
refresh.addEventListener("click", loadLive);
loadLive(); schedulePolling();
