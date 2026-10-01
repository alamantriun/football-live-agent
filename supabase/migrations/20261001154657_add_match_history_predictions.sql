create function private.match_history_predictions(
  p_fixture_id uuid,
  p_limit integer default 90
)
returns table (
  snapshot_id uuid,
  probabilities jsonb,
  lambda_adjusted jsonb,
  created_at timestamptz,
  model_version text
)
language sql
stable
security invoker
set search_path = ''
as $$
  with bounded_snapshots as (
    select
      snapshots.id,
      snapshots.provider_observed_at
    from private.live_snapshots as snapshots
    where snapshots.fixture_id = p_fixture_id
    order by snapshots.provider_observed_at desc, snapshots.id desc
    limit least(greatest(coalesce(p_limit, 90), 1), 90)
  ),
  ranked as (
    select
      predictions.snapshot_id,
      predictions.probabilities,
      predictions.lambda_adjusted,
      predictions.created_at,
      models.version as model_version,
      bounded_snapshots.provider_observed_at,
      row_number() over (
        partition by predictions.snapshot_id
        order by
          (models.state = 'active') desc,
          predictions.created_at desc,
          predictions.id desc
      ) as preference
    from bounded_snapshots
    join private.predictions as predictions
      on predictions.snapshot_id = bounded_snapshots.id
     and predictions.fixture_id = p_fixture_id
    join private.model_versions as models
      on models.id = predictions.model_version_id
  )
  select
    ranked.snapshot_id,
    ranked.probabilities,
    ranked.lambda_adjusted,
    ranked.created_at,
    ranked.model_version
  from ranked
  where ranked.preference = 1
  order by ranked.provider_observed_at desc, ranked.snapshot_id desc
  limit least(greatest(coalesce(p_limit, 90), 1), 90);
$$;

revoke all on function private.match_history_predictions(uuid, integer) from public, anon, authenticated, service_role;
grant execute on function private.match_history_predictions(uuid, integer) to service_role;
