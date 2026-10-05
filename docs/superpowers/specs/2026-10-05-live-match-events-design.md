# Live Match Events Design

## Objective

Add a reliable event timeline to the selected live match, correct the matrix team identity strip so it uses the provider's real club or national-team crests, and make the supporting analytics copy concise and readable. The feature improves match observability only; it does not alter the prediction model, its probabilities, or its evidence claims.

## Confirmed provider capability

The current 365Scores game-detail response includes `game.events`. In a live read on 2026-10-05, each event included a provider order, minute and added time, competitor ID, and an event type object. The observed event types included `Gol` and `Tarjeta amarilla`; the adapter must also support red-card types when published by the same response.

Individual fouls were not present in the observed event feed. The current normalized statistics provide only cumulative fouls. The product must never fabricate a foul event from an aggregate change. Foul totals remain in the team statistics comparison, not in the event timeline, unless a future provider event explicitly identifies a foul.

## Approved product scope

The selected-match analysis gains a compact, chronological section named `Momentos del partido`. It displays only provider-verified goals, yellow cards, and red cards. Every rendered entry identifies:

- the displayed minute, including added time when supplied;
- the team that received or scored the event;
- the event label and an accessible icon/text equivalent;
- the home or away side using the match's existing team identity.

The timeline is visible even if it is empty. Its empty state says `Sin eventos verificables publicados todavía.` It does not show unknown provider event types, substitutions, VAR annotations, player names, provider IDs, internal IDs, raw payloads, or aggregates masquerading as events.

Before the existing score matrix, the home/away reference strip continues to show `LOCAL` and `VISITANTE`, now with the real team crest whenever its provider URL is available and safely allowlisted. It works for clubs and national teams alike and retains the accessible initials fallback only if the crest is unavailable or fails to load.

The following verbose text is removed rather than paraphrased:

- the BTTS visual-effect disclaimer;
- the probability-history observation count and raw percentage dump;
- the observed-activity methodological paragraph and raw values.

Replacement copy is intentionally concise:

- the probability history announces only whether it has enough readings to draw a trend;
- the activity chart uses a short `Actividad observada` label and no accuracy implication;
- `Ambos equipos marcan` becomes `¿Anotan ambos equipos?`, with the outcomes `Sí, ambos marcan` and `No, uno se queda sin marcar`.

## Architecture

### Provider normalization

`ProviderClient.get_snapshot()` already fetches `game/` and combines it with `game/stats/`. The normalized provider snapshot gains a bounded sequence of `ProviderMatchEvent` values parsed from the existing `game.events` array.

The parser accepts only events that meet all of these rules:

1. The event has a positive, known provider order and a finite match minute.
2. Its competitor ID exactly matches the game home or away competitor ID.
3. Its type is mapped to one of `goal`, `yellow_card`, or `red_card` using provider event-type identifiers/names. The mapping is explicit and tested; unrecognized types are ignored.
4. The added-time value is non-negative when present; it is optional.

The canonical event has this public-safe shape:

```text
ProviderMatchEvent
  provider_order: integer
  minute: integer
  added_time: integer | null
  side: home | away
  kind: goal | yellow_card | red_card
```

The server does not retain player IDs or raw event labels. It does not infer a red card from card totals and does not infer fouls from the stats endpoint.

### Persistence and idempotency

Add a private `match_events` table owned by `private` schema. It has a generated UUID primary key, the fixture UUID foreign key, provider event order, minute, optional added time, canonical side, canonical kind, first observed timestamp, and last observed timestamp. The unique key is `(fixture_id, provider_event_order)`.

On each collection cycle, the repository upserts the parsed events for that fixture. Re-observed events update only `last_observed_at`; they do not duplicate the timeline. An empty provider event array performs no deletion, because temporarily incomplete provider detail must not erase events already verified and stored.

Events remain private. No browser can read the private table, provider fixture ID, provider response, service-role credentials, or collection job data directly.

### Public read contract

Extend the existing `GET /api/matches/{public_id}/history?limit=90` response rather than add a second browser request. `MatchHistory` gains the already-approved crest fields and an `events` list:

```text
MatchHistory
  public_id: UUID
  home_name: string
  away_name: string
  home_logo_url: https URL | null
  away_logo_url: https URL | null
  events: MatchEvent[]
  points: HistoryPoint[]
  analytics: LiveAnalyticsResponse

MatchEvent
  minute: integer
  added_time: integer | null
  side: home | away
  kind: goal | yellow_card | red_card
```

The private gateway resolves the public UUID to one fixture, selects an explicit allowlist of columns, sorts events by minute, added time, and provider order, and caps output at 80 events. The API validates every value with Pydantic and retains the existing sanitized `404`, `422`, and `503` behaviour. The history endpoint keeps its short public cache; a normal client refresh receives the next persisted collection result.

### Frontend rendering

The existing history request feeds the timeline and team strip. The frontend never uses provider data directly and creates all DOM nodes with `createElement`, `textContent`, `setAttribute`, and `replaceChildren`; it does not use HTML injection APIs.

The timeline uses semantic list markup. A goal has a lime accent, a yellow card has a yellow accent, and a red card has a red accent. Icons are decorative only; readable labels remain in text. Each row carries an accessible label such as `Minuto 63: gol de Local`.

The crest helper retains its restrictive image-host allowlist and adds an image-error fallback to the initials badge. It receives the history response's `home_logo_url` and `away_logo_url`, fixing the missing crests in the matrix reference strip without trusting arbitrary URLs.

## Failure and data states

- **No event feed or no mapped events:** show the empty timeline state; do not imply that no foul occurred.
- **Malformed event:** discard only that event, preserve the rest of the match response.
- **Provider/degraded/stale match state:** keep already persisted events readable; the established freshness state remains visible elsewhere.
- **Missing crest or image failure:** use the team-initials fallback with the team name text still present.
- **History request error:** retain the selected match header, show the existing retry state, and do not render stale timeline rows from another match.

## Security and data boundaries

- Browser access remains same-origin FastAPI only; no browser Supabase client is added.
- The service role remains server-only and is never included in response payloads, static assets, logs, or frontend code.
- The new table enables RLS with no anon/authenticated policy and has its privileges revoked from public roles. Only service-role server access is granted.
- Queries use public match UUIDs, explicit columns, fixed output bounds, and parameterized Supabase/PostgREST calls.
- The frontend retains the allowlist for remote crest hosts and does not inject provider text as HTML.

## Verification

Implementation follows red-green TDD and includes:

- provider parser tests for valid goals, yellow cards, red cards, added time, unknown types, invalid competitors, and malformed events;
- repository tests for idempotent event upserts, safe private-column selects, output ordering, and the public-ID boundary;
- API tests for the extended history response, no events, invalid public IDs, unknown matches, and sanitized repository errors;
- Supabase SQL tests proving RLS/privileges for `private.match_events` and uniqueness by fixture/order;
- frontend contract/behaviour tests for real crest URLs in matrix teams, blocked logo URLs, image fallback, compact copy, the event timeline, empty states, selection replacement, and absence of HTML injection APIs;
- the complete Python suite, migration test suite where available, a clean Git diff check, and a Vercel production deployment reaching `READY`;
- browser verification on desktop and mobile layouts with a real live match when available.

The release must not claim that the timeline, the revised BTTS presentation, or the visual work increases prediction accuracy.
