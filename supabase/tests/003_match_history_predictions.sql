begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(10);

select has_function(
  'private',
  'match_history_predictions',
  array['uuid', 'integer'],
  'bounded private match-history prediction RPC exists'
);
select is(
  pg_get_function_result('private.match_history_predictions(uuid,integer)'::regprocedure),
  'TABLE(snapshot_id uuid, probabilities jsonb, lambda_adjusted jsonb, created_at timestamp with time zone, model_version text)',
  'RPC returns only the explicit backend merge columns'
);
select ok(
  not (select prosecdef from pg_proc where oid = 'private.match_history_predictions(uuid,integer)'::regprocedure)
  and 'search_path=""' = any(
    coalesce(
      (select proconfig from pg_proc where oid = 'private.match_history_predictions(uuid,integer)'::regprocedure),
      array[]::text[]
    )
  ),
  'RPC is SECURITY INVOKER with an empty search_path'
);
select ok(
  has_function_privilege('service_role', 'private.match_history_predictions(uuid,integer)', 'EXECUTE'),
  'service_role can execute the private history RPC'
);
select ok(
  not has_function_privilege('anon', 'private.match_history_predictions(uuid,integer)', 'EXECUTE')
  and not has_function_privilege('authenticated', 'private.match_history_predictions(uuid,integer)', 'EXECUTE')
  and not exists (
    select 1
    from pg_proc as procedures
    cross join lateral aclexplode(
      coalesce(procedures.proacl, acldefault('f', procedures.proowner))
    ) as grants
    where procedures.oid = 'private.match_history_predictions(uuid,integer)'::regprocedure
      and grants.grantee = 0
      and grants.privilege_type = 'EXECUTE'
  ),
  'PUBLIC, anon, and authenticated cannot execute the RPC'
);

insert into private.fixtures (
  id, provider, provider_fixture_id, competition, home_name, away_name,
  scheduled_at, status
)
values (
  '71000000-0000-0000-0000-000000000001',
  '365scores',
  'history-test-fixture',
  'test league',
  'history home',
  'history away',
  '2026-09-30 10:00:00+00',
  'live'
);

insert into private.model_versions (
  id, version, state, parameters, parameter_hash, code_version,
  evidence_origin, train_size, validation_size
)
values
  (
    '72000000-0000-0000-0000-000000000001',
    'history-candidate-old',
    'candidate',
    '{}',
    repeat('7', 64),
    'history-test',
    'controlled_training',
    70,
    30
  ),
  (
    '72000000-0000-0000-0000-000000000002',
    'history-candidate-new',
    'candidate',
    '{}',
    repeat('8', 64),
    'history-test',
    'controlled_training',
    70,
    30
  );

insert into private.live_snapshots (
  id, fixture_id, minute, score_home, score_away, normalized_stats,
  sanitized_provider_data, provider_observed_at, collected_at,
  quality, observation_bucket
)
select
  ('73000000-0000-0000-0000-' || lpad(n::text, 12, '0'))::uuid,
  '71000000-0000-0000-0000-000000000001',
  n,
  0,
  0,
  '{}'::jsonb,
  '{}'::jsonb,
  '2026-09-30 10:00:00+00'::timestamptz + make_interval(mins => n),
  '2026-09-30 10:00:01+00'::timestamptz + make_interval(mins => n),
  'fresh',
  '2026-09-30 10:00:00+00'::timestamptz + make_interval(mins => n)
from generate_series(1, 95) as series(n);

insert into private.predictions (
  fixture_id, snapshot_id, model_version_id, lambda_base,
  lambda_adjusted, probabilities, explanation, quality, created_at
)
select
  '71000000-0000-0000-0000-000000000001',
  snapshots.id,
  active_model.id,
  '{"home":1.0,"away":0.5}'::jsonb,
  '{"home":1.0,"away":0.5}'::jsonb,
  '{"home":45,"draw":30,"away":25}'::jsonb,
  '{}'::jsonb,
  'fresh',
  snapshots.provider_observed_at + interval '1 second'
from private.live_snapshots as snapshots
cross join lateral (
  select models.id
  from private.model_versions as models
  where models.state = 'active'
  limit 1
) as active_model
where snapshots.fixture_id = '71000000-0000-0000-0000-000000000001'
  and snapshots.minute <> 94;

insert into private.predictions (
  fixture_id, snapshot_id, model_version_id, lambda_base,
  lambda_adjusted, probabilities, explanation, quality, created_at
)
select
  '71000000-0000-0000-0000-000000000001',
  snapshots.id,
  candidate.model_id,
  '{"home":2.0,"away":0.2}'::jsonb,
  '{"home":2.0,"away":0.2}'::jsonb,
  candidate.probabilities,
  '{}'::jsonb,
  'fresh',
  snapshots.provider_observed_at + candidate.created_offset
from private.live_snapshots as snapshots
cross join (
  values
    (
      '72000000-0000-0000-0000-000000000001'::uuid,
      '{"home":70,"draw":20,"away":10}'::jsonb,
      interval '2 seconds'
    ),
    (
      '72000000-0000-0000-0000-000000000002'::uuid,
      '{"home":80,"draw":10,"away":10}'::jsonb,
      interval '3 seconds'
    )
) as candidate(model_id, probabilities, created_offset)
where snapshots.fixture_id = '71000000-0000-0000-0000-000000000001'
  and snapshots.minute in (94, 95);

set local role service_role;

select is(
  (
    select count(*)
    from private.match_history_predictions(
      '71000000-0000-0000-0000-000000000001',
      500
    )
  ),
  90::bigint,
  'oversized input is capped to 90 output rows'
);
select is(
  (
    select count(*)
    from private.match_history_predictions(
      '71000000-0000-0000-0000-000000000001',
      500
    ) as selected
    join private.live_snapshots as snapshots on snapshots.id = selected.snapshot_id
    where snapshots.minute between 6 and 95
  ),
  90::bigint,
  'the RPC ranks predictions only for the newest bounded snapshots'
);
select is(
  (
    select selected.model_version
    from private.match_history_predictions(
      '71000000-0000-0000-0000-000000000001',
      90
    ) as selected
    join private.live_snapshots as snapshots on snapshots.id = selected.snapshot_id
    where snapshots.minute = 95
  ),
  (select models.version from private.model_versions as models where models.state = 'active' limit 1),
  'active-model prediction outranks newer predictions for the exact snapshot'
);
select is(
  (
    select selected.model_version
    from private.match_history_predictions(
      '71000000-0000-0000-0000-000000000001',
      90
    ) as selected
    join private.live_snapshots as snapshots on snapshots.id = selected.snapshot_id
    where snapshots.minute = 94
  ),
  'history-candidate-new',
  'newest exact-snapshot prediction wins when no active prediction exists'
);
select is(
  (
    select count(*)
    from private.match_history_predictions(
      '71000000-0000-0000-0000-000000000001',
      0
    )
  ),
  1::bigint,
  'nonpositive input is clamped to one row'
);

reset role;
select * from finish();
rollback;
