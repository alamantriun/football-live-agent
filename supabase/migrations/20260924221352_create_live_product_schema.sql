create extension if not exists pgcrypto;

create schema if not exists private;

revoke all on schema private from public, anon, authenticated;
grant usage on schema private to service_role;

alter default privileges in schema private
  revoke all on tables from public, anon, authenticated;
alter default privileges in schema private
  revoke all on sequences from public, anon, authenticated;
alter default privileges in schema private
  revoke execute on functions from public, anon, authenticated;
alter default privileges in schema private
  revoke usage on types from public, anon, authenticated;

alter default privileges in schema public
  revoke all on tables from public, anon, authenticated, service_role;
alter default privileges in schema public
  revoke all on sequences from public, anon, authenticated, service_role;
alter default privileges in schema public
  revoke execute on functions from public, anon, authenticated, service_role;

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

create unique index one_active_model
  on private.model_versions ((state))
  where state = 'active';

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

create index fixtures_status_scheduled_at_idx
  on private.fixtures (status, scheduled_at);
create index live_snapshots_fixture_collected_at_idx
  on private.live_snapshots (fixture_id, collected_at desc);
create index model_versions_previous_model_id_idx
  on private.model_versions (previous_model_id)
  where previous_model_id is not null;
create index predictions_fixture_created_at_idx
  on private.predictions (fixture_id, created_at desc);
create index predictions_model_version_id_idx
  on private.predictions (model_version_id);
create unique index training_runs_candidate_model_id_unique
  on private.training_runs (candidate_model_id)
  where candidate_model_id is not null;
create index training_runs_finished_at_idx
  on private.training_runs (finished_at desc)
  where finished_at is not null;
create index job_runs_active_lease_idx
  on private.job_runs (lease_until, job_name)
  where state = 'running';

create table public.live_match_projection (
  public_id uuid primary key,
  competition text not null,
  home_name text not null,
  away_name text not null,
  home_logo_url text,
  away_logo_url text,
  scheduled_at timestamptz,
  status text not null,
  minute smallint check (minute between 0 and 150),
  score_home smallint check (score_home between 0 and 30),
  score_away smallint check (score_away between 0 and 30),
  data_status text not null check (data_status in ('fresh', 'degraded', 'stale', 'suspended')),
  provider_observed_at timestamptz,
  collected_at timestamptz,
  probabilities jsonb,
  explanation jsonb,
  model_version text,
  prediction_created_at timestamptz,
  updated_at timestamptz not null default now()
);

create index live_match_projection_status_updated_at_idx
  on public.live_match_projection (status, updated_at desc);

create table public.model_status_projection (
  singleton boolean primary key default true check (singleton),
  version text not null,
  train_size integer not null check (train_size >= 0),
  validation_size integer not null check (validation_size >= 0),
  brier double precision not null,
  log_loss double precision not null,
  activated_at timestamptz not null,
  last_training_finished_at timestamptz,
  last_training_decision text,
  updated_at timestamptz not null default now()
);

alter table private.fixtures enable row level security;
alter table private.live_snapshots enable row level security;
alter table private.predictions enable row level security;
alter table private.outcomes enable row level security;
alter table private.model_versions enable row level security;
alter table private.training_runs enable row level security;
alter table private.job_runs enable row level security;
alter table public.live_match_projection enable row level security;
alter table public.model_status_projection enable row level security;

create policy live_match_projection_public_read
  on public.live_match_projection
  for select
  to anon, authenticated
  using (true);

create policy model_status_projection_public_read
  on public.model_status_projection
  for select
  to anon, authenticated
  using (true);

create view public.live_matches with (security_invoker = true) as
select * from public.live_match_projection;

create view public.model_status with (security_invoker = true) as
select * from public.model_status_projection;

create function private.claim_job(
  p_job_name text,
  p_idempotency_key text,
  p_lease_seconds integer
)
returns setof private.job_runs
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if current_user <> 'service_role' then
    raise exception 'service_role is required'
      using errcode = '42501';
  end if;

  if nullif(btrim(p_job_name), '') is null
     or nullif(btrim(p_idempotency_key), '') is null then
    raise exception 'job name and idempotency key are required'
      using errcode = '22023';
  end if;

  if p_lease_seconds is null or p_lease_seconds not between 30 and 900 then
    raise exception 'lease seconds must be between 30 and 900'
      using errcode = '22023';
  end if;

  return query
  insert into private.job_runs as existing (
    job_name,
    idempotency_key,
    state,
    lease_until,
    request_id
  )
  values (
    p_job_name,
    p_idempotency_key,
    'running',
    now() + make_interval(secs => p_lease_seconds),
    gen_random_uuid()
  )
  on conflict (job_name, idempotency_key) do update
  set state = 'running',
      lease_until = excluded.lease_until,
      request_id = excluded.request_id,
      counters = '{}'::jsonb,
      sanitized_error = null,
      started_at = now(),
      finished_at = null
  where existing.state = 'running'
    and existing.lease_until <= now()
  returning existing.*;
end;
$$;

create function private.finish_job(
  p_run_id uuid,
  p_request_id uuid,
  p_state text,
  p_counters jsonb default '{}'::jsonb,
  p_sanitized_error text default null
)
returns private.job_runs
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_run private.job_runs%rowtype;
begin
  if current_user <> 'service_role' then
    raise exception 'service_role is required'
      using errcode = '42501';
  end if;

  if p_state is null or p_state not in ('succeeded', 'failed', 'rejected') then
    raise exception 'terminal state must be succeeded, failed, or rejected'
      using errcode = '22023';
  end if;

  update private.job_runs
  set state = p_state::private.run_state,
      counters = coalesce(p_counters, '{}'::jsonb),
      sanitized_error = case
        when p_state = 'succeeded' then null
        else p_sanitized_error
      end,
      lease_until = now(),
      finished_at = now()
  where id = p_run_id
    and request_id = p_request_id
    and state = 'running'
    and lease_until >= now()
  returning * into v_run;

  if not found then
    raise exception 'running job lease not found, fenced, or expired'
      using errcode = 'P0002';
  end if;

  return v_run;
end;
$$;

create function private.promote_model(
  p_candidate_id uuid,
  p_current_id uuid
)
returns private.model_versions
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_candidate private.model_versions%rowtype;
  v_current private.model_versions%rowtype;
  v_run private.training_runs%rowtype;
  v_active_id uuid;
  v_train_count integer;
  v_validation_count integer;
  v_train_evidence_count integer;
  v_validation_evidence_count integer;
  v_validation_snapshot_count integer;
  v_train_max_confirmed_at timestamptz;
  v_validation_min_observed_at timestamptz;
  v_run_brier double precision;
  v_run_log_loss double precision;
begin
  if current_user <> 'service_role' then
    raise exception 'service_role is required'
      using errcode = '42501';
  end if;

  if p_candidate_id is null
     or p_current_id is null
     or p_candidate_id = p_current_id then
    raise exception 'distinct candidate and current model IDs are required'
      using errcode = '22023';
  end if;

  perform 1
  from private.model_versions
  where id in (p_candidate_id, p_current_id)
  order by id
  for update;

  select * into v_candidate
  from private.model_versions
  where id = p_candidate_id;

  if not found then
    raise exception 'candidate model not found'
      using errcode = 'P0002';
  end if;

  select * into v_current
  from private.model_versions
  where id = p_current_id;

  if not found then
    raise exception 'current model not found'
      using errcode = 'P0002';
  end if;

  select id into v_active_id
  from private.model_versions
  where state = 'active';

  if v_active_id is distinct from p_current_id or v_current.state <> 'active' then
    raise exception 'current model ID does not match the active model'
      using errcode = '40001';
  end if;

  if v_candidate.state <> 'candidate' then
    raise exception 'model to promote must be a candidate'
      using errcode = '22023';
  end if;

  select * into v_run
  from private.training_runs
  where candidate_model_id = p_candidate_id
  for update;

  if not found then
    raise exception 'candidate must be bound to exactly one training run'
      using errcode = '22023';
  end if;

  if v_run.decision <> 'running' or v_run.finished_at is not null then
    raise exception 'training run is not eligible for promotion'
      using errcode = '22023';
  end if;

  v_train_count := cardinality(v_run.train_fixture_ids);
  v_validation_count := cardinality(v_run.validation_fixture_ids);

  if v_train_count < 70 or v_validation_count < 30 then
    raise exception 'training evidence requires at least 70 train and 30 validation fixtures'
      using errcode = '22023';
  end if;

  if exists (
       select 1 from unnest(v_run.train_fixture_ids) as ids(fixture_id)
       where fixture_id is null
     )
     or exists (
       select 1 from unnest(v_run.validation_fixture_ids) as ids(fixture_id)
       where fixture_id is null
     ) then
    raise exception 'training evidence cannot contain null fixture IDs'
      using errcode = '22023';
  end if;

  if (select count(distinct fixture_id) from unnest(v_run.train_fixture_ids) as ids(fixture_id)) <> v_train_count
     or (select count(distinct fixture_id) from unnest(v_run.validation_fixture_ids) as ids(fixture_id)) <> v_validation_count then
    raise exception 'training evidence cannot contain duplicate fixture IDs'
      using errcode = '22023';
  end if;

  if exists (
    select 1
    from unnest(v_run.train_fixture_ids) as train_ids(fixture_id)
    join unnest(v_run.validation_fixture_ids) as validation_ids(fixture_id)
      using (fixture_id)
  ) then
    raise exception 'training and validation fixtures cannot overlap'
      using errcode = '22023';
  end if;

  if v_candidate.train_size <> v_train_count
     or v_candidate.validation_size <> v_validation_count then
    raise exception 'candidate sample counts must match its training run'
      using errcode = '22023';
  end if;

  select count(*) into v_train_evidence_count
  from unnest(v_run.train_fixture_ids) as ids(fixture_id)
  join private.fixtures as fixtures on fixtures.id = ids.fixture_id
  join private.outcomes as outcomes
    on outcomes.fixture_id = ids.fixture_id
   and outcomes.confirmed;

  select count(*) into v_validation_evidence_count
  from unnest(v_run.validation_fixture_ids) as ids(fixture_id)
  join private.fixtures as fixtures on fixtures.id = ids.fixture_id
  join private.outcomes as outcomes
    on outcomes.fixture_id = ids.fixture_id
   and outcomes.confirmed;

  if v_train_evidence_count <> v_train_count
     or v_validation_evidence_count <> v_validation_count then
    raise exception 'every train and validation fixture requires a confirmed outcome'
      using errcode = '22023';
  end if;

  select max(outcomes.confirmed_at)
  into v_train_max_confirmed_at
  from private.outcomes as outcomes
  where outcomes.fixture_id = any(v_run.train_fixture_ids)
    and outcomes.confirmed;

  select count(distinct snapshots.fixture_id), min(snapshots.provider_observed_at)
  into v_validation_snapshot_count, v_validation_min_observed_at
  from private.live_snapshots as snapshots
  where snapshots.fixture_id = any(v_run.validation_fixture_ids);

  if v_validation_snapshot_count <> v_validation_count
     or v_validation_min_observed_at is null then
    raise exception 'every validation fixture requires provider observation evidence'
      using errcode = '22023';
  end if;

  if v_train_max_confirmed_at is null
     or v_train_max_confirmed_at >= v_validation_min_observed_at then
    raise exception 'validation observations must be later than all training outcome confirmations'
      using errcode = '22023';
  end if;

  if jsonb_typeof(v_run.metrics -> 'brier') is distinct from 'number'
     or jsonb_typeof(v_run.metrics -> 'log_loss') is distinct from 'number' then
    raise exception 'training run metrics must contain numeric Brier and log loss'
      using errcode = '22023';
  end if;

  v_run_brier := (v_run.metrics ->> 'brier')::double precision;
  v_run_log_loss := (v_run.metrics ->> 'log_loss')::double precision;

  if v_candidate.brier is null
     or v_candidate.log_loss is null
     or v_current.brier is null
     or v_current.log_loss is null
     or v_run_brier is null
     or v_run_log_loss is null
     or v_candidate.brier in ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision)
     or v_candidate.log_loss in ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision)
     or v_current.brier in ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision)
     or v_current.log_loss in ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision)
     or v_run_brier in ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision)
     or v_run_log_loss in ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision) then
    raise exception 'candidate, run, and current metrics must be finite'
      using errcode = '22023';
  end if;

  if v_candidate.brier is distinct from v_run_brier
     or v_candidate.log_loss is distinct from v_run_log_loss then
    raise exception 'candidate metrics must match its training run'
      using errcode = '22023';
  end if;

  if v_run_brier >= v_current.brier
     or v_run_log_loss >= v_current.log_loss then
    raise exception 'candidate must strictly improve both Brier and log loss'
      using errcode = '22023';
  end if;

  update private.model_versions
  set state = 'retired'
  where id = p_current_id;

  update private.model_versions
  set state = 'active',
      previous_model_id = p_current_id,
      activated_at = now()
  where id = p_candidate_id
  returning * into v_candidate;

  update private.training_runs
  set decision = 'succeeded',
      reason = 'candidate promoted',
      finished_at = now()
  where id = v_run.id
  returning * into v_run;

  insert into public.model_status_projection (
    singleton,
    version,
    train_size,
    validation_size,
    brier,
    log_loss,
    activated_at,
    last_training_finished_at,
    last_training_decision,
    updated_at
  )
  values (
    true,
    v_candidate.version,
    v_train_count,
    v_validation_count,
    v_run_brier,
    v_run_log_loss,
    v_candidate.activated_at,
    v_run.finished_at,
    v_run.decision::text,
    now()
  )
  on conflict (singleton) do update
  set version = excluded.version,
      train_size = excluded.train_size,
      validation_size = excluded.validation_size,
      brier = excluded.brier,
      log_loss = excluded.log_loss,
      activated_at = excluded.activated_at,
      last_training_finished_at = excluded.last_training_finished_at,
      last_training_decision = excluded.last_training_decision,
      updated_at = excluded.updated_at;

  return v_candidate;
end;
$$;

revoke all on all tables in schema private from public, anon, authenticated, service_role;
revoke all on all sequences in schema private from public, anon, authenticated, service_role;
revoke all on all functions in schema private from public, anon, authenticated, service_role;
revoke usage on type private.model_state, private.run_state from public, anon, authenticated, service_role;

grant usage on schema private to service_role;
grant usage on type private.model_state, private.run_state to service_role;
grant select, insert, update on all tables in schema private to service_role;

grant execute on function private.claim_job(text, text, integer) to service_role;
grant execute on function private.finish_job(uuid, uuid, text, jsonb, text) to service_role;
grant execute on function private.promote_model(uuid, uuid) to service_role;

revoke all on public.live_match_projection,
  public.model_status_projection,
  public.live_matches,
  public.model_status
  from public, anon, authenticated, service_role;

grant usage on schema public to anon, authenticated, service_role;
grant select on public.live_match_projection,
  public.model_status_projection,
  public.live_matches,
  public.model_status
  to anon, authenticated, service_role;

grant insert, update on public.live_match_projection,
  public.model_status_projection
  to service_role;
