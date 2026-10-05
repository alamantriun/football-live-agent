from pathlib import Path
from html.parser import HTMLParser
import shutil
import subprocess


APP = Path("public/app")


def test_live_analytics_behavior():
    node = shutil.which("node")
    assert node, "Node is required for dashboard behavioral tests"
    result = subprocess.run(
        [node, "--test", "tests/e2e/public_flow_behavior.mjs"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_dashboard_history_contract_and_responsive_styles():
    javascript = (APP / "app.js").read_text(encoding="utf-8")
    assert 'from "./charts.js"' in javascript
    assert '"/history?limit=90"' in javascript
    for name in ("loadHistory", "renderAnalytics", "renderTeamStats", "renderScenarios", "renderAnalyticsState"):
        assert f"function {name}" in javascript
    for forbidden in ("innerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in javascript
    css = (APP / "app.css").read_text(encoding="utf-8")
    for selector in (".analytics-flow", ".stat-comparison", ".analysis-split", "[hidden]", "prefers-reduced-motion", "max-width:48rem"):
        assert selector in css


def test_dashboard_has_a_safe_same_origin_live_data_contract():
    html = (APP / "index.html").read_text(encoding="utf-8")
    javascript = (APP / "app.js").read_text(encoding="utf-8")

    assert 'id="match-list"' in html
    assert 'id="match-detail"' in html
    assert 'href="/"' in html and 'href="/modelo"' in html
    assert '"/api/live?limit=24"' in javascript
    assert '"/api/matches/"' in javascript
    assert "AbortController" in javascript
    assert "document.hidden" in javascript
    assert "innerHTML" not in javascript
    assert "insertAdjacentHTML" not in javascript
    assert "document.write" not in javascript
    assert "eval(" not in javascript


def test_dashboard_declares_all_live_data_states_and_safe_logo_fallback():
    javascript = (APP / "app.js").read_text(encoding="utf-8")
    css = (APP / "app.css").read_text(encoding="utf-8")

    for state in ("loading", "empty", "fresh", "degraded", "stale", "suspended", "error"):
        assert f'"{state}"' in javascript
    assert "URL(" in javascript
    assert "https:" in javascript
    assert "loading" in javascript
    assert "localStorage" in javascript
    assert "featuredClubLogos" in javascript
    assert "imagecache.365scores.com" in javascript
    assert "freebiesupply" not in javascript.lower()
    match_list = javascript.split("function renderMatchList", 1)[1].split("function probabilityCard", 1)[0]
    assert "clubMark(item.home_name, item.home_logo_url)" in match_list
    assert "clubMark(item.away_name, item.away_logo_url)" in match_list
    assert ".match-clubs" in css


def test_dashboard_includes_accessible_live_analysis_surfaces():
    html = (APP / "index.html").read_text(encoding="utf-8")
    assert (APP / "charts.js").is_file(), "Task 4 chart renderer is missing"
    javascript = (APP / "charts.js").read_text(encoding="utf-8")
    elements = {}

    class Elements(HTMLParser):
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if "id" in attrs:
                assert attrs["id"] not in elements, "Duplicate DOM anchor"
                elements[attrs["id"]] = (tag, attrs)

    Elements().feed(html)
    for element_id in (
        "analytics-status", "probability-history", "team-stats",
        "activity-chart", "scenario-markets", "goals-distribution",
        "scorelines", "score-matrix", "analytics-evidence",
    ):
        assert element_id in elements
        assert "hidden" in elements[element_id][1]
    assert "score-matrix-summary" in elements
    assert elements["analytics-status"][1]["role"] == "status"
    for element_id in ("probability-history", "activity-chart"):
        tag, attrs = elements[element_id]
        assert tag == "canvas"
        assert attrs["role"] == "img"
        assert attrs["aria-label"]
        assert attrs["aria-describedby"] in elements
    assert elements["team-stats"][0] == "dl"
    assert elements["scorelines"][0] == "ol"
    assert elements["score-matrix"][0] == "div"
    assert elements["score-matrix"][1]["role"] == "table"
    assert elements["score-matrix"][1]["aria-describedby"] == "score-matrix-summary"
    assert 'type="module" src="/app/app.js"' in html
    for export in ("drawLineChart", "drawBarChart", "clearChart"):
        assert f"export function {export}" in javascript
    assert "devicePixelRatio" in javascript
    assert "prefers-reduced-motion" in javascript
    for forbidden in ("innerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in javascript
    css = (APP / "app.css").read_text(encoding="utf-8")
    for selector in (".analytics-flow", ".stat-comparison", ".analysis-split", "[hidden]", ":focus-visible"):
        assert selector in css


def test_chart_renderer_handles_gaps_scaling_percentages_and_empty_states():
    """Recording Canvas boundary catches bridged gaps, stale pixels and bad geometry."""
    assert (APP / "charts.js").is_file(), "Task 4 chart renderer is missing"
    node = shutil.which("node")
    assert node, "Node is required to exercise the ES-module renderer"
    result = subprocess.run([node, "--input-type=module", "-e", r'''
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
const source = readFileSync("public/app/charts.js", "utf8");
const charts = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
globalThis.devicePixelRatio = 2;
let motionChecks = 0;
globalThis.matchMedia = query => {
  assert.equal(query, "(prefers-reduced-motion: reduce)");
  motionChecks++;
  return { matches: true };
};
globalThis.requestAnimationFrame = () => { throw Error("Unexpected animation"); };
const calls = [];
const context = new Proxy({}, {
  get: (_, method) => (...args) => {
    for (const value of args) if (typeof value === "number") assert.ok(Number.isFinite(value));
    calls.push([method, ...args]);
  },
  set: (_, key, value) => { calls.push([key, value]); return true; },
});
const canvas = {
  width: 0, height: 0,
  getBoundingClientRect: () => ({ width: 400, height: 240 }),
  getContext: () => context,
};
charts.drawLineChart(canvas, [{ label: "Home", color: "#dfff54", values: [0, null, 100, 50, NaN, Infinity] }]);
assert.equal(canvas.width, 800);
assert.equal(canvas.height, 480);
assert.ok(calls.some(c => c[0] === "setTransform" && c[1] === 2));
assert.ok(calls.some(c => c[0] === "clearRect"));
const seriesStart = calls.findIndex(c => c[0] === "strokeStyle" && c[1] === "#dfff54");
const path = calls.slice(seriesStart).filter(c => ["moveTo", "lineTo"].includes(c[0]));
assert.deepEqual(path.map(c => c[0]), ["moveTo", "moveTo", "lineTo"]);
assert.ok(calls.some(c => c[0] === "lineWidth" && c[1] === 2));
assert.ok(calls.some(c => c[0] === "fillText" && String(c[1]).includes("100")));
calls.length = 0;
charts.drawBarChart(canvas, [{ label: "Home", probability: 25.125 }, { label: "Draw", probability: 0 }, { label: "Away", probability: 50.25 }]);
const bars = calls.filter(c => c[0] === "fillRect");
assert.equal(bars.length, 3);
assert.equal(bars[1][3], 0);
assert.equal(bars[2][3], bars[0][3] * 2);
assert.ok(calls.some(c => c[0] === "fillText" && c[1] === "25.125%"));
calls.length = 0;
charts.clearChart(canvas, "Dato no disponible");
assert.ok(calls.some(c => c[0] === "clearRect"));
assert.ok(calls.some(c => c[0] === "fillText" && c[1] === "Dato no disponible" && c[2] === 200 && c[3] === 120));
for (const values of [[], [null, NaN, Infinity], [50], [0, 0], [-10, 10]]) {
  calls.length = 0;
  charts.drawLineChart(canvas, [{ label: "Test", values }]);
  assert.ok(calls.some(c => c[0] === "clearRect"));
}
canvas.getBoundingClientRect = () => ({ width: 0, height: 0 });
charts.drawLineChart(canvas, [{ values: [1, 2] }]);
assert.ok(canvas.width > 0 && canvas.height > 0);
assert.ok(motionChecks > 0);
'''], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
