import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// Exercise the real application and charts; only browser/network/time boundaries are doubled.
class Node {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.attributes = {}; this.dataset = {};
    this.listeners = {}; this.hidden = false; this.calls = []; this._text = "";
    this.style = { setProperty: (key, value) => { this.attributes[key] = value; } };
  }
  set textContent(text) { this._text = String(text); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(" "); }
  append(...nodes) { for (const node of nodes) { node.parentElement = this; this.children.push(node); } }
  replaceChildren(...nodes) { this._text = ""; this.children = []; this.append(...nodes); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(event, fn) { this.listeners[event] = fn; }
  getBoundingClientRect() { return { width: 400, height: 240 }; }
  getContext() {
    return new Proxy({}, {
      get: (_, key) => (...args) => { this.calls.push([key, ...args]); },
      set: (_, key, value) => { this.calls.push([key, value]); return true; },
    });
  }
}
const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const crest = team => `https://imagecache.365scores.com/image/upload/f_png,w_160,h_160,c_limit,q_auto:eco,dpr_2/d_Competitors:default1.png/v20/Competitors/${team}`;
const summary = id => ({ public_id: id, home_name: id === A ? "Alpha" : "Beta", away_name: "Away", home_logo_url: crest(id === A ? 101 : 102), away_logo_url: crest(103), score_home: 0, score_away: 0, probabilities: { home: null, draw: 0, away: 50 } });
const point = (minute, probabilities = { home: 25.125, draw: 0, away: 74.875 }) => ({
  minute, score_home: 0, score_away: 0, provider_observed_at: "2026-10-05T10:00:00Z",
  collected_at: null, quality: "fresh", stats: { shots: { home: 0, away: null }, possession: { home: 60, away: 40 } },
  probabilities, lambda_adjusted: { home: 1, away: 1 }, prediction_created_at: null,
});
const history = (id = A, state = "fresh", points = [point(1), point(2, null), point(3)]) => ({
  request_id: A, generated_at: "2026-10-05T10:00:00Z", data_status: state, model_version: "envelope-v5",
  item: { ...summary(id), points, events: id === A ? [
    { minute: 8, added_time: null, side: "home", kind: "goal" },
    { minute: 29, added_time: 2, side: "away", kind: "yellow_card" },
    { minute: 61, added_time: null, side: "away", kind: "red_card" },
  ] : [], analytics: {
    activity: [{ minute: 1, observed_at: "t1", home: 60, away: 40 }, { minute: 2, observed_at: "t2", home: null, away: null }, { minute: 3, observed_at: "t3", home: 70, away: 30 }],
    momentum: [], next_goal: { home: 25.125, none: 0, away: 74.875 },
    markets: { over_2_5: 30, under_2_5: 70, btts_yes: 10, btts_no: 90 },
    total_goals: [{ label: "0", probability: 0 }, { label: "7+", probability: 25.125 }],
    scorelines: [{ home: 2, away: 1, probability: 25.125 }, { home: 0, away: 0, probability: 0 }],
    score_matrix: [
      { home: "0", away: "0", probability: 0 },
      { home: "1", away: "0", probability: 25.125 },
      { home: "6+", away: "6+", probability: 0.5 },
    ],
    coverage: { available: 2, total: 14, percent: 14.2857 },
  } },
});
let serial = 0;
async function harness() {
  const nodes = new Map(); const stack = []; let root;
  for (const token of readFileSync("public/app/index.html", "utf8").matchAll(/<\/?([\w-]+)([^>]*)>/g)) {
    if (token[0].startsWith("</")) { stack.pop(); continue; }
    const node = new Node(token[1]);
    node.hidden = /\bhidden\b/.test(token[2]);
    const id = token[2].match(/\bid="([^"]+)"/);
    if (id) nodes.set(`#${id[1]}`, node);
    if (/class="analytics"/.test(token[2])) nodes.set(".analytics", node);
    if (stack.length) stack.at(-1).append(node); else root = node;
    if (!["meta", "link", "img", "br"].includes(token[1])) stack.push(node);
  }
  const timers = new Map(); const intervals = []; const requests = []; const events = {};
  globalThis.document = { hidden: false, querySelector: selector => nodes.get(selector), createElement: tag => new Node(tag), addEventListener: (key, fn) => { events[key] = fn; } };
  globalThis.window = { setTimeout: (fn, ms) => { const id = ++serial; timers.set(id, { fn, ms }); return id; }, clearTimeout: id => timers.delete(id), setInterval: fn => { intervals.push(fn); return ++serial; }, clearInterval() {}, addEventListener: (key, fn) => { events[key] = fn; } };
  globalThis.localStorage = { getItem: () => "[]" };
  globalThis.fetch = (url, options) => new Promise((resolve, reject) => {
    const request = { url, signal: options.signal, resolve: payload => resolve({ ok: true, json: async () => payload }), reject, ignoreAbort: false };
    options.signal.addEventListener("abort", () => { if (!request.ignoreAbort) reject(new DOMException("Aborted", "AbortError")); });
    requests.push(request);
  });
  const charts = `data:text/javascript;base64,${Buffer.from(readFileSync("public/app/charts.js", "utf8")).toString("base64")}`;
  const source = readFileSync("public/app/app.js", "utf8").replace('"./charts.js"', JSON.stringify(charts));
  const api = await import(`data:text/javascript;base64,${Buffer.from(source + `\nexport { loadMatch, loadLive }; // harness ${++serial}`).toString("base64")}`);
  const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
  requests[0].resolve({ items: [summary(A), summary(B)], data_status: "fresh" }); await flush();
  return { api, nodes, requests, timers, intervals, events, flush, root };
}
async function selected(h, payload = history(), summaryPayload = summary(payload.item.public_id)) {
  const promise = h.api.loadMatch(payload.item.public_id);
  h.requests.at(-1).resolve({ item: summaryPayload, data_status: "fresh", model_version: "detail-v" }); await h.flush();
  const request = h.requests.at(-1);
  assert.ok(request.url.endsWith("/history?limit=90"), "selection must fetch history after summary");
  request.resolve(payload); await promise;
}

test("selection aborts history immediately and late results cannot overwrite a new match", async () => {
  const h = await harness(); const first = h.api.loadMatch(A);
  h.requests.at(-1).resolve({ item: summary(A) }); await h.flush();
  const old = h.requests.at(-1); assert.ok(old.url.endsWith("/history?limit=90")); old.ignoreAbort = true;
  const second = h.api.loadMatch(B); assert.equal(old.signal.aborted, true);
  h.requests.at(-1).resolve({ item: summary(B) }); await h.flush();
  h.requests.at(-1).resolve(history(B)); await second;
  old.resolve(history(A)); await first;
  assert.match(h.nodes.get("#match-detail").textContent, /Beta/);
  assert.doesNotMatch(h.nodes.get("#match-detail").textContent, /Alpha/);
  assert.match(h.nodes.get("#analytics-evidence").textContent, /envelope-v5/);
});

test("late detail is suppressed and list polling cannot abort detail or history", async () => {
  const h = await harness(); const first = h.api.loadMatch(A); const old = h.requests.at(-1); old.ignoreAbort = true;
  const second = h.api.loadMatch(B); const current = h.requests.at(-1);
  const listing = h.api.loadLive(); assert.equal(current.signal.aborted, false);
  h.requests.at(-1).resolve({ items: [summary(A), summary(B)] }); await listing;
  current.resolve({ item: summary(B) }); await h.flush(); const hist = h.requests.at(-1);
  const listAgain = h.api.loadLive(); assert.equal(hist.signal.aborted, false);
  h.requests.at(-1).resolve({ items: [summary(A), summary(B)] }); await listAgain;
  hist.resolve(history(B)); await second; old.resolve({ item: summary(A) }); await first;
  assert.match(h.nodes.get("#match-detail").textContent, /Beta/);
});

test("request-local timeout cannot abort a newer selection; history timeout offers retry", async () => {
  const h = await harness(); const first = h.api.loadMatch(A);
  const oldTimer = [...h.timers.values()].find(t => t.ms === 8000).fn;
  const second = h.api.loadMatch(B); const current = h.requests.at(-1); oldTimer();
  assert.equal(current.signal.aborted, false); await first;
  current.resolve({ item: summary(B) }); await h.flush();
  [...h.timers.values()].find(t => t.ms === 8000).fn(); await second;
  assert.equal(h.nodes.get("#analytics-retry").hidden, false);
  assert.match(h.nodes.get("#analytics-status").textContent, /reintentar|nuevo|tiempo/i);
  assert.match(h.nodes.get("#match-detail").textContent, /Beta/);
  assert.equal(h.timers.size, 0);
});

test("polling refreshes selected history and a late list never cancels a newer selection", async () => {
  const h = await harness(); await selected(h); h.intervals[0](); await h.flush();
  const list = h.requests.findLast(r => r.url.startsWith("/api/live"));
  const pollDetail = h.requests.at(-1);
  assert.ok(pollDetail.url.endsWith(A));
  pollDetail.resolve({ item: summary(A), data_status: "fresh" }); await h.flush();
  const refreshed = h.requests.findLast(r => r.url.endsWith("/history?limit=90"));
  assert.notEqual(refreshed, h.requests[2], "20s polling refreshes history");
  const next = h.api.loadMatch(B); const detail = h.requests.at(-1);
  list.resolve({ items: [summary(A), summary(B)] }); await h.flush();
  assert.equal(detail.signal.aborted, false);
  detail.resolve({ item: summary(B) }); await h.flush(); h.requests.at(-1).resolve(history(B)); await next;
});

test("null differs from zero, exact scenarios and historical gaps are rendered with envelope evidence", async () => {
  const h = await harness(); await selected(h);
  assert.match(h.nodes.get("#team-stats").textContent, /0/);
  assert.match(h.nodes.get("#team-stats").textContent, /—.*Dato no disponible/);
  assert.equal(h.nodes.get("#team-stats").children.length, 7);
  assert.match(h.nodes.get("#match-detail").textContent, /Dato no disponible/);
  assert.match(h.nodes.get("#next-goal").textContent, /25\.125%/);
  assert.match(h.nodes.get("#next-goal").textContent, /0%/);
  assert.match(h.nodes.get("#scorelines").textContent, /2.*1.*25\.125%.*0.*0.*0%/);
  assert.equal(h.nodes.get("#score-matrix").hidden, false);
  assert.match(h.nodes.get("#score-matrix").textContent, /Local.*Visitante.*25\.125%.*6\+/);
  const matrixTeams = h.nodes.get("#matrix-teams");
  assert.ok(matrixTeams, "matrix team reference is rendered before the score matrix");
  assert.equal(matrixTeams.hidden, false);
  assert.match(matrixTeams.textContent, /LOCAL.*Alpha.*VISITANTE.*Away/);
  assert.match(matrixTeams.attributes["aria-label"], /Local: Alpha.*Visitante: Away/);
  const matrixSection = h.nodes.get("#score-matrix").parentElement;
  assert.equal(matrixSection.children[matrixSection.children.indexOf(matrixTeams) + 1], h.nodes.get("#score-matrix"));
  assert.equal(h.nodes.get("#score-matrix").parentElement.parentElement.children[0], h.nodes.get("#score-matrix").parentElement, "the score matrix leads the live analysis");
  assert.match(h.nodes.get("#analytics-evidence").textContent, /fresh.*fresh.*envelope-v5.*2026-10-05/);
  assert.equal(h.nodes.get("#probability-summary").textContent, "Evolución de las probabilidades publicadas.");
  assert.equal(h.nodes.get("#activity-summary").textContent, "Actividad observada durante el partido.");
  const calls = h.nodes.get("#probability-history").calls;
  const start = calls.findIndex(c => c[0] === "strokeStyle" && c[1] === "#dfff54");
  assert.deepEqual(calls.slice(start).filter(c => ["moveTo", "lineTo"].includes(c[0])).map(c => c[0]), ["moveTo", "moveTo", "moveTo", "moveTo", "moveTo", "moveTo"]);
});

test("stale/suspended envelopes hide scenarios even when points remain fresh", async () => {
  for (const state of ["stale", "suspended"]) {
    const h = await harness(); await selected(h, history(A, state));
    assert.equal(h.nodes.get("#scenario-markets").hidden, true);
    assert.equal(h.nodes.get("#goals-distribution").hidden, true);
    assert.equal(h.nodes.get("#score-matrix").hidden, true);
    assert.equal(h.nodes.get("#scorelines").hidden, true);
    assert.match(h.nodes.get("#analytics-evidence").textContent, new RegExp(state));
    assert.match(h.nodes.get("#analytics-status").textContent, /escenarios/i);
    assert.equal(h.nodes.get("#team-stats").hidden, false);
  }
});

test("one/zero observations, absent scenarios, mismatched ID and resize never fabricate data", async () => {
  const h = await harness(); const payload = history(A, "degraded", [point(1, null)]);
  Object.assign(payload.item.analytics, { next_goal: null, markets: null, total_goals: [], scorelines: [], activity: [] });
  await selected(h, payload);
  assert.equal(h.nodes.get("#probability-summary").textContent, "Se necesitan al menos dos lecturas para mostrar una evolución.");
  assert.equal(h.nodes.get("#scenario-markets").hidden, true);
  assert.match(h.nodes.get("#analytics-status").textContent, /Dato no disponible/);
  const request = h.api.loadMatch(B); h.requests.at(-1).resolve({ item: summary(B) }); await h.flush();
  h.requests.at(-1).resolve(history(A)); await request;
  assert.equal(h.nodes.get("#analytics-retry").hidden, false);
  assert.equal(h.nodes.get("#team-stats").hidden, true);
  h.events.resize(); const resize = [...h.timers.values()].find(t => t.ms !== 8000); resize.fn();
  assert.equal(h.nodes.get("#team-stats").hidden, true);
  await selected(h, history(B, "fresh", []));
  assert.match(h.nodes.get("#analytics-status").textContent, /observaciones/i);
});

test("cancellation releases its timeout even when the transport ignores abort", async () => {
  const h = await harness(); const first = h.api.loadMatch(A);
  const old = h.requests.at(-1); old.ignoreAbort = true;
  const second = h.api.loadMatch(B); await h.flush();
  assert.equal(h.timers.size, 1, "only the current request owns an active timeout");
  h.requests.at(-1).resolve({ item: summary(B) }); await h.flush();
  h.requests.at(-1).resolve(history(B)); await second; await first;
});

test("manual refresh retries a failed summary rather than leaving an unrelated detail visible", async () => {
  const h = await harness(); await selected(h);
  const next = h.api.loadMatch(B); h.requests.at(-1).reject(Error("503")); await next;
  h.nodes.get("#refresh").listeners.click();
  assert.ok(h.requests.at(-1).url.endsWith(B), "Actualizar must retry the selected summary");
  h.requests.at(-1).resolve({ item: summary(B) }); await h.flush();
  h.requests.at(-1).resolve(history(B)); await h.flush();
  assert.match(h.nodes.get("#match-detail").textContent, /Beta/);
});

test("match list shows live minute and translated match state beside each score", async () => {
  const h = await harness();
  const refresh = h.api.loadLive();
  h.requests.at(-1).resolve({
    items: [
      { ...summary(A), minute: 63, status: "live" },
      { ...summary(B), minute: null, status: "half time" },
      { ...summary(A), minute: 90, status: "finished" },
      { ...summary(B), minute: null, status: "scheduled" },
    ],
    data_status: "fresh",
  });
  await refresh;
  const listText = h.nodes.get("#match-list").textContent;
  assert.match(listText, /63'/);
  assert.match(listText, /Descanso/);
  assert.match(listText, /Finalizado/);
  assert.match(listText, /Próximo/);
  assert.match(h.nodes.get("#match-list").children[0].className, /match-live/);
  assert.match(h.nodes.get("#match-list").children[1].className, /match-halftime/);
  assert.match(h.nodes.get("#match-list").children[2].className, /match-finished/);
  assert.match(h.nodes.get("#match-list").children[3].className, /match-scheduled/);
});

test("live analysis countdown resets after a polling refresh", async () => {
  const h = await harness();
  assert.match(h.nodes.get("#refresh-countdown")?.textContent || "", /20/);
  h.intervals[1]();
  assert.match(h.nodes.get("#refresh-countdown").textContent, /19/);
  h.intervals[0]();
  assert.match(h.nodes.get("#refresh-countdown").textContent, /20/);
});

test("both teams to score uses an accessible visual instead of generic market rows", async () => {
  const h = await harness(); await selected(h);
  const visual = h.nodes.get("#btts-visual");
  assert.equal(visual?.hidden, false);
  assert.match(visual?.textContent || "", /Sí, ambos marcan\s*10\s*\.00\s*%.*No, uno se queda sin marcar\s*90\s*\.00\s*%/);
  assert.match(visual?.attributes["aria-label"] || "", /sí, ambos marcan 10\.00%.*no, uno se queda sin marcar 90\.00%/i);
  assert.doesNotMatch(h.nodes.get("#market-probabilities").textContent, /Ambos marcan/);
});

test("visual decimal motion keeps the published probability available to assistive technology", async () => {
  const h = await harness(); await selected(h);
  const walk = node => [node, ...node.children.flatMap(walk)];
  const decimals = walk(h.nodes.get("#match-detail")).filter(node => node.className === "probability-visual-decimal");
  const bases = walk(h.nodes.get("#match-detail")).filter(node => node.className === "sr-only");
  assert.equal(decimals.length, 2, "only available 1X2 probabilities receive the visual decimal layer");
  assert.equal(decimals[1].attributes["aria-hidden"], "true");
  assert.match(bases.map(node => node.textContent).join(" "), /50%/);
  const before = decimals[1].textContent;
  h.intervals[2]();
  assert.notEqual(decimals[1].textContent, before, "only the decorative decimal digits change between provider reads");
});

test("both teams to score is placed immediately after the Monte Carlo matrix", async () => {
  const h = await harness(); await selected(h);
  const bttsSection = h.nodes.get("#btts-section");
  assert.ok(bttsSection, "BTTS has its own section");
  const flow = h.nodes.get("#score-matrix").parentElement.parentElement;
  const matrixIndex = flow.children.indexOf(h.nodes.get("#score-matrix").parentElement);
  assert.equal(flow.children[matrixIndex + 1], bttsSection);
  assert.equal(bttsSection.hidden, false);
});

test("verified events show chronological local/away match moments and safe crest fallback", async () => {
  const h = await harness(); await selected(h);
  const events = h.nodes.get("#match-events");
  assert.equal(h.nodes.get("#match-events-section").hidden, false);
  assert.match(events.textContent, /8'.*Gol.*Alpha.*29\+2'.*Tarjeta amarilla.*Away.*61'.*Tarjeta roja.*Away/);
  assert.equal(events.children.length, 3);
  assert.equal(events.children[0].attributes["aria-label"], "8': gol de Alpha.");
  const walk = node => [node, ...node.children.flatMap(walk)];
  const matrixImage = walk(h.nodes.get("#matrix-teams")).find(node => node.tagName === "img");
  assert.ok(matrixImage?.src?.startsWith("https://imagecache.365scores.com/"));
  matrixImage.listeners.error();
  assert.match(h.nodes.get("#matrix-teams").textContent, /AL/);
});

test("timeline uses incident symbols and detects shot increases between provider readings", async () => {
  const h = await harness();
  const points = [point(5), point(14), point(21)];
  points[0].stats.shots = { home: 1, away: 2 };
  points[1].stats.shots = { home: 3, away: 2 };
  points[2].stats.shots = { home: 3, away: 4 };

  await selected(h, history(A, "fresh", points));

  const events = h.nodes.get("#match-events");
  assert.match(events.textContent, /⚽.*Gol.*🟨.*Tarjeta amarilla.*🟥.*Tarjeta roja/);
  assert.match(events.textContent, /Tiros detectados \+2.*Alpha.*Tiros detectados \+2.*Away/);
  const shotRows = events.children.filter(row => /match-event-shot/.test(row.className));
  assert.equal(shotRows.length, 2);
  assert.equal(shotRows[0].attributes["aria-label"], "14': aumento detectado de 2 tiros de Alpha entre actualizaciones.");
});

test("timeline never treats an unavailable shot count as zero", async () => {
  const h = await harness();
  const points = [point(5), point(14)];
  points[0].stats.shots = { home: null, away: null };
  points[1].stats.shots = { home: 3, away: 2 };

  await selected(h, history(A, "fresh", points));

  const shotRows = h.nodes.get("#match-events").children.filter(row => /match-event-shot/.test(row.className));
  assert.equal(shotRows.length, 0);
});

test("event timeline clears rows for a newly selected match and states verified absence", async () => {
  const h = await harness(); await selected(h);
  assert.equal(h.nodes.get("#match-events").children.length, 3);
  await selected(h, history(B));
  assert.equal(h.nodes.get("#match-events-section").hidden, false);
  assert.equal(h.nodes.get("#match-events").textContent, "Sin eventos verificables publicados todavía.");
  assert.equal(h.nodes.get("#match-events").children.length, 1);
});

test("selected live match exposes broadcast badges and motion hooks", async () => {
  const h = await harness(); const payload = history();
  await selected(h, payload, { ...summary(A), status: "live", minute: 63 });
  assert.equal(h.nodes.get("#match-detail").dataset.liveState, "live");
  assert.equal(h.nodes.get(".analytics").dataset.liveState, "live");
  assert.match(h.nodes.get("#match-detail").textContent, /EN VIVO/);
  assert.match(h.nodes.get("#match-detail").children[1].children[1].className, /score-live/);
});

test("history retry works, invalid UUID never fetches, detail/list timeouts stay isolated", async () => {
  const h = await harness(); const count = h.requests.length;
  await h.api.loadMatch("not-a-uuid"); assert.equal(h.requests.length, count);
  const pending = h.api.loadMatch(A); h.requests.at(-1).resolve({ item: summary(A) }); await h.flush();
  h.requests.at(-1).reject(Error("503")); await pending;
  assert.equal(h.nodes.get("#analytics-retry").hidden, false);
  h.nodes.get("#analytics-retry").listeners.click(); h.requests.at(-1).resolve(history(A)); await h.flush();
  assert.equal(h.nodes.get("#analytics-retry").hidden, true);
  const listing = h.api.loadLive(); const listTimer = [...h.timers.values()].find(t => t.ms === 8000).fn;
  const selection = h.api.loadMatch(B); const current = h.requests.at(-1);
  listTimer(); await listing; assert.equal(current.signal.aborted, false);
  [...h.timers.values()].find(t => t.ms === 8000).fn(); await selection;
  assert.equal(h.nodes.get("#match-detail").attributes["aria-busy"], "false");
  assert.equal(h.nodes.get("#data-status").dataset.state, "error");
});

test("same-ID re-selection cannot accept an old generation and overlapping polls preserve pending work", async () => {
  const h = await harness(); const first = h.api.loadMatch(A); const old = h.requests.at(-1); old.ignoreAbort = true;
  const middle = h.api.loadMatch(B); const last = h.api.loadMatch(A); const current = h.requests.at(-1);
  h.intervals[0](); h.intervals[0](); assert.equal(current.signal.aborted, false);
  current.resolve({ item: summary(A) }); await h.flush(); const latestHistory = h.requests.at(-1);
  h.intervals[0](); assert.equal(latestHistory.signal.aborted, false);
  latestHistory.resolve(history(A)); await last;
  old.resolve({ item: { ...summary(A), home_name: "OLD GENERATION" } }); await first; await middle;
  assert.doesNotMatch(h.nodes.get("#match-detail").textContent, /OLD GENERATION/);
  const before = h.requests.length; document.hidden = true; h.intervals[0](); assert.equal(h.requests.length, before);
});

test("resize debounces a real canvas redraw and stale-to-fresh refresh restores individual panels", async () => {
  const h = await harness(); await selected(h, history(A, "stale"));
  const canvas = h.nodes.get("#probability-history"); const before = canvas.calls.length;
  h.events.resize(); h.events.resize();
  assert.equal(h.timers.size, 1); assert.equal(canvas.calls.length, before);
  const timer = [...h.timers.values()][0]; h.timers.clear(); timer.fn();
  assert.ok(canvas.calls.length > before);
  h.intervals[0](); h.requests.at(-1).resolve({ item: summary(A), data_status: "fresh" }); await h.flush();
  const pending = h.requests.at(-1);
  const payload = history(); payload.model_version = null; payload.item.model_version = "MUST NOT FALL BACK";
  payload.item.analytics.next_goal = null;
  pending.resolve(payload); await h.flush();
  assert.equal(h.nodes.get("#scenario-markets").hidden, false);
  assert.equal(h.nodes.get("#next-goal").parentElement.hidden, true);
  assert.equal(h.nodes.get("#market-probabilities").parentElement.hidden, false);
  assert.equal(h.nodes.get("#goals-distribution").hidden, false);
  assert.match(h.nodes.get("#analytics-evidence").textContent, /Modelo: Dato no disponible/);
  assert.doesNotMatch(h.nodes.get("#analytics-evidence").textContent, /MUST NOT FALL BACK/);
});

test("untrusted team names and labels remain literal semantic text", async () => {
  const h = await harness(); const payload = history();
  payload.item.analytics.total_goals[0].label = '<img src=x onerror="attack()">';
  await selected(h, payload);
  assert.match(h.nodes.get("#goals-distribution").textContent, /<img src=x/);
  assert.equal(h.nodes.get("#goals-distribution").children.some(n => n.tagName === "img"), false);
});

test("20s polling updates selected score and 1X2 before history without clearing readable analysis", async () => {
  const h = await harness(); await selected(h);
  const statsBefore = h.nodes.get("#team-stats").textContent;
  h.intervals[0](); await h.flush();
  const detail = h.requests.findLast(r => r.url === `/api/matches/${A}`);
  assert.notEqual(detail, h.requests[1], "poll must refresh the selected summary");
  assert.equal(h.nodes.get("#team-stats").hidden, false);
  assert.equal(h.nodes.get("#team-stats").textContent, statsBefore);
  detail.resolve({ item: { ...summary(A), score_home: 2, score_away: 1, minute: 65, probabilities: { home: 80.125, draw: 10, away: 9.875 } }, data_status: "fresh", model_version: "poll-model" });
  await h.flush();
  assert.match(h.nodes.get("#match-detail").textContent, /2—1.*65'.*Alpha 80\s*\.13\s*%.*Empate 10\s*\.00\s*%.*Away 9\s*\.88\s*%/);
  assert.match(h.nodes.get("#match-detail").children[1].children[1].className, /value-changed/);
  assert.match(h.nodes.get("#match-detail").children[2].children[0].children[1].className, /value-changed/);
  const refreshedHistory = h.requests.at(-1);
  assert.ok(refreshedHistory.url.endsWith("/history?limit=90"));
  assert.equal(h.nodes.get("#team-stats").hidden, false);
  const payload = history();
  payload.item.points.at(-1).stats.shots = { home: 12, away: 3 };
  payload.item.analytics.score_matrix = payload.item.analytics.score_matrix.map(row => row.home === "1" && row.away === "0" ? { ...row, probability: 26.125 } : row);
  refreshedHistory.resolve(payload); await h.flush();
  assert.match(h.nodes.get("#team-stats").textContent, /Local: 12.*Visitante: 3/);
  assert.match(h.nodes.get("#team-stats").children[1].className, /stat-changed/);
  assert.match(h.nodes.get("#score-matrix").children[2].children[1].className, /matrix-cell-changed/);
});

test("failed poll summary invalidates current evidence and hides simulations but preserves history", async () => {
  for (const failure of ["503", "timeout"]) {
    const h = await harness(); await selected(h);
    const stats = h.nodes.get("#team-stats").textContent;
    const summaryText = h.nodes.get("#probability-summary").textContent;
    h.intervals[0](); const request = h.requests.at(-1);
    if (failure === "503") request.reject(Error("503"));
    else [...h.timers.values()].at(-1).fn();
    await h.flush();
    assert.equal(h.nodes.get("#scenario-markets").hidden, true, "failed current summary cannot keep fresh simulations");
    assert.equal(h.nodes.get("#goals-distribution").hidden, true);
    assert.equal(h.nodes.get("#scorelines").hidden, true);
    assert.equal(h.nodes.get("#btts-visual").hidden, true);
    assert.doesNotMatch(h.nodes.get("#analytics-evidence").textContent, /Estado actual: fresh/);
    assert.match(h.nodes.get("#analytics-evidence").textContent, /Calidad histórica: fresh/);
    assert.equal(h.nodes.get("#team-stats").hidden, false);
    assert.equal(h.nodes.get("#team-stats").textContent, stats);
    assert.equal(h.nodes.get("#probability-history").hidden, false);
    assert.equal(h.nodes.get("#probability-summary").textContent, summaryText);
    assert.equal(h.nodes.get("#analytics-status").dataset.state, "error");
    h.events.resize(); [...h.timers.values()].find(t => t.ms === 150).fn();
    assert.equal(h.nodes.get("#scenario-markets").hidden, true);
    h.nodes.get("#analytics-retry").listeners.click();
    assert.equal(h.requests.at(-1).url, `/api/matches/${A}`, "failed polling generation needs summary retry even for the same ID");
    h.requests.at(-1).resolve({ item: summary(A), data_status: "fresh" }); await h.flush();
    assert.ok(h.requests.at(-1).url.endsWith("/history?limit=90"));
    h.requests.at(-1).resolve(history()); await h.flush();
    assert.equal(h.nodes.get("#scenario-markets").hidden, false);
    assert.match(h.nodes.get("#analytics-evidence").textContent, /Estado actual: fresh/);
  }
});

test("retry after B summary failure must replace A scoreboard before requesting B history", async () => {
  const h = await harness(); await selected(h);
  const failed = h.api.loadMatch(B); h.requests.at(-1).reject(Error("503")); await failed;
  assert.match(h.nodes.get("#match-detail").textContent, /Alpha/);
  h.nodes.get("#analytics-retry").listeners.click();
  assert.equal(h.requests.at(-1).url, `/api/matches/${B}`, "retry must request B summary, not B history over A scoreboard");
  h.requests.at(-1).reject(Error("503")); await h.flush();
  assert.equal(h.nodes.get("#team-stats").hidden, true);
  h.nodes.get("#analytics-retry").listeners.click();
  assert.equal(h.requests.at(-1).url, `/api/matches/${B}`);
  h.requests.at(-1).resolve({ item: summary(B), data_status: "fresh" }); await h.flush();
  assert.match(h.nodes.get("#match-detail").textContent, /Beta/);
  assert.doesNotMatch(h.nodes.get("#match-detail").textContent, /Alpha/);
  assert.equal(h.requests.at(-1).url, `/api/matches/${B}/history?limit=90`);
  h.requests.at(-1).resolve(history(B)); await h.flush();
  assert.equal(h.nodes.get("#team-stats").hidden, false);
});
