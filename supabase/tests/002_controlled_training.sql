begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(26);

select has_view('private', 'training_examples', 'training example projection exists');
select has_table('private', 'consumed_validation_fixtures', 'validation consumption ledger exists');
select has_function(
  'private',
  'record_training_evaluation',
  array['text', 'jsonb', 'text', 'text', 'uuid', 'uuid[]', 'uuid[]', 'jsonb', 'boolean', 'text'],
  'atomic training evidence RPC exists'
);
select ok(
  has_function_privilege(
    'service_role',
    'private.record_training_evaluation(text,jsonb,text,text,uuid,uuid[],uuid[],jsonb,boolean,text)',
    'EXECUTE'
  ),
  'service role can record training evidence'
);
select ok(
  not has_function_privilege(
    'anon',
    'private.record_training_evaluation(text,jsonb,text,text,uuid,uuid[],uuid[],jsonb,boolean,text)',
    'EXECUTE'
  ),
  'anon cannot record training evidence'
);
select ok(
  not has_table_privilege('anon', 'private.consumed_validation_fixtures', 'SELECT'),
  'anon cannot read consumed validation IDs'
);

set local role anon;
select throws_ok(
  $$select count(*) from private.training_examples$$,
  '42501',
  null,
  'anon cannot read private training examples'
);
reset role;
set local role service_role;

insert into private.model_versions (
  id, version, state, parameters, parameter_hash, code_version,
  train_size, validation_size, brier, log_loss, activated_at
)
values (
  '40000000-0000-0000-0000-000000000001',
  'training-champion',
  'active',
  '{"factores":{"local":1.0,"visitante":1.0}}',
  repeat('0', 64),
  'old-code',
  70,
  30,
  0.05,
  0.10,
  now()
);

insert into private.fixtures (
  provider, provider_fixture_id, competition, home_name, away_name,
  scheduled_at, status, finished_at
)
select
  '365scores',
  'controlled-' || lpad(n::text, 3, '0'),
  'test league',
  'home ' || n,
  'away ' || n,
  '2026-02-01 10:00:00+00'::timestamptz + make_interval(days => n),
  'finished',
  '2026-02-01 12:00:00+00'::timestamptz + make_interval(days => n)
from generate_series(1, 130) as series(n);

insert into private.outcomes (
  fixture_id, home_score, away_score, source, confirmed, confirmed_at
)
select
  id,
  2,
  0,
  'test',
  true,
  scheduled_at + interval '2 hours'
from private.fixtures
where provider_fixture_id like 'controlled-%';

insert into private.live_snapshots (
  fixture_id, minute, score_home, score_away, normalized_stats,
  sanitized_provider_data, provider_observed_at, collected_at,
  quality, observation_bucket
)
select
  id,
  60,
  0,
  0,
  '{}'::jsonb,
  '{}'::jsonb,
  scheduled_at,
  scheduled_at + interval '1 second',
  'fresh',
  scheduled_at
from private.fixtures
where provider_fixture_id like 'controlled-%';

insert into private.predictions (
  fixture_id, snapshot_id, model_version_id, lambda_base,
  lambda_adjusted, probabilities, explanation, quality, created_at
)
select
  snapshots.fixture_id,
  snapshots.id,
  '40000000-0000-0000-0000-000000000001',
  '{"home":0.8,"away":0.2}'::jsonb,
  '{"home":0.8,"away":0.2}'::jsonb,
  '{"home":50,"draw":30,"away":20}'::jsonb,
  '{}'::jsonb,
  'fresh',
  snapshots.provider_observed_at + interval '1 second'
from private.live_snapshots as snapshots
join private.fixtures as fixtures on fixtures.id = snapshots.fixture_id
where fixtures.provider_fixture_id like 'controlled-%';

select is(
  (select count(*) from private.training_examples),
  130::bigint,
  'the view returns one row per eligible fixture'
);
select is(
  (select jsonb_array_length(observations) from private.training_examples limit 1),
  1,
  'training observations are grouped per fixture'
);

update private.predictions
set created_at = '2027-01-01 00:00:00+00'
where fixture_id = (
  select id from private.fixtures where provider_fixture_id = 'controlled-130'
);
select is(
  (select count(*) from private.training_examples),
  129::bigint,
  'predictions created after outcome confirmation are never training evidence'
);
update private.predictions
set created_at = (
  select scheduled_at + interval '1 second'
  from private.fixtures
  where provider_fixture_id = 'controlled-130'
)
where fixture_id = (
  select id from private.fixtures where provider_fixture_id = 'controlled-130'
);

select throws_ok(
  $$
    select private.record_training_evaluation(
      'too-small',
      '{"factores":{"local":1.1,"visitante":0.9}}',
      repeat('1', 64),
      'new-code',
      '40000000-0000-0000-0000-000000000001',
      (select array_agg(id order by provider_fixture_id) from private.fixtures where provider_fixture_id between 'controlled-001' and 'controlled-070'),
      (select array_agg(id order by provider_fixture_id) from private.fixtures where provider_fixture_id between 'controlled-071' and 'controlled-099'),
      '{"candidate":{"brier":0.4,"log_loss":0.8},"champion":{"brier":0.4,"log_loss":0.8},"baseline":{"brier":0.5,"log_loss":0.9}}',
      false,
      'rejected'
    )
  $$,
  '22023',
  null,
  'recording requires exactly 30 validation fixtures'
);

update private.outcomes
set confirmed_at = '2027-01-01 00:00:00+00'
where fixture_id = (
  select id from private.fixtures where provider_fixture_id = 'controlled-001'
);
select throws_ok(
  $$
    select private.record_training_evaluation(
      'bad-chronology',
      '{"factores":{"local":1.1,"visitante":0.9}}',
      repeat('2', 64),
      'new-code',
      '40000000-0000-0000-0000-000000000001',
      (select array_agg(id order by provider_fixture_id) from private.fixtures where provider_fixture_id between 'controlled-001' and 'controlled-070'),
      (select array_agg(id order by provider_fixture_id) from private.fixtures where provider_fixture_id between 'controlled-071' and 'controlled-100'),
      '{"candidate":{"brier":0.4,"log_loss":0.8},"champion":{"brier":0.4,"log_loss":0.8},"baseline":{"brier":0.5,"log_loss":0.9}}',
      false,
      'rejected'
    )
  $$,
  '22023',
  null,
  'recording rejects leaked chronology before consuming validation IDs'
);
update private.outcomes
set confirmed_at = (
  select scheduled_at + interval '2 hours'
  from private.fixtures
  where provider_fixture_id = 'controlled-001'
)
where fixture_id = (
  select id from private.fixtures where provider_fixture_id = 'controlled-001'
);

select throws_ok(
  $$
    select private.record_training_evaluation(
      'wrong-decision',
      '{"factores":{"local":1.1,"visitante":0.9}}',
      repeat('3', 64),
      'new-code',
      '40000000-0000-0000-0000-000000000001',
      (select array_agg(id order by provider_fixture_id) from private.fixtures where provider_fixture_id between 'controlled-001' and 'controlled-070'),
      (select array_agg(id order by provider_fixture_id) from private.fixtures where provider_fixture_id between 'controlled-071' and 'controlled-100'),
      '{"candidate":{"brier":0.3,"log_loss":0.7},"champion":{"brier":0.4,"log_loss":0.8},"baseline":{"brier":0.5,"log_loss":0.9}}',
      false,
      'rejected'
    )
  $$,
  '22023',
  null,
  'the persisted decision must match the strict metric gate'
);

select set_config(
  'test.rejected_run_id',
  (select id::text from private.record_training_evaluation(
    'controlled-rejected',
    '{"factores":{"local":1.1,"visitante":0.9}}',
    repeat('4', 64),
    'new-code',
    '40000000-0000-0000-0000-000000000001',
    (select array_agg(id order by provider_fixture_id) from private.fixtures where provider_fixture_id between 'controlled-001' and 'controlled-070'),
    (select array_agg(id order by provider_fixture_id) from private.fixtures where provider_fixture_id between 'controlled-071' and 'controlled-100'),
    '{"candidate":{"brier":0.4,"log_loss":0.8},"champion":{"brier":0.4,"log_loss":0.8},"baseline":{"brier":0.5,"log_loss":0.9}}',
    false,
    'candidate did not strictly improve both metrics'
  )),
  true
);

select is(
  (select state::text from private.model_versions where version = 'controlled-rejected'),
  'rejected',
  'a losing candidate is retained as rejected evidence'
);
select is(
  (select decision::text from private.training_runs where id = current_setting('test.rejected_run_id')::uuid),
  'rejected',
  'a losing training run is finalized as rejected'
);
select is(
  (select count(*) from private.consumed_validation_fixtures where training_run_id = current_setting('test.rejected_run_id')::uuid),
  30::bigint,
  'a rejected run consumes all 30 validation fixtures'
);
select is(
  (select count(*) from private.consumed_validation_fixtures),
  30::bigint,
  'the consumed validation projection exposes the rejected holdout'
);
select throws_ok(
  $$
    update private.model_versions
    set parameters = '{"factores":{"local":1.6,"visitante":0.6}}'
    where version = 'controlled-rejected'
  $$,
  '22023',
  null,
  'persisted candidate parameters are immutable'
);
select throws_ok(
  $$
    select private.record_training_evaluation(
      'reused-holdout',
      '{"factores":{"local":1.1,"visitante":0.9}}',
      repeat('5', 64),
      'new-code',
      '40000000-0000-0000-0000-000000000001',
      (select array_agg(id order by provider_fixture_id) from private.fixtures where provider_fixture_id between 'controlled-001' and 'controlled-070'),
      (select array_agg(id order by provider_fixture_id) from private.fixtures where provider_fixture_id between 'controlled-071' and 'controlled-100'),
      '{"candidate":{"brier":0.4,"log_loss":0.8},"champion":{"brier":0.4,"log_loss":0.8},"baseline":{"brier":0.5,"log_loss":0.9}}',
      false,
      'rejected'
    )
  $$,
  '23505',
  null,
  'consumed validation fixtures cannot be recorded twice'
);

select set_config(
  'test.accepted_run_id',
  (select id::text from private.record_training_evaluation(
    'controlled-candidate',
    '{"factores":{"local":1.2,"visitante":0.8}}',
    repeat('6', 64),
    'new-code',
    '40000000-0000-0000-0000-000000000001',
    (select array_agg(id order by provider_fixture_id) from private.fixtures where provider_fixture_id between 'controlled-001' and 'controlled-100'),
    (select array_agg(id order by provider_fixture_id) from private.fixtures where provider_fixture_id between 'controlled-101' and 'controlled-130'),
    '{"candidate":{"brier":0.3,"log_loss":0.7},"champion":{"brier":0.4,"log_loss":0.8},"baseline":{"brier":0.5,"log_loss":0.9}}',
    true,
    'candidate strictly improved both metrics'
  )),
  true
);

select is(
  (select state::text from private.model_versions where version = 'controlled-candidate'),
  'candidate',
  'a winning candidate is persisted before promotion'
);
select is(
  (select decision::text from private.training_runs where id = current_setting('test.accepted_run_id')::uuid),
  'running',
  'a winning run remains pending until transactional promotion'
);
select is(
  (select count(*) from private.consumed_validation_fixtures where training_run_id = current_setting('test.accepted_run_id')::uuid),
  30::bigint,
  'a winning run also consumes its holdout before promotion'
);

select lives_ok(
  $$
    select private.promote_model(
      (select candidate_model_id from private.training_runs where id = current_setting('test.accepted_run_id')::uuid),
      '40000000-0000-0000-0000-000000000001'
    )
  $$,
  'promotion uses holdout-relative champion and baseline metrics'
);
select is(
  (select state::text from private.model_versions where version = 'controlled-candidate'),
  'active',
  'the accepted immutable candidate becomes active'
);
select is(
  (select decision::text from private.training_runs where id = current_setting('test.accepted_run_id')::uuid),
  'succeeded',
  'the accepted run is finalized by promote_model'
);
select is(
  (select count(*) from private.consumed_validation_fixtures),
  60::bigint,
  'accepted and rejected holdouts remain consumed'
);

reset role;
select * from finish();
rollback;
