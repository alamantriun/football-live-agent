begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(71);

select has_view('private', 'training_examples', 'training example projection exists');
select has_table('private', 'consumed_validation_fixtures', 'validation consumption ledger exists');
select col_is_pk('private', 'consumed_validation_fixtures', 'fixture_id', 'consumed IDs have one authoritative row');
select has_column('private', 'training_runs', 'parameter_hash', 'training runs bind the canonical parameter hash');
select has_column('private', 'model_versions', 'evidence_origin', 'model provenance is explicit');

select has_function(
  'private',
  'record_training_evaluation',
  array['uuid', 'jsonb', 'text', 'uuid', 'uuid[]', 'uuid[]', 'jsonb', 'boolean', 'text'],
  'atomic training evidence RPC uses a caller-assigned run ID but no caller hash'
);
select has_function('private', 'finalize_training_evaluation', array['uuid'], 'terminal reconciliation RPC exists');
select has_function(
  'private',
  'recover_abandoned_training_evaluations',
  array['integer', 'integer'],
  'bounded stale-run recovery RPC exists'
);
select ok(
  (select not prosecdef
          and provolatile = 'i'
          and proisstrict
          and proparallel = 's'
          and 'search_path=""' = any(coalesce(proconfig, array[]::text[]))
   from pg_proc
   where oid = 'private.compute_training_hash(jsonb,uuid[],uuid[],text)'::regprocedure),
  'training hash is immutable, strict, parallel safe, SECURITY INVOKER, and empty-search-path'
);
select ok(
  (select position('extensions.digest(' in prosrc) > 0
          and position('public.digest(' in prosrc) = 0
   from pg_proc
   where oid = 'private.compute_training_hash(jsonb,uuid[],uuid[],text)'::regprocedure),
  'training hash binds digest to the pgcrypto extensions schema'
);
select is(
  private.compute_training_hash(
    '{"b":2,"a":1}',
    array[
      '00000000-0000-0000-0000-000000000002',
      '00000000-0000-0000-0000-000000000001'
    ]::uuid[],
    array[
      '00000000-0000-0000-0000-000000000004',
      '00000000-0000-0000-0000-000000000003'
    ]::uuid[],
    'schema-fix'
  ),
  '5d18e7032342bdeb284aa34cd252e7e3bc3483193e2baa48d2c294e6077a4350',
  'training hash executes pgcrypto digest and remains canonical'
);
select ok(
  has_function_privilege('service_role', 'private.compute_training_hash(jsonb,uuid[],uuid[],text)', 'EXECUTE')
  and not has_function_privilege('anon', 'private.compute_training_hash(jsonb,uuid[],uuid[],text)', 'EXECUTE')
  and not has_function_privilege('authenticated', 'private.compute_training_hash(jsonb,uuid[],uuid[],text)', 'EXECUTE')
  and not exists (
    select 1
    from pg_proc as procedures
    cross join lateral aclexplode(coalesce(procedures.proacl, acldefault('f', procedures.proowner))) as grants
    where procedures.oid = 'private.compute_training_hash(jsonb,uuid[],uuid[],text)'::regprocedure
      and grants.grantee = 0
      and grants.privilege_type = 'EXECUTE'
  ),
  'training hash execute is granted only to service_role'
);

select ok(
  not (select prosecdef from pg_proc where oid = 'private.record_training_evaluation(uuid,jsonb,text,uuid,uuid[],uuid[],jsonb,boolean,text)'::regprocedure)
  and not (select prosecdef from pg_proc where oid = 'private.promote_model(uuid,uuid)'::regprocedure),
  'record and promotion RPCs are SECURITY INVOKER'
);
select ok(
  not (select prosecdef from pg_proc where oid = 'private.finalize_training_evaluation(uuid)'::regprocedure),
  'finalize RPC is SECURITY INVOKER'
);
select ok(
  not (select prosecdef from pg_proc where oid = 'private.recover_abandoned_training_evaluations(integer,integer)'::regprocedure),
  'recovery RPC is SECURITY INVOKER'
);
select ok(
  'search_path=""' = any(coalesce((select proconfig from pg_proc where oid = 'private.record_training_evaluation(uuid,jsonb,text,uuid,uuid[],uuid[],jsonb,boolean,text)'::regprocedure), array[]::text[]))
  and 'search_path=""' = any(coalesce((select proconfig from pg_proc where oid = 'private.promote_model(uuid,uuid)'::regprocedure), array[]::text[])),
  'record and promotion RPCs have an empty search_path'
);
select ok(
  'search_path=""' = any(coalesce((select proconfig from pg_proc where oid = 'private.finalize_training_evaluation(uuid)'::regprocedure), array[]::text[])),
  'finalize RPC has an empty search_path'
);
select ok(
  'search_path=""' = any(coalesce((select proconfig from pg_proc where oid = 'private.recover_abandoned_training_evaluations(integer,integer)'::regprocedure), array[]::text[])),
  'recovery RPC has an empty search_path'
);
select ok(
  (select prosrc like '%current_user <> ''service_role''%' from pg_proc where oid = 'private.record_training_evaluation(uuid,jsonb,text,uuid,uuid[],uuid[],jsonb,boolean,text)'::regprocedure)
  and (select prosrc like '%current_user <> ''service_role''%' from pg_proc where oid = 'private.promote_model(uuid,uuid)'::regprocedure)
  and (select prosrc like '%current_user <> ''service_role''%' from pg_proc where oid = 'private.finalize_training_evaluation(uuid)'::regprocedure)
  and (select prosrc like '%current_user <> ''service_role''%' from pg_proc where oid = 'private.recover_abandoned_training_evaluations(integer,integer)'::regprocedure),
  'every lifecycle RPC explicitly enforces current_user'
);

select ok(
  has_function_privilege('service_role', 'private.record_training_evaluation(uuid,jsonb,text,uuid,uuid[],uuid[],jsonb,boolean,text)', 'EXECUTE')
  and has_function_privilege('service_role', 'private.promote_model(uuid,uuid)', 'EXECUTE')
  and has_function_privilege('service_role', 'private.finalize_training_evaluation(uuid)', 'EXECUTE')
  and has_function_privilege('service_role', 'private.recover_abandoned_training_evaluations(integer,integer)', 'EXECUTE'),
  'only the service contract is granted to service_role'
);
select ok(
  not has_function_privilege('anon', 'private.record_training_evaluation(uuid,jsonb,text,uuid,uuid[],uuid[],jsonb,boolean,text)', 'EXECUTE')
  and not has_function_privilege('authenticated', 'private.record_training_evaluation(uuid,jsonb,text,uuid,uuid[],uuid[],jsonb,boolean,text)', 'EXECUTE')
  and not has_function_privilege('anon', 'private.promote_model(uuid,uuid)', 'EXECUTE')
  and not has_function_privilege('authenticated', 'private.promote_model(uuid,uuid)', 'EXECUTE')
  and not has_function_privilege('anon', 'private.finalize_training_evaluation(uuid)', 'EXECUTE')
  and not has_function_privilege('authenticated', 'private.finalize_training_evaluation(uuid)', 'EXECUTE')
  and not has_function_privilege('anon', 'private.recover_abandoned_training_evaluations(integer,integer)', 'EXECUTE')
  and not has_function_privilege('authenticated', 'private.recover_abandoned_training_evaluations(integer,integer)', 'EXECUTE'),
  'anon and authenticated cannot execute any training RPC'
);
select ok(
  not exists (
    select 1
    from pg_proc as procedures
    cross join lateral aclexplode(coalesce(procedures.proacl, acldefault('f', procedures.proowner))) as grants
    where procedures.oid in (
      'private.record_training_evaluation(uuid,jsonb,text,uuid,uuid[],uuid[],jsonb,boolean,text)'::regprocedure,
      'private.promote_model(uuid,uuid)'::regprocedure,
      'private.finalize_training_evaluation(uuid)'::regprocedure,
      'private.recover_abandoned_training_evaluations(integer,integer)'::regprocedure
    )
      and grants.grantee = 0
      and grants.privilege_type = 'EXECUTE'
  ),
  'PUBLIC has no execute grant on training lifecycle RPCs'
);
select ok(not has_table_privilege('anon', 'private.consumed_validation_fixtures', 'SELECT'), 'anon cannot read consumed IDs');
select ok(
  not has_table_privilege('service_role', 'private.model_versions', 'INSERT')
  and not has_table_privilege('service_role', 'private.model_versions', 'UPDATE')
  and not has_table_privilege('service_role', 'private.training_runs', 'INSERT')
  and not has_table_privilege('service_role', 'private.training_runs', 'UPDATE'),
  'service role has no table-wide model or run mutation grants'
);
select ok(
  has_column_privilege('service_role', 'private.model_versions', 'version', 'INSERT')
  and not has_column_privilege('service_role', 'private.model_versions', 'id', 'INSERT')
  and has_column_privilege('service_role', 'private.model_versions', 'state', 'UPDATE')
  and not has_column_privilege('service_role', 'private.model_versions', 'parameters', 'UPDATE')
  and has_column_privilege('service_role', 'private.training_runs', 'id', 'INSERT')
  and not has_column_privilege('service_role', 'private.training_runs', 'started_at', 'INSERT')
  and has_column_privilege('service_role', 'private.training_runs', 'decision', 'UPDATE')
  and not has_column_privilege('service_role', 'private.training_runs', 'metrics', 'UPDATE'),
  'service role has only RPC-required model and run columns'
);

grant usage on schema private to authenticated;
grant execute on function private.finalize_training_evaluation(uuid) to authenticated;
set local role authenticated;
select throws_ok(
  $$select private.finalize_training_evaluation('00000000-0000-0000-0000-000000000001')$$,
  '42501',
  'service_role is required',
  'current_user is enforced even if execute is accidentally granted'
);
reset role;
revoke execute on function private.finalize_training_evaluation(uuid) from authenticated;
revoke usage on schema private from authenticated;

set local role anon;
select throws_ok(
  $$select count(*) from private.training_examples$$,
  '42501',
  null,
  'anon cannot read private training examples'
);
reset role;

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

set local role service_role;

insert into private.fixtures (
  id, provider, provider_fixture_id, competition, home_name, away_name,
  scheduled_at, status, finished_at
)
select
  ('50000000-0000-0000-0000-' || lpad(n::text, 12, '0'))::uuid,
  '365scores',
  'controlled-' || lpad(n::text, 3, '0'),
  'test league',
  'home ' || n,
  'away ' || n,
  '2026-02-01 10:00:00+00'::timestamptz + make_interval(days => n),
  'finished',
  '2026-02-01 12:00:00+00'::timestamptz + make_interval(days => n)
from generate_series(1, 190) as series(n);

insert into private.outcomes (fixture_id, home_score, away_score, source, confirmed, confirmed_at)
select id, 2, 0, 'test', true, scheduled_at + interval '2 hours'
from private.fixtures
where provider_fixture_id like 'controlled-%';

insert into private.live_snapshots (
  fixture_id, minute, score_home, score_away, normalized_stats,
  sanitized_provider_data, provider_observed_at, collected_at,
  quality, observation_bucket
)
select
  id, 60, 0, 0, '{}'::jsonb, '{}'::jsonb, scheduled_at,
  scheduled_at + interval '1 second', 'fresh', scheduled_at
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

select is((select count(*) from private.training_examples), 190::bigint, 'eligible fixtures are projected once');
select is((select jsonb_array_length(observations) from private.training_examples limit 1), 1, 'observations are grouped');

update private.predictions
set created_at = '2027-01-01 00:00:00+00'
where fixture_id = '50000000-0000-0000-0000-000000000190';
select is((select count(*) from private.training_examples), 189::bigint, 'post-outcome predictions never become evidence');
update private.predictions
set created_at = '2026-08-10 10:00:01+00'
where fixture_id = '50000000-0000-0000-0000-000000000190';

select throws_ok(
  $$
    select private.record_training_evaluation(
      gen_random_uuid(),
      '{"factores":{"local":1.1,"visitante":0.9}}', 'new-code',
      '40000000-0000-0000-0000-000000000001',
      (select array_agg(id order by id) from private.fixtures where provider_fixture_id between 'controlled-001' and 'controlled-070'),
      (select array_agg(id order by id) from private.fixtures where provider_fixture_id between 'controlled-071' and 'controlled-099'),
      '{"candidate":{"brier":0.4,"log_loss":0.8},"champion":{"brier":0.4,"log_loss":0.8},"baseline":{"brier":0.5,"log_loss":0.9}}',
      false, 'too small'
    )
  $$,
  '22023', null, 'exactly 30 validation fixtures are required'
);

update private.outcomes
set confirmed_at = '2027-01-01 00:00:00+00'
where fixture_id = '50000000-0000-0000-0000-000000000001';
select throws_ok(
  $$
    select private.record_training_evaluation(
      gen_random_uuid(),
      '{"factores":{"local":1.1,"visitante":0.9}}', 'new-code',
      '40000000-0000-0000-0000-000000000001',
      (select array_agg(id order by id) from private.fixtures where provider_fixture_id between 'controlled-001' and 'controlled-070'),
      (select array_agg(id order by id) from private.fixtures where provider_fixture_id between 'controlled-131' and 'controlled-160'),
      '{"candidate":{"brier":0.4,"log_loss":0.8},"champion":{"brier":0.4,"log_loss":0.8},"baseline":{"brier":0.5,"log_loss":0.9}}',
      false, 'leaked chronology'
    )
  $$,
  '22023', null, 'training outcomes must be confirmed before validation begins'
);
update private.outcomes
set confirmed_at = scheduled_at + interval '2 hours'
from private.fixtures
where private.outcomes.fixture_id = private.fixtures.id
  and private.fixtures.id = '50000000-0000-0000-0000-000000000001';

select throws_ok(
  $$
    select private.record_training_evaluation(
      gen_random_uuid(),
      '{"factores":{"local":1.1,"visitante":0.9}}', 'new-code',
      '40000000-0000-0000-0000-000000000001',
      (select array_agg(id order by id) from private.fixtures where provider_fixture_id between 'controlled-001' and 'controlled-070'),
      (select array_agg(id order by id) from private.fixtures where provider_fixture_id between 'controlled-131' and 'controlled-160'),
      '{"candidate":{"brier":0.3,"log_loss":0.7},"champion":{"brier":0.4,"log_loss":0.8},"baseline":{"brier":0.5,"log_loss":0.9}}',
      false, 'wrong decision'
    )
  $$,
  '22023', null, 'stored decision must match the strict metric gate'
);

select set_config(
  'test.rejected_run_id',
  (select id::text from private.record_training_evaluation(
    '71000000-0000-0000-0000-000000000001',
    '{"factores":{"local":1.1,"visitante":0.9}}', 'new-code',
    '40000000-0000-0000-0000-000000000001',
    (select array_agg(id order by id) from private.fixtures where provider_fixture_id between 'controlled-001' and 'controlled-070'),
    (select array_agg(id order by id) from private.fixtures where provider_fixture_id between 'controlled-131' and 'controlled-160'),
    '{"candidate":{"brier":0.4,"log_loss":0.8},"champion":{"brier":0.4,"log_loss":0.8},"baseline":{"brier":0.5,"log_loss":0.9}}',
    false, 'candidate did not strictly improve both metrics'
  )),
  true
);

select is((select state::text from private.model_versions where id = (select candidate_model_id from private.training_runs where id = current_setting('test.rejected_run_id')::uuid)), 'rejected', 'losing candidate is retained');
select is((select decision::text from private.training_runs where id = current_setting('test.rejected_run_id')::uuid), 'rejected', 'losing run is terminal');
select is((select count(*) from private.consumed_validation_fixtures where training_run_id = current_setting('test.rejected_run_id')::uuid), 30::bigint, 'rejected validation IDs are consumed');

select throws_ok(
  $$
    insert into private.model_versions (
      version, state, parameters, parameter_hash, code_version,
      previous_model_id, train_size, validation_size, brier, log_loss
    ) values (
      'noncanonical-direct-candidate', 'candidate', '{}', 'not-a-sha256',
      'new-code', '40000000-0000-0000-0000-000000000001', 70, 30, 0.2, 0.6
    )
  $$,
  '22023', 'new service models require controlled canonical provenance',
  'service role cannot insert a new noncanonical candidate'
);

reset role;
select throws_ok(
  $$update private.training_runs set train_fixture_ids = '{}' where id = current_setting('test.rejected_run_id')::uuid$$,
  '22023', 'training run evidence is immutable', 'train IDs are immutable'
);
select throws_ok(
  $$update private.training_runs set validation_fixture_ids = '{}' where id = current_setting('test.rejected_run_id')::uuid$$,
  '22023', 'training run evidence is immutable', 'validation IDs are immutable'
);
select throws_ok(
  $$update private.training_runs set metrics = '{}' where id = current_setting('test.rejected_run_id')::uuid$$,
  '22023', 'training run evidence is immutable', 'metrics are immutable'
);
select throws_ok(
  $$update private.training_runs set candidate_model_id = null where id = current_setting('test.rejected_run_id')::uuid$$,
  '22023', 'training run evidence is immutable', 'candidate binding is immutable'
);
select throws_ok(
  $$update private.training_runs set parameter_hash = repeat('f', 64) where id = current_setting('test.rejected_run_id')::uuid$$,
  '22023', 'training run evidence is immutable', 'run hash is immutable'
);
select throws_ok(
  $$update private.training_runs set decision = 'running', finished_at = null where id = current_setting('test.rejected_run_id')::uuid$$,
  '22023', 'invalid training run lifecycle transition', 'terminal runs cannot be reopened'
);
select throws_ok(
  $$update private.model_versions set parameters = '{"tampered":true}' where id = (select candidate_model_id from private.training_runs where id = current_setting('test.rejected_run_id')::uuid)$$,
  '22023', 'model evidence is immutable', 'model parameters are immutable'
);
select throws_ok(
  $$update private.model_versions set id = '49999999-9999-9999-9999-999999999999' where id = (select candidate_model_id from private.training_runs where id = current_setting('test.rejected_run_id')::uuid)$$,
  '22023', 'model evidence is immutable', 'model IDs are immutable in the trigger'
);
set local role service_role;

select set_config(
  'test.holdout_collision_run_id',
  (select id::text from private.record_training_evaluation(
      '71000000-0000-0000-0000-000000000002',
      '{"duplicate_marker":true}', 'new-code',
      '40000000-0000-0000-0000-000000000001',
      (select array_agg(id order by id) from private.fixtures where provider_fixture_id between 'controlled-001' and 'controlled-070'),
      (select array_agg(id order by id) from private.fixtures where provider_fixture_id between 'controlled-131' and 'controlled-160'),
      '{"candidate":{"brier":0.4,"log_loss":0.8},"champion":{"brier":0.4,"log_loss":0.8},"baseline":{"brier":0.5,"log_loss":0.9}}',
      false, 'duplicate'
  )),
  true
);
select is((select decision::text from private.training_runs where id = current_setting('test.holdout_collision_run_id')::uuid), 'failed', 'holdout collision persists a terminal loser run');
select is((select candidate_model_id from private.training_runs where id = current_setting('test.holdout_collision_run_id')::uuid), null::uuid, 'holdout collision rolls back its candidate binding');
select is((select count(*) from private.model_versions where parameters ? 'duplicate_marker'), 0::bigint, 'holdout collision rolls back its orphan candidate');
select is((select count(*) from private.consumed_validation_fixtures where fixture_id between '50000000-0000-0000-0000-000000000131' and '50000000-0000-0000-0000-000000000160'), 30::bigint, 'holdout collision does not duplicate consumption rows');
select is((select decision::text from private.finalize_training_evaluation(current_setting('test.holdout_collision_run_id')::uuid)), 'failed', 'terminal collision can be reconciled after an uncertain response');

select set_config(
  'test.accepted_run_id',
  (select id::text from private.record_training_evaluation(
    '72000000-0000-0000-0000-000000000001',
    '{"factores":{"local":1.2,"visitante":0.8}}', 'new-code',
    '40000000-0000-0000-0000-000000000001',
    (select array_agg(id order by id desc) from private.fixtures where provider_fixture_id between 'controlled-001' and 'controlled-100'),
    (select array_agg(id order by id desc) from private.fixtures where provider_fixture_id between 'controlled-101' and 'controlled-130'),
    '{"candidate":{"brier":0.3,"log_loss":0.7},"champion":{"brier":0.4,"log_loss":0.8},"baseline":{"brier":0.5,"log_loss":0.9}}',
    true, 'candidate strictly improved both metrics'
  )),
  true
);

select is(
  (select parameter_hash from private.training_runs where id = current_setting('test.accepted_run_id')::uuid),
  '9a8c2e8920d02b731f9ff5c77fbe4040fb3d59b05a239e164c493c38e519fa81',
  'PostgreSQL computes the canonical hash with sorted ID sets'
);
select is(
  (select models.parameter_hash from private.model_versions as models join private.training_runs as runs on runs.candidate_model_id = models.id where runs.id = current_setting('test.accepted_run_id')::uuid),
  (select parameter_hash from private.training_runs where id = current_setting('test.accepted_run_id')::uuid),
  'candidate and run bind the same SQL-computed hash'
);
select is((select state::text from private.model_versions where id = (select candidate_model_id from private.training_runs where id = current_setting('test.accepted_run_id')::uuid)), 'candidate', 'winning candidate exists before promotion');
select is((select decision::text from private.training_runs where id = current_setting('test.accepted_run_id')::uuid), 'running', 'winning run remains pending before promotion');

select set_config(
  'test.identical_loser_run_id',
  (select id::text from private.record_training_evaluation(
    '72000000-0000-0000-0000-000000000002',
    '{"factores":{"local":1.2,"visitante":0.8}}', 'new-code',
    '40000000-0000-0000-0000-000000000001',
    (select array_agg(id order by id) from private.fixtures where provider_fixture_id between 'controlled-001' and 'controlled-100'),
    (select array_agg(id order by id) from private.fixtures where provider_fixture_id between 'controlled-101' and 'controlled-130'),
    '{"candidate":{"brier":0.3,"log_loss":0.7},"champion":{"brier":0.4,"log_loss":0.8},"baseline":{"brier":0.5,"log_loss":0.9}}',
    true, 'candidate strictly improved both metrics'
  )),
  true
);
select is((select decision::text from private.training_runs where id = current_setting('test.identical_loser_run_id')::uuid), 'failed', 'serialized identical evaluation persists one terminal loser');
select is((select candidate_model_id from private.training_runs where id = current_setting('test.identical_loser_run_id')::uuid), null::uuid, 'identical loser has no orphan candidate binding');
select is((select count(*) from private.model_versions where parameter_hash = '9a8c2e8920d02b731f9ff5c77fbe4040fb3d59b05a239e164c493c38e519fa81'), 1::bigint, 'identical evaluations create exactly one candidate');
select is((select count(*) from private.consumed_validation_fixtures where fixture_id between '50000000-0000-0000-0000-000000000101' and '50000000-0000-0000-0000-000000000130'), 30::bigint, 'identical loser cannot reuse or duplicate the winner holdout');

select throws_ok(
  $$select private.promote_model((select candidate_model_id from private.training_runs where id = current_setting('test.accepted_run_id')::uuid), '99999999-9999-9999-9999-999999999999')$$,
  'P0002', 'current model not found', 'failed promotion aborts transactionally'
);
select is((select state::text from private.model_versions where id = (select candidate_model_id from private.training_runs where id = current_setting('test.accepted_run_id')::uuid)), 'candidate', 'promotion rollback leaves candidate pending');
select is((select decision::text from private.training_runs where id = current_setting('test.accepted_run_id')::uuid), 'running', 'promotion rollback leaves run pending');

select lives_ok(
  $$select private.promote_model((select candidate_model_id from private.training_runs where id = current_setting('test.accepted_run_id')::uuid), '40000000-0000-0000-0000-000000000001')$$,
  'Task2 promotion signature still promotes valid evidence'
);
select is((select decision::text from private.finalize_training_evaluation(current_setting('test.accepted_run_id')::uuid)), 'succeeded', 'reconciliation recognizes an already committed promotion');

select set_config(
  'test.stale_champion_run_id',
  (select id::text from private.record_training_evaluation(
    '72000000-0000-0000-0000-000000000003',
    '{"factores":{"local":1.3,"visitante":0.7}}', 'new-code',
    '40000000-0000-0000-0000-000000000001',
    (select array_agg(id order by id) from private.fixtures where provider_fixture_id between 'controlled-001' and 'controlled-130'),
    (select array_agg(id order by id) from private.fixtures where provider_fixture_id between 'controlled-161' and 'controlled-190'),
    '{"candidate":{"brier":0.2,"log_loss":0.6},"champion":{"brier":0.3,"log_loss":0.7},"baseline":{"brier":0.5,"log_loss":0.9}}',
    true, 'candidate strictly improved both metrics'
  )),
  true
);
select is((select decision::text from private.training_runs where id = current_setting('test.stale_champion_run_id')::uuid), 'failed', 'evaluation losing the promotion race is terminal');
select is((select candidate_model_id from private.training_runs where id = current_setting('test.stale_champion_run_id')::uuid), null::uuid, 'stale-champion loser has no candidate');
select is((select count(*) from private.consumed_validation_fixtures where fixture_id between '50000000-0000-0000-0000-000000000161' and '50000000-0000-0000-0000-000000000190'), 0::bigint, 'stale-champion loser does not claim holdout evidence');

reset role;
insert into private.model_versions (
  id, version, state, parameters, parameter_hash, code_version,
  previous_model_id, train_size, validation_size, brier, log_loss
)
values (
  '60000000-0000-0000-0000-000000000001', 'abandoned-candidate', 'candidate',
  '{"factores":{"local":1.3,"visitante":0.7}}', repeat('7', 64), 'new-code',
  (select candidate_model_id from private.training_runs where id = current_setting('test.accepted_run_id')::uuid),
  130, 30, 0.2, 0.6
);
insert into private.training_runs (
  id, started_at, train_fixture_ids, validation_fixture_ids,
  candidate_model_id, parameter_hash, metrics, decision, reason
)
values (
  '70000000-0000-0000-0000-000000000001', now() - interval '901 seconds',
  (select array_agg(id order by id) from private.fixtures where provider_fixture_id between 'controlled-001' and 'controlled-130'),
  (select array_agg(id order by id) from private.fixtures where provider_fixture_id between 'controlled-161' and 'controlled-190'),
  '60000000-0000-0000-0000-000000000001', repeat('7', 64),
  '{"candidate":{"brier":0.2,"log_loss":0.6},"champion":{"brier":0.3,"log_loss":0.7},"baseline":{"brier":0.5,"log_loss":0.9}}',
  'running', 'awaiting promotion'
);
insert into private.consumed_validation_fixtures (fixture_id, training_run_id)
select id, '70000000-0000-0000-0000-000000000001'
from private.fixtures
where provider_fixture_id between 'controlled-161' and 'controlled-190';

set local role service_role;
select is((select count(*) from private.recover_abandoned_training_evaluations(900, 100)), 1::bigint, 'stale recovery is callable and bounded');
select is((select state::text from private.model_versions where id = '60000000-0000-0000-0000-000000000001'), 'rejected', 'stale candidate is rejected');
select is((select decision::text from private.training_runs where id = '70000000-0000-0000-0000-000000000001'), 'failed', 'stale run becomes terminal failed');
select throws_ok(
  $$select private.recover_abandoned_training_evaluations(899, 100)$$,
  '22023', 'safe recovery age and bounded batch are required', 'unsafe cleanup age is rejected'
);

select is((select count(*) from private.consumed_validation_fixtures), 90::bigint, 'accepted, rejected, and recovered holdouts remain consumed');

reset role;
select * from finish();
rollback;
