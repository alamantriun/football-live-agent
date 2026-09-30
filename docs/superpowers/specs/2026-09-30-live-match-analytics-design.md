# Live Match Analytics Design

## Objective

Restore the useful live charts and statistics from the original local dashboard inside the approved production `/app` experience. Every visual must be backed by persisted Supabase data, must distinguish missing data from zero, and must not imply that an experimental signal is validated predictive evidence.

## Approved product scope

The selected match expands into a single continuous analysis area below the existing score and 1X2 summary. It includes:

- probability history for home, draw, and away;
- a current team-stat comparison for possession, shots, shots on target, corners, cards, and provider xG when available;
- observed pressure and momentum trends;
- next-goal probabilities;
- over/under 2.5 and both-teams-to-score probabilities;
- total-goal distribution;
- the most probable final scores;
- data coverage, freshness, model version, and the observation time.

The interface keeps the current dark green, lime, yellow, and white visual language. Spacing and typography group the analysis; borders and boxed cards are used only where they improve comparison. No decorative dashboard grid or unrelated redesign is introduced.

## Architecture

### Persisted source

`private.live_snapshots` remains the source of observed match history. It already stores minute, score, normalized statistics, provider observation time, collection time, and quality. `private.predictions` remains the source of the model history and stores 1X2 probabilities, adjusted goal rates, explanation, model version reference, and creation time.

No browser receives the private schema, provider fixture identifiers, raw provider payloads, job information, service-role credentials, or model parameters.

### Repository boundary

Add a repository method that resolves a public match ID to at most 90 chronological observation points. The query joins private fixtures, snapshots, predictions, and model versions on their internal UUID relationships. It selects only allowlisted fields and validates every row before returning it to the API.

The method must:

- require an exact public UUID;
- cap the requested history to 90 points;
- return points oldest to newest after selecting the newest bounded set;
- preserve `null` for absent statistics;
- prefer the prediction associated with the exact snapshot and active model record;
- never return `sanitized_provider_data`, provider identifiers, internal UUIDs, or training parameters.

### Public API

Add `GET /api/matches/{public_id}/history?limit=90`. Its response uses the existing request envelope conventions and cache policy. The contract is:

```text
MatchHistoryEnvelope
  request_id: UUID
  generated_at: aware datetime
  data_status: fresh | degraded | stale | suspended
  model_version: string | null
  item:
    public_id: UUID
    home_name: string
    away_name: string
    points: HistoryPoint[]

HistoryPoint
  minute: integer | null
  score_home: integer
  score_away: integer
  provider_observed_at: aware datetime
  collected_at: aware datetime | null
  quality: fresh | degraded | stale | suspended
  stats:
    possession: { home: number | null, away: number | null }
    shots: { home: number | null, away: number | null }
    shots_on_target: { home: number | null, away: number | null }
    corners: { home: number | null, away: number | null }
    yellow_cards: { home: number | null, away: number | null }
    red_cards: { home: number | null, away: number | null }
    expected_goals: { home: number | null, away: number | null }
  probabilities: { home: number, draw: number, away: number } | null
  lambda_adjusted: { home: number, away: number } | null
  prediction_created_at: aware datetime | null
```

Unknown matches return the existing sanitized `404` response. Repository failures return the existing sanitized `503` response. Limits outside `1..90` return `422` before any database query.

### Derived analytics

Derived analytics are computed in a dedicated pure Python module so their formulas are testable and do not depend on the frontend.

- **Observed pressure:** a transparent 0–100 relative index using only available shots, shots on target, corners, and xG. Missing inputs are excluded from the denominator rather than converted to zero. The UI labels this an observed index, not a probability.
- **Momentum:** change in observed pressure over the recent valid observations, smoothed over three points. It is hidden when fewer than three valid points exist.
- **Next goal:** calculated from the latest non-negative adjusted home and away goal rates. The three outcomes are home, no further goal, and away over the remaining match horizon. It is labelled experimental.
- **Over/Under and BTTS:** calculated from the latest adjusted rates and current score with the same Poisson assumptions used by the prediction engine. They are labelled model scenarios, not separate validated models.
- **Total goals and score matrix:** bounded Poisson distributions. The API returns only totals 0–7 and the five most probable scores, combining the tail into `7+` where necessary.

If adjusted rates are absent, stale, suspended, non-finite, or negative, market and score simulations are omitted. Calculations never forward-fill missing observations.

## Interface design

### Layout

The existing match list and selected-match header remain unchanged. Under the current probability/context row, add an `Análisis en vivo` section with this reading order:

1. A full-width probability-history line chart.
2. A wide, text-forward team-stat comparison with paired bars.
3. A full-width pressure and momentum chart.
4. A two-column scenario area for next goal and Over/Under plus BTTS.
5. A two-column distribution area for total goals and probable scores.
6. A compact evidence footer for coverage, freshness, observation time, and model version.

Desktop uses deliberate asymmetry and broad charts rather than a wall of equal cards. Mobile stacks every section, keeps labels visible, and permits no horizontal page scrolling.

### Rendering

Charts use a small first-party canvas renderer stored with the application. No CDN script, `eval`, inline executable script, or new runtime dependency is introduced. Canvas charts receive accessible summaries through adjacent text and `aria-label` values.

The renderer supports line and horizontal-bar charts only. The score distribution uses semantic HTML bars because exact percentages are more important than animation. Motion is limited to a quick update transition and is removed under `prefers-reduced-motion`.

### States

- **Loading:** the current match remains visible and the analysis area announces that history is loading.
- **Success:** charts render from real persisted points.
- **Partial:** unavailable series are replaced with an explicit `Dato no disponible`; available sections still render.
- **Empty:** a single observation shows current statistics but explains that a trend needs more observations.
- **Stale or suspended:** historical visuals remain readable, but simulation panels are hidden and freshness is prominent.
- **Error:** the match summary remains usable and the analysis offers a retry action.
- **Selection change:** the prior history request is aborted before the new request begins.

The frontend continues to avoid `innerHTML`, `insertAdjacentHTML`, `document.write`, and untrusted URL insertion.

## Supabase and security

The browser calls only the same-origin FastAPI route. The backend continues using the service-role key only in the server environment. No new public table, direct browser Supabase client, or permissive RLS policy is required.

The private history query uses explicit columns and a hard row limit. Public responses contain normalized statistics and model outputs only. Error messages remain sanitized, and API responses keep short-lived public caching while internal jobs remain `no-store`.

The existing public projection stays responsible for the live-match list and latest summary. History is read on demand for the selected match so the landing list remains fast and the database does not duplicate every observation into a public table.

## Verification

Implementation follows red-green TDD and must include:

- unit tests for pressure, momentum, Poisson scenarios, missing values, and invalid rates;
- repository tests proving explicit columns, public-ID filtering, chronological order, and the 90-point cap;
- API tests for success, `404`, `422`, sanitized `503`, and response shape;
- frontend contract tests for the history endpoint, required canvases, accessible summaries, abort behavior, missing-data states, and forbidden DOM injection APIs;
- the complete Python test suite;
- production deployment reaching Vercel `READY`;
- production logs showing successful history reads without private-data leakage;
- browser verification on desktop and mobile-width layouts with a real live match when one is available.

The release must not claim improved prediction accuracy. It improves observability and product usefulness while preserving the existing model-evidence warning.
