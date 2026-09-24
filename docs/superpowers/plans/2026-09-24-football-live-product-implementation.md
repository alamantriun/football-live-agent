# Football Live Product Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convertir `football-live-agent` en un producto web público desplegable en Vercel, con recolección live durable en Supabase, predicciones explicables, aprendizaje controlado y la propuesta visual ya aprobada.

**Architecture:** Vercel servirá tres páginas estáticas y una única aplicación FastAPI stateless. FastAPI consultará Supabase mediante un gateway inyectable, encapsulará 365Scores tras un adaptador de host fijo y ejecutará trabajos idempotentes invocados por Supabase Cron. El modelo Poisson seguirá siendo la distribución única; el aprendizaje sólo podrá promover parámetros inmutables después de una separación cronológica y de mejorar simultáneamente Brier y log loss.

**Tech Stack:** Python 3.12, FastAPI, Pydantic Settings, `httpx`, `supabase-py`, Supabase Postgres/Cron/Vault, HTML/CSS/JavaScript sin framework, Playwright, pytest, Vercel Functions.

**Spec:** `docs/superpowers/specs/2026-09-24-football-product-supabase-vercel-design.md`

## Global Constraints

- El acceso público no requiere cuenta en esta fase.
- Ningún secreto, `service_role`, URL privada o cabecera interna llega al navegador, Git, HTML ni logs.
- El navegador nunca consulta directamente 365Scores ni escribe en Supabase.
- Vercel Functions son stateless: no usar hilos, procesos residentes, SQLite ni archivos JSON como fuente de verdad cloud.
- Mantener el proveedor actual detrás de `ProviderClient`; ninguna URL suministrada por el usuario puede llegar al cliente HTTP.
- No fabricar ceros cuando una estadística está ausente. Conservar `None` y degradar la calidad.
- Un snapshot viejo se muestra como `desactualizado` o `suspendido`; nunca aparenta ser actual.
- El aprendizaje requiere al menos 70 partidos de entrenamiento y 30 partidos posteriores de validación.
- La promoción exige mejora estricta de Brier y log loss contra el modelo activo y contra el baseline; si falla, el activo no cambia.
- La evidencia histórica prepartido no se presentará como precisión live certificada.
- El frontend usa nodos DOM y `textContent` para datos externos; no inserta respuestas del proveedor con `innerHTML`.
- Cuerpo visual de 17–19 px, auxiliares de al menos 14 px, foco visible, hovers no exclusivos y `prefers-reduced-motion`.
- Cada tarea termina con pruebas dirigidas y un commit pequeño. No incluir cambios ajenos del worktree.

---

## Task 1: Establish the cloud runtime and configuration boundary

**Files:**

- Create: `.python-version`
- Create: `requirements.in`
- Modify: `requirements.txt`
- Modify: `requirements-dev.txt`
- Create: `.env.example`
- Modify: `.gitignore`
- Create: `football_live/__init__.py`
- Create: `football_live/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**

- Produces `Settings` and `get_settings()` for every backend component.
- Consumes only environment variables; no module may call `os.getenv()` for cloud secrets outside `settings.py`.

- [ ] **Step 1: Add the failing configuration tests**

```python
# tests/test_settings.py
import pytest
from pydantic import ValidationError

from football_live.settings import Settings


def test_production_requires_server_secrets():
    with pytest.raises(ValidationError):
        Settings(environment="production")


def test_secrets_are_not_rendered():
    settings = Settings(
        environment="test",
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="server-secret",
        cron_secret="cron-secret",
    )
    assert "server-secret" not in repr(settings)
    assert "cron-secret" not in repr(settings)
```

- [ ] **Step 2: Run the test and confirm the expected failure**

Run: `python -m pytest tests/test_settings.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'football_live'`.

- [ ] **Step 3: Add the runtime boundary**

`.python-version`:

```text
3.12
```

`requirements.in`:

```text
fastapi>=0.115,<1
httpx>=0.27,<1
pydantic-settings>=2.6,<3
supabase>=2.10,<3
curl-cffi>=0.7,<1
```

Add `pip-tools` to `requirements-dev.txt`, then regenerate a fully pinned, hashed `requirements.txt`:

```powershell
python -m pip install pip-tools
python -m piptools compile requirements.in --generate-hashes --output-file requirements.txt
```

Keep local analysis/test-only packages such as `pandas`, `pytest`, `pytest-asyncio` and `playwright` in `requirements-dev.txt`; do not ship them in the Vercel runtime unless imported by production code.

```python
# football_live/settings.py
from functools import lru_cache
from typing import Literal

from pydantic import HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    supabase_url: HttpUrl | None = None
    supabase_service_role_key: SecretStr | None = None
    cron_secret: SecretStr | None = None
    allowed_hosts: str = "localhost,127.0.0.1"
    public_origin: str = "http://127.0.0.1:8765"
    provider_timeout_seconds: float = 12.0

    @model_validator(mode="after")
    def require_production_secrets(self):
        if self.environment == "production":
            required = (
                self.supabase_url,
                self.supabase_service_role_key,
                self.cron_secret,
            )
            if any(value is None for value in required):
                raise ValueError("Faltan secretos requeridos del servidor")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`.env.example` contiene únicamente nombres y marcadores vacíos:

```dotenv
ENVIRONMENT=development
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
CRON_SECRET=
ALLOWED_HOSTS=localhost,127.0.0.1
PUBLIC_ORIGIN=http://127.0.0.1:8765
PROVIDER_TIMEOUT_SECONDS=12
```

Ensure `.gitignore` includes `.env.local`, `.vercel/`, Supabase local temp files and generated coverage artifacts without re-adding ignored runtime data. Because the current `.env*` rule is broad, add `!.env.example` after it so the empty template can be committed.

- [ ] **Step 4: Run configuration and secret checks**

Run: `python -m pytest tests/test_settings.py -q`

Expected: `2 passed`.

Run: `git grep -nE "(sb_secret_|SUPABASE_SERVICE_ROLE_KEY=[^[:space:]]+|CRON_SECRET=[^[:space:]]+)" -- ':!docs/superpowers/**' ':!.env.example'`

Expected: no secret values; identifier names in backend code are allowed after manual review.

- [ ] **Step 5: Commit only the runtime boundary**

```powershell
git add .python-version requirements.in requirements.txt requirements-dev.txt .env.example .gitignore football_live/__init__.py football_live/settings.py tests/test_settings.py
git commit -m "build: establish secure cloud runtime"
```

## Task 2: Create the Supabase schema, projections, RLS and transactional RPCs

**Files:**

- Create with CLI: `supabase/config.toml`
- Create with CLI: `supabase/migrations/<generated>_create_live_product_schema.sql`
- Create: `supabase/tests/001_live_product_schema.sql`
- Create: `docs/database.md`

**Interfaces:**

- Produces private tables: `fixtures`, `live_snapshots`, `predictions`, `outcomes`, `model_versions`, `training_runs`, `job_runs`.
- Produces safe read models `public.live_match_projection`, `public.model_status_projection` and security-invoker views `public.live_matches`, `public.model_status`.
- Produces service-only RPCs `private.claim_job`, `private.finish_job`, and `private.promote_model`.

- [ ] **Step 1: Initialize Supabase and generate the migration path**

Run from the repository root:

```powershell
supabase init
supabase migration new create_live_product_schema
```

Expected: the CLI prints the exact timestamped migration path. Use that path; do not hand-invent a timestamped filename.

- [ ] **Step 2: Add failing SQL security tests**

```sql
-- supabase/tests/001_live_product_schema.sql
begin;
select plan(12);

select has_table('private', 'fixtures', 'fixtures exists');
select has_table('private', 'predictions', 'predictions exists');
select has_view('public', 'live_matches', 'public live view exists');
select has_view('public', 'model_status', 'public model view exists');
select ok(not has_schema_privilege('anon', 'private', 'USAGE'), 'anon cannot use private schema');
select ok(not has_table_privilege('anon', 'private.fixtures', 'SELECT'), 'anon cannot read fixtures');
select ok(not has_table_privilege('anon', 'private.fixtures', 'INSERT'), 'anon cannot write fixtures');
select ok(has_table_privilege('anon', 'public.live_matches', 'SELECT'), 'anon can read safe live view');
select ok(not has_table_privilege('anon', 'public.live_match_projection', 'INSERT'), 'anon cannot write projection');
select ok(has_function_privilege('service_role', 'private.claim_job(text,text,integer)', 'EXECUTE'), 'service can claim jobs');
select ok(not has_function_privilege('anon', 'private.claim_job(text,text,integer)', 'EXECUTE'), 'anon cannot claim jobs');
select is(
  (select count(*) from information_schema.routine_privileges
   where routine_schema = 'private' and routine_name = 'promote_model' and grantee = 'PUBLIC'),
  0::bigint,
  'PUBLIC cannot promote'
);

select * from finish();
rollback;
```

- [ ] **Step 3: Run the DB tests before implementing the migration**

Run: `supabase start` then `supabase test db`

Expected: FAIL because the schemas and objects do not exist.

- [ ] **Step 4: Implement the additive schema migration**

The generated migration must include:

```sql
create extension if not exists pgcrypto;
create schema if not exists private;
revoke all on schema private from public, anon, authenticated;
grant usage on schema private to service_role;

create type private.model_state as enum ('candidate', 'active', 'rejected', 'retired');
create type private.run_state as enum ('running', 'succeeded', 'failed', 'rejected');

create table private.fixtures (
  id uuid primary key default gen_random_uuid(),
  public_id uuid not null unique default gen_random_uuid(),
  provider text not null check (provider = '365scores'),
  provider_fixture_id text not null,
  competition text not null,
  home_name text not null,
  away_name text not null,
  home_logo_url text,
  away_logo_url text,
  scheduled_at timestamptz,
  status text not null,
  final_home smallint check (final_home between 0 and 30),
  final_away smallint check (final_away between 0 and 30),
  discovered_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  finished_at timestamptz,
  unique (provider, provider_fixture_id)
);

create table private.live_snapshots (
  id uuid primary key default gen_random_uuid(),
  fixture_id uuid not null references private.fixtures(id),
  minute smallint check (minute between 0 and 150),
  score_home smallint not null check (score_home between 0 and 30),
  score_away smallint not null check (score_away between 0 and 30),
  normalized_stats jsonb not null,
  sanitized_provider_data jsonb not null,
  provider_observed_at timestamptz not null,
  collected_at timestamptz not null default now(),
  quality text not null check (quality in ('fresh', 'degraded', 'stale', 'suspended')),
  observation_bucket timestamptz not null,
  unique (fixture_id, observation_bucket)
);

create table private.model_versions (
  id uuid primary key default gen_random_uuid(),
  version text not null unique,
  state private.model_state not null,
  parameters jsonb not null,
  parameter_hash text not null unique,
  code_version text not null,
  previous_model_id uuid references private.model_versions(id),
  train_size integer not null default 0,
  validation_size integer not null default 0,
  brier double precision,
  log_loss double precision,
  created_at timestamptz not null default now(),
  activated_at timestamptz
);

create unique index one_active_model on private.model_versions ((state)) where state = 'active';

create table private.predictions (
  id uuid primary key default gen_random_uuid(),
  fixture_id uuid not null references private.fixtures(id),
  snapshot_id uuid not null references private.live_snapshots(id),
  model_version_id uuid not null references private.model_versions(id),
  lambda_base jsonb not null,
  lambda_adjusted jsonb not null,
  probabilities jsonb not null,
  explanation jsonb not null,
  quality text not null check (quality in ('fresh', 'degraded', 'stale', 'suspended')),
  created_at timestamptz not null default now(),
  unique (snapshot_id, model_version_id)
);

create table private.outcomes (
  fixture_id uuid primary key references private.fixtures(id),
  home_score smallint not null check (home_score between 0 and 30),
  away_score smallint not null check (away_score between 0 and 30),
  source text not null,
  confirmed boolean not null default false,
  confirmed_at timestamptz not null
);

create table private.training_runs (
  id uuid primary key default gen_random_uuid(),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  train_fixture_ids uuid[] not null default '{}',
  validation_fixture_ids uuid[] not null default '{}',
  candidate_model_id uuid references private.model_versions(id),
  metrics jsonb not null default '{}',
  decision private.run_state not null default 'running',
  reason text,
  sanitized_error text
);

create table private.job_runs (
  id uuid primary key default gen_random_uuid(),
  job_name text not null,
  idempotency_key text not null,
  state private.run_state not null default 'running',
  lease_until timestamptz not null,
  request_id uuid not null,
  counters jsonb not null default '{}',
  sanitized_error text,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  unique (job_name, idempotency_key)
);
```

Add indexes on fixture status/start, snapshot fixture/time, prediction fixture/time, training finish and job lease. Enable RLS on every table in `private` and both public projection tables. Revoke all privileges from `PUBLIC`, `anon` and `authenticated` on `private`; grant only the required table/sequence/function privileges to `service_role`.

The two public projection tables contain only approved public fields. Their RLS policy permits `SELECT using (true)` to `anon`; they have no public write policy. Create the views as:

```sql
create view public.live_matches with (security_invoker = true) as
select * from public.live_match_projection;

create view public.model_status with (security_invoker = true) as
select * from public.model_status_projection;

grant select on public.live_matches, public.model_status to anon, authenticated, service_role;
```

`private.claim_job` must atomically insert or reacquire only an expired lease, return the run row, set `search_path = ''`, reject `auth.role() <> 'service_role'`, and accept 30–900 lease seconds. `private.promote_model(candidate_id, current_id)` must lock both model rows, verify the current active ID, verify train/validation minima, verify finite metrics and strict improvement of both metrics, retire the prior model, activate the candidate and refresh `public.model_status_projection` in one transaction.

- [ ] **Step 5: Reset and verify schema behavior**

Run:

```powershell
supabase db reset
supabase test db
supabase db lint --level warning
```

Expected: all pgTAP tests pass and lint has no unreviewed security warnings.

- [ ] **Step 6: Document roles and Data API exposure**

In `docs/database.md`, record that production must explicitly expose only the schemas required by the API configuration, that new tables are not assumed to be automatically exposed, and that browser access is limited to the safe public projections. Record the exact grants from the migration and the rollback rule: migrations in this phase are additive; model rollback is data-level.

- [ ] **Step 7: Commit the database foundation**

```powershell
git add supabase docs/database.md
git commit -m "feat: add secure Supabase persistence"
```

## Task 3: Define canonical domain contracts and a Supabase gateway

**Files:**

- Create: `football_live/domain.py`
- Create: `football_live/repository.py`
- Create: `football_live/supabase_gateway.py`
- Test: `tests/test_repository.py`

**Interfaces:**

- `Fixture`, `LiveSnapshot`, `PredictionRecord`, `ModelVersion`, `DataStatus` are the only shapes crossing services.
- `Repository` is a `Protocol`; all jobs and API handlers depend on it, never on `supabase-py` directly.

- [ ] **Step 1: Write a failing repository contract test**

```python
# tests/test_repository.py
from datetime import datetime, timezone

from football_live.domain import LiveSnapshot


def test_snapshot_bucket_is_stable_per_fixture_and_minute():
    snapshot = LiveSnapshot(
        fixture_id="4b1c7cb7-7ce5-4fc4-bf89-4744c80a31b1",
        minute=63,
        score_home=1,
        score_away=1,
        stats={"shots_on_target": {"home": 4, "away": None}},
        provider_observed_at=datetime(2026, 9, 24, 20, 3, 44, tzinfo=timezone.utc),
        quality="degraded",
    )
    assert snapshot.observation_bucket.isoformat() == "2026-09-24T20:03:00+00:00"
    assert snapshot.stats["shots_on_target"]["away"] is None
```

- [ ] **Step 2: Run the failing test**

Run: `python -m pytest tests/test_repository.py -q`

Expected: FAIL because the domain module does not exist.

- [ ] **Step 3: Implement strict domain models and repository protocol**

Use Pydantic models with `extra="forbid"`, timezone-aware datetimes, probability bounds and explicit aliases (`home`/`away`, not mixed Spanish/provider names). Add a validator that truncates `provider_observed_at` to the minute for `observation_bucket`; it must not alter the original timestamp.

```python
# football_live/repository.py
from typing import Protocol, Sequence
from uuid import UUID

from .domain import Fixture, LiveSnapshot, ModelVersion, PredictionRecord


class Repository(Protocol):
    def upsert_fixtures(self, fixtures: Sequence[Fixture]) -> int: ...
    def active_fixtures(self, limit: int = 12) -> list[Fixture]: ...
    def store_snapshot(self, snapshot: LiveSnapshot) -> UUID: ...
    def store_prediction(self, prediction: PredictionRecord) -> UUID: ...
    def active_model(self) -> ModelVersion: ...
    def public_live(self, limit: int, cursor: str | None) -> list[dict]: ...
    def public_match(self, public_id: UUID) -> dict | None: ...
    def public_model_status(self) -> dict: ...
    def claim_job(self, name: str, key: str, lease_seconds: int, request_id: UUID) -> dict | None: ...
    def finish_job(self, run_id: UUID, state: str, counters: dict, error: str | None = None) -> None: ...
```

- [ ] **Step 4: Implement the Supabase gateway**

Create one server-only Supabase client from `Settings`. Explicitly choose `.schema("private")` for private operations and `.schema("public")` for projections. Use `upsert(..., on_conflict=...)`, bounded select lists and `.limit()`. Never log the client, key, full request headers or raw provider payload.

Translate Supabase transport errors into `RepositoryUnavailable` with a generic public message. Preserve the original exception only as `__cause__` for server logs filtered by the logging task.

- [ ] **Step 5: Verify with a fake Supabase client**

Tests must prove the gateway:

- chooses `private` for writes;
- chooses `public` for reads;
- uses `(provider, provider_fixture_id)` for fixture upserts;
- never sends fields not in the canonical model;
- limits public lists to 50 even if a larger value is requested.

Run: `python -m pytest tests/test_repository.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit the domain boundary**

```powershell
git add football_live/domain.py football_live/repository.py football_live/supabase_gateway.py tests/test_repository.py
git commit -m "feat: add domain and Supabase gateway"
```

## Task 4: Harden and normalize the 365Scores provider adapter

**Files:**

- Create: `football_live/provider.py`
- Modify: `extractor_365scores.py`
- Create: `tests/fixtures/365scores/live_list.json`
- Create: `tests/fixtures/365scores/game_partial.json`
- Test: `tests/test_provider.py`

**Interfaces:**

- `ProviderClient.list_live() -> list[ProviderFixture]`
- `ProviderClient.get_snapshot(provider_fixture_id: str) -> ProviderSnapshot`
- Network destinations are fixed constants under `https://webws.365scores.com/web/`.

- [ ] **Step 1: Add contract tests from sanitized provider fixtures**

```python
def test_missing_stat_is_not_converted_to_zero(adapter):
    snapshot = adapter.parse_snapshot(load_fixture("game_partial.json"))
    assert snapshot.stats.shots_on_target.away is None
    assert snapshot.quality == "degraded"


def test_provider_id_rejects_url_syntax(client):
    with pytest.raises(ValueError):
        client.get_snapshot("https://attacker.invalid/")
```

The saved fixtures must contain only the minimum sanitized keys needed for parsing; remove tracking IDs, headers and unrelated response content.

- [ ] **Step 2: Run the failing tests**

Run: `python -m pytest tests/test_provider.py -q`

Expected: FAIL because the adapter is absent.

- [ ] **Step 3: Implement the adapter with a fixed outbound surface**

Use `httpx.AsyncClient` with a 12-second total timeout, 5-second connect timeout, response-size guard, bounded connection pool and a fixed `base_url`. Accept only digit-only fixture IDs of at most 20 characters. Expose no method accepting a URL.

Retry only timeouts, `429` and `5xx`, at most three attempts, with jittered backoff below the function timeout. Map errors to `ProviderUnavailable`; do not return an all-zero event.

Refactor reusable pure parsing from `extractor_365scores.py` into the adapter while keeping compatibility wrappers for the local application. `_parse_num(None)` must no longer erase missingness in normalized cloud paths.

- [ ] **Step 4: Verify parsing, allowlisting and timeout behavior**

Run: `python -m pytest tests/test_provider.py test_extractor.py test_vivo_confiable.py -q`

Expected: all directed tests pass; specifically zero xG remains `0`, missing possession remains `None`, and URL-like fixture IDs are rejected before network access.

- [ ] **Step 5: Commit the provider adapter**

```powershell
git add football_live/provider.py extractor_365scores.py tests/fixtures/365scores tests/test_provider.py
git commit -m "refactor: isolate the live data provider"
```

## Task 5: Build the stateless prediction service and freshness policy

**Files:**

- Create: `football_live/prediction_service.py`
- Modify: `calidad_vivo.py`
- Modify: `simulador.py`
- Test: `tests/test_prediction_service.py`

**Interfaces:**

- `PredictionService.predict(snapshot, active_model) -> PredictionRecord`
- `classify_freshness(observed_at, now, completeness) -> fresh|degraded|stale|suspended`
- Existing `modelo_poisson.predecir` remains the sole probability distribution.

- [ ] **Step 1: Add freshness and consistency tests**

```python
def test_stale_snapshot_is_not_recomputed(service, snapshot, active_model):
    snapshot.provider_observed_at -= timedelta(seconds=121)
    result = service.predict(snapshot, active_model)
    assert result.quality == "suspended"
    assert result.probabilities == {}


def test_1x2_probabilities_share_one_distribution(service, snapshot, active_model):
    result = service.predict(snapshot, active_model)
    total = sum(result.probabilities[key] for key in ("home", "draw", "away"))
    assert total == pytest.approx(100.0)
```

- [ ] **Step 2: Confirm failure**

Run: `python -m pytest tests/test_prediction_service.py -q`

Expected: FAIL because `PredictionService` is missing.

- [ ] **Step 3: Implement the service as a pure orchestration layer**

Rules:

- `fresh`: age at most 60 seconds and required score/minute present;
- `degraded`: age at most 60 seconds but optional signals missing;
- `stale`: 61–120 seconds; retain last persisted prediction but do not claim a refresh;
- `suspended`: over 120 seconds, impossible score/minute, or finished/disputed event; emit no derived probabilities.

Map canonical stats to the existing `simulador.correr()` input without replacing `None` by zero. Pass only an `active` model; reject candidate/rejected models. Preserve `model_version`, lambda base/adjusted, inputs used and warnings in `PredictionRecord.explanation`.

- [ ] **Step 4: Run model and prediction regression tests**

Run:

```powershell
python -m pytest tests/test_prediction_service.py test_modelo_probabilistico.py test_vivo_confiable.py test_simulador.py -q
```

Expected: all tests pass and existing analytical probabilities remain seed-independent.

- [ ] **Step 5: Commit prediction orchestration**

```powershell
git add football_live/prediction_service.py calidad_vivo.py simulador.py tests/test_prediction_service.py
git commit -m "feat: persistable live prediction service"
```

## Task 6: Port controlled learning to repository-backed, immutable model versions

**Files:**

- Create: `football_live/training.py`
- Modify: `aprendizaje.py`
- Test: `tests/test_training.py`

**Interfaces:**

- `build_chronological_split(examples, consumed_ids, min_train=70, min_valid=30)`
- `evaluate_candidate(train, validation, active_model) -> TrainingDecision`
- `TrainingService.run(repository, code_version) -> TrainingRun`

- [ ] **Step 1: Add failing gates and leakage tests**

```python
def test_split_uses_only_outcomes_known_before_validation(examples):
    train, valid = build_chronological_split(examples, set())
    cutoff = min(item.first_observed_at for item in valid)
    assert len(train) >= 70
    assert len(valid) == 30
    assert all(item.outcome_confirmed_at < cutoff for item in train)


@pytest.mark.parametrize(
    ("brier", "log_loss"),
    [(0.59, 1.01), (0.61, 0.99), (float("nan"), 0.99)],
)
def test_candidate_must_improve_both_metrics(brier, log_loss, champion):
    assert not promotion_allowed(
        candidate={"brier": brier, "log_loss": log_loss},
        champion=champion,
        baseline={"brier": 0.62, "log_loss": 1.08},
    )
```

- [ ] **Step 2: Confirm the tests fail**

Run: `python -m pytest tests/test_training.py -q`

Expected: FAIL because cloud training functions are absent.

- [ ] **Step 3: Extract the pure learning core**

Move the valid chronology, regularized factor fitting and scoring behavior from `aprendizaje.py` into `football_live/training.py`. Keep compatibility wrappers in `aprendizaje.py` for local SQLite tests, but cloud code must consume repository rows.

Promotion rules are explicit:

```python
MIN_TRAIN = 70
MIN_VALID = 30


def promotion_allowed(candidate, champion, baseline):
    values = (*candidate.values(), *champion.values(), *baseline.values())
    if not all(math.isfinite(value) for value in values):
        return False
    return (
        candidate["brier"] < champion["brier"]
        and candidate["log_loss"] < champion["log_loss"]
        and candidate["brier"] < baseline["brier"]
        and candidate["log_loss"] < baseline["log_loss"]
    )
```

The candidate hash covers sorted parameters, train IDs, validation IDs and code version. Persist the candidate before promotion. Call the transactional `promote_model` RPC only when the pure decision passes. Validation fixture IDs become consumed whether the candidate wins or loses, preventing repeated tuning on the same holdout.

- [ ] **Step 4: Verify all learning gates**

Run: `python -m pytest tests/test_training.py test_modelo_probabilistico.py test_vivo_confiable.py -q`

Expected: all tests pass; a failed, tied or non-finite metric never activates a candidate.

- [ ] **Step 5: Commit controlled training**

```powershell
git add football_live/training.py aprendizaje.py tests/test_training.py
git commit -m "feat: add controlled model promotion"
```

## Task 7: Implement idempotent discover, collect, settle and train jobs

**Files:**

- Create: `football_live/jobs.py`
- Modify: `recolector_aprendizaje.py`
- Test: `tests/test_jobs.py`

**Interfaces:**

- `run_discover(repo, provider, request_id, key)`
- `run_collect(repo, provider, predictor, request_id, key)`
- `run_settle(repo, provider, request_id, key)`
- `run_train(repo, trainer, request_id, key)`

- [ ] **Step 1: Add idempotency and failure-safety tests**

```python
def test_duplicate_job_does_not_call_provider(repo, provider):
    repo.claim_job_result = None
    result = run_discover(repo, provider, REQUEST_ID, "discover:2026-09-24T20:02Z")
    assert result.status == "duplicate"
    provider.list_live.assert_not_called()


def test_training_failure_keeps_active_model(repo, trainer):
    previous = repo.active_model()
    trainer.run.side_effect = RuntimeError("boom")
    result = run_train(repo, trainer, REQUEST_ID, "train:2026-09-25")
    assert result.status == "failed"
    assert repo.active_model().id == previous.id
```

- [ ] **Step 2: Confirm failure**

Run: `python -m pytest tests/test_jobs.py -q`

Expected: FAIL because jobs are absent.

- [ ] **Step 3: Implement bounded serverless jobs**

- `discover`: upsert at most 100 fixtures returned by the provider.
- `collect`: load at most 12 active fixtures, use concurrency 2, store at most one snapshot per minute and one prediction per snapshot/model.
- `settle`: inspect at most 30 unfinished fixtures, persist only confirmed finals and mark disputed corrections for review.
- `train`: run once per idempotency key; preserve active model on all errors or rejected candidates.

Every job must claim a 30–900 second lease, finish with counters and a sanitized error code, and respect an overall time budget below the Vercel function limit. Existing `recolector_aprendizaje.py` becomes a local compatibility runner that calls these same job functions; it no longer contains separate business rules.

- [ ] **Step 4: Verify retries and concurrency limits**

Run: `python -m pytest tests/test_jobs.py -q`

Expected: all tests pass, duplicates do zero work, partial provider failures are counted, and leases are always finalized.

- [ ] **Step 5: Commit serverless jobs**

```powershell
git add football_live/jobs.py recolector_aprendizaje.py tests/test_jobs.py
git commit -m "feat: add idempotent collection jobs"
```

## Task 8: Expose the public and internal FastAPI contract securely

**Files:**

- Create: `football_live/api.py`
- Create: `football_live/errors.py`
- Create: `football_live/logging.py`
- Create: `api/index.py`
- Test: `tests/test_api.py`

**Interfaces:**

- Public: `GET /api/live`, `GET /api/matches/{public_id}`, `GET /api/model/status`, `GET /api/health`.
- Internal: `POST /api/jobs/discover`, `POST /api/jobs/collect`, `POST /api/jobs/settle`, `POST /api/jobs/train`.
- Every response carries `X-Request-ID`; JSON success includes `request_id`, `generated_at`, `data_status`, `model_version` where applicable.

- [ ] **Step 1: Add failing API contract tests**

```python
def test_internal_job_rejects_missing_secret(client):
    response = client.post("/api/jobs/discover", headers={"X-Idempotency-Key": "discover:1"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_public_error_is_sanitized(client, broken_repository):
    response = client.get("/api/live")
    assert response.status_code == 503
    body = response.json()
    assert "Traceback" not in response.text
    assert "supabase" not in body["error"]["message"].lower()
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
```

- [ ] **Step 2: Confirm failure**

Run: `python -m pytest tests/test_api.py -q`

Expected: FAIL because no FastAPI app exists.

- [ ] **Step 3: Implement app factory, response models and middleware**

Create `create_app(settings, repository, provider)` for dependency injection. In production set `docs_url=None`, `redoc_url=None`, `openapi_url=None`, `debug=False`. Add `TrustedHostMiddleware` from the allowlist and no CORS middleware unless a different explicit origin is required.

Use Pydantic request/response models with `extra="forbid"`; cap `limit` at 50 and history points at 120. Validate UUID path params. Return `404` for unknown public IDs and `503` for unavailable repository/provider states.

Generate or accept a valid request UUID, store it in request state and return it in the header. Map exceptions to the approved generic error envelope. The log formatter must redact case-insensitive keys matching `authorization`, `cookie`, `secret`, `token`, `key`, and truncate external values to 300 characters.

- [ ] **Step 4: Protect job routes**

Require:

- `X-Cron-Secret` equal to `CRON_SECRET`, compared with `hmac.compare_digest`;
- `X-Idempotency-Key` matching `^[a-z]+:[A-Za-z0-9T:+._-]{1,80}$`;
- empty or at most 1 KB JSON body with no extra fields.

Return the prior run result for duplicates without redoing provider work. Do not include the expected secret in any error.

`api/index.py` contains only:

```python
from football_live.api import create_production_app

app = create_production_app()
```

- [ ] **Step 5: Verify contracts and security behavior**

Run: `python -m pytest tests/test_api.py tests/test_jobs.py -q`

Expected: all tests pass, docs are absent in production, hostile hosts are rejected, oversized limits are validated and errors reveal no internals.

- [ ] **Step 6: Commit the API**

```powershell
git add football_live/api.py football_live/errors.py football_live/logging.py api/index.py tests/test_api.py
git commit -m "feat: expose secure stateless API"
```

## Task 9: Port the approved visual proposal into the public landing page

Esta tarea implementa la Portada aprobada como la primera experiencia del producto.

**Files:**

- Create: `public/index.html`
- Create: `public/assets/site.css`
- Create: `public/assets/site.js`
- Copy binary: `public/assets/football-players-white-kits.png`
- Create: `public/assets/fonts/README.md`
- Test: `tests/test_public_markup.py`

**Interfaces:**

- `/` introduces the product; its main CTA links to `/app` and transparency CTA to `/modelo`.
- Hero includes only proposition, CTAs and anonymous white-kit player art.
- Arsenal–Chelsea example is a second section and is explicitly labeled as an illustrative demonstration.

- [ ] **Step 1: Add structural and accessibility tests**

```python
def test_landing_structure():
    html = Path("public/index.html").read_text(encoding="utf-8")
    assert html.index('id="hero"') < html.index('id="live-reading"')
    assert "Demostración ilustrativa" in html
    assert 'href="/app"' in html
    assert 'href="/modelo"' in html
    assert "01" not in html and "02" not in html and "03" not in html
    assert "wikipedia" not in html.lower()
```

- [ ] **Step 2: Confirm failure**

Run: `python -m pytest tests/test_public_markup.py -q`

Expected: FAIL because `public/index.html` does not exist.

- [ ] **Step 3: Port the final approved prototype**

Use `.superpowers/brainstorm/205-1790270119/content/landing-product-proposal-v7-final-readable.html` as the visual source, but split HTML/CSS/JS and remove:

- remote Wikipedia logos;
- inline handlers and inline style blocks;
- numbered `01/02/03` ornaments;
- preview-only `/files/` paths;
- any statement implying proven live accuracy.

Copy the approved PNG:

```powershell
New-Item -ItemType Directory -Force public/assets | Out-Null
Copy-Item -LiteralPath '.superpowers/brainstorm/205-1790270119/content/football-players-white-kits.png' -Destination 'public/assets/football-players-white-kits.png'
```

Optimize losslessly and preserve transparency. Keep Barlow/Barlow Condensed either as local WOFF2 files with their OFL license or use system fallbacks until the licensed local files are added; do not use an unrestricted CSP exception. `fonts/README.md` records font family, source and license.

Use open spacing and section separators instead of borders around every block. All muted copy must meet contrast and use white or warm yellow/lime as approved. Hover states may translate or brighten interactive elements but never move layout.

- [ ] **Step 4: Verify markup, asset and reduced motion**

Run: `python -m pytest tests/test_public_markup.py -q`

Run: `rg -n "innerHTML|on(click|load|error)=|wikipedia|font-size:\s*(1[0-3]|[0-9])px" public`

Expected: no unsafe handlers, remote Wikipedia assets or body/auxiliary microtext. Any `innerHTML` match must be absent.

- [ ] **Step 5: Commit the landing page**

```powershell
git add public/index.html public/assets/site.css public/assets/site.js public/assets/football-players-white-kits.png public/assets/fonts/README.md tests/test_public_markup.py
git commit -m "feat: add approved product landing page"
```

## Task 10: Build the live dashboard with safe DOM rendering

**Files:**

- Create: `public/app/index.html`
- Create: `public/app/app.css`
- Create: `public/app/app.js`
- Test: `tests/e2e/test_public_flow.py`

**Interfaces:**

- Consumes only same-origin `/api/live` and `/api/matches/{public_id}`.
- Supports loading, empty, fresh, degraded, stale, suspended and error states.
- Club marks use provider URLs only after URL validation; initials are the fallback.

- [ ] **Step 1: Add an end-to-end test against mocked API responses**

```python
def test_dashboard_renders_degraded_state_without_fabricated_stats(page, live_server):
    page.route("**/api/live", lambda route: route.fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(DEGRADED_FIXTURE_RESPONSE),
    ))
    page.goto(f"{live_server}/app")
    page.get_by_role("button", name="Arsenal contra Chelsea").click()
    expect(page.get_by_text("Datos parciales")).to_be_visible()
    expect(page.get_by_text("Dato no disponible")).to_be_visible()
    expect(page.locator("body")).not_to_have_css("overflow-x", "scroll")
```

- [ ] **Step 2: Confirm the E2E test fails**

Run: `python -m pytest tests/e2e/test_public_flow.py -q`

Expected: FAIL because `/app` is absent.

- [ ] **Step 3: Implement semantic dashboard markup and state machine**

The page includes:

- list of live/recent matches;
- active score/minute/freshness/model version;
- 1X2, next goal, ten-minute goal, totals and both-teams-score probabilities;
- recent signal summary and accessible timeline;
- explanatory text and no-bet/no-guarantee disclaimer;
- links back to `/` and `/modelo`.

Build every provider-derived element with `document.createElement`, `textContent`, `setAttribute` and validated values. Logo URLs must be HTTPS and match an explicit hostname allowlist derived from real provider responses; otherwise render initials. Add `loading="lazy"`, width/height and an error listener that replaces the image with initials.

Use `AbortController`, an 8-second fetch timeout and a 20-second refresh interval while visible. Pause polling under `document.hidden`. Keep only non-sensitive favorites in `localStorage`, validate stored IDs, and never persist API responses or tokens.

- [ ] **Step 4: Verify desktop, mobile, keyboard and reduced-motion behavior**

Run:

```powershell
python -m pytest tests/e2e/test_public_flow.py -q
rg -n "innerHTML|insertAdjacentHTML|document\.write|eval\(" public/app
```

Expected: E2E tests pass at 1440×900 and 390×844; unsafe DOM sinks have zero matches.

- [ ] **Step 5: Commit the dashboard**

```powershell
git add public/app tests/e2e/test_public_flow.py
git commit -m "feat: add public live prediction dashboard"
```

## Task 11: Add the model transparency page

**Files:**

- Create: `public/modelo/index.html`
- Create: `public/modelo/modelo.css`
- Create: `public/modelo/modelo.js`
- Test: `tests/e2e/test_model_page.py`

**Interfaces:**

- Consumes `GET /api/model/status`.
- Displays active version, samples, validation status, Brier/log loss, last run and limitations.

- [ ] **Step 1: Add a failing transparency test**

```python
def test_model_page_does_not_overclaim_live_accuracy(page, live_server):
    page.goto(f"{live_server}/modelo")
    expect(page.get_by_text("Evidencia live todavía en recolección")).to_be_visible()
    expect(page.get_by_text("70", exact=True)).to_be_visible()
    expect(page.get_by_text("30", exact=True)).to_be_visible()
    copy = page.locator("main").inner_text().lower()
    assert "precisión garantizada" not in copy
    assert "apuesta segura" not in copy
```

- [ ] **Step 2: Confirm failure**

Run: `python -m pytest tests/e2e/test_model_page.py -q`

Expected: FAIL because the page is absent.

- [ ] **Step 3: Implement the status page**

Render a large status statement, then the sample counter, active model, latest candidate decision and two metric comparisons. If metrics are missing or sample gates are unmet, render `Faltan datos verificados` rather than zero. Separate the historical pre-match benchmark into a clearly labeled antecedent and state that it does not validate live performance or other leagues.

Use the same safe DOM and typography rules as `/app`; avoid a grid of small cards. Provide plain-language definitions for Brier and log loss: lower is better.

- [ ] **Step 4: Verify empty and mature model states**

Run: `python -m pytest tests/e2e/test_model_page.py -q`

Expected: tests pass for collecting, rejected-candidate and active-validated fixtures.

- [ ] **Step 5: Commit model transparency**

```powershell
git add public/modelo tests/e2e/test_model_page.py
git commit -m "feat: add model transparency page"
```

## Task 12: Configure Vercel routes, security headers and Supabase Cron activation

**Files:**

- Create: `vercel.json`
- Create with CLI after Preview verification: `supabase/migrations/<generated>_schedule_collection_jobs.sql`
- Create: `scripts/smoke_preview.py`
- Modify: `README.md`
- Test: `tests/test_deployment_config.py`

**Interfaces:**

- Vercel maps `/api/*` to FastAPI and serves `public/**`.
- Supabase Cron calls only the protected same-origin job endpoints using Vault values.

- [ ] **Step 1: Add deployment-config tests**

```python
def test_security_headers_cover_all_routes():
    config = json.loads(Path("vercel.json").read_text())
    headers = {item["key"].lower(): item["value"] for item in config["headers"][0]["headers"]}
    assert "content-security-policy" in headers
    assert "unsafe-eval" not in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
```

- [ ] **Step 2: Confirm failure**

Run: `python -m pytest tests/test_deployment_config.py -q`

Expected: FAIL because `vercel.json` is absent.

- [ ] **Step 3: Add Vercel configuration**

Create `vercel.json` with clean URLs, rewrites for `/`, `/app`, `/modelo`, a bounded Python function duration and headers:

- CSP allowing only same-origin scripts/images/connect, local data images if required for fallback, and the exact local font policy;
- `frame-ancestors 'none'`;
- `X-Content-Type-Options: nosniff`;
- `Referrer-Policy: strict-origin-when-cross-origin`;
- minimal `Permissions-Policy` disabling camera, microphone, geolocation and payment;
- `Cache-Control: public, max-age=0, s-maxage=15, stale-while-revalidate=30` only on public GET API responses, not internal job routes.

Run `vercel dev` and confirm `/api/health`, `/`, `/app`, `/modelo` resolve through the same project.

- [ ] **Step 4: Add the Preview smoke script**

`scripts/smoke_preview.py` accepts `--base-url`, performs bounded GETs, validates JSON schema fragments, checks security headers, verifies no secret-like text appears, and exits non-zero unless all four pages and public API routes succeed.

Run: `python scripts/smoke_preview.py --base-url http://127.0.0.1:3000`

Expected: PASS against `vercel dev`.

- [ ] **Step 5: Create Cron migration only after Preview is green**

Run:

```powershell
supabase migration new schedule_collection_jobs
```

In the generated file, use `pg_cron`, `pg_net` and Vault lookups by fixed names `football_api_base_url` and `football_cron_secret`. Schedule:

- discover every 2 minutes;
- collect every minute;
- settle every 5 minutes;
- train once daily.

Each request builds `X-Idempotency-Key` from job name plus its current time bucket and sends `X-Cron-Secret` from Vault. The migration must fail clearly if extensions are unavailable, but must not contain either secret value. Keep each job bounded; do not overlap more than the database/provider budget permits.

- [ ] **Step 6: Verify deployment configuration and SQL**

Run:

```powershell
python -m pytest tests/test_deployment_config.py -q
supabase db reset
supabase test db
git grep -nE "(sb_secret_|service_role=|X-Cron-Secret[^A-Za-z]*[A-Za-z0-9_-]{20,})" -- ':!docs/superpowers/**'
```

Expected: tests pass and no committed secret value is found.

- [ ] **Step 7: Commit deploy configuration**

```powershell
git add vercel.json scripts/smoke_preview.py README.md tests/test_deployment_config.py supabase
git commit -m "ops: configure Vercel and scheduled jobs"
```

## Task 13: Run full verification, deploy Preview and activate production safely

**Files:**

- Modify: `docs/operations.md`
- Modify: `README.md`
- Modify only if failures require it: files introduced in Tasks 1–12

**Interfaces:**

- Produces a verified Preview URL first.
- Production and Cron are activated only after migrations, RLS, smoke and visual checks pass.

- [ ] **Step 1: Run the complete local quality gate**

```powershell
python -m compileall football_live api
python -m pytest -q
supabase db reset
supabase test db
supabase db lint --level warning
```

Expected: compilation succeeds; all Python and SQL tests pass; no unreviewed lint warning remains.

- [ ] **Step 2: Run static security checks**

```powershell
rg -n "innerHTML|insertAdjacentHTML|document\.write|eval\(|new Function|Access-Control-Allow-Origin:\s*\*" public football_live api
git grep -nE "(BEGIN (RSA|OPENSSH|EC) PRIVATE KEY|sb_secret_|SUPABASE_SERVICE_ROLE_KEY=.+|CRON_SECRET=.+)"
```

Expected: zero unsafe sink/wildcard matches and zero committed secret values. Identifier names and `.env.example` blanks are reviewed, not treated as leaks.

- [ ] **Step 3: Create/link infrastructure without exposing credentials**

Authenticate Supabase through its browser/CLI flow or a local environment token; never paste the token into chat or commit it. Link the intended Supabase project, apply migrations and verify the exact project reference before continuing. Set Vault entries through Supabase's protected interface.

Use the authenticated Vercel account to link the repository and set server-only environment variables for Preview and Production:

- `ENVIRONMENT`;
- `SUPABASE_URL`;
- `SUPABASE_SERVICE_ROLE_KEY`;
- `CRON_SECRET`;
- `ALLOWED_HOSTS`;
- `PUBLIC_ORIGIN`.

Never prefix privileged values with `NEXT_PUBLIC_`, `VITE_` or another browser-exposed prefix.

- [ ] **Step 4: Deploy Preview, then verify the full story**

Run: `vercel`

Expected: a Preview deployment reaches `Ready`.

Store the exact URL printed by Vercel in a process-local variable and run the smoke test without writing it to a repository file:

```powershell
$previewUrl = Read-Host 'URL Preview exacta impresa por Vercel'
python scripts/smoke_preview.py --base-url $previewUrl
```

Then execute one manual job twice with the same idempotency key from a local secret-bearing command environment. Expected: first run performs bounded work; second reports duplicate/prior result and creates no duplicate snapshot.

Use browser verification at 1440×900 and 390×844 for:

1. landing hero;
2. separate prediction section;
3. `/app` empty/live/degraded/stale states;
4. `/modelo` collecting and validated states;
5. keyboard focus and reduced motion;
6. real club logo plus initials fallback.

- [ ] **Step 5: Verify live-data truthfulness before activating Cron**

Confirm in Supabase that:

- a discovered fixture has one provider identity;
- repeated collection in one minute has one snapshot;
- prediction references the active model version;
- missing stats remain null;
- public projections omit raw provider payload and internal IDs;
- `anon` cannot select private tables or invoke job/promotion RPCs.

If no real match is live, record the collection path as technically verified with saved fixtures and keep the public UI in an explicit empty/demo state; do not claim real live success.

- [ ] **Step 6: Promote and observe production**

Only after the preceding gates are green, deploy/promote with `vercel --prod`, confirm terminal state `Ready`, update the Vault base URL to the final production origin and apply the Cron scheduling migration. Observe the first discover, collect and settle runs in `private.job_runs` without printing secrets.

If any production gate fails, roll back the Vercel deployment, unschedule Cron and keep the previous active model. Do not run destructive database rollback.

- [ ] **Step 7: Document evidence and current model status**

`docs/operations.md` must record:

- deployment and rollback steps;
- job cadence and time budgets;
- how to pause/resume Cron;
- how to rotate `CRON_SECRET` and Supabase server key;
- how to inspect job failures without exposing payloads;
- how to reactivate the previous model;
- the distinction between pre-match benchmark and unverified/maturing live evidence.

Update README with public routes, architecture, local setup and exact verification commands. Do not embed actual project IDs, URLs containing secrets or credentials.

- [ ] **Step 8: Commit operational documentation**

```powershell
git add README.md docs/operations.md
git commit -m "docs: add deployment and model operations guide"
```

## Final Acceptance Gate

- [ ] Landing is first, dashboard is separate, prediction example sits below the hero, and approved typography/players/hover direction is preserved.
- [ ] `/api/live`, `/api/matches/{public_id}`, `/api/model/status` and `/api/health` satisfy documented contracts.
- [ ] Internal jobs reject missing/incorrect secrets, are idempotent and leave an audit row.
- [ ] No browser code has a provider credential, Supabase server key or direct provider request.
- [ ] Snapshots and predictions survive process restarts and deployment changes.
- [ ] Missing/stale data is visibly degraded or suspended; no fabricated values appear.
- [ ] `anon` can read only approved projections and cannot write or invoke privileged RPCs.
- [ ] Training uses chronological 70/30 gates and promotes only when Brier and log loss both improve.
- [ ] The active model can be rolled back without redeploying code.
- [ ] Full Python, SQL, E2E, security and Preview smoke gates pass.
- [ ] Production is announced only after Vercel reports `Ready` and first scheduled jobs are observed.
