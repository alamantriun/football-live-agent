create or replace function private.compute_training_hash(
  p_parameters jsonb,
  p_train_fixture_ids uuid[],
  p_validation_fixture_ids uuid[],
  p_code_version text
)
returns text
language plpgsql
immutable
strict
parallel safe
security invoker
set search_path = ''
as $$
declare
  v_payload text;
begin
  v_payload := private.canonical_jsonb(jsonb_build_object(
    'code_version', p_code_version,
    'parameters', p_parameters,
    'train_fixture_ids', coalesce((
      select jsonb_agg(ids.fixture_id::text order by ids.fixture_id::text collate "C")
      from unnest(p_train_fixture_ids) as ids(fixture_id)
    ), '[]'::jsonb),
    'validation_fixture_ids', coalesce((
      select jsonb_agg(ids.fixture_id::text order by ids.fixture_id::text collate "C")
      from unnest(p_validation_fixture_ids) as ids(fixture_id)
    ), '[]'::jsonb)
  ));

  return encode(extensions.digest(convert_to(v_payload, 'UTF8'), 'sha256'), 'hex');
end;
$$;

revoke all on function private.compute_training_hash(jsonb, uuid[], uuid[], text)
  from public, anon, authenticated, service_role;
grant execute on function private.compute_training_hash(jsonb, uuid[], uuid[], text)
  to service_role;
