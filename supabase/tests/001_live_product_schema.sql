begin;
select plan(39);

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
select ok(
  has_function_privilege('service_role', 'private.finish_job(uuid,uuid,text,jsonb,text)', 'EXECUTE'),
  'service can finish jobs with a fencing token'
);
select ok(
  not has_function_privilege('anon', 'private.finish_job(uuid,uuid,text,jsonb,text)', 'EXECUTE'),
  'anon cannot finish jobs'
);

set local role anon;
select throws_ok(
  $$select count(*) from private.fixtures$$,
  '42501',
  null,
  'anon private-table reads fail at execution time'
);
select lives_ok(
  $$select count(*) from public.live_matches$$,
  'anon can execute reads through the safe public view'
);
select throws_ok(
  $$
    insert into public.live_match_projection (
      public_id, competition, home_name, away_name, status, data_status
    ) values (
      '90000000-0000-0000-0000-000000000001', 'test', 'home', 'away', 'live', 'fresh'
    )
  $$,
  '42501',
  null,
  'anon projection writes fail at execution time'
);
reset role;

set local role service_role;

select
  set_config('test.old_run_id', id::text, true),
  set_config('test.old_request_id', request_id::text, true)
from private.claim_job('collect', 'fence-test', 30);

update private.job_runs
set lease_until = now() - interval '1 second'
where id = current_setting('test.old_run_id')::uuid;

select
  set_config('test.new_run_id', id::text, true),
  set_config('test.new_request_id', request_id::text, true)
from private.claim_job('collect', 'fence-test', 30);

select is(
  current_setting('test.new_run_id'),
  current_setting('test.old_run_id'),
  'lease reacquisition keeps the same job row'
);
select isnt(
  current_setting('test.new_request_id'),
  current_setting('test.old_request_id'),
  'lease reacquisition rotates the fencing token'
);
select throws_ok(
  format(
    'select private.finish_job(%L::uuid, %L::uuid, %L, %L::jsonb, null)',
    current_setting('test.old_run_id'),
    current_setting('test.old_request_id'),
    'succeeded',
    '{}'
  ),
  'P0002',
  null,
  'an expired worker cannot finish a reacquired lease'
);
select lives_ok(
  format(
    'select private.finish_job(%L::uuid, %L::uuid, %L, %L::jsonb, null)',
    current_setting('test.new_run_id'),
    current_setting('test.new_request_id'),
    'succeeded',
    '{"stored":1}'
  ),
  'the renewed fencing token can finish the lease'
);
select is(
  (select state::text from private.job_runs where id = current_setting('test.new_run_id')::uuid),
  'succeeded',
  'the fenced job records its terminal state'
);

insert into private.fixtures (
  provider, provider_fixture_id, competition, home_name, away_name,
  scheduled_at, status, finished_at
)
select
  '365scores',
  'test-train-' || lpad(n::text, 3, '0'),
  'test league',
  'train home ' || n,
  'train away ' || n,
  '2026-01-01 10:00:00+00'::timestamptz + make_interval(mins => n),
  'finished',
  '2026-01-01 12:00:00+00'::timestamptz + make_interval(mins => n)
from generate_series(1, 70) as series(n);

insert into private.fixtures (
  provider, provider_fixture_id, competition, home_name, away_name,
  scheduled_at, status, finished_at
)
select
  '365scores',
  prefix || lpad(n::text, 3, '0'),
  'test league',
  prefix || ' home ' || n,
  prefix || ' away ' || n,
  scheduled_base + make_interval(mins => n),
  'finished',
  scheduled_base + make_interval(mins => n + 120)
from (
  values
    ('test-valid-', '2026-01-03 10:00:00+00'::timestamptz),
    ('test-badvalid-', '2026-01-01 08:00:00+00'::timestamptz)
) as groups(prefix, scheduled_base)
cross join generate_series(1, 30) as series(n);

insert into private.fixtures (
  provider, provider_fixture_id, competition, home_name, away_name,
  scheduled_at, status, finished_at
)
values
  ('365scores', 'test-no-outcome', 'test league', 'no outcome home', 'no outcome away', '2026-01-01 10:00:00+00', 'finished', '2026-01-01 12:00:00+00'),
  ('365scores', 'test-no-snapshot', 'test league', 'no snapshot home', 'no snapshot away', '2026-01-03 10:00:00+00', 'finished', '2026-01-03 12:00:00+00');

insert into private.outcomes (
  fixture_id, home_score, away_score, source, confirmed, confirmed_at
)
select
  id,
  1,
  0,
  'test',
  true,
  case
    when provider_fixture_id like 'test-train-%'
      then '2026-01-02 00:00:00+00'::timestamptz
    else '2026-01-04 00:00:00+00'::timestamptz
  end
from private.fixtures
where provider_fixture_id like 'test-%'
  and provider_fixture_id <> 'test-no-outcome';

insert into private.live_snapshots (
  fixture_id, minute, score_home, score_away, normalized_stats,
  sanitized_provider_data, provider_observed_at, collected_at,
  quality, observation_bucket
)
select
  id,
  75,
  1,
  0,
  '{}'::jsonb,
  '{}'::jsonb,
  case
    when provider_fixture_id like 'test-valid-%'
      then '2026-01-03 00:00:00+00'::timestamptz
    else '2026-01-01 00:00:00+00'::timestamptz
  end,
  case
    when provider_fixture_id like 'test-valid-%'
      then '2026-01-03 00:00:01+00'::timestamptz
    else '2026-01-01 00:00:01+00'::timestamptz
  end,
  'fresh',
  case
    when provider_fixture_id like 'test-valid-%'
      then '2026-01-03 00:00:00+00'::timestamptz
    else '2026-01-01 00:00:00+00'::timestamptz
  end
from private.fixtures
where provider_fixture_id like 'test-valid-%'
   or provider_fixture_id like 'test-badvalid-%';

insert into private.model_versions (
  id, version, state, parameters, parameter_hash, code_version,
  train_size, validation_size, brier, log_loss, activated_at
)
values
  ('10000000-0000-0000-0000-000000000001', 'baseline', 'active', '{}', 'hash-baseline', 'test-code', 70, 30, 0.40, 0.80, now()),
  ('10000000-0000-0000-0000-000000000002', 'candidate-invalid', 'candidate', '{}', 'hash-invalid', 'test-code', 70, 30, 0.30, 0.70, null),
  ('10000000-0000-0000-0000-000000000003', 'candidate-valid', 'candidate', '{}', 'hash-valid', 'test-code', 70, 30, 0.30, 0.70, null),
  ('10000000-0000-0000-0000-000000000004', 'candidate-duplicate', 'candidate', '{}', 'hash-duplicate', 'test-code', 70, 30, 0.30, 0.70, null);

insert into private.training_runs (
  id, train_fixture_ids, validation_fixture_ids,
  candidate_model_id, metrics, decision
)
select
  run_id,
  (select array_agg(id order by provider_fixture_id) from private.fixtures where provider_fixture_id like 'test-train-%'),
  (select array_agg(id order by provider_fixture_id) from private.fixtures where provider_fixture_id like 'test-valid-%'),
  candidate_id,
  jsonb_build_object('brier', 0.30, 'log_loss', 0.70),
  'running'::private.run_state
from (
  values
    ('20000000-0000-0000-0000-000000000002'::uuid, '10000000-0000-0000-0000-000000000002'::uuid),
    ('20000000-0000-0000-0000-000000000003'::uuid, '10000000-0000-0000-0000-000000000003'::uuid),
    ('20000000-0000-0000-0000-000000000004'::uuid, '10000000-0000-0000-0000-000000000004'::uuid)
) as runs(run_id, candidate_id);

select throws_ok(
  $$
    insert into private.training_runs (
      id, train_fixture_ids, validation_fixture_ids, candidate_model_id, metrics, decision
    )
    select
      '20000000-0000-0000-0000-000000000005',
      train_fixture_ids,
      validation_fixture_ids,
      '10000000-0000-0000-0000-000000000004',
      metrics,
      'running'
    from private.training_runs
    where id = '20000000-0000-0000-0000-000000000004'
  $$,
  '23505',
  null,
  'a candidate can be bound to only one training run'
);

update private.training_runs
set train_fixture_ids = (
  select array_agg(id order by provider_fixture_id)
  from private.fixtures
  where provider_fixture_id between 'test-train-001' and 'test-train-069'
)
where id = '20000000-0000-0000-0000-000000000002';
select throws_ok(
  $$select private.promote_model('10000000-0000-0000-0000-000000000002', '10000000-0000-0000-0000-000000000001')$$,
  '22023',
  null,
  'promotion rejects insufficient training cardinality'
);

update private.training_runs
set train_fixture_ids = (
  select array_agg(id order by provider_fixture_id)
  from private.fixtures
  where provider_fixture_id between 'test-train-001' and 'test-train-069'
) || array[null::uuid]
where id = '20000000-0000-0000-0000-000000000002';
select throws_ok(
  $$select private.promote_model('10000000-0000-0000-0000-000000000002', '10000000-0000-0000-0000-000000000001')$$,
  '22023',
  null,
  'promotion rejects null fixture IDs'
);

update private.training_runs
set train_fixture_ids = (
      select array_agg(id order by provider_fixture_id)
      from private.fixtures
      where provider_fixture_id like 'test-train-%'
    ),
    validation_fixture_ids = array[
      (select id from private.fixtures where provider_fixture_id = 'test-train-001')
    ] || (
      select array_agg(id order by provider_fixture_id)
      from private.fixtures
      where provider_fixture_id between 'test-valid-001' and 'test-valid-029'
    )
where id = '20000000-0000-0000-0000-000000000002';
select throws_ok(
  $$select private.promote_model('10000000-0000-0000-0000-000000000002', '10000000-0000-0000-0000-000000000001')$$,
  '22023',
  null,
  'promotion rejects train-validation overlap'
);

update private.training_runs
set train_fixture_ids = (
      select array_agg(id order by provider_fixture_id)
      from private.fixtures
      where provider_fixture_id between 'test-train-001' and 'test-train-069'
    ) || array[
      (select id from private.fixtures where provider_fixture_id = 'test-no-outcome')
    ],
    validation_fixture_ids = (
      select array_agg(id order by provider_fixture_id)
      from private.fixtures
      where provider_fixture_id like 'test-valid-%'
    )
where id = '20000000-0000-0000-0000-000000000002';
select throws_ok(
  $$select private.promote_model('10000000-0000-0000-0000-000000000002', '10000000-0000-0000-0000-000000000001')$$,
  '22023',
  null,
  'promotion rejects fixtures without confirmed outcomes'
);

update private.training_runs
set train_fixture_ids = (
      select array_agg(id order by provider_fixture_id)
      from private.fixtures
      where provider_fixture_id like 'test-train-%'
    ),
    validation_fixture_ids = (
      select array_agg(id order by provider_fixture_id)
      from private.fixtures
      where provider_fixture_id between 'test-valid-001' and 'test-valid-029'
    ) || array[
      (select id from private.fixtures where provider_fixture_id = 'test-no-snapshot')
    ]
where id = '20000000-0000-0000-0000-000000000002';
select throws_ok(
  $$select private.promote_model('10000000-0000-0000-0000-000000000002', '10000000-0000-0000-0000-000000000001')$$,
  '22023',
  null,
  'promotion rejects validation fixtures without observations'
);

update private.training_runs
set validation_fixture_ids = (
  select array_agg(id order by provider_fixture_id)
  from private.fixtures
  where provider_fixture_id like 'test-badvalid-%'
)
where id = '20000000-0000-0000-0000-000000000002';
select throws_ok(
  $$select private.promote_model('10000000-0000-0000-0000-000000000002', '10000000-0000-0000-0000-000000000001')$$,
  '22023',
  null,
  'promotion rejects validation observed before training outcomes were known'
);

update private.training_runs
set validation_fixture_ids = (
      select array_agg(id order by provider_fixture_id)
      from private.fixtures
      where provider_fixture_id like 'test-valid-%'
    ),
    metrics = jsonb_build_object('brier', 0.29, 'log_loss', 0.70)
where id = '20000000-0000-0000-0000-000000000002';
select throws_ok(
  $$select private.promote_model('10000000-0000-0000-0000-000000000002', '10000000-0000-0000-0000-000000000001')$$,
  '22023',
  null,
  'promotion rejects candidate and run metric mismatch'
);

update private.training_runs
set metrics = jsonb_build_object('brier', 0.30, 'log_loss', 0.70)
where id = '20000000-0000-0000-0000-000000000002';
update private.model_versions
set brier = 'NaN'::double precision
where id = '10000000-0000-0000-0000-000000000002';
select throws_ok(
  $$select private.promote_model('10000000-0000-0000-0000-000000000002', '10000000-0000-0000-0000-000000000001')$$,
  '22023',
  null,
  'promotion rejects non-finite metrics'
);

update private.model_versions
set brier = 0.30
where id = '10000000-0000-0000-0000-000000000002';
update private.training_runs
set decision = 'failed'
where id = '20000000-0000-0000-0000-000000000002';
select throws_ok(
  $$select private.promote_model('10000000-0000-0000-0000-000000000002', '10000000-0000-0000-0000-000000000001')$$,
  '22023',
  null,
  'promotion rejects an ineligible training-run state'
);

select lives_ok(
  $$select private.promote_model('10000000-0000-0000-0000-000000000003', '10000000-0000-0000-0000-000000000001')$$,
  'valid evidence promotes atomically'
);
select is(
  (select state::text from private.model_versions where id = '10000000-0000-0000-0000-000000000001'),
  'retired',
  'the previous active model is retired'
);
select is(
  (select state::text from private.model_versions where id = '10000000-0000-0000-0000-000000000003'),
  'active',
  'the validated candidate becomes active'
);
select is(
  (select version from public.model_status_projection where singleton),
  'candidate-valid',
  'the public projection refreshes to the promoted version'
);
select is(
  (select brier from public.model_status_projection where singleton),
  0.30::double precision,
  'the public projection contains the bound run metrics'
);
select is(
  (select decision::text from private.training_runs where id = '20000000-0000-0000-0000-000000000003'),
  'succeeded',
  'the promoted training run is finalized'
);
select is(
  (select reason from private.training_runs where id = '20000000-0000-0000-0000-000000000003'),
  'candidate promoted',
  'the promoted training run records the decision reason'
);

reset role;
select * from finish();
rollback;
