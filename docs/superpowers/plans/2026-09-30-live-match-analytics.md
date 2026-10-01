# Live Match Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore real, persisted live charts and match statistics inside the production `/app` experience without exposing private Supabase data or overstating model accuracy.

**Architecture:** FastAPI resolves a public match UUID, reads a bounded private history through the server-only Supabase gateway, validates an allowlisted history contract, and computes transparent analytics in a pure Python module. The existing public dashboard requests that same-origin endpoint on match selection and renders first-party canvas charts plus semantic HTML comparisons; no browser Supabase client, public history table, CDN chart library, or in-memory SSE server is introduced.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, supabase-py/PostgREST, pytest, vanilla ES modules, Canvas 2D, HTML/CSS, Vercel.

**Spec:** `docs/superpowers/specs/2026-09-30-live-match-analytics-design.md`

## Global Constraints

- Preserve `null` for absent statistics; never convert missing observations to zero or forward-fill them.
- Expose at most 90 chronological observations per request.
- Never expose private UUIDs, provider fixture identifiers, `sanitized_provider_data`, job data, service-role credentials, or model parameters.
- Keep all Supabase access server-side with the existing service-role environment variable.
- Use same-origin API requests and first-party JavaScript only; add no CDN script or runtime dependency.
- Keep the approved dark-green, lime, yellow, and white visual language and reduce decorative borders.
- Label observed activity, momentum, next goal, and goal markets as experimental scenarios rather than validated accuracy claims.
- Do not claim that this feature improves predictive accuracy.

---

### Task 1: Pure live-analytics calculations

**Files:**
- Create: `football_live/live_analytics.py`
- Create: `tests/test_live_analytics.py`

**Interfaces:**
- Consumes: chronological history rows shaped as `dict[str, Any]` with `minute`, `provider_observed_at`, `stats`, `probabilities`, `lambda_adjusted`, and current score.
- Produces: `build_live_analytics(points: Sequence[dict[str, Any]]) -> dict[str, Any]`.
- Produces keys: `activity`, `momentum`, `next_goal`, `markets`, `total_goals`, `scorelines`, and `coverage` exactly as specified in the design.

- [ ] **Step 1: Write failing tests for activity, missing values, and momentum**

```python
from datetime import datetime, timedelta, timezone

from football_live.live_analytics import build_live_analytics


NOW = datetime(2026, 9, 30, 20, 0, tzinfo=timezone.utc)


def point(index, home_shots, away_shots, home_on_target, away_on_target):
    return {
        "minute": 50 + index,
        "provider_observed_at": NOW + timedelta(minutes=index),
        "score_home": 1,
        "score_away": 0,
        "stats": {
            "possession": {"home": 55.0, "away": 45.0},
            "shots": {"home": home_shots, "away": away_shots},
            "shots_on_target": {"home": home_on_target, "away": away_on_target},
            "corners": {"home": None, "away": None},
            "yellow_cards": {"home": 1, "away": 2},
            "red_cards": {"home": 0, "away": 0},
            "expected_goals": {"home": None, "away": None},
        },
        "probabilities": {"home": 60.0, "draw": 25.0, "away": 15.0},
        "lambda_adjusted": {"home": 1.4, "away": 0.6},
    }


def test_activity_uses_recent_cumulative_deltas_and_preserves_missing_signals():
    points = [
        point(0, 5, 4, 2, 1),
        point(1, 6, 4, 3, 1),
        point(2, 7, 5, 4, 1),
        point(3, 8, 5, 5, 1),
    ]

    analytics = build_live_analytics(points)

    assert analytics["activity"][-1]["home"] > analytics["activity"][-1]["away"]
    assert analytics["momentum"][-1]["home"] > 50.0
    assert analytics["coverage"] == {"available": 10, "total": 14, "percent": 71.43}


def test_zero_activity_falls_back_to_possession_but_absent_data_stays_null():
    same = [point(0, 5, 4, 2, 1), point(1, 5, 4, 2, 1)]
    analytics = build_live_analytics(same)
    assert analytics["activity"][-1]["home"] == 55.0
    assert analytics["activity"][-1]["away"] == 45.0

    missing = point(2, None, None, None, None)
    missing["stats"]["possession"] = {"home": None, "away": None}
    analytics = build_live_analytics([missing])
    assert analytics["activity"][-1]["home"] is None
    assert analytics["activity"][-1]["away"] is None
```

- [ ] **Step 2: Run the activity tests and verify RED**

Run: `python -m pytest tests/test_live_analytics.py -q`

Expected: collection fails because `football_live.live_analytics` does not exist.

- [ ] **Step 3: Implement bounded activity, momentum, and coverage helpers**

Create `football_live/live_analytics.py` with these exact public and private interfaces:

```python
from __future__ import annotations

from collections.abc import Sequence
from math import exp, factorial, isfinite
from typing import Any


STAT_KEYS = (
    "possession", "shots", "shots_on_target", "corners",
    "yellow_cards", "red_cards", "expected_goals",
)
ACTIVITY_WEIGHTS = {
    "shots": 1.0,
    "shots_on_target": 2.0,
    "corners": 0.5,
    "expected_goals": 3.0,
}


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


def _non_negative_delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return max(0.0, current - previous)


def build_live_analytics(points: Sequence[dict[str, Any]]) -> dict[str, Any]:
    bounded = list(points)[-90:]
    activity = _activity_series(bounded)
    return {
        "activity": activity,
        "momentum": _momentum_series(activity),
        "next_goal": _next_goal(bounded),
        "markets": _goal_markets(bounded),
        "total_goals": _total_goal_distribution(bounded),
        "scorelines": _scoreline_distribution(bounded),
        "coverage": _coverage(bounded),
    }
```

Implement `_activity_series` using the last five observations ending at each point. For each available weighted cumulative stat, calculate `max(0, current - oldest)`; exclude a stat entirely when either endpoint is missing. Divide the two side scores into a 0–100 share. When both scores are zero, use the current possession pair if both values are finite and sum above zero; otherwise return `None` for both sides. Round output to two decimals.

Implement `_momentum_series` with `None` until four valid activity points exist, then calculate each side as `clamp(50 + (current_share - share_three_valid_points_ago) / 2)` and round to two decimals.

Implement `_coverage` over all seven stat pairs in the latest point only: `total=14`, `available` counts finite home and away values, and `percent=round(available / total * 100, 2)`. Coverage describes the current observation rather than every historical point.

- [ ] **Step 4: Add failing tests for Poisson scenarios and invalid rates**

```python
def test_poisson_outputs_are_bounded_normalized_and_sorted():
    analytics = build_live_analytics([point(0, 5, 4, 2, 1)])

    assert round(sum(analytics["next_goal"].values()), 6) == 100.0
    assert round(analytics["markets"]["over_2_5"] + analytics["markets"]["under_2_5"], 6) == 100.0
    assert round(analytics["markets"]["btts_yes"] + analytics["markets"]["btts_no"], 6) == 100.0
    assert analytics["total_goals"][-1]["label"] == "7+"
    assert len(analytics["scorelines"]) == 5
    probabilities = [row["probability"] for row in analytics["scorelines"]]
    assert probabilities == sorted(probabilities, reverse=True)


def test_invalid_or_absent_rates_hide_model_scenarios():
    invalid = point(0, 5, 4, 2, 1)
    invalid["lambda_adjusted"] = {"home": -1.0, "away": float("nan")}

    analytics = build_live_analytics([invalid])

    assert analytics["next_goal"] is None
    assert analytics["markets"] is None
    assert analytics["total_goals"] == []
    assert analytics["scorelines"] == []
```

- [ ] **Step 5: Run the scenario tests and verify RED**

Run: `python -m pytest tests/test_live_analytics.py -q`

Expected: activity tests pass and scenario tests fail because the Poisson helpers return no results.

- [ ] **Step 6: Implement the Poisson scenario helpers**

Use `P(X=k)=exp(-lambda) * lambda**k / factorial(k)` with final-score grids bounded to 0–10 additional goals per side. Combine the omitted probability tail into `7+` for total goals. Use the current score plus remaining-goal distributions for Over/Under 2.5 and BTTS. Use the total remaining rate to compute next-goal outcomes: `none=exp(-(home+away))`, and split the complementary probability proportionally between home and away. Convert all public probabilities to percentages rounded to two decimals and normalize complementary pairs so each pair sums to exactly `100.0` after rounding.

- [ ] **Step 7: Run analytics tests and commit**

Run: `python -m pytest tests/test_live_analytics.py -q`

Expected: all analytics tests pass.

```bash
git add football_live/live_analytics.py tests/test_live_analytics.py
git commit -m "feat: calculate transparent live match analytics"
```

---

### Task 2: Bounded private match-history repository

**Files:**
- Modify: `football_live/repository.py`
- Modify: `football_live/supabase_gateway.py`
- Modify: `tests/test_repository.py`

**Interfaces:**
- Consumes: `public_id: UUID`, `limit: int`.
- Produces: `Repository.public_match_history(public_id: UUID, limit: int = 90) -> dict | None`.
- Produces a safe dictionary with `public_id`, team names, and chronological `points`; internal IDs are used only during the merge and removed before return.

- [ ] **Step 1: Write failing repository tests for safe selection and chronology**

Add fixtures for three snapshot rows returned newest-first and their matching prediction rows. Add:

```python
def test_public_match_history_uses_private_allowlist_and_returns_chronological_points(
    gateway, fake_client
):
    fake_client.responses[("private", "table", "fixtures")] = [{
        "id": str(FIXTURE_ID),
        "public_id": "337a09f3-a806-4e56-a068-d758f74a78cb",
        "home_name": "Arsenal",
        "away_name": "Chelsea",
    }]
    fake_client.responses[("private", "table", "live_snapshots")] = history_snapshot_rows()
    fake_client.responses[("private", "table", "predictions")] = history_prediction_rows()

    result = gateway.public_match_history(
        UUID("337a09f3-a806-4e56-a068-d758f74a78cb"), limit=500
    )

    assert [point["minute"] for point in result["points"]] == [60, 61, 62]
    assert result["points"][-1]["stats"]["shots_on_target"] == {"home": 4, "away": 2}
    assert "id" not in result["points"][-1]
    assert "snapshot_id" not in result["points"][-1]
    assert "sanitized_provider_data" not in str(result)
    fixture_query, snapshot_query, prediction_query = fake_client.operations[-3:]
    assert fixture_query["filters"] == [("eq", "public_id", str(result["public_id"]))]
    assert snapshot_query["limit"] == 90
    assert prediction_query["limit"] == 90
```

Add a second test where the fixture response is empty and assert the method returns `None` without snapshot or prediction queries.

- [ ] **Step 2: Run repository tests and verify RED**

Run: `python -m pytest tests/test_repository.py -q -k "public_match_history"`

Expected: failure because `SupabaseGateway.public_match_history` does not exist.

- [ ] **Step 3: Implement the protocol and three bounded PostgREST queries**

Add to `Repository`:

```python
def public_match_history(self, public_id: UUID, limit: int = 90) -> dict | None:
    raise NotImplementedError
```

Add explicit select constants:

```python
HISTORY_FIXTURE_SELECT = "id,public_id,home_name,away_name"
HISTORY_SNAPSHOT_SELECT = (
    "id,minute,score_home,score_away,normalized_stats,"
    "provider_observed_at,collected_at,quality"
)
HISTORY_PREDICTION_SELECT = (
    "snapshot_id,probabilities,lambda_adjusted,created_at"
)
HISTORY_STAT_KEYS = (
    "possession", "shots", "shots_on_target", "corners",
    "yellow_cards", "red_cards", "expected_goals",
)
```

Implement fixture lookup by `public_id` with `.limit(1)`. If found, query snapshots by internal `fixture_id`, ordered `provider_observed_at desc`, limited with `_cap(limit, 90)`. Query predictions by the same fixture, ordered `created_at desc`, with the same cap. Keep only the first prediction per `snapshot_id`, construct allowlisted point dictionaries, and reverse the snapshot rows so output is chronological. Normalize every stat to `{home, away}` with missing sides set to `None`; reject malformed non-dictionary stat payloads with `RepositoryUnavailable(PUBLIC_ERROR)`.

- [ ] **Step 4: Run repository tests and commit**

Run: `python -m pytest tests/test_repository.py -q`

Expected: all repository tests pass.

```bash
git add football_live/repository.py football_live/supabase_gateway.py tests/test_repository.py
git commit -m "feat: expose bounded private match history"
```

---

### Task 3: Validated public history API

**Files:**
- Modify: `football_live/api.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: `Repository.public_match_history` and `build_live_analytics`.
- Produces: `GET /api/matches/{public_id}/history?limit=90` with `MatchHistoryEnvelope`.

- [ ] **Step 1: Write failing API tests for success and validation**

Extend `FakeRepository` with `history_calls` and `public_match_history`. Add:

```python
def test_match_history_returns_safe_points_and_derived_analytics(client, repository):
    response = client.get(
        f"/api/matches/{PUBLIC_ID}/history?limit=30",
        headers={"X-Request-ID": str(REQUEST_ID)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == str(REQUEST_ID)
    assert body["item"]["public_id"] == str(PUBLIC_ID)
    assert body["item"]["analytics"]["activity"]
    assert "provider_fixture_id" not in response.text
    assert "sanitized_provider_data" not in response.text
    assert repository.history_calls == [(PUBLIC_ID, 30)]


def test_match_history_rejects_invalid_limit_before_repository_query(client, repository):
    assert client.get(f"/api/matches/{PUBLIC_ID}/history?limit=91").status_code == 422
    assert repository.history_calls == []


def test_match_history_returns_404_for_unknown_public_match(client):
    response = client.get(f"/api/matches/{UUID(int=999)}/history")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
```

Extend the existing unavailable-repository test to call the history route and assert the body contains neither traceback text nor the repository exception.

- [ ] **Step 2: Run API tests and verify RED**

Run: `python -m pytest tests/test_api.py -q -k "match_history"`

Expected: `404` from the absent route or fake-repository attribute failure.

- [ ] **Step 3: Add strict history response models and route**

In `football_live/api.py`, define strict Pydantic models matching the spec: `HistoryStatPair`, `HistoryStats`, `HistoryPoint`, `TrendPoint`, `LiveAnalyticsResponse`, `MatchHistory`, and `MatchHistoryEnvelope`. Use bounded numeric fields and aware datetimes. The route must validate `1 <= limit <= 90`, call the repository once, raise `ApiError(404, "not_found", "El partido no está disponible.")` for `None`, calculate analytics from validated chronological points, derive envelope status from the latest point, build `MatchHistoryEnvelope`, and return `_json(200, envelope, public_cache=True)`.

Route signature:

```python
@app.get(
    "/api/matches/{public_id}/history",
    response_model=MatchHistoryEnvelope,
)
async def match_history(
    public_id: UUID,
    request: Request,
    limit: int = 90,
) -> JSONResponse:
```

- [ ] **Step 4: Run API tests and commit**

Run: `python -m pytest tests/test_api.py -q`

Expected: all API tests pass.

```bash
git add football_live/api.py tests/test_api.py
git commit -m "feat: add safe live analytics API"
```

---

### Task 4: First-party accessible chart renderer

**Files:**
- Create: `public/app/charts.js`
- Modify: `public/app/index.html`
- Modify: `public/app/app.css`
- Modify: `tests/e2e/test_public_flow.py`

**Interfaces:**
- Produces ES-module exports `drawLineChart(canvas, series, options)`, `drawBarChart(canvas, rows, options)`, and `clearChart(canvas, message)`.
- Canvas `series` uses `{label, color, values}` where each value is `number | null`.

- [ ] **Step 1: Write failing frontend contract tests for analysis structure and renderer**

```python
def test_dashboard_includes_accessible_live_analysis_surfaces():
    html = (APP / "index.html").read_text(encoding="utf-8")
    javascript = (APP / "charts.js").read_text(encoding="utf-8")

    for element_id in (
        "analytics-status", "probability-history", "team-stats",
        "activity-chart", "scenario-markets", "goals-distribution",
        "scorelines", "analytics-evidence",
    ):
        assert f'id="{element_id}"' in html
    assert 'type="module"' in html
    assert "export function drawLineChart" in javascript
    assert "devicePixelRatio" in javascript
    assert "prefers-reduced-motion" in javascript
    assert "innerHTML" not in javascript
    assert "eval(" not in javascript
```

- [ ] **Step 2: Run the frontend contract test and verify RED**

Run: `python -m pytest tests/e2e/test_public_flow.py -q`

Expected: failure because `charts.js` and analysis surface IDs do not exist.

- [ ] **Step 3: Add semantic analysis markup and CSS**

Append one `section.analytics` after the existing detail grid, with a heading, `role="status"` element, two full-width canvases, semantic team-stat rows, scenario regions, goal-distribution region, scoreline list, and evidence footer using the exact IDs in the test. Keep every region initially hidden with the `hidden` attribute until a match is selected.

Change the script to:

```html
<script type="module" src="/app/app.js"></script>
```

Add responsive styles that use a wide `analytics-flow`, `analysis-split` only for scenario/distribution pairs, no page-level horizontal overflow, visible focus, and minimum 44-pixel retry targets. Reuse the current tokens. Use spacing and typographic headings before borders; charts receive only a subtle top rule.

- [ ] **Step 4: Implement the canvas renderer**

`drawLineChart` must resize the backing store by `devicePixelRatio`, clear before every render, calculate finite min/max values, leave gaps for `null`, render labelled horizontal guides, and draw series with two-pixel strokes. `drawBarChart` renders proportional horizontal bars and exact percentage labels. `clearChart` clears and draws a centered state message. Respect `matchMedia("(prefers-reduced-motion: reduce)")`; the renderer must never schedule animation when reduced motion is active.

- [ ] **Step 5: Run frontend contract tests and commit**

Run: `python -m pytest tests/e2e/test_public_flow.py -q`

Expected: all public-flow tests pass.

```bash
git add public/app/charts.js public/app/index.html public/app/app.css tests/e2e/test_public_flow.py
git commit -m "feat: add accessible live analytics surfaces"
```

---

### Task 5: Connect history, statistics, and scenarios to match selection

**Files:**
- Modify: `public/app/app.js`
- Modify: `public/app/app.css`
- Modify: `tests/e2e/test_public_flow.py`

**Interfaces:**
- Consumes: `/api/matches/{public_id}/history?limit=90` and chart exports from `./charts.js`.
- Produces: `loadHistory(id)`, `renderAnalytics(item)`, `renderTeamStats(point)`, `renderScenarios(analytics)`, and `renderAnalyticsState(state, message)`.

- [ ] **Step 1: Write failing integration-contract tests**

```python
def test_dashboard_fetches_and_renders_real_history_without_dom_injection():
    javascript = (APP / "app.js").read_text(encoding="utf-8")

    assert 'from "./charts.js"' in javascript
    assert '"/history?limit=90"' in javascript
    assert "function renderAnalytics" in javascript
    assert "function renderTeamStats" in javascript
    assert "function renderScenarios" in javascript
    assert "historyController.abort()" in javascript
    assert "Dato no disponible" in javascript
    assert "Índice experimental" in javascript
    for forbidden in ("innerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in javascript
```

Add CSS assertions for the mobile breakpoint, `.analytics-flow`, `.stat-comparison`, `.analysis-split`, `[hidden]`, and `prefers-reduced-motion`.

- [ ] **Step 2: Run the integration-contract tests and verify RED**

Run: `python -m pytest tests/e2e/test_public_flow.py -q`

Expected: failure because history loading and analytics renderers are absent.

- [ ] **Step 3: Implement abortable history loading**

Import chart functions at the top of `app.js`. Add a separate `historyController` so list polling does not cancel selected-match history. `loadMatch` must render the latest summary first, then await `loadHistory(id)`. On selection change, abort the previous history request. Use the exact same UUID guard and eight-second timeout behavior as current public requests.

`loadHistory` requests:

```javascript
`/api/matches/${encodeURIComponent(id)}/history?limit=90`
```

It must map aborts to no visible error, other failures to the retry state, and reject a response whose `item.public_id` differs from the requested ID.

- [ ] **Step 4: Render real series and partial states**

`renderAnalytics` must:

- draw home/draw/away probability history with the existing lime/yellow/red palette;
- render current stat comparisons for all seven allowlisted stat pairs and use an em dash plus `Dato no disponible` when either side is `null`;
- draw observed activity and momentum as separate labelled series with “Índice experimental” text;
- omit scenario panels when API values are `null` and explain why;
- render next-goal and market percentages with semantic rows and exact values;
- render total goals and scorelines as HTML bars sorted by API order;
- render coverage, quality, model version, and observation time in the evidence footer;
- explain that one observation can show current stats but cannot form a trend.

Use `replaceChildren`, `textContent`, `createElement`, and `setAttribute` only. Re-render canvases on a debounced `resize` event, and remove stale chart state when selection changes.

- [ ] **Step 5: Run frontend tests and commit**

Run: `python -m pytest tests/e2e/test_public_flow.py -q`

Expected: all public-flow tests pass.

```bash
git add public/app/app.js public/app/app.css tests/e2e/test_public_flow.py
git commit -m "feat: render persisted live match analytics"
```

---

### Task 6: Full verification, production release, and browser QA

**Files:**
- Verify: `public/app/app.css`
- Verify: `public/app/app.js`
- Verify: `football_live/api.py`
- Verify: the focused tests from Tasks 1–5

No source change is planned in this task. Any observed defect reopens its owning task with a new failing regression test before the production correction.

**Interfaces:**
- Consumes all deliverables from Tasks 1–5.
- Produces a Vercel `READY` deployment on `https://football-live-agent.vercel.app/app`.

- [ ] **Step 1: Run the complete automated suite**

Run: `python -m pytest -q`

Expected: every test passes; the existing Starlette/httpx deprecation warning may remain, but there are zero failures.

- [ ] **Step 2: Run static and repository checks**

Run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors and only intended analytics files changed.

- [ ] **Step 3: Push the implementation branch**

```powershell
git push origin codex/football-live-product
```

Expected: the remote branch advances to the verified implementation commit.

- [ ] **Step 4: Deploy explicitly to production**

Run: `npx.cmd vercel deploy --prod --yes`

Expected: deployment JSON reports `readyState: READY`, `target: production`, and alias `https://football-live-agent.vercel.app`.

- [ ] **Step 5: Verify backend behavior through production logs**

Run: `npx.cmd vercel logs --environment production --since 5m --expand --limit 200`

Expected: history requests return successful private fixture/snapshot/prediction reads; logs contain no credential values, tracebacks, or raw provider payloads.

- [ ] **Step 6: Verify the original user journey in the browser**

Open `/app`, select a live match, and verify the following visible outcomes:

- match summary and 1X2 probabilities remain visible;
- probability history uses at least two real points when history exists;
- missing provider statistics render as unavailable rather than zero;
- current team statistics, observed activity, momentum, scenarios, distributions, and evidence are present when supported;
- changing the selected match replaces all analytics;
- stale/suspended data hides simulations;
- retry recovers from a simulated request failure;
- no console errors occur.

Repeat at a desktop width and at a mobile width near 390 pixels. Confirm no clipping, overlap, distorted logos, illegible labels, or horizontal page scrolling. If a defect is found, add a failing focused test before changing code, then rerun the complete suite.

- [ ] **Step 7: Final repository and deployment evidence**

Run:

```powershell
git status --branch --short
git log -5 --oneline
```

Expected: branch is synchronized with `origin/codex/football-live-product`, the worktree is clean, and the implementation commits are visible.
