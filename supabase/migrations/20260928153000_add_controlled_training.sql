create table private.consumed_validation_fixtures (
  fixture_id uuid primary key references private.fixtures(id),
  training_run_id uuid not null references private.training_runs(id),
  consumed_at timestamptz not null default now()
);

create index consumed_validation_fixtures_training_run_id_idx
  on private.consumed_validation_fixtures (training_run_id);

alter table private.consumed_validation_fixtures enable row level security;

insert into private.consumed_validation_fixtures (fixture_id, training_run_id, consumed_at)
select distinct on (validation_ids.fixture_id)
  validation_ids.fixture_id,
  runs.id,
  coalesce(runs.finished_at, runs.started_at)
from private.training_runs as runs
cross join lateral unnest(runs.validation_fixture_ids) as validation_ids(fixture_id)
where runs.candidate_model_id is not null
  and validation_ids.fixture_id is not null
order by validation_ids.fixture_id, runs.started_at, runs.id;

create view private.training_examples
with (security_invoker = true)
as
with latest_snapshot_prediction as (
  select distinct on (snapshots.id)
    snapshots.id as snapshot_id,
    snapshots.fixture_id,
    snapshots.minute,
    snapshots.score_home,
    snapshots.score_away,
    snapshots.provider_observed_at,
    predictions.lambda_base,
    outcomes.home_score as final_home,
    outcomes.away_score as final_away,
    outcomes.confirmed_at as outcome_confirmed_at,
    case
      when snapshots.minute between 15 and 20 then 20
      when snapshots.minute between 35 and 40 then 40
      when snapshots.minute between 55 and 60 then 60
      when snapshots.minute between 75 and 80 then 80
    end as observation_window
  from private.live_snapshots as snapshots
  join private.predictions as predictions
    on predictions.snapshot_id = snapshots.id
   and predictions.fixture_id = snapshots.fixture_id
  join private.outcomes as outcomes
    on outcomes.fixture_id = snapshots.fixture_id
   and outcomes.confirmed
  where snapshots.minute between 15 and 80
    and predictions.quality in ('fresh', 'degraded')
    and snapshots.provider_observed_at < outcomes.confirmed_at
    and predictions.created_at < outcomes.confirmed_at
    and outcomes.confirmed_at - snapshots.provider_observed_at <= interval '6 hours'
    and outcomes.home_score >= snapshots.score_home
    and outcomes.away_score >= snapshots.score_away
    and jsonb_typeof(predictions.lambda_base) = 'object'
    and jsonb_typeof(predictions.lambda_base -> 'home') = 'number'
    and jsonb_typeof(predictions.lambda_base -> 'away') = 'number'
  order by snapshots.id, predictions.created_at desc, predictions.id desc
), ranked_windows as (
  select
    latest.*,
    row_number() over (
      partition by latest.fixture_id, latest.observation_window
      order by latest.minute desc, latest.provider_observed_at desc, latest.snapshot_id
    ) as window_rank
  from latest_snapshot_prediction as latest
  where latest.observation_window is not null
)
select
  fixture_id,
  min(provider_observed_at) as first_observed_at,
  min(outcome_confirmed_at) as outcome_confirmed_at,
  min(final_home) as final_home,
  min(final_away) as final_away,
  jsonb_agg(
    jsonb_build_object(
      'observed_at', provider_observed_at,
      'minute', minute,
      'score_home', score_home,
      'score_away', score_away,
      'lambda_base', lambda_base
    )
    order by provider_observed_at, minute, snapshot_id
  ) as observations
from ranked_windows
where window_rank = 1
group by fixture_id;

create function private.enforce_model_version_immutability()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if old.parameter_hash !~ '^[0-9a-f]{64}$' then
    return new;
  end if;

  if new.version is distinct from old.version
     or new.parameters is distinct from old.parameters
     or new.parameter_hash is distinct from old.parameter_hash
     or new.code_version is distinct from old.code_version
     or new.train_size is distinct from old.train_size
     or new.validation_size is distinct from old.validation_size
     or new.brier is distinct from old.brier
     or new.log_loss is distinct from old.log_loss
     or new.created_at is distinct from old.created_at then
    raise exception 'model evidence is immutable'
      using errcode = '22023';
  end if;

  if new.previous_model_id is distinct from old.previous_model_id
     and not (
       old.state = 'candidate'
       and new.state = 'active'
       and old.previous_model_id is null
       and new.previous_model_id is not null
     ) then
    raise exception 'model ancestry is immutable'
      using errcode = '22023';
  end if;

  if new.state is distinct from old.state
     and not (
       (old.state = 'candidate' and new.state in ('active', 'rejected'))
       or (old.state = 'active' and new.state = 'retired')
     ) then
    raise exception 'invalid model state transition'
      using errcode = '22023';
  end if;

  if new.activated_at is distinct from old.activated_at
     and not (
       old.state = 'candidate'
       and new.state = 'active'
       and old.activated_at is null
       and new.activated_at is not null
     ) then
    raise exception 'model activation timestamp is immutable'
      using errcode = '22023';
  end if;

  return new;
end;
$$;

create trigger model_versions_immutable_evidence
before update on private.model_versions
for each row execute function private.enforce_model_version_immutability();

create function private.record_training_evaluation(
  p_version text,
  p_parameters jsonb,
  p_parameter_hash text,
  p_code_version text,
  p_previous_model_id uuid,
  p_train_fixture_ids uuid[],
  p_validation_fixture_ids uuid[],
  p_metrics jsonb,
  p_approved boolean,
  p_reason text
)
returns private.training_runs
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_active private.model_versions%rowtype;
  v_candidate_id uuid;
  v_run private.training_runs%rowtype;
  v_train_count integer;
  v_validation_count integer;
  v_train_evidence_count integer;
  v_validation_evidence_count integer;
  v_train_max_confirmed_at timestamptz;
  v_validation_min_observed_at timestamptz;
  v_candidate_brier double precision;
  v_candidate_log_loss double precision;
  v_champion_brier double precision;
  v_champion_log_loss double precision;
  v_baseline_brier double precision;
  v_baseline_log_loss double precision;
  v_metrics_approve boolean;
begin
  if current_user <> 'service_role' then
    raise exception 'service_role is required'
      using errcode = '42501';
  end if;

  if nullif(btrim(p_version), '') is null
     or nullif(btrim(p_code_version), '') is null
     or p_parameter_hash !~ '^[0-9a-f]{64}$'
     or jsonb_typeof(p_parameters) is distinct from 'object'
     or p_previous_model_id is null
     or p_train_fixture_ids is null
     or p_validation_fixture_ids is null
     or p_approved is null
     or nullif(btrim(p_reason), '') is null then
    raise exception 'complete canonical training evidence is required'
      using errcode = '22023';
  end if;

  select * into v_active
  from private.model_versions
  where id = p_previous_model_id
  for update;

  if not found or v_active.state <> 'active' then
    raise exception 'previous model must be the active model'
      using errcode = '40001';
  end if;

  v_train_count := cardinality(p_train_fixture_ids);
  v_validation_count := cardinality(p_validation_fixture_ids);

  if v_train_count < 70 or v_validation_count <> 30 then
    raise exception 'training evidence requires at least 70 train and exactly 30 validation fixtures'
      using errcode = '22023';
  end if;

  if array_position(p_train_fixture_ids, null) is not null
     or array_position(p_validation_fixture_ids, null) is not null
     or (select count(distinct fixture_id) from unnest(p_train_fixture_ids) as ids(fixture_id)) <> v_train_count
     or (select count(distinct fixture_id) from unnest(p_validation_fixture_ids) as ids(fixture_id)) <> v_validation_count
     or exists (
       select 1
       from unnest(p_train_fixture_ids) as train_ids(fixture_id)
       join unnest(p_validation_fixture_ids) as validation_ids(fixture_id)
         using (fixture_id)
     ) then
    raise exception 'training fixture IDs must be non-null, unique, and disjoint'
      using errcode = '22023';
  end if;

  select count(*) into v_train_evidence_count
  from private.training_examples
  where fixture_id = any(p_train_fixture_ids);

  select count(*), min(first_observed_at)
  into v_validation_evidence_count, v_validation_min_observed_at
  from private.training_examples
  where fixture_id = any(p_validation_fixture_ids);

  if v_train_evidence_count <> v_train_count
     or v_validation_evidence_count <> v_validation_count then
    raise exception 'every fixture requires complete training evidence'
      using errcode = '22023';
  end if;

  select max(outcomes.confirmed_at)
  into v_train_max_confirmed_at
  from private.outcomes as outcomes
  where outcomes.fixture_id = any(p_train_fixture_ids)
    and outcomes.confirmed;

  if v_train_max_confirmed_at is null
     or v_validation_min_observed_at is null
     or v_train_max_confirmed_at >= v_validation_min_observed_at then
    raise exception 'validation observations must be later than all training outcome confirmations'
      using errcode = '22023';
  end if;

  if jsonb_typeof(p_metrics -> 'candidate' -> 'brier') is distinct from 'number'
     or jsonb_typeof(p_metrics -> 'candidate' -> 'log_loss') is distinct from 'number'
     or jsonb_typeof(p_metrics -> 'champion' -> 'brier') is distinct from 'number'
     or jsonb_typeof(p_metrics -> 'champion' -> 'log_loss') is distinct from 'number'
     or jsonb_typeof(p_metrics -> 'baseline' -> 'brier') is distinct from 'number'
     or jsonb_typeof(p_metrics -> 'baseline' -> 'log_loss') is distinct from 'number' then
    raise exception 'candidate, champion, and baseline metrics must be numeric'
      using errcode = '22023';
  end if;

  v_candidate_brier := (p_metrics -> 'candidate' ->> 'brier')::double precision;
  v_candidate_log_loss := (p_metrics -> 'candidate' ->> 'log_loss')::double precision;
  v_champion_brier := (p_metrics -> 'champion' ->> 'brier')::double precision;
  v_champion_log_loss := (p_metrics -> 'champion' ->> 'log_loss')::double precision;
  v_baseline_brier := (p_metrics -> 'baseline' ->> 'brier')::double precision;
  v_baseline_log_loss := (p_metrics -> 'baseline' ->> 'log_loss')::double precision;

  if v_candidate_brier in ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision)
     or v_candidate_log_loss in ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision)
     or v_champion_brier in ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision)
     or v_champion_log_loss in ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision)
     or v_baseline_brier in ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision)
     or v_baseline_log_loss in ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision)
     or least(
       v_candidate_brier,
       v_candidate_log_loss,
       v_champion_brier,
       v_champion_log_loss,
       v_baseline_brier,
       v_baseline_log_loss
     ) < 0 then
    raise exception 'candidate, champion, and baseline metrics must be finite and non-negative'
      using errcode = '22023';
  end if;

  v_metrics_approve :=
    v_candidate_brier < v_champion_brier
    and v_candidate_log_loss < v_champion_log_loss
    and v_candidate_brier < v_baseline_brier
    and v_candidate_log_loss < v_baseline_log_loss;

  if p_approved is distinct from v_metrics_approve then
    raise exception 'training decision does not match the strict metric gate'
      using errcode = '22023';
  end if;

  insert into private.model_versions (
    version,
    state,
    parameters,
    parameter_hash,
    code_version,
    previous_model_id,
    train_size,
    validation_size,
    brier,
    log_loss
  )
  values (
    p_version,
    case when p_approved then 'candidate'::private.model_state else 'rejected'::private.model_state end,
    p_parameters,
    p_parameter_hash,
    p_code_version,
    p_previous_model_id,
    v_train_count,
    v_validation_count,
    v_candidate_brier,
    v_candidate_log_loss
  )
  returning id into v_candidate_id;

  insert into private.training_runs (
    finished_at,
    train_fixture_ids,
    validation_fixture_ids,
    candidate_model_id,
    metrics,
    decision,
    reason
  )
  values (
    case when p_approved then null else now() end,
    p_train_fixture_ids,
    p_validation_fixture_ids,
    v_candidate_id,
    jsonb_build_object(
      'brier', v_candidate_brier,
      'log_loss', v_candidate_log_loss
    ) || p_metrics,
    case when p_approved then 'running'::private.run_state else 'rejected'::private.run_state end,
    p_reason
  )
  returning * into v_run;

  insert into private.consumed_validation_fixtures (fixture_id, training_run_id)
  select fixture_id, v_run.id
  from unnest(p_validation_fixture_ids) as validation_ids(fixture_id);

  return v_run;
end;
$$;

create or replace function private.promote_model(
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
  v_candidate_brier double precision;
  v_candidate_log_loss double precision;
  v_champion_brier double precision;
  v_champion_log_loss double precision;
  v_baseline_brier double precision;
  v_baseline_log_loss double precision;
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
    raise exception 'candidate model not found' using errcode = 'P0002';
  end if;

  select * into v_current
  from private.model_versions
  where id = p_current_id;
  if not found then
    raise exception 'current model not found' using errcode = 'P0002';
  end if;

  select id into v_active_id
  from private.model_versions
  where state = 'active';

  if v_active_id is distinct from p_current_id or v_current.state <> 'active' then
    raise exception 'current model ID does not match the active model'
      using errcode = '40001';
  end if;

  if v_candidate.state <> 'candidate'
     or (
       v_candidate.parameter_hash ~ '^[0-9a-f]{64}$'
       and v_candidate.previous_model_id is distinct from p_current_id
     ) then
    raise exception 'model to promote must be a candidate for the current model'
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
  if v_train_count < 70 or v_validation_count <> 30 then
    raise exception 'training evidence requires at least 70 train and exactly 30 validation fixtures'
      using errcode = '22023';
  end if;

  if array_position(v_run.train_fixture_ids, null) is not null
     or array_position(v_run.validation_fixture_ids, null) is not null
     or (select count(distinct fixture_id) from unnest(v_run.train_fixture_ids) as ids(fixture_id)) <> v_train_count
     or (select count(distinct fixture_id) from unnest(v_run.validation_fixture_ids) as ids(fixture_id)) <> v_validation_count
     or exists (
       select 1
       from unnest(v_run.train_fixture_ids) as train_ids(fixture_id)
       join unnest(v_run.validation_fixture_ids) as validation_ids(fixture_id)
         using (fixture_id)
     ) then
    raise exception 'training fixture IDs must be non-null, unique, and disjoint'
      using errcode = '22023';
  end if;

  if v_candidate.train_size <> v_train_count
     or v_candidate.validation_size <> v_validation_count then
    raise exception 'candidate sample counts must match its training run'
      using errcode = '22023';
  end if;

  select count(*) into v_train_evidence_count
  from unnest(v_run.train_fixture_ids) as ids(fixture_id)
  join private.outcomes as outcomes
    on outcomes.fixture_id = ids.fixture_id
   and outcomes.confirmed;

  select count(*) into v_validation_evidence_count
  from unnest(v_run.validation_fixture_ids) as ids(fixture_id)
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
     or v_validation_min_observed_at is null
     or v_train_max_confirmed_at is null
     or v_train_max_confirmed_at >= v_validation_min_observed_at then
    raise exception 'validation observations must be later than all training outcome confirmations'
      using errcode = '22023';
  end if;

  if jsonb_typeof(v_run.metrics -> 'candidate' -> 'brier') is distinct from 'number'
     or jsonb_typeof(v_run.metrics -> 'candidate' -> 'log_loss') is distinct from 'number'
     or jsonb_typeof(v_run.metrics -> 'champion' -> 'brier') is distinct from 'number'
     or jsonb_typeof(v_run.metrics -> 'champion' -> 'log_loss') is distinct from 'number'
     or jsonb_typeof(v_run.metrics -> 'baseline' -> 'brier') is distinct from 'number'
     or jsonb_typeof(v_run.metrics -> 'baseline' -> 'log_loss') is distinct from 'number' then
    raise exception 'training run requires candidate, champion, and baseline metrics'
      using errcode = '22023';
  end if;

  v_candidate_brier := (v_run.metrics -> 'candidate' ->> 'brier')::double precision;
  v_candidate_log_loss := (v_run.metrics -> 'candidate' ->> 'log_loss')::double precision;
  v_champion_brier := (v_run.metrics -> 'champion' ->> 'brier')::double precision;
  v_champion_log_loss := (v_run.metrics -> 'champion' ->> 'log_loss')::double precision;
  v_baseline_brier := (v_run.metrics -> 'baseline' ->> 'brier')::double precision;
  v_baseline_log_loss := (v_run.metrics -> 'baseline' ->> 'log_loss')::double precision;

  if v_candidate.brier is distinct from v_candidate_brier
     or v_candidate.log_loss is distinct from v_candidate_log_loss
     or v_candidate_brier in ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision)
     or v_candidate_log_loss in ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision)
     or v_champion_brier in ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision)
     or v_champion_log_loss in ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision)
     or v_baseline_brier in ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision)
     or v_baseline_log_loss in ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision)
     or least(
       v_candidate_brier,
       v_candidate_log_loss,
       v_champion_brier,
       v_champion_log_loss,
       v_baseline_brier,
       v_baseline_log_loss
     ) < 0 then
    raise exception 'candidate and comparison metrics must match and be finite'
      using errcode = '22023';
  end if;

  if v_candidate_brier >= v_champion_brier
     or v_candidate_log_loss >= v_champion_log_loss
     or v_candidate_brier >= v_baseline_brier
     or v_candidate_log_loss >= v_baseline_log_loss then
    raise exception 'candidate must strictly improve both metrics against champion and baseline'
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
    v_candidate_brier,
    v_candidate_log_loss,
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

revoke all on private.consumed_validation_fixtures from public, anon, authenticated, service_role;
revoke all on private.training_examples from public, anon, authenticated, service_role;
revoke all on function private.enforce_model_version_immutability() from public, anon, authenticated, service_role;
revoke all on function private.record_training_evaluation(text, jsonb, text, text, uuid, uuid[], uuid[], jsonb, boolean, text) from public, anon, authenticated, service_role;
revoke all on function private.promote_model(uuid, uuid) from public, anon, authenticated, service_role;

grant select, insert on private.consumed_validation_fixtures to service_role;
grant select on private.training_examples to service_role;
grant execute on function private.record_training_evaluation(text, jsonb, text, text, uuid, uuid[], uuid[], jsonb, boolean, text) to service_role;
grant execute on function private.promote_model(uuid, uuid) to service_role;
