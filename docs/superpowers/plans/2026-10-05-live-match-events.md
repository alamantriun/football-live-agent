# Live Match Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish verified match incidents, show genuine club and national-team crests before the score matrix, and simplify live-analysis copy without changing predictions.

**Architecture:** The existing 365Scores detail response supplies raw events to a strict parser. A private RLS-protected table stores canonical events idempotently, and the existing same-origin history endpoint returns a bounded public-safe timeline plus crest URLs. The vanilla frontend renders only validated API data.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, supabase-py/PostgREST, PostgreSQL/Supabase, pytest, pgTAP, vanilla ES modules, HTML/CSS, Vercel.

**Spec:** `docs/superpowers/specs/2026-10-05-live-match-events-design.md`

## Global Constraints

- Do not change prediction algorithms, published probabilities, Monte Carlo values, training, or accuracy claims.
- Persist and render only provider-verified goals, yellow cards, and red cards. Never infer incidents or fouls from aggregate stats.
- Keep browser access same-origin. Do not expose raw provider/player data, private identifiers, or credentials.
- Use explicit column allowlists, bounded reads, RLS, and revoked anon/authenticated privileges for private events.
- Preserve the existing crest-host allowlist plus an accessible initials fallback on a failed or unavailable image.
- Use DOM construction APIs only; do not introduce HTML injection APIs or `eval`.
- Keep copy exact and concise: `¿Anotan ambos equipos?`, `Sí, ambos marcan`, `No, uno se queda sin marcar`, and `Sin eventos verificables publicados todavía.`
- Every behavior begins with a focused test that is observed failing before production code is added.

---

### Task 1: Parse canonical provider events

**Files:**
- Modify: `football_live/domain.py`
- Modify: `football_live/provider.py`
- Modify: `tests/test_provider.py`
- Create: `tests/fixtures/365scores/game_events.json`

**Interfaces:**
- Produces `MatchEvent(provider_order: int, minute: int, added_time: int | None, side: Literal["home", "away"], kind: Literal["goal", "yellow_card", "red_card"])`.
- Extends `ProviderSnapshot` with `events: tuple[MatchEvent, ...] = ()`.
- Adds `ProviderAdapter.parse_events(game, home_id, away_id) -> tuple[MatchEvent, ...]`.

- [ ] **Step 1: Write the failing fixture-based parser test**

Create `game_events.json` with home competitor `11`, away competitor `22`, a home goal at `17'`, an away yellow at `45+3'`, a home red at `73'`, and substitution, VAR, unknown, wrong-team, and malformed events.

```python
def test_snapshot_keeps_only_verified_goals_and_cards_with_team_and_added_time():
    payload = load_fixture("game_events.json")

    snapshot = ProviderAdapter(clock=lambda: OBSERVED_AT).parse_snapshot(payload)

    assert [event.model_dump() for event in snapshot.events] == [
        {"provider_order": 1, "minute": 17, "added_time": None, "side": "home", "kind": "goal"},
        {"provider_order": 2, "minute": 45, "added_time": 3, "side": "away", "kind": "yellow_card"},
        {"provider_order": 3, "minute": 73, "added_time": None, "side": "home", "kind": "red_card"},
    ]
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python -m pytest tests/test_provider.py::test_snapshot_keeps_only_verified_goals_and_cards_with_team_and_added_time -q`

Expected: FAIL because `ProviderSnapshot.events` and the parser do not exist.

- [ ] **Step 3: Write the minimal parser**

Add this model near `LiveSnapshot`:

```python
class MatchEvent(StrictDomainModel):
    provider_order: int = Field(ge=1, le=10_000)
    minute: MatchMinute
    added_time: int | None = Field(default=None, ge=0, le=30)
    side: Literal["home", "away"]
    kind: Literal["goal", "yellow_card", "red_card"]
```

In `provider.py`, map only explicit provider IDs or normalized names for goal, yellow card, and red card. Use `parse_number` to validate integral order/minute/added-time, require the competitor ID to match home or away, discard all other rows, and sort accepted items by `(minute, added_time or 0, provider_order)`. Pass the parsed tuple to `ProviderSnapshot`; do not add raw events to `sanitized_provider_data`.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `python -m pytest tests/test_provider.py::test_snapshot_keeps_only_verified_goals_and_cards_with_team_and_added_time -q`

Expected: PASS with only three canonical incidents.

- [ ] **Step 5: Add malformed-data tests**

```python
def test_event_parser_isolates_malformed_rows():
    payload = load_fixture("game_events.json")
    payload["game"]["events"] = [None, {"eventType": {"id": 1}}, *payload["game"]["events"][:1]]

    snapshot = ProviderAdapter(clock=lambda: OBSERVED_AT).parse_snapshot(payload)

    assert [(event.minute, event.kind) for event in snapshot.events] == [(17, "goal")]
```

Also test missing/empty events returns `()` and that `ProviderClient.get_snapshot()` retains events while calling `game/` and `game/stats/` exactly once.

- [ ] **Step 6: Run the provider suite and commit**

Run: `python -m pytest tests/test_provider.py -q`

Expected: PASS without outbound network requests.

```powershell
git add football_live/domain.py football_live/provider.py tests/test_provider.py tests/fixtures/365scores/game_events.json
git commit -m "feat: parse verified live match events"
```

---

### Task 2: Persist events privately and idempotently

**Files:**
- Create: `supabase/migrations/20261005170000_add_private_match_events.sql`
- Modify: `supabase/tests/001_live_product_schema.sql`
- Modify: `football_live/repository.py`
- Modify: `football_live/supabase_gateway.py`
- Modify: `football_live/jobs.py`
- Modify: `tests/test_repository.py`
- Modify: `tests/test_jobs.py`

**Interfaces:**
- Adds `Repository.store_match_events(fixture: Fixture, events: Sequence[MatchEvent]) -> int`.
- `run_collect()` stores events before the final-status prediction skip and reports `events` plus `event_errors` counters.

- [ ] **Step 1: Write failing persistence and collection tests**

```python
def test_store_match_events_upserts_only_canonical_fields_by_fixture_and_order(gateway, fake_client):
    fixture = make_fixture(1)
    events = [MatchEvent(provider_order=2, minute=45, added_time=3, side="away", kind="yellow_card")]

    assert gateway.store_match_events(fixture, events) == 1

    operation = fake_client.operations[-1]
    assert operation["schema"] == "private"
    assert operation["name"] == "match_events"
    assert operation["method"] == "upsert"
    assert operation["kwargs"] == {"on_conflict": "fixture_id,provider_event_order"}
    assert set(operation["payload"][0]) == {
        "fixture_id", "provider_event_order", "minute", "added_time", "side", "kind", "last_observed_at"
    }
```

```python
@pytest.mark.asyncio
async def test_collect_persists_events_before_skipping_finished_fixture():
    fixture = make_fixture(1)
    repo = FakeRepository(fixtures=[fixture])
    provider = FakeProvider(snapshots={fixture.provider_fixture_id: make_snapshot(
        fixture.provider_fixture_id, status="Finalizado",
        events=(MatchEvent(provider_order=1, minute=74, side="away", kind="goal"),),
    )})

    result = await run_collect(repo, provider, FakePredictor(), HTTP_REQUEST_ID, "collect:events")

    assert result.counters["events"] == 1
    assert result.counters["skipped_predictions"] == 1
    assert len(repo.stored_events) == 1
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_repository.py::test_store_match_events_upserts_only_canonical_fields_by_fixture_and_order tests/test_jobs.py::test_collect_persists_events_before_skipping_finished_fixture -q`

Expected: FAIL because the protocol, gateway behavior, and collection stage do not yet exist.

- [ ] **Step 3: Create the secure migration and its database tests**

Create the migration with this schema and permission boundary:

```sql
create table private.match_events (
  id uuid primary key default gen_random_uuid(),
  fixture_id uuid not null references private.fixtures(id) on delete cascade,
  provider_event_order integer not null check (provider_event_order between 1 and 10000),
  minute smallint not null check (minute between 0 and 150),
  added_time smallint check (added_time between 0 and 30),
  side text not null check (side in ('home', 'away')),
  kind text not null check (kind in ('goal', 'yellow_card', 'red_card')),
  first_observed_at timestamptz not null default now(),
  last_observed_at timestamptz not null default now(),
  unique (fixture_id, provider_event_order)
);
create index match_events_fixture_timeline_idx
  on private.match_events (fixture_id, minute, added_time, provider_event_order);
alter table private.match_events enable row level security;
revoke all on table private.match_events from public, anon, authenticated;
grant select, insert, update on table private.match_events to service_role;
```

Extend `supabase/tests/001_live_product_schema.sql` to assert table existence, RLS, no anon read/write privileges, service-role access, and that a conflict update leaves exactly one event per `(fixture_id, provider_event_order)` while advancing `last_observed_at`.

- [ ] **Step 4: Implement gateway and job staging**

Implement this repository boundary in `supabase_gateway.py`:

```python
def store_match_events(self, fixture: Fixture, events: Sequence[MatchEvent]) -> int:
    if fixture.id is None:
        raise ValueError("persisted fixture is required")
    if not events:
        return 0
    payload = [{
        "fixture_id": str(fixture.id),
        "provider_event_order": event.provider_order,
        "minute": event.minute,
        "added_time": event.added_time,
        "side": event.side,
        "kind": event.kind,
        "last_observed_at": datetime.now(timezone.utc).isoformat(),
    } for event in events]
    self._execute(lambda: self._client.schema("private").table("match_events")
        .upsert(payload, on_conflict="fixture_id,provider_event_order").execute())
    return len(payload)
```

Make the conflict behavior preserve `first_observed_at` and update canonical event fields plus `last_observed_at`. Update `Repository`, `FakeRepository`, and `make_snapshot`. In `run_collect`, call `store_match_events` after logo hydration and before final/disputed checks. On an event-store exception increment `event_errors` and `processing_error_stages["events"]`, stop that fixture, and keep the job error sanitized.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the Step 2 command.

Expected: PASS. A finished match persists its incidents but makes no prediction.

- [ ] **Step 6: Add write-boundary tests and run the suites**

Add tests proving an empty event tuple causes no database write, repeated provider events use the same conflict key, and a store failure does not expose exception text.

Run: `python -m pytest tests/test_repository.py tests/test_jobs.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add supabase/migrations/20261005170000_add_private_match_events.sql supabase/tests/001_live_product_schema.sql football_live/repository.py football_live/supabase_gateway.py football_live/jobs.py tests/test_repository.py tests/test_jobs.py
git commit -m "feat: persist verified match events privately"
```

---

### Task 3: Publish safe crests and event timeline through history API

**Files:**
- Modify: `football_live/supabase_gateway.py`
- Modify: `football_live/api.py`
- Modify: `tests/test_repository.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Extends `HISTORY_FIXTURE_SELECT` with `home_logo_url,away_logo_url`.
- Adds `MATCH_EVENT_SELECT = "minute,added_time,side,kind,provider_event_order"`.
- Adds `HistoryMatchEvent(minute, added_time, side, kind)` and extends `MatchHistory` with crest URLs plus `events`.

- [ ] **Step 1: Write failing gateway and API tests**

```python
def test_public_match_history_returns_safe_crests_and_ordered_events(gateway, fake_client):
    public_id = UUID(int=123)
    fake_client.responses[("private", "table", "fixtures")] = [{
        "id": str(FIXTURE_ID), "public_id": str(public_id),
        "home_name": "Local", "away_name": "Visitante",
        "home_logo_url": "https://imagecache.365scores.com/home.png",
        "away_logo_url": "https://imagecache.365scores.com/away.png",
    }]
    fake_client.responses[("private", "table", "match_events")] = [
        {"minute": 45, "added_time": 3, "side": "away", "kind": "yellow_card", "provider_event_order": 2},
        {"minute": 17, "added_time": None, "side": "home", "kind": "goal", "provider_event_order": 1},
    ]

    result = gateway.public_match_history(public_id)

    assert result["home_logo_url"].endswith("home.png")
    assert result["events"] == [
        {"minute": 17, "added_time": None, "side": "home", "kind": "goal"},
        {"minute": 45, "added_time": 3, "side": "away", "kind": "yellow_card"},
    ]
```

Extend `tests/test_api.py` to assert each public event exposes exactly `minute`, `added_time`, `side`, and `kind`; assert logos are URL-or-null and that no provider order or private fixture ID occurs.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_repository.py::test_public_match_history_returns_safe_crests_and_ordered_events tests/test_api.py::test_match_history_returns_safe_points_and_derived_analytics -q`

Expected: FAIL because history currently omits crest URLs and incident rows.

- [ ] **Step 3: Implement bounded allowlisted history reads**

Within `public_match_history`, query the private events table only by the resolved internal fixture UUID:

```python
events_response = self._execute(
    lambda: self._client.schema("private").table("match_events")
    .select(MATCH_EVENT_SELECT).eq("fixture_id", fixture_id)
    .order("minute").order("added_time", nullsfirst=True)
    .order("provider_event_order").limit(80).execute()
)
```

Validate every database row before returning only the four public event fields. Raise the existing sanitized repository exception for malformed records. Return `events: []` when none exist. Extend the API models/assembly while preserving existing `404`, `422`, `503`, data-freshness, and cache semantics.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command.

Expected: PASS with canonical event fields and real crest URLs only.

- [ ] **Step 5: Add read-boundary tests and commit**

Add tests for: empty events; limit exactly 80; malformed side/kind becoming a sanitized `503`; null crest fields not blocking valid events; and chronological order after database rows arrive out of order.

Run: `python -m pytest tests/test_repository.py tests/test_api.py -q`

Expected: PASS.

```powershell
git add football_live/supabase_gateway.py football_live/api.py tests/test_repository.py tests/test_api.py
git commit -m "feat: publish safe live event history"
```

---

### Task 4: Render timeline, fallback crests, concise summaries and revised BTTS

**Files:**
- Modify: `public/app/index.html`
- Modify: `public/app/app.js`
- Modify: `public/app/app.css`
- Modify: `tests/e2e/test_public_flow.py`
- Modify: `tests/e2e/public_flow_behavior.mjs`

**Interfaces:**
- Adds `#match-events` within `#match-events-section`.
- Adds `renderMatchEvents(item)` and a closed event-label map in `app.js`.
- Extends `clubMark(name, logoUrl)` with an image-error fallback.

- [ ] **Step 1: Write failing frontend contract and behavior tests**

```python
def test_dashboard_includes_safe_match_event_timeline_and_concise_copy():
    html = (APP / "index.html").read_text(encoding="utf-8")
    javascript = (APP / "app.js").read_text(encoding="utf-8")

    assert 'id="match-events"' in html
    assert "Momentos del partido" in html
    assert "¿Anotan ambos equipos?" in html
    assert "Escenario del modelo. Los decimales" not in html
    assert "function renderMatchEvents" in javascript
    assert "Sin eventos verificables publicados todavía." in javascript
    assert 'addEventListener("error"' in javascript
    for forbidden in ("innerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in javascript
```

Extend the JavaScript harness `history()` payload with crest URLs and events. Assert incident rows are chronological, identify the correct local/visitor team, safe crest URLs create matrix images, and an empty event array prints the approved empty state.

- [ ] **Step 2: Run frontend tests and verify RED**

Run: `python -m pytest tests/e2e/test_public_flow.py -q; node --test tests/e2e/public_flow_behavior.mjs`

Expected: FAIL because markup, renderer, revised copy, and error fallback do not exist.

- [ ] **Step 3: Add semantic layout and responsive visual system**

Insert this section immediately after `#btts-section` and before probability history:

```html
<section id="match-events-section" aria-labelledby="match-events-heading" hidden>
  <h3 id="match-events-heading">Momentos del partido</h3>
  <p class="muted">Eventos verificados por el proveedor.</p>
  <ol id="match-events" class="match-events" aria-live="polite"></ol>
</section>
```

Replace the BTTS title with `¿Anotan ambos equipos?` and delete its old visual-effect paragraph. Add a restrained vertical timeline: goal/lime, yellow-card/yellow, red-card/red, strong minute, readable team label, 44px minimum rows, no over-boxing, no mobile horizontal overflow, and `prefers-reduced-motion` support.

- [ ] **Step 4: Implement safe DOM rendering**

```javascript
const eventLabels = { goal: "Gol", yellow_card: "Tarjeta amarilla", red_card: "Tarjeta roja" };

function renderMatchEvents(item) {
  const events = Array.isArray(item.events) ? item.events : [];
  matchEvents.replaceChildren();
  matchEventsSection.hidden = false;
  if (!events.length) {
    matchEvents.append(element("li", "match-event-empty", "Sin eventos verificables publicados todavía."));
    return;
  }
  for (const event of events) {
    if (!(event?.kind in eventLabels) || !["home", "away"].includes(event.side)) continue;
    const team = event.side === "home" ? item.home_name : item.away_name;
    const minute = `${event.minute}'${Number.isFinite(event.added_time) && event.added_time > 0 ? `+${event.added_time}` : ""}`;
    const row = element("li", `match-event ${event.kind}`);
    row.setAttribute("aria-label", `Minuto ${minute}: ${eventLabels[event.kind]} de ${team}.`);
    row.append(element("strong", "match-event-minute", minute), element("span", "match-event-kind", eventLabels[event.kind]), element("span", "match-event-team", team));
    matchEvents.append(row);
  }
}
```

Call the renderer from `renderAnalytics`. Keep `clubMark` behind its current host allowlist, use empty `alt` for a decorative crest, and on `error` replace the image with initials while leaving the visible team name. Update BTTS visible/ARIA copy to `Sí, ambos marcan` and `No, uno se queda sin marcar`.

Replace the two verbose dynamic summaries with:

```javascript
probabilitySummary.textContent = probabilityTrend
  ? "Evolución de las probabilidades publicadas."
  : "Se necesitan al menos dos lecturas para mostrar una evolución.";
activitySummary.textContent = trends.length
  ? "Actividad observada durante el partido."
  : "Aún no hay suficientes lecturas para mostrar actividad.";
```

- [ ] **Step 5: Verify GREEN and add regression coverage**

Run: `python -m pytest tests/e2e/test_public_flow.py -q; node --test tests/e2e/public_flow_behavior.mjs`

Expected: PASS. Add selection A-with-events then B-with-none coverage, blocked-logo initials fallback coverage, and a history-crest regression proving matrix teams no longer lose crest URLs.

- [ ] **Step 6: Commit**

```powershell
git add public/app/index.html public/app/app.js public/app/app.css tests/e2e/test_public_flow.py tests/e2e/public_flow_behavior.mjs
git commit -m "feat: show verified match event timeline"
```

---

### Task 5: Migrate, verify, release, and inspect production

**Files:**
- Verify: `supabase/migrations/20261005170000_add_private_match_events.sql`
- Verify: `football_live/provider.py`
- Verify: `football_live/supabase_gateway.py`
- Verify: `football_live/api.py`
- Verify: `public/app/index.html`
- Verify: `public/app/app.js`
- Verify: `public/app/app.css`

**Interfaces:**
- Consumes all committed deliverables from Tasks 1–4.
- Produces a migrated Supabase project and a Vercel production deployment at `https://football-live-agent.vercel.app/app` with `READY` status.

- [ ] **Step 1: Run focused pre-migration verification**

```powershell
python -m pytest tests/test_provider.py tests/test_repository.py tests/test_jobs.py tests/test_api.py tests/e2e/test_public_flow.py -q
node --test tests/e2e/public_flow_behavior.mjs
git diff --check
```

Expected: all tests PASS and no whitespace errors.

- [ ] **Step 2: Apply and verify the Supabase migration**

Use the authenticated project workflow to apply pending migrations only, including `20261005170000_add_private_match_events.sql`, then run the configured database test suite.

Expected: `private.match_events` exists; anon cannot select, insert, or update it; the service role can persist canonical incidents; `(fixture_id, provider_event_order)` is unique; and the browser still holds no Supabase credential.

- [ ] **Step 3: Run the full suite and push**

Run: `python -m pytest -q`

Expected: PASS. If anything fails, return to its owning task and first repair/add the failing regression test before changing implementation.

```powershell
git status --short
git push origin codex/football-live-product
```

Expected: only intended commits are present and the remote branch is current.

- [ ] **Step 4: Deploy and confirm Vercel terminal state**

```powershell
node C:\Users\Visitante\AppData\Local\npm-cache\_npx\69f9afb961c37556\node_modules\vercel\dist\index.js deploy --prod --yes
```

Inspect the returned deployment with the same Vercel CLI.

Expected: Vercel reports `READY` and aliases `https://football-live-agent.vercel.app`.

- [ ] **Step 5: Perform production browser QA**

At desktop width and approximately 390px width, select a live match and verify:

- the matrix identity strip uses actual crests when provider URLs exist;
- `LOCAL`, `VISITANTE`, and team names remain readable;
- the timeline contains only goals/yellow/red cards with correct minute/team, or the approved empty state;
- no false foul event is shown;
- BTTS uses the approved question and yes/no labels;
- removed verbose/raw percentage text is absent;
- changing match replaces crest, event, chart, and scenario state together;
- there is no console error, horizontal scroll, clipped marker, secret, or private identifier.

If no live fixture is available, run controlled UI tests and report real-event visual QA as deferred rather than claiming it occurred.

- [ ] **Step 6: Record final evidence**

```powershell
git status --branch --short
git log -6 --oneline
```

Expected: clean branch synchronized with `origin/codex/football-live-product`, verified commits visible, and a ready production URL.
