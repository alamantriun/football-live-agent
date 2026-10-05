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
const summary = id => ({ public_id: id, home_name: id === A ? "Alpha" : "Beta", away_name: "Away", score_home: 0, score_away: 0, probabilities: { home: null, draw: 0, away: 50 } });
const point = (minute, probabilities = { home: 25.125, draw: 0, away: 74.875 }) => ({
  minute, score_home: 0, score_away: 0, provider_observed_at: "2026-10-05T10:00:00Z",
  collected_at: null, quality: "fresh", stats: { shots: { home: 0, away: null }, possession: { home: 60, away: 40 } },
  probabilities, lambda_adjusted: { home: 1, away: 1 }, prediction_created_at: null,
});
const history = (id = A, state = "fresh", points = [point(1), point(2, null), point(3)]) => ({
  request_id: A, generated_at: "2026-10-05T10:00:00Z", data_status: state, model_version: "envelope-v5",
  item: { ...summary(id), points, analytics: {
    activity: [{ minute: 1, observed_at: "t1", home: 60, away: 40 }, { minute: 2, observed_at: "t2", home: null, away: null }, { minute: 3, observed_at: "t3", home: 70, away: 30 }],
    momentum: [], next_goal: { home: 25.125, none: 0, away: 74.875 },
    markets: { over_2_5: 30, under_2_5: 70, btts_yes: 10, btts_no: 90 },
    total_goals: [{ label: "0", probability: 0 }, { label: "7+", probability: 25.125 }],
    scorelines: [{ home: 2, away: 1, probability: 25.125 }, { home: 0, away: 0, probability: 0 }],
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
async function selected(h, payload = history()) {
  const promise = h.api.loadMatch(payload.item.public_id);
  h.requests.at(-1).resolve({ item: summary(payload.item.public_id), data_status: "fresh", model_version: "detail-v" }); await h.flush();
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
  assert.match(h.nodes.get("#analytics-evidence").textContent, /fresh.*fresh.*envelope-v5.*2026-10-05/);
  assert.match(h.nodes.get("#probability-summary").textContent, /huecos|ausentes/i);
  assert.match(h.nodes.get("#activity-summary").textContent, /Índice experimental/);
  const calls = h.nodes.get("#probability-history").calls;
  const start = calls.findIndex(c => c[0] === "strokeStyle" && c[1] === "#dfff54");
  assert.deepEqual(calls.slice(start).filter(c => ["moveTo", "lineTo"].includes(c[0])).map(c => c[0]), ["moveTo", "moveTo", "moveTo", "moveTo", "moveTo", "moveTo"]);
});

test("stale/suspended envelopes hide scenarios even when points remain fresh", async () => {
  for (const state of ["stale", "suspended"]) {
    const h = await harness(); await selected(h, history(A, state));
    assert.equal(h.nodes.get("#scenario-markets").hidden, true);
    assert.equal(h.nodes.get("#goals-distribution").hidden, true);
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
  assert.match(h.nodes.get("#probability-summary").textContent, /observaci[oó]n.*tendencia/i);
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
  h.intervals[0](); const pending = h.requests.at(-1);
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
