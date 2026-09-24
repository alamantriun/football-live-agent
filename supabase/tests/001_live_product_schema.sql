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
